// Service worker de Aleph Lens (MV3, módulo ES).
// - Guarda el estado de captura por pestaña (chrome.storage.session).
// - Tiene el token y habla con el servidor Aleph configurado (la página de opciones solo hace el
//   inicio de sesión y la prueba de conexión). El content script nunca ve el token.
// - Inyecta el content script solo en la pestaña donde el analista encendió la captura.

import { AlephClient, ApiError, normalizeServerUrl } from '../lib/api.js';
import { SendQueue } from '../lib/queue.js';
import { validateCaptureBatch, validateFindingCreate, DEFAULT_SECTIONS, UNCLASSIFIED, ENTITY_TYPES } from '../lib/schema.js';
import { buildFinding, chunkKey, CHUNK_KIND_LABELS } from '../lib/chunks.js';
import { excludedReason, detectPlatform } from '../lib/routes.js';
import { clip } from '../lib/normalize.js';
import { newTabState, applyBatch, applyLookup, addNote, badgeFor } from './state.js';

const VERSION = chrome.runtime.getManifest().version;
const SECTIONS_TTL_MS = 30000;
const MAX_SENT_KEYS = 3000;

const tabs = new Map(); // tabId -> estado (copia en memoria de storage.session)
const sectionsCache = new Map(); // caseId -> { at, sections, fromServer }
const ai = { disabled: false, failures: 0, server: '' };
let drag = null; // { tabId, chunk } mientras el analista arrastra un chunk

// --- Configuración y cliente ---

async function getConfig() {
  const [local, session] = await Promise.all([
    chrome.storage.local.get(['serverUrl', 'token', 'user']),
    chrome.storage.session.get(['token', 'user']),
  ]);
  return {
    serverUrl: normalizeServerUrl(local.serverUrl || ''),
    token: session.token || local.token || '',
    user: session.user || local.user || '',
  };
}

async function client() {
  const { serverUrl, token } = await getConfig();
  return new AlephClient({ baseUrl: serverUrl, token });
}

// --- Estado por pestaña ---

async function loadTab(tabId) {
  if (tabs.has(tabId)) return tabs.get(tabId);
  const key = `tab:${tabId}`;
  const stored = (await chrome.storage.session.get(key))[key];
  const state = stored || newTabState();
  tabs.set(tabId, state);
  return state;
}

async function saveTab(tabId, { broadcast = true } = {}) {
  const state = tabs.get(tabId);
  if (!state) return;
  state.counts.pending = queue.pendingFor(tabId);
  await chrome.storage.session.set({ [`tab:${tabId}`]: state });
  const badge = badgeFor(state);
  chrome.action.setBadgeText({ tabId, text: badge.text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ tabId, color: badge.color }).catch(() => {});
  if (broadcast) chrome.runtime.sendMessage({ type: 'state:update', tabId, state }).catch(() => {});
}

function toTab(tabId, message) {
  return chrome.tabs.sendMessage(tabId, message).catch(() => null);
}

// --- Cola de envío de capturas ---

const queue = new SendQueue({
  send: async (item) => (await client()).sendCapture(item.caseId, item.payload),
  persist: (items) => chrome.storage.session.set({ queue: items }),
  onEvent: async ({ type, item, error }) => {
    const state = await loadTab(item.tabId);
    if (type === 'sent') {
      state.counts.sent += 1;
      state.error = '';
    } else if (type === 'retry') {
      state.error = `${error.message} Reintentando…`;
    } else if (type === 'auth') {
      state.error = 'La sesión con Aleph venció: iniciá sesión de nuevo en Opciones. Lo capturado queda en cola.';
    } else if (type === 'dropped') {
      state.counts.failed += 1;
      state.error = `Se descartó un lote: ${error && error.message ? error.message : 'error desconocido'}`;
      addNote(state, { kind: 'error', level: 'error', title: 'Lote descartado', detail: clip(state.error, 160) });
    }
    await saveTab(item.tabId);
  },
});

const ready = chrome.storage.session.get('queue').then(({ queue: items }) => {
  queue.restore(items);
  if (queue.size) queue.process();
});

// --- Encender / apagar la captura ---

async function inject(tabId) {
  await chrome.scripting.insertCSS({ target: { tabId }, files: ['src/content/highlight.css'] });
  await chrome.scripting.executeScript({ target: { tabId }, files: ['src/content/loader.js'] });
}

