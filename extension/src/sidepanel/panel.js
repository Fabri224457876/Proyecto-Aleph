// Panel lateral de Aleph Lens: caso, interruptor de captura, contadores, expediente (incisos como
// destino de arrastre), mini-grafo en vivo y hallazgos. No habla con el servidor: le pide todo al
// service worker.

import { layout, mainEntities } from '../lib/graph.js';
import { CHUNK_MIME } from '../lib/chunks.js';
import { originPattern, excludedReason } from '../lib/routes.js';
import { clip } from '../lib/normalize.js';

const $ = (id) => document.getElementById(id);
const SVG = 'http://www.w3.org/2000/svg';
const GRAPH = { width: 340, height: 230 };

let tab = null; // { id, url } de la pestaña activa de esta ventana
let state = null;
let cases = [];
let sections = [];
let positions = {};
let knownNodes = new Set();
let lastFindingId = 0;
let dragging = null; // chunk que avisó la página al empezar a arrastrar
let busy = false;

function ask(message) {
  return chrome.runtime.sendMessage(message).catch(() => null);
}

function el(tag, className, textValue) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (textValue !== undefined) node.textContent = textValue;
  return node;
}

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

// --- Pestaña activa ---

async function refreshTab() {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  tab = active ? { id: active.id, url: active.url || '' } : null;
  await refreshState();
}

async function refreshState() {
  if (!tab) return;
  const reply = await ask({ type: 'panel:getState', tabId: tab.id });
  if (!reply) return;
  $('setup').hidden = reply.configured;
  const previousCase = state && state.caseId;
  state = reply.state;
  if (!cases.length && reply.configured) await loadCases(reply.lastCaseId);
  render();
  const caseId = currentCaseId();
  if (caseId !== null && (caseId !== previousCase || !sections.length)) await loadSections();
}

function currentCaseId() {
  if (state && state.active && Number.isInteger(state.caseId)) return state.caseId;
  const v = Number($('case').value);
  return Number.isInteger(v) && $('case').value !== '' ? v : null;
}

async function loadCases(preferred) {
  const select = $('case');
  const reply = await ask({ type: 'panel:listCases' });
  select.textContent = '';
  if (!reply || !reply.ok) {
    cases = [];
    select.append(new Option(reply && reply.auth ? 'Sesión vencida: iniciá sesión' : 'No se pudo cargar la lista', ''));
    showError(reply ? reply.error : 'Sin respuesta de la extensión.');
    return;
  }
  cases = reply.cases;
  if (!cases.length) select.append(new Option('No hay casos: creá uno en Aleph', ''));
  for (const c of cases) {
    // Solo los casos abiertos admiten capturas: el servidor responde 409 al resto.
    const closed = c.status && c.status !== 'open';
    const label = closed ? (c.status === 'archived' ? ' (archivado)' : ' (cerrado)') : '';
    const option = new Option(`${c.name}${c.tlp ? ` · TLP:${c.tlp.toUpperCase()}` : ''}${label}`, String(c.id));
    option.disabled = Boolean(closed);
    select.append(option);
  }
  const want = state && state.active ? state.caseId : preferred;
  if (Number.isInteger(want) && cases.some((c) => c.id === want)) select.value = String(want);
}

async function loadSections(force = false) {
  const caseId = currentCaseId();
  if (caseId === null) { sections = []; renderSections(); return; }
  const reply = await ask({ type: 'panel:getSections', caseId, force });
  sections = (reply && reply.sections) || [];
  renderSections();
}

// --- Dibujo ---

function showError(message) {
  $('error').hidden = !message;
  $('error').textContent = message || '';
}

function setCounter(id, value) {
  const node = $(id);
  const textValue = String(value);
  if (node.textContent === textValue) return;
  node.textContent = textValue;
  node.classList.remove('bump');
  void node.offsetWidth; // reinicia la animación
  node.classList.add('bump');
}

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return '';
  }
}

function render() {
  if (!state) return;
  const on = Boolean(state.active);
  const toggle = $('toggle');
  toggle.setAttribute('aria-checked', String(on));
  toggle.setAttribute('aria-label', on ? 'Apagar la captura en esta pestaña' : 'Encender la captura en esta pestaña');
  $('case').disabled = on;
  $('capture-state').textContent = on ? (state.paused ? 'Captura en pausa' : 'Capturando esta pestaña') : 'Captura apagada';
  $('capture-state').classList.toggle('on', on && !state.paused);
  $('capture-tab').textContent = hostOf(state.url || (tab && tab.url) || '') || 'pestaña actual';
  $('led').className = `led ${on ? (state.error ? 'err' : state.paused ? 'paused' : 'on') : ''}`;
  showError(state.error);
  $('paused').hidden = !(on && state.paused);
  $('paused').textContent = state.paused || '';
  if (on && Number.isInteger(state.caseId)) $('case').value = String(state.caseId);

  setCounter('c-accounts', state.counts.accounts);
  setCounter('c-posts', state.counts.posts);
  setCounter('c-sent', state.counts.sent);
  setCounter('c-pending', state.counts.pending);

  renderFindings();
  renderGraph();
  renderSections();
}

