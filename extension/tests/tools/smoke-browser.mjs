// Prueba de humo en un navegador real (opcional, no forma parte de `npm test`).
// Carga la extensión descomprimida en Edge/Chrome sin interfaz, abre una página de prueba local,
// enciende la captura y verifica que el content script arranque, resalte y envíe al servidor simulado.
//
// Uso:  node tests/tools/smoke-browser.mjs ["C:\ruta\a\msedge.exe"]
//
// Para poder encender la captura sin un clic humano, trabaja sobre una COPIA temporal de la extensión
// con permiso fijo solo para http://127.0.0.1 (la extensión real no lo tiene). Usa un perfil temporal.

import { spawn } from 'node:child_process';
import http from 'node:http';
import { cpSync, mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { ROOT } from './helpers.mjs';
import { createMockServer, MOCK_TOKEN } from './mock-server.mjs';

const CANDIDATES = [
  process.argv[2],
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  '/usr/bin/chromium', '/usr/bin/google-chrome',
].filter(Boolean);
const browser = CANDIDATES.find((p) => existsSync(p));
if (!browser) { console.error('No encontré Edge ni Chrome. Pasá la ruta como argumento.'); process.exit(2); }

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const work = mkdtempSync(join(tmpdir(), 'aleph-lens-smoke-'));
const extDir = join(work, 'ext');
for (const part of ['manifest.json', 'src', 'icons']) cpSync(join(ROOT, part), join(extDir, part), { recursive: true });
const manifest = JSON.parse(readFileSync(join(extDir, 'manifest.json'), 'utf8'));
manifest.host_permissions = ['http://127.0.0.1/*'];
writeFileSync(join(extDir, 'manifest.json'), JSON.stringify(manifest, null, 2));

const mock = createMockServer();
const serverUrl = await mock.listen();
const page = http.createServer((req, res) => {
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(readFileSync(join(ROOT, 'tests/fixtures/generic.html')));
});
await new Promise((r) => page.listen(0, '127.0.0.1', r));
const pageUrl = `http://127.0.0.1:${page.address().port}/hilo/1`;

const port = 9300 + Math.floor(Math.random() * 500);
const child = spawn(browser, [
  '--headless=new', `--user-data-dir=${join(work, 'profile')}`, `--remote-debugging-port=${port}`,
  `--load-extension=${extDir}`, `--disable-extensions-except=${extDir}`,
  '--disable-features=DisableLoadExtensionCommandLineSwitch', '--no-first-run', '--no-default-browser-check',
  '--window-size=1200,900', 'about:blank',
], { stdio: 'ignore' });

const results = [];
const check = (name, ok, detail = '') => { results.push({ name, ok: Boolean(ok), detail }); console.log(`${ok ? 'OK  ' : 'FALLA'} ${name}${detail ? ` — ${detail}` : ''}`); };

async function targets() {
  const res = await fetch(`http://127.0.0.1:${port}/json/list`);
  return res.json();
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let id = 0;
    const pending = new Map();
    const errors = [];
    ws.onerror = () => reject(new Error('no se pudo abrir el WebSocket de depuración'));
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
      if (msg.method === 'Runtime.exceptionThrown') errors.push(msg.params.exceptionDetails.exception?.description || msg.params.exceptionDetails.text);
      if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(msg.params.type)) errors.push(msg.params.args.map((a) => a.value ?? a.description).join(' '));
    };
    const call = (method, params = {}) => new Promise((res) => { const n = ++id; pending.set(n, res); ws.send(JSON.stringify({ id: n, method, params })); });
    const evaluate = async (expression) => {
      const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
      if (r.result?.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text);
      return r.result?.result?.value;
    };
    ws.onopen = async () => { await call('Runtime.enable'); resolve({ call, evaluate, errors, close: () => ws.close() }); };
  });
}