async function sentMap(caseId) {
  const key = `sent:${caseId}`;
  return (await chrome.storage.local.get(key))[key] || {};
}

async function startPayload(state) {
  return { type: 'lens:start', active: true, caseId: state.caseId, caseName: state.caseName, sent: await sentMap(state.caseId), aiChunks: !ai.disabled };
}

async function setCapture(tabId, on, caseId, caseName) {
  const state = await loadTab(tabId);
  if (!on) {
    state.active = false;
    state.paused = '';
    await saveTab(tabId);
    await toTab(tabId, { type: 'lens:stop' });
    return { ok: true, state };
  }
  if (!Number.isInteger(caseId)) return { ok: false, error: 'Elegí un caso antes de encender la captura.' };
  const config = await getConfig();
  if (!config.serverUrl || !config.token) return { ok: false, error: 'Falta configurar el servidor Aleph e iniciar sesión (Opciones).' };

  const tab = await chrome.tabs.get(tabId).catch(() => null);
  const url = (tab && tab.url) || '';
  if (url) {
    const reason = excludedReason(url);
    if (reason) return { ok: false, error: reason };
  }
  // Al cambiar de caso se empieza una sesión limpia: no se mezclan hallazgos de dos casos.
  const fresh = state.caseId !== caseId ? Object.assign(state, newTabState()) : state;
  fresh.active = true;
  fresh.caseId = caseId;
  fresh.caseName = caseName || `Caso ${caseId}`;
  fresh.url = url;
  fresh.platform = url ? detectPlatform(url) : '';
  fresh.error = '';
  try {
    await inject(tabId);
  } catch (err) {
    fresh.active = false;
    await saveTab(tabId);
    return { ok: false, needsAccess: true, error: 'Aleph Lens no tiene acceso a esta pestaña. Hacé clic en el ícono de la extensión sobre la pestaña (o aceptá el permiso del sitio) y volvé a encender.' };
  }
  await chrome.storage.local.set({ lastCaseId: caseId });
  await saveTab(tabId);
  await toTab(tabId, await startPayload(fresh));
  return { ok: true, state: fresh };
}

// --- Incisos y hallazgos ---

async function getSections(caseId, { force = false } = {}) {
  const cached = sectionsCache.get(caseId);
  let entry = cached;
  if (force || !cached || Date.now() - cached.at > SECTIONS_TTL_MS) {
    let sections = [];
    let fromServer = false;
    try {
      sections = await (await client()).listSections(caseId);
      fromServer = true;
    } catch {
      sections = [];
    }
    // Sin incisos (o sin endpoint): se muestran los predeterminados. Se crean en el servidor recién
    // cuando el analista suelta algo en uno.
    if (!sections.length) sections = DEFAULT_SECTIONS.filter((n) => n !== UNCLASSIFIED).map((name, i) => ({ id: null, name, position: i, findings: 0 }));
    sections.sort((a, b) => a.position - b.position);
    if (!sections.some((s) => s.name === UNCLASSIFIED)) sections.push({ id: null, name: UNCLASSIFIED, position: 9999, findings: cached ? cached.unclassified || 0 : 0 });
    entry = { at: Date.now(), sections, fromServer, unclassified: cached ? cached.unclassified || 0 : 0 };
    sectionsCache.set(caseId, entry);
  }
  const lastKey = `lastSection:${caseId}`;
  const last = (await chrome.storage.local.get(lastKey))[lastKey] || '';
  return entry.sections.map((s) => ({ ...s, last: s.name === last }));
}

async function withRetry(fn, attempts = 3) {
  let lastError;
  for (let i = 0; i < attempts; i++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      if (!(err instanceof ApiError) || !err.retriable) break;
      await new Promise((r) => setTimeout(r, 600 * (i + 1)));
    }
  }
  throw lastError;
}

/**
 * Inciso por nombre: lo crea, o si el servidor ya lo tiene (409: lo creó otra pestaña, o la lista de esta
 * pestaña estaba vieja), usa el existente. El servidor compara nombres sin distinguir mayúsculas.
 */