function renderFindings() {
  const list = $('findings');
  const findings = state.findings || [];
  $('empty').hidden = findings.length > 0;
  $('c-findings').textContent = findings.length ? String(state.counts.findings) : '';
  list.textContent = '';
  for (const f of findings.slice(0, 60)) {
    const li = el('li', `finding ${f.level || ''}`);
    if (f.id > lastFindingId) li.classList.add('fresh');
    const time = el('time', '', new Date(f.at).toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }));
    li.append(time, el('div', 'title', f.title), el('div', 'detail', f.detail || ''));
    list.append(li);
  }
  lastFindingId = findings.length ? findings[0].id : lastFindingId;
}

function renderGraph() {
  const root = $('graph');
  const graph = state.graph || { nodes: {}, edges: {} };
  positions = layout(graph, { ...GRAPH, previous: positions });
  root.textContent = '';
  for (const e of Object.values(graph.edges)) {
    const a = positions[e.src];
    const b = positions[e.dst];
    if (!a || !b) continue;
    root.append(svg('line', { class: `edge ${e.type}`, x1: a.x, y1: a.y, x2: b.x, y2: b.y }));
    if (e.type === 'menard') {
      const label = svg('text', { class: 'score', x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 - 3 });
      label.textContent = Number(e.weight).toFixed(2);
      root.append(label);
    }
  }
  const seen = new Set();
  for (const n of Object.values(graph.nodes)) {
    const p = positions[n.id];
    if (!p) continue;
    seen.add(n.id);
    const isTarget = Number.isInteger(n.entityId);
    const g = svg('g', { class: `node ${n.menard > 0 ? 'menard' : n.known ? 'known' : ''} ${isTarget ? 'target' : ''} ${knownNodes.has(n.id) ? '' : 'new'}`, transform: `translate(${p.x},${p.y})` });
    g.append(svg('circle', { r: n.menard > 0 || n.known ? 6 : 4 }));
    const label = svg('text', { y: -9 });
    label.textContent = clip(n.label, 16);
    const title = svg('title');
    title.textContent = `@${n.label}${n.known ? ' · en el caso' : ''}${n.menard > 0 ? ` · hipótesis MENARD ${n.menard.toFixed(2)}` : ''}${isTarget ? ' · soltá un datachunk acá para adjuntarlo' : ''}`;
    g.append(label, title);
    if (isTarget) asDropTarget(g, { entityId: n.entityId, entityLabel: `@${n.label}` });
    root.append(g);
  }
  knownNodes = seen;
  renderEntities();
}

function renderEntities() {
  const box = $('entities');
  const main = mainEntities(state.graph || { nodes: {}, edges: {} });
  box.textContent = '';
  for (const e of main) {
    const chip = el('div', `entity ${e.menard > 0 ? 'menard' : ''}`, `@${clip(e.label, 24)}`);
    asDropTarget(chip, { entityId: e.entityId, entityLabel: `@${e.label}` });
    box.append(chip);
  }
  $('entities-box').hidden = !(dragging && main.length);
}

function renderSections() {
  const box = $('sections');
  box.textContent = '';
  const saved = ((state && state.findings) || []).filter((f) => f.kind === 'saved');
  for (const s of sections) {
    const row = el('div', 'section');
    row.dataset.name = s.name;
    const head = el('div', 'head');
    const mine = saved.filter((f) => f.section === s.name);
    head.append(el('span', 'name', s.name), el('span', 'count', String(s.findings || 0)));
    row.append(head);
    if (mine.length) {
      const last = el('ul', 'last');
      for (const f of mine.slice(0, 3)) last.append(el('li', '', f.title));
      row.append(last);
    }
    asDropTarget(row, { section: { id: s.id, name: s.name } });
    box.append(row);
  }
  $('new-section').hidden = currentCaseId() === null;
}

// --- Arrastrar y soltar ---
// El arrastre nace en la página (otro contexto). El chunk viaja por dataTransfer con un tipo MIME
// propio; si el navegador no deja leerlo acá, se usa el que la página avisó por mensaje al empezar.