let exitCode = 1;
try {
  let sw = null;
  for (let i = 0; i < 60 && !sw; i++) {
    await sleep(250);
    sw = (await targets().catch(() => [])).find((t) => t.type === 'service_worker' && t.url.includes('service-worker.js'));
  }
  check('la extensión carga y registra su service worker', sw, sw ? sw.url : 'este navegador no aceptó --load-extension');
  if (!sw) throw new Error('sin service worker');
  const extId = new URL(sw.url).host;

  const open = async (url) => (await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' })).json();
  const pageTarget = await open(pageUrl);
  const panelTarget = await open(`chrome-extension://${extId}/src/sidepanel/panel.html`);
  await sleep(800);
  const panel = await connect(panelTarget.webSocketDebuggerUrl);
  const tab = await connect(pageTarget.webSocketDebuggerUrl);

  check('la página no tiene nada de Aleph Lens antes de encender', await tab.evaluate(`!document.querySelector('aleph-lens-root') && CSS.highlights.size === 0`));

  await panel.evaluate(`chrome.storage.local.set({ serverUrl: ${JSON.stringify(serverUrl)} }).then(() => chrome.storage.session.set({ token: ${JSON.stringify(MOCK_TOKEN)}, user: 'analista' })).then(() => true)`);
  const tabId = await panel.evaluate(`chrome.tabs.query({}).then((tabs) => (tabs.find((t) => (t.url || '').startsWith(${JSON.stringify(pageUrl)})) || {}).id)`);
  check('el panel encuentra la pestaña de prueba', Number.isInteger(tabId), `tabId ${tabId}`);
  const cases = await panel.evaluate(`chrome.runtime.sendMessage({ type: 'panel:listCases' })`);
  check('el service worker lista los casos del servidor simulado', cases && cases.ok && cases.cases.length === 2, JSON.stringify(cases && (cases.error || cases.cases.map((c) => c.name))));
  const on = await panel.evaluate(`chrome.runtime.sendMessage({ type: 'panel:setCapture', tabId: ${tabId}, on: true, caseId: 1, caseName: 'Operación Demo' })`);
  check('encender la captura', on && on.ok, on && on.error);
  // La pestaña de prueba tiene que estar al frente: en una pestaña oculta el navegador no avisa
  // qué entra en pantalla (IntersectionObserver), igual que le pasaría a un analista.
  await tab.call('Page.bringToFront');

  let ui = null;
  for (let i = 0; i < 40; i++) {
    await sleep(250);
    ui = await tab.evaluate(`({ host: !!document.querySelector('aleph-lens-root'), shadowHidden: document.querySelector('aleph-lens-root')?.shadowRoot === null, names: [...CSS.highlights.keys()], count: CSS.highlights.get('aleph-chunk-new')?.size || 0, texts: [...(CSS.highlights.get('aleph-chunk-new') || [])].map((r) => r.toString()) })`);
    if (ui.host && ui.count > 0 && mock.db.captures.length > 0) break;
  }
  check('content script: módulo cargado e indicador montado (Shadow DOM cerrado)', ui.host && ui.shadowHidden);
  check('datachunks resaltados con la CSS Custom Highlight API', ui.count >= 8, `${ui.count} rangos: ${ui.texts.slice(0, 14).join(' · ')}`);
  check('no resalta lo privado ni falsos positivos', !ui.texts.some((t) => /no-capturar|valor-de-input|textarea|borrador|secreto|^1\.2\.3\.4$|informe\.txt|Node\.js/.test(t)), '');
  check('la página no fue modificada (solo se agregó el elemento de la extensión)', await tab.evaluate(`document.querySelectorAll('span[class*="aleph"], mark').length === 0 && document.body.querySelector('aleph-lens-root') === null`));
  check('el lote llegó a POST /api/cases/1/captures', mock.db.captures.length > 0, `${mock.db.captures.length} lote(s), ${mock.db.captures[0]?.batch.text_snippets.length || 0} fragmentos`);
  const sentText = JSON.stringify(mock.db.captures);
  check('lo enviado no incluye formularios, contraseñas ni zonas editables', !/NoLeerEstoNunca123|valor-de-input|texto-en-textarea|no-capturar|borrador@/.test(sentText));

  const state = await panel.evaluate(`chrome.runtime.sendMessage({ type: 'panel:getState', tabId: ${tabId} })`);
  check('el estado del panel refleja la captura', state.state.active && state.state.counts.sent >= 1 && state.state.findings.length > 0, `enviados ${state.state.counts.sent}, hallazgos ${state.state.findings.length}`);

  // Hallazgo por la vía de respaldo del arrastre (el chunk que "avisó la página").
  const dropped = await panel.evaluate(`chrome.runtime.sendMessage({ type: 'panel:dropFinding', tabId: ${tabId}, section: { id: null, name: 'Contactos' }, chunk: { kind: 'email', value: 'ventas@sitio-demo.com.ar', quote: 'ventas@sitio-demo.com.ar', context: '', page_url: ${JSON.stringify(pageUrl)}, page_title: 'Foro', platform: 'generic', author_handle: '', post_id: '', detected_by: 'rule', captured_at: new Date().toISOString() } })`);
  check('soltar un datachunk guarda el FindingCreate', dropped && dropped.ok && mock.db.findings.length === 1, dropped && (dropped.error || dropped.section));
  await sleep(700);
  const after = await tab.evaluate(`[...(CSS.highlights.get('aleph-chunk-sent') || [])].map((r) => r.toString())`);
  check('el chunk enviado cambia de color en la página', after.includes('ventas@sitio-demo.com.ar'), after.join(' · '));

  // Interfaz sobre la página: manija al pasar el mouse por un chunk y botón al seleccionar texto.
  // El Shadow DOM es cerrado: desde la página no se ve; se inspecciona con el protocolo de depuración.
  const shadowClasses = async () => {
    const { result } = await tab.call('DOM.getDocument', { depth: -1, pierce: true });
    const found = [];
    const walk = (n) => {
      const cls = n.attributes ? n.attributes[n.attributes.indexOf('class') + 1] : '';
      if (n.attributes && n.attributes.includes('class')) found.push(cls);
      for (const c of [...(n.children || []), ...(n.shadowRoots || [])]) walk(c);
    };
    walk(result.root);
    return found;
  };
  const rect = await tab.evaluate(`(() => { const r = [...CSS.highlights.get('aleph-chunk-new')].find((x) => x.toString() === '203.0.113.45').getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; })()`);
  await tab.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: rect.x - 3, y: rect.y });
  await tab.call('Input.dispatchMouseEvent', { type: 'mouseMoved', x: rect.x, y: rect.y });
  await sleep(300);
  let classes = await shadowClasses();
  check('indicador de captura visible', classes.some((c) => c.startsWith('indicator')) && classes.includes('frame'));
  check('al pasar el mouse por un chunk aparece la manija arrastrable', classes.some((c) => c.startsWith('grip')) && classes.includes('outline'));
  await tab.evaluate(`getSelection().selectAllChildren(document.getElementById('p4')); true`);
  await sleep(500);
  classes = await shadowClasses();
  check('al seleccionar texto aparece el botón "+ Datachunk"', classes.includes('selbtn'));
  await tab.evaluate(`getSelection().selectAllChildren(document.querySelector('textarea')); true`);
  if (process.env.ALEPH_SMOKE_SHOT) {
    const shot = await tab.call('Page.captureScreenshot', { format: 'png' });
    writeFileSync(process.env.ALEPH_SMOKE_SHOT, Buffer.from(shot.result.data, 'base64'));
  }

  const off = await panel.evaluate(`chrome.runtime.sendMessage({ type: 'panel:setCapture', tabId: ${tabId}, on: false })`);
  await sleep(500);
  check('apagar limpia la página', off.ok && await tab.evaluate(`!document.querySelector('aleph-lens-root') && CSS.highlights.size === 0`));
  check('sin errores en la consola de la página ni del panel', tab.errors.length + panel.errors.length === 0, [...tab.errors, ...panel.errors].slice(0, 3).join(' | '));

  panel.close();
  tab.close();
  exitCode = results.every((r) => r.ok) ? 0 : 1;
} catch (err) {
  console.error('Prueba de humo interrumpida:', err.message);
} finally {
  child.kill();
  await mock.close();
  page.close();
  await sleep(600);
  try { rmSync(work, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }); } catch { /* el perfil temporal puede quedar bloqueado un momento */ }
  console.log(`\n${results.filter((r) => r.ok).length}/${results.length} verificaciones correctas en ${browser}`);
  process.exit(exitCode);
}