async function ensureSection(api, caseId, name) {
  try {
    const created = await withRetry(() => api.createSection(caseId, name));
    if (created && Number.isInteger(created.id)) return created;
    throw new ApiError('El servidor no devolvió el inciso creado.');
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 409) throw err;
  }
  const existing = (await api.listSections(caseId)).find((s) => s.name.toLowerCase() === name.toLowerCase());
  if (!existing) throw new ApiError(`No se encontró el inciso «${name}» en el caso.`);
  return existing;
}

/**
 * Guarda un hallazgo. Solo se llama por una acción explícita del analista: soltar un chunk en el panel,
 * elegir un inciso en el menú "Enviar a…" o usar el atajo de teclado.
 */
async function sendFinding(tabId, { chunk, section = null, entityId = null, entityLabel = '', note = '' }) {
  const state = await loadTab(tabId);
  if (!state.active || !Number.isInteger(state.caseId)) return { ok: false, error: 'La captura no está encendida en esta pestaña.' };
  if (!chunk || excludedReason(chunk.page_url)) return { ok: false, error: 'Ese fragmento no se puede guardar.' };
  const caseId = state.caseId;
  const api = await client();
  let sectionId = null;
  let sectionName = UNCLASSIFIED;
  try {
    if (section && Number.isInteger(section.id)) {
      sectionId = section.id;
      sectionName = section.name || `Inciso ${section.id}`;
    } else if (section && section.name && section.name !== UNCLASSIFIED) {
      // Inciso predeterminado que todavía no existe en el servidor: se crea ahora.
      const row = await ensureSection(api, caseId, section.name);
      sectionId = row.id;
      sectionName = row.name || section.name;
      sectionsCache.delete(caseId);
    }
    const finding = buildFinding({ chunk, sectionId, entityId, note });
    const errors = validateFindingCreate(finding);
    if (errors.length) return { ok: false, error: `Hallazgo inválido: ${errors[0]}` };
    await withRetry(() => api.createFinding(caseId, finding));
  } catch (err) {
    const message = err && err.message ? err.message : 'No se pudo guardar el hallazgo.';
    state.error = message;
    await saveTab(tabId);
    return { ok: false, error: message };
  }

  const label = Number.isInteger(entityId) && sectionId === null ? `entidad ${entityLabel || entityId}` : sectionName;
  const sentKey = `sent:${caseId}`;
  const sent = (await chrome.storage.local.get(sentKey))[sentKey] || {};
  sent[chunkKey(chunk.kind, chunk.value)] = label;
  const keys = Object.keys(sent);
  for (const k of keys.slice(0, Math.max(0, keys.length - MAX_SENT_KEYS))) delete sent[k];
  await chrome.storage.local.set({ [sentKey]: sent, [`lastSection:${caseId}`]: sectionName });

  const cached = sectionsCache.get(caseId);
  if (cached) {
    const row = cached.sections.find((s) => (sectionId !== null ? s.id === sectionId : s.name === UNCLASSIFIED));
    if (row) row.findings += 1;
    if (sectionId === null) cached.unclassified = (cached.unclassified || 0) + 1;
  }
  state.error = '';
  const entry = addNote(state, {
    kind: 'saved', level: 'saved', section: sectionName, entityId: Number.isInteger(entityId) ? entityId : null,
    title: clip(chunk.value, 80),
    detail: `${CHUNK_KIND_LABELS[chunk.kind] || chunk.kind} → ${label}`,
  });
  await saveTab(tabId);
  chrome.runtime.sendMessage({ type: 'finding:saved', tabId, section: sectionName, sectionId, entityId, finding: entry }).catch(() => {});
  return { ok: true, section: label, sectionId };
}

async function aiChunks(tabId, message) {
  const state = await loadTab(tabId);
  const config = await getConfig();
  if (ai.server !== config.serverUrl) Object.assign(ai, { disabled: false, failures: 0, server: config.serverUrl });
  if (ai.disabled || !state.active || excludedReason(message.url)) return { chunks: [], disabled: ai.disabled };
  try {
    const chunks = await (await client()).aiChunks(state.caseId, {
      page_url: message.url, page_title: message.title || '', platform: message.platform || 'generic', texts: message.texts || [],
    });
    ai.failures = 0;
    // Solo tipos del contrato: lo que no sea un ENTITY_TYPES no se podría incorporar al expediente.
    const valid = (c) => c && ENTITY_TYPES.includes(c.kind) && typeof c.value === 'string' && typeof c.quote === 'string';
    return { chunks: chunks.filter(valid).slice(0, 200), disabled: false };
  } catch (err) {
    // Sin endpoint (404/405/501) o con fallas repetidas: la extensión sigue solo con reglas.
    ai.failures += 1;
    if ([404, 405, 501].includes(err && err.status) || ai.failures >= 3) ai.disabled = true;
    return { chunks: [], disabled: ai.disabled };
  }
}