function asDropTarget(node, target) {
  const accepts = (ev) => dragging || [...(ev.dataTransfer?.types || [])].includes(CHUNK_MIME);
  node.addEventListener('dragenter', (ev) => { if (accepts(ev)) { ev.preventDefault(); node.classList.add('over'); } });
  node.addEventListener('dragover', (ev) => {
    if (!accepts(ev)) return;
    ev.preventDefault();
    ev.dataTransfer.dropEffect = 'copy';
    node.classList.add('over');
  });
  node.addEventListener('dragleave', () => node.classList.remove('over'));
  node.addEventListener('drop', async (ev) => {
    ev.preventDefault();
    node.classList.remove('over');
    let chunk = null;
    try {
      const raw = ev.dataTransfer.getData(CHUNK_MIME);
      chunk = raw ? JSON.parse(raw) : null;
    } catch {
      chunk = null;
    }
    chunk = chunk || dragging;
    setDragging(null);
    const reply = await ask({ type: 'panel:dropFinding', tabId: tab ? tab.id : null, chunk, ...target });
    if (!reply || !reply.ok) showError((reply && reply.error) || 'No se pudo guardar el hallazgo.');
  });
}

function setDragging(chunk) {
  dragging = chunk;
  document.body.classList.toggle('dragging', Boolean(chunk));
  $('drag-hint').hidden = !chunk;
  if (state) renderEntities();
  if (chunk) $('dossier').scrollIntoView({ block: 'nearest', behavior: 'smooth' });
}

// Si el arrastre se suelta fuera de un destino, el navegador no tiene que abrir el texto como página.
document.addEventListener('dragover', (ev) => { if (dragging) ev.preventDefault(); });
document.addEventListener('drop', (ev) => { ev.preventDefault(); });

// --- Acciones ---

$('toggle').addEventListener('click', async () => {
  if (busy || !tab) return;
  const turnOn = !(state && state.active);
  const caseId = Number($('case').value);
  if (turnOn && ($('case').value === '' || !Number.isInteger(caseId))) {
    showError('Elegí un caso antes de encender la captura.');
    return;
  }
  busy = true;
  try {
    if (turnOn && tab.url) {
      const reason = excludedReason(tab.url);
      if (reason) { showError(reason); return; }
      // Permiso del sitio pedido en el momento (hace falta que sea dentro de este clic).
      const origins = [originPattern(tab.url)].filter(Boolean);
      if (origins.length && !(await chrome.permissions.contains({ origins }))) {
        await chrome.permissions.request({ origins }).catch(() => false);
      }
    }
    const chosen = cases.find((c) => c.id === caseId);
    const reply = await ask({ type: 'panel:setCapture', tabId: tab.id, on: turnOn, caseId, caseName: chosen ? chosen.name : '' });
    if (reply && reply.state) { state = reply.state; render(); }
    if (!reply || !reply.ok) showError((reply && reply.error) || 'No se pudo cambiar la captura.');
    else await loadSections(true);
  } finally {
    busy = false;
  }
});

$('case').addEventListener('change', () => loadSections(true));
$('reload-cases').addEventListener('click', async () => {
  cases = [];
  await loadCases(currentCaseId());
  await loadSections(true);
});
$('open-options').addEventListener('click', () => chrome.runtime.openOptionsPage());
$('setup-options').addEventListener('click', () => chrome.runtime.openOptionsPage());

$('new-section').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const input = $('new-section-name');
  const name = input.value.trim();
  const caseId = currentCaseId();
  if (!name || caseId === null) return;
  const reply = await ask({ type: 'panel:createSection', caseId, name });
  if (!reply || !reply.ok) { showError((reply && reply.error) || 'No se pudo crear el inciso.'); return; }
  input.value = '';
  sections = reply.sections;
  renderSections();
});

chrome.runtime.onMessage.addListener((message) => {
  if (!message || !tab) return;
  if (message.type === 'state:update' && message.tabId === tab.id) {
    state = message.state;
    render();
  } else if (message.type === 'drag:start' && message.tabId === tab.id) {
    setDragging(message.chunk);
  } else if (message.type === 'drag:end') {
    // El "drop" puede llegar después del aviso de fin: se espera un momento antes de cerrar las zonas.
    setTimeout(() => setDragging(null), 400);
  } else if (message.type === 'finding:saved' && message.tabId === tab.id) {
    loadSections(true).then(() => {
      const row = [...document.querySelectorAll('.section')].find((n) => n.dataset.name === message.section);
      if (row) row.classList.add('landed');
    });
  }
});

chrome.tabs.onActivated.addListener(() => { sections = []; positions = {}; knownNodes = new Set(); refreshTab(); });
chrome.tabs.onUpdated.addListener((tabId, info) => { if (tab && tabId === tab.id && (info.url || info.status === 'complete')) refreshTab(); });
chrome.storage.onChanged.addListener((changes, area) => {
  if ((area === 'local' || area === 'session') && (changes.token || changes.serverUrl)) { cases = []; refreshState(); }
});

refreshTab();
