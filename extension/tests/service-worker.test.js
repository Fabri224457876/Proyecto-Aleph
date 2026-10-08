// El service worker real, corriendo en Node con un objeto `chrome` simulado y el servidor Aleph
// simulado. Cubre el recorrido completo: encender captura -> lote -> lookup -> hallazgo -> apagar.

import test from 'node:test';
import assert from 'node:assert/strict';
import { createMockServer, MOCK_TOKEN } from './tools/mock-server.mjs';
import { CaptureAccumulator } from '../src/lib/batch.js';
import { makeChunk } from '../src/lib/chunks.js';
import { validateFindingCreate } from '../src/lib/schema.js';

const EXT = 'chrome-extension://alephlensfake/';

function storageArea() {
  const data = {};
  return {
    data,
    get: async (keys) => {
      const list = keys == null ? Object.keys(data) : (Array.isArray(keys) ? keys : [keys]);
      return Object.fromEntries(list.filter((k) => k in data).map((k) => [k, structuredClone(data[k])]));
    },
    set: async (obj) => { Object.assign(data, structuredClone(obj)); },
    remove: async (keys) => { for (const k of Array.isArray(keys) ? keys : [keys]) delete data[k]; },
  };
}

function fakeChrome() {
  const event = () => { const fns = []; return { addListener: (fn) => fns.push(fn), fns }; };
  const log = { broadcasts: [], tabMessages: [], injected: [], css: [], badges: [], panelOpened: [] };
  const tabs = { 7: { id: 7, url: 'https://x.com/home' }, 8: { id: 8, url: 'https://x.com/messages/1-2' }, 9: { id: 9, url: 'https://sin-permiso.example/' } };
  const chrome = {
    runtime: {
      id: 'alephlensfake',
      getManifest: () => ({ version: '0.1.0' }),
      getURL: (p) => `${EXT}${p}`,
      sendMessage: async (m) => { log.broadcasts.push(structuredClone(m)); },
      onMessage: event(),
      onInstalled: event(),
    },
    storage: { local: storageArea(), session: storageArea() },
    scripting: {
      insertCSS: async ({ target, files }) => { if (target.tabId === 9) throw new Error('Cannot access contents of the page'); log.css.push([target.tabId, files]); },
      executeScript: async ({ target, files }) => { if (target.tabId === 9) throw new Error('Cannot access contents of the page'); log.injected.push([target.tabId, files]); },
    },
    tabs: {
      get: async (id) => tabs[id],
      query: async () => [tabs[7]],
      sendMessage: async (id, m) => { log.tabMessages.push([id, structuredClone(m)]); return { ok: true }; },
      onUpdated: event(),
      onRemoved: event(),
    },
    action: { setBadgeText: async (b) => { log.badges.push(b); }, setBadgeBackgroundColor: async () => {}, onClicked: event() },
    sidePanel: { open: async (o) => { log.panelOpened.push(o); }, setPanelBehavior: async () => {} },
    commands: { onCommand: event() },
  };
  const dispatch = (message, sender) => new Promise((resolve) => {
    const handled = chrome.runtime.onMessage.fns[0](message, sender, resolve);
    if (handled !== true) resolve(undefined);
  });
  return {
    chrome, log,
    fromContent: (tabId, message, url = tabs[tabId].url) => dispatch(message, { id: 'alephlensfake', tab: { id: tabId }, frameId: 0, url }),
    fromPanel: (message) => dispatch(message, { id: 'alephlensfake', url: `${EXT}src/sidepanel/panel.html` }),
    fromForeign: (message) => dispatch(message, { id: 'otra-extension', url: 'https://x.com/home', tab: { id: 7 }, frameId: 0 }),
  };
}