// --- Mensajes del content script (solo de la pestaña con captura encendida) ---

async function fromContent(tabId, message) {
  await ready;
  const state = await loadTab(tabId);
  switch (message.type) {
    case 'lens:hello':
      if (!state.active) return { active: false };
      return startPayload(state);

    case 'lens:route': {
      state.url = String(message.url || '');
      state.paused = String(message.paused || '');
      state.platform = detectPlatform(state.url);
      await saveTab(tabId);
      return { ok: true };
    }

    case 'lens:batch': {
      if (!state.active) return { ok: false };
      const batch = message.batch || null;
      if (batch) {
        // Doble control: nada de una ruta privada sale hacia el servidor, pase lo que pase en la página.
        if (excludedReason(batch.page_url)) return { ok: false };
        batch.extension_version = VERSION;
        const errors = validateCaptureBatch(batch);
        if (errors.length) {
          state.counts.failed += 1;
          addNote(state, { kind: 'error', level: 'error', title: 'Lote inválido (no se envió)', detail: clip(errors[0], 160) });
          await saveTab(tabId);
          return { ok: false, errors };
        }
        await queue.enqueue({ tabId, caseId: state.caseId, payload: batch });
      }
      applyBatch(state, batch, message.detections || []);
      await saveTab(tabId);
      queue.process();
      return { ok: true };
    }

    case 'lens:lookup': {
      if (!state.active) return { handles: {} };
      const handles = (message.handles || []).filter((h) => typeof h === 'string' && h).slice(0, 100);
      if (!handles.length) return { handles: {} };
      try {
        const found = await (await client()).lookup(state.caseId, String(message.platform || 'generic'), handles);
        applyLookup(state, message.platform, found);
        await saveTab(tabId);
        return { handles: found };
      } catch (err) {
        if (err && err.auth) {
          state.error = 'La sesión con Aleph venció: iniciá sesión de nuevo en Opciones.';
          await saveTab(tabId);
        }
        return { handles: {} };
      }
    }

    case 'lens:aiChunks':
      return aiChunks(tabId, message);

    case 'lens:getSections':
      if (!state.active) return { sections: [] };
      return { sections: await getSections(state.caseId) };

    case 'lens:sendFinding':
      return sendFinding(tabId, { chunk: message.chunk, section: message.section });

    case 'lens:dragStart':
      drag = { tabId, chunk: message.chunk };
      chrome.runtime.sendMessage({ type: 'drag:start', tabId, chunk: message.chunk }).catch(() => {});
      return { ok: true };

    case 'lens:dragEnd':
      chrome.runtime.sendMessage({ type: 'drag:end', tabId }).catch(() => {});
      // El "drop" del panel puede llegar apenas después del "dragend" de la página.
      setTimeout(() => { if (drag && drag.tabId === tabId) drag = null; }, 1500);
      return { ok: true };

    default:
      return null;
  }
}

// --- Mensajes del panel lateral y de la página de opciones ---

