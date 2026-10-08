// Estado de captura por pestaña y cómo cambia con cada lote y cada consulta. Puro (sin chrome.*):
// el service worker lo guarda en chrome.storage.session y lo muestra el panel lateral.

import { emptyGraph, addAccount, addEdge, applyHandleInfo } from '../lib/graph.js';
import { CHUNK_KIND_LABELS } from '../lib/chunks.js';
import { clip } from '../lib/normalize.js';

const MAX_FINDINGS = 120;
const MAX_SEEN = 6000;

export function newTabState() {
  return {
    active: false,
    caseId: null,
    caseName: '',
    platform: '',
    url: '',
    paused: '', // motivo de pausa (ruta privada), "" si no
    error: '',
    counts: { accounts: 0, posts: 0, sent: 0, pending: 0, failed: 0, findings: 0 },
    accounts: [], // claves "plataforma:handle" vistas en esta sesión
    posts: [], // "clave|id" vistos (acotado)
    noted: [], // claves de hallazgos ya avisados, para no repetir
    findings: [],
    graph: emptyGraph(),
    seq: 0,
  };
}

function push(state, finding) {
  if (finding.key) {
    if (state.noted.includes(finding.key)) return null;
    state.noted.push(finding.key);
    if (state.noted.length > MAX_SEEN) state.noted.splice(0, state.noted.length - MAX_SEEN);
  }
  state.seq += 1;
  const entry = { id: state.seq, at: finding.at || new Date().toISOString(), level: 'info', ...finding };
  delete entry.key;
  state.findings.unshift(entry);
  if (state.findings.length > MAX_FINDINGS) state.findings.length = MAX_FINDINGS;
  return entry;
}

export function addNote(state, finding) {
  return push(state, finding);
}

/** Incorpora un CaptureBatch al estado: contadores, grafo y hallazgos. Devuelve los hallazgos nuevos. */
export function applyBatch(state, batch, detections = []) {
  const out = [];
  const note = (f) => { const e = push(state, f); if (e) out.push(e); };
  if (batch) {
    state.platform = batch.platform;
    state.url = batch.page_url;
    for (const profile of batch.profiles || []) {
      const handle = profile.account.handle;
      const key = `${profile.account.platform}:${handle.toLowerCase()}`;
      if (!state.accounts.includes(key)) {
        state.accounts.push(key);
        if (state.accounts.length > MAX_SEEN) state.accounts.shift();
        state.counts.accounts += 1;
        note({ key: `acc|${key}`, kind: 'account', title: `@${handle}`, detail: profile.account.display_name ? `Cuenta nueva en pantalla · ${clip(profile.account.display_name, 60)}` : 'Cuenta nueva en pantalla' });
      }
      addAccount(state.graph, handle);
      for (const post of profile.posts || []) {
        const pid = `${key}|${post.platform_post_id}`;
        if (state.posts.includes(pid)) continue;
        state.posts.push(pid);
        if (state.posts.length > MAX_SEEN) state.posts.shift();
        state.counts.posts += 1;
      }
    }
    for (const [src, dst, type] of batch.interactions || []) {
      const edge = addEdge(state.graph, src, dst, type);
      if (edge && type !== 'mention') {
        const verb = { reply: 'responde a', repost: 'reposteó a', quote: 'cita a' }[type] || type;
        note({ key: `int|${src}|${dst}|${type}`, kind: 'connection', title: `@${src} ${verb} @${dst}`, detail: 'Interacción vista en pantalla' });
      }
    }
  }
  for (const d of detections || []) {
    note({ key: `det|${d.kind}|${d.value}`, kind: 'detection', title: clip(d.value, 80), detail: `${CHUNK_KIND_LABELS[d.kind] || d.kind} detectado en la página` });
  }
  state.counts.findings = state.seq;
  return out;
}

/** Incorpora la respuesta de lookup (handle -> HandleInfo). Devuelve los hallazgos nuevos. */
export function applyLookup(state, platform, handles) {
  const out = [];
  const note = (f) => { const e = push(state, f); if (e) out.push(e); };
  for (const [handle, info] of Object.entries(handles || {})) {
    if (!info) continue;
    const h = handle.replace(/^@/, '').toLowerCase();
    const links = (info.links || []).filter((l) => l && l.other && l.review_status !== 'rejected');
    if (!info.known && !links.length) continue;
    applyHandleInfo(state.graph, h, info);
    const node = state.graph.nodes[h];
    if (node) node.onScreen = true;
    if (info.known) {
      note({ key: `known|${platform}:${h}`, kind: 'known', level: 'known', title: `@${h}`, detail: `Ya está en el caso · ${info.posts_captured || 0} publicaciones capturadas` });
    }
    for (const link of links) {
      const other = String(link.other).replace(/^@/, '').toLowerCase();
      const pair = [h, other].sort().join('~');
      const score = Number(link.score || 0).toFixed(2);
      const otherNode = state.graph.nodes[other];
      const both = Boolean(otherNode && otherNode.onScreen);
      note({
        key: `menard|${pair}`, kind: 'menard', level: 'menard', title: `@${h} ↔ @${other}`,
        detail: `Hipótesis MENARD de mismo operador · puntaje ${score}${link.review_status === 'confirmed' ? ' · confirmada' : ' · para revisar'}`,
      });
      if (both) {
        note({ key: `both|${pair}`, kind: 'connection', level: 'menard', title: `@${h} y @${other} aparecen juntas`, detail: `Las dos cuentas de una hipótesis MENARD (${score}) están en esta página` });
      }
    }
  }
  state.counts.findings = state.seq;
  return out;
}

/** Texto corto para la insignia del ícono de la extensión. */
export function badgeFor(state) {
  if (!state || !state.active) return { text: '', color: '#35e0c2' };
  if (state.paused) return { text: '❚❚', color: '#8a97a3' };
  if (state.error) return { text: '!', color: '#ff5c7a' };
  return { text: 'REC', color: '#35e0c2' };
}