function batch(id, pageUrl = 'https://x.com/home') {
  const acc = new CaptureAccumulator();
  acc.add({ platform: 'x', handle: 'lau_ficticia' }, { platform_post_id: id, text: `publicación ${id}` });
  acc.add({ platform: 'x', handle: 'cuenta_demo_3' }, { platform_post_id: `${id}9`, text: 'respuesta', kind: 'reply', reply_to: 'lau_ficticia' });
  acc.addInteraction('cuenta_demo_3', 'lau_ficticia', 'reply');
  return acc.drain({ pageUrl, platform: 'x', now: new Date('2026-10-07T12:00:00Z'), version: 'content' });
}

async function until(check, ms = 3000) {
  const start = Date.now();
  while (!check()) {
    if (Date.now() - start > ms) throw new Error('tiempo de espera agotado');
    await new Promise((r) => setTimeout(r, 15));
  }
}

test('service worker: recorrido completo contra el servidor simulado', async (t) => {
  const mock = createMockServer({ withAiChunks: false });
  const base = await mock.listen();
  t.after(() => mock.close());

  const env = fakeChrome();
  globalThis.chrome = env.chrome;
  await env.chrome.storage.local.set({ serverUrl: base });
  await env.chrome.storage.session.set({ token: MOCK_TOKEN, user: 'analista' });
  await import('../src/background/service-worker.js');
  const { log, fromContent, fromPanel, fromForeign } = env;

  await t.test('la captura arranca apagada: el content script no recibe nada', async () => {
    assert.deepEqual(await fromContent(7, { type: 'lens:hello' }), { active: false });
    assert.deepEqual(await fromContent(7, { type: 'lens:batch', batch: batch('0') }), { ok: false });
    assert.equal(mock.db.captures.length, 0);
    assert.equal(log.injected.length, 0, 'no se inyecta nada hasta que el analista enciende');
    const state = await fromPanel({ type: 'panel:getState', tabId: 7 });
    assert.equal(state.state.active, false);
    assert.equal(state.configured, true);
  });

  await t.test('el panel lista los casos', async () => {
    const reply = await fromPanel({ type: 'panel:listCases' });
    assert.deepEqual(reply.cases.map((c) => c.id), [1, 2]);
  });

  await t.test('no se enciende sin caso, ni en mensajes directos, ni sin acceso a la pestaña', async () => {
    assert.match((await fromPanel({ type: 'panel:setCapture', tabId: 7, on: true })).error, /Elegí un caso/);
    assert.match((await fromPanel({ type: 'panel:setCapture', tabId: 8, on: true, caseId: 1 })).error, /[Mm]ensajes privados/);
    const denied = await fromPanel({ type: 'panel:setCapture', tabId: 9, on: true, caseId: 1 });
    assert.equal(denied.ok, false);
    assert.equal(denied.needsAccess, true);
    assert.equal(log.injected.length, 0);
  });

  await t.test('encender: inyecta solo en esa pestaña y le avisa el caso', async () => {
    const reply = await fromPanel({ type: 'panel:setCapture', tabId: 7, on: true, caseId: 1, caseName: 'Operación Demo' });
    assert.equal(reply.ok, true);
    assert.deepEqual(log.injected, [[7, ['src/content/loader.js']]]);
    assert.deepEqual(log.css, [[7, ['src/content/highlight.css']]]);
    const start = log.tabMessages.find(([id, m]) => id === 7 && m.type === 'lens:start')[1];
    assert.equal(start.caseName, 'Operación Demo');
    assert.ok(!('token' in start) && !JSON.stringify(start).includes(MOCK_TOKEN), 'el token nunca viaja a la página');
    assert.equal(log.badges.at(-1).text, 'REC');
    const hello = await fromContent(7, { type: 'lens:hello' });
    assert.equal(hello.active, true);
    assert.equal(hello.caseId, 1);
    assert.ok(!JSON.stringify(hello).includes(MOCK_TOKEN));
  });

  await t.test('un lote válido llega a POST /api/cases/1/captures con Bearer', async () => {
    assert.deepEqual(await fromContent(7, { type: 'lens:batch', batch: batch('1'), detections: [{ kind: 'email', value: 'a@demo.com' }] }), { ok: true });
    await until(() => mock.db.captures.length === 1);
    assert.equal(mock.db.captures[0].caseId, 1);
    assert.equal(mock.db.captures[0].batch.extension_version, '0.1.0', 'la versión la pone el service worker');
    const req = mock.db.requests.find((r) => r.path === '/api/cases/1/captures');
    assert.equal(req.auth, `Bearer ${MOCK_TOKEN}`);
    const update = log.broadcasts.filter((m) => m.type === 'state:update' && m.tabId === 7).at(-1);
    assert.deepEqual([update.state.counts.accounts, update.state.counts.posts], [2, 2]);
    assert.ok(update.state.findings.some((f) => f.kind === 'detection'));
    await until(() => env.chrome.storage.session.data['tab:7'].counts.sent === 1);
  });

  await t.test('un lote inválido o de una ruta privada no sale', async () => {
    const before = mock.db.captures.length;
    const broken = batch('2');
    broken.profiles[0].posts[0].kind = 'retweet';
    const r1 = await fromContent(7, { type: 'lens:batch', batch: broken });
    assert.equal(r1.ok, false);
    const r2 = await fromContent(7, { type: 'lens:batch', batch: batch('3', 'https://x.com/messages/1-2') });
    assert.equal(r2.ok, false);
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(mock.db.captures.length, before);
  });

  await t.test('una página no puede usar los mensajes del panel, ni otra extensión los de la página', async () => {
    assert.equal(await fromContent(7, { type: 'panel:setCapture', tabId: 7, on: false }), undefined);
    assert.equal(await fromContent(7, { type: 'panel:listCases' }), undefined);
    assert.equal(await fromForeign({ type: 'lens:hello' }), undefined);
    assert.equal((await fromPanel({ type: 'panel:getState', tabId: 7 })).state.active, true);
  });

  await t.test('lookup: devuelve HandleInfo y genera hallazgos MENARD', async () => {
    const reply = await fromContent(7, { type: 'lens:lookup', platform: 'x', handles: ['lau_ficticia', 'cuenta_demo_3', 'nadie_demo'] });
    assert.equal(reply.handles.lau_ficticia.links[0].score, 0.87);
    assert.deepEqual(mock.db.requests.find((r) => r.path.endsWith('/captures/lookup')).body, { platform: 'x', handles: ['lau_ficticia', 'cuenta_demo_3', 'nadie_demo'] });
    const state = (await fromPanel({ type: 'panel:getState', tabId: 7 })).state;
    assert.ok(state.findings.some((f) => f.kind === 'menard'));
    assert.equal(state.graph.nodes.lau_ficticia.entityId, 101);
  });

  await t.test('detector del servidor (captures/chunks): si el endpoint no existe se apaga solo', async () => {
    const reply = await fromContent(7, { type: 'lens:aiChunks', texts: ['Hola Laura Ficticia'], url: 'https://x.com/home', platform: 'x' });
    assert.deepEqual(reply, { chunks: [], disabled: true });
    const calls = mock.db.requests.filter((r) => r.path.endsWith('/captures/chunks')).length;
    await fromContent(7, { type: 'lens:aiChunks', texts: ['otra'], url: 'https://x.com/home', platform: 'x' });
    assert.equal(mock.db.requests.filter((r) => r.path.endsWith('/captures/chunks')).length, calls, 'no vuelve a preguntar');
  });

  const chunk = makeChunk({ kind: 'email', value: 'contacto@dominio-demo.com.ar', quote: 'contacto@dominio-demo.com.ar', author_handle: 'cuenta_demo_3', post_id: '19' }, { url: 'https://x.com/home', title: 'X', platform: 'x' });

  await t.test('incisos: sin incisos en el servidor se muestran los predeterminados', async () => {
    const { sections } = await fromContent(7, { type: 'lens:getSections' });
    assert.deepEqual(sections.map((s) => s.name), ['Identidad', 'Cuentas', 'Contactos', 'Ubicaciones', 'Actividad', 'Infraestructura', 'Sin clasificar']);
    assert.ok(sections.every((s) => s.id === null));
    assert.equal(mock.db.findings.length, 0, 'ver chunks e incisos no guarda nada');
  });

  await t.test('clic -> "Enviar a…": crea el inciso predeterminado y guarda el FindingCreate', async () => {
    const reply = await fromContent(7, { type: 'lens:sendFinding', chunk, section: { id: null, name: 'Contactos' } });
    assert.equal(reply.ok, true);
    assert.equal(reply.section, 'Contactos');
    assert.equal(mock.db.sections.length, 1);
    const saved = mock.db.findings[0].finding;
    assert.deepEqual(validateFindingCreate(saved), []);
    assert.equal(saved.section_id, mock.db.sections[0].id);
    assert.equal(saved.chunk.author_handle, 'cuenta_demo_3');
    assert.equal(saved.attach_to_entity_id, null);
    assert.deepEqual(env.chrome.storage.local.data['sent:1'], { 'email|contacto@dominio-demo.com.ar': 'Contactos' });
    assert.equal(env.chrome.storage.local.data['lastSection:1'], 'Contactos');
    const { sections } = await fromContent(7, { type: 'lens:getSections' });
    assert.deepEqual(sections.filter((s) => s.last).map((s) => s.name), ['Contactos']);
    assert.equal(sections.find((s) => s.name === 'Contactos').findings, 1);
    assert.ok(log.broadcasts.some((m) => m.type === 'finding:saved' && m.section === 'Contactos'));
  });

  await t.test('arrastre: si dataTransfer no trae el chunk, el panel usa el que avisó la página', async () => {
    const dragged = makeChunk({ kind: 'account', value: 'tomi_ejemplo', quote: '@Tomi_Ejemplo' }, { url: 'https://x.com/home', platform: 'x' });
    await fromContent(7, { type: 'lens:dragStart', chunk: dragged });
    assert.ok(log.broadcasts.some((m) => m.type === 'drag:start' && m.chunk.value === 'tomi_ejemplo'));
    const reply = await fromPanel({ type: 'panel:dropFinding', tabId: 7, chunk: null, section: null });
    assert.equal(reply.ok, true);
    assert.equal(reply.section, 'Sin clasificar');
    assert.equal(mock.db.findings[1].finding.section_id, null);
    assert.equal(mock.db.findings[1].finding.chunk.value, 'tomi_ejemplo');
    const back = log.tabMessages.filter(([id, m]) => id === 7 && m.type === 'lens:findingSaved').at(-1)[1];
    assert.deepEqual(back.chunk, { kind: 'account', value: 'tomi_ejemplo' });
    assert.match((await fromPanel({ type: 'panel:dropFinding', tabId: 7, chunk: null })).error, /No llegó el datachunk/);
  });

  await t.test('soltar sobre una entidad del grafo: attach_to_entity_id', async () => {
    const reply = await fromPanel({ type: 'panel:dropFinding', tabId: 7, chunk: { ...chunk, value: 'otro@dominio-demo.com.ar' }, entityId: 101, entityLabel: '@lau_ficticia' });
    assert.equal(reply.ok, true);
    assert.equal(mock.db.findings[2].finding.attach_to_entity_id, 101);
    assert.equal(mock.db.findings[2].finding.section_id, null);
    assert.equal(reply.section, 'entidad @lau_ficticia');
  });

  await t.test('crear un inciso nuevo desde el panel', async () => {
    const reply = await fromPanel({ type: 'panel:createSection', caseId: 1, name: '  Vehículos ' });
    assert.equal(reply.ok, true);
    assert.ok(reply.sections.some((s) => s.name === 'Vehículos' && Number.isInteger(s.id)));
    assert.equal(reply.sections.at(-1).name, 'Sin clasificar');
    assert.equal((await fromPanel({ type: 'panel:createSection', caseId: 1, name: ' ' })).ok, false);
    // El servidor compara sin distinguir mayúsculas: el nombre repetido vuelve con su mensaje (409).
    assert.match((await fromPanel({ type: 'panel:createSection', caseId: 1, name: 'vehículos' })).error, /Ya existe/);
  });

  await t.test('inciso predeterminado que ya existe en el servidor (409): se usa el existente', async () => {
    // Otra pestaña creó "Ubicaciones" mientras esta tenía la lista vieja, sin id.
    assert.equal((await fromPanel({ type: 'panel:createSection', caseId: 1, name: 'Ubicaciones' })).ok, true);
    const reply = await fromContent(7, { type: 'lens:sendFinding', chunk: { ...chunk, value: 'calle-demo@sitio.com.ar' }, section: { id: null, name: 'Ubicaciones' } });
    assert.equal(reply.ok, true, reply.error);
    const ubicaciones = mock.db.sections.find((s) => s.name === 'Ubicaciones');
    assert.equal(reply.sectionId, ubicaciones.id);
    assert.equal(mock.db.findings.at(-1).finding.section_id, ubicaciones.id);
    assert.equal(mock.db.sections.filter((s) => s.name === 'Ubicaciones').length, 1, 'no se duplica el inciso');
  });

  await t.test('si el servidor falla, el hallazgo no se marca como enviado', async () => {
    mock.db.failNext = 5;
    const reply = await fromContent(7, { type: 'lens:sendFinding', chunk: { ...chunk, value: 'falla@demo.com' }, section: null });
    mock.db.failNext = 0;
    assert.equal(reply.ok, false);
    assert.ok(!('email|falla@demo.com' in env.chrome.storage.local.data['sent:1']));
  });

  await t.test('recarga de la pestaña con captura activa: se vuelve a inyectar', async () => {
    const before = log.injected.length;
    await env.chrome.tabs.onUpdated.fns[0](7, { status: 'complete' });
    assert.equal(log.injected.length, before + 1);
    await env.chrome.tabs.onUpdated.fns[0](55, { status: 'complete' });
    assert.equal(log.injected.length, before + 1, 'una pestaña sin captura no se toca');
  });

  await t.test('ícono y atajos', async () => {
    env.chrome.action.onClicked.fns[0]({ id: 7 });
    assert.deepEqual(log.panelOpened, [{ tabId: 7 }]);
    await env.chrome.commands.onCommand.fns[0]('send-focused-chunk', { id: 7 });
    assert.deepEqual(log.tabMessages.at(-1), [7, { type: 'lens:command', command: 'send-focused-chunk' }]);
  });

  await t.test('apagar: avisa a la página y deja de aceptar lotes', async () => {
    const reply = await fromPanel({ type: 'panel:setCapture', tabId: 7, on: false });
    assert.equal(reply.ok, true);
    assert.deepEqual(log.tabMessages.at(-1), [7, { type: 'lens:stop' }]);
    assert.equal(log.badges.at(-1).text, '');
    const count = mock.db.captures.length;
    assert.deepEqual(await fromContent(7, { type: 'lens:batch', batch: batch('50') }), { ok: false });
    assert.deepEqual(await fromContent(7, { type: 'lens:hello' }), { active: false });
    assert.equal((await fromContent(7, { type: 'lens:sendFinding', chunk, section: null })).ok, false);
    await new Promise((r) => setTimeout(r, 50));
    assert.equal(mock.db.captures.length, count);
  });

  await t.test('la extensión solo habló con el servidor configurado y nunca mandó cookies', () => {
    assert.ok(mock.db.requests.length > 8);
    assert.ok(mock.db.requests.every((r) => r.cookie === '' && r.auth === `Bearer ${MOCK_TOKEN}`));
  });
});