async function fromExtensionPage(message) {
  await ready;
  switch (message.type) {
    case 'panel:getState': {
      const config = await getConfig();
      const state = Number.isInteger(message.tabId) ? await loadTab(message.tabId) : newTabState();
      state.counts.pending = queue.pendingFor(message.tabId);
      const { lastCaseId } = await chrome.storage.local.get('lastCaseId');
      return { state, configured: Boolean(config.serverUrl && config.token), serverUrl: config.serverUrl, user: config.user, lastCaseId: lastCaseId ?? null };
    }
    case 'panel:listCases':
      try {
        return { ok: true, cases: await (await client()).listCases() };
      } catch (err) {
        return { ok: false, error: err.message, auth: Boolean(err.auth) };
      }
    case 'panel:setCapture':
      return setCapture(message.tabId, Boolean(message.on), message.caseId, message.caseName);
    case 'panel:getSections':
      return { sections: await getSections(message.caseId, { force: Boolean(message.force) }) };
    case 'panel:createSection':
      try {
        const name = String(message.name || '').trim().slice(0, 80);
        if (!name) return { ok: false, error: 'El inciso necesita un nombre.' };
        await (await client()).createSection(message.caseId, name);
        return { ok: true, sections: await getSections(message.caseId, { force: true }) };
      } catch (err) {
        return { ok: false, error: err.message };
      }
    case 'panel:dropFinding': {
      // El chunk llega por dataTransfer; si el navegador no lo dejó pasar entre contextos, se usa el
      // que avisó la página al empezar el arrastre.
      const chunk = message.chunk || (drag && drag.chunk);
      const tabId = Number.isInteger(message.tabId) ? message.tabId : drag && drag.tabId;
      drag = null;
      if (!chunk || !Number.isInteger(tabId)) return { ok: false, error: 'No llegó el datachunk. Probá con clic → «Enviar a…».' };
      const result = await sendFinding(tabId, { chunk, section: message.section || null, entityId: message.entityId ?? null, entityLabel: message.entityLabel || '' });
      if (result.ok) toTab(tabId, { type: 'lens:findingSaved', chunk: { kind: chunk.kind, value: chunk.value }, section: result.section });
      return result;
    }
    case 'panel:retry':
      return queue.resume().then(() => ({ ok: true }));
    case 'options:changed':
      sectionsCache.clear();
      Object.assign(ai, { disabled: false, failures: 0 });
      await queue.resume();
      return { ok: true };
    default:
      return null;
  }
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message || typeof message.type !== 'string' || sender.id !== chrome.runtime.id) return false;
  let work = null;
  // Un content script solo puede usar mensajes "lens:*"; el panel y las opciones (páginas propias de
  // la extensión) usan el resto. Así lo que corre dentro de una página no puede accionar el panel.
  const fromOwnPage = typeof sender.url === 'string' && sender.url.startsWith(chrome.runtime.getURL(''));
  if (message.type.startsWith('lens:')) {
    if (!fromOwnPage && sender.tab && Number.isInteger(sender.tab.id) && sender.frameId === 0) work = fromContent(sender.tab.id, message);
  } else if (fromOwnPage && /^(panel|options):/.test(message.type)) {
    work = fromExtensionPage(message);
  }
  if (!work) return false;
  work.then((result) => sendResponse(result), (err) => sendResponse({ ok: false, error: err && err.message ? err.message : String(err) }));
  return true;
});

// --- Eventos del navegador ---

// Clic en el ícono: abre el panel lateral de esa pestaña (y le da a la extensión acceso temporal a ella).
chrome.action.onClicked.addListener((tab) => {
  if (tab && Number.isInteger(tab.id)) chrome.sidePanel.open({ tabId: tab.id }).catch(() => {});
});

// Recarga o navegación completa en una pestaña con captura encendida: se vuelve a inyectar.
chrome.tabs.onUpdated.addListener(async (tabId, changeInfo) => {
  if (changeInfo.status !== 'complete') return;
  const key = `tab:${tabId}`;
  if (!tabs.has(tabId) && !(await chrome.storage.session.get(key))[key]) return;
  const state = await loadTab(tabId);
  if (!state.active) return;
  try {
    await inject(tabId);
    state.error = '';
  } catch {
    state.paused = 'Sin permiso para este sitio: hacé clic en el ícono de Aleph Lens para seguir capturando acá.';
  }
  await saveTab(tabId);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  tabs.delete(tabId);
  chrome.storage.session.remove(`tab:${tabId}`);
});

chrome.commands.onCommand.addListener(async (command, tab) => {
  const target = tab && Number.isInteger(tab.id) ? tab : (await chrome.tabs.query({ active: true, lastFocusedWindow: true }))[0];
  if (target && Number.isInteger(target.id)) toTab(target.id, { type: 'lens:command', command });
});

chrome.runtime.onInstalled.addListener(() => {
  // El ícono abre el panel con sidePanel.open (arriba); no se usa la apertura automática.
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false }).catch(() => {});
});
