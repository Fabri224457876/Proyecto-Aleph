// Modelo y disposición del mini-grafo del panel. Puro: sin DOM ni chrome.*.
// Nodos = cuentas vistas en pantalla. Aristas = menciones, respuestas, reposts, citas e hipótesis MENARD.

import { shortHash } from './normalize.js';

export const EDGE_TYPES = ['mention', 'reply', 'repost', 'quote', 'menard'];
const MAX_NODES = 60;

export function emptyGraph() {
  return { nodes: {}, edges: {}, tick: 0 };
}

function touch(graph, id, label) {
  const key = String(id || '').toLowerCase();
  if (!key) return null;
  graph.tick += 1;
  let node = graph.nodes[key];
  if (!node) {
    node = graph.nodes[key] = { id: key, label: label || key, known: false, menard: 0, entityId: null, seen: graph.tick, onScreen: false };
  } else {
    node.seen = graph.tick;
    if (label && node.label === node.id) node.label = label;
  }
  return node;
}

export function addAccount(graph, handle, { onScreen = true } = {}) {
  const node = touch(graph, handle, handle);
  if (node && onScreen) node.onScreen = true;
  prune(graph);
  return node;
}

export function addEdge(graph, src, dst, type, weight = 1) {
  const a = touch(graph, src);
  const b = touch(graph, dst);
  if (!a || !b || a.id === b.id) return null;
  const t = EDGE_TYPES.includes(type) ? type : 'mention';
  // Las hipótesis MENARD no tienen dirección: se guarda una sola arista por par.
  const [s, d] = t === 'menard' && a.id > b.id ? [b.id, a.id] : [a.id, b.id];
  const key = `${s}|${d}|${t}`;
  const edge = graph.edges[key];
  if (edge) {
    if (t === 'menard') edge.weight = Math.max(edge.weight, weight);
    else edge.weight += weight;
    return null;
  }
  graph.edges[key] = { src: s, dst: d, type: t, weight };
  prune(graph);
  return graph.edges[key];
}

/** Aplica lo que el caso sabe de una cuenta (HandleInfo). Devuelve las aristas MENARD nuevas. */
export function applyHandleInfo(graph, handle, info) {
  const node = touch(graph, handle, handle);
  if (!node || !info) return [];
  node.known = Boolean(info.known);
  node.entityId = Number.isInteger(info.entity_id) ? info.entity_id : null;
  const created = [];
  for (const link of Array.isArray(info.links) ? info.links : []) {
    if (!link || !link.other || link.review_status === 'rejected') continue;
    const score = Number(link.score) || 0;
    node.menard = Math.max(node.menard, score);
    const edge = addEdge(graph, node.id, String(link.other).replace(/^@/, ''), 'menard', score);
    const other = graph.nodes[String(link.other).replace(/^@/, '').toLowerCase()];
    if (other) other.menard = Math.max(other.menard, score);
    if (edge) created.push(edge);
  }
  return created;
}

function prune(graph) {
  const ids = Object.keys(graph.nodes);
  if (ids.length <= MAX_NODES) return;
  const degree = {};
  for (const e of Object.values(graph.edges)) {
    degree[e.src] = (degree[e.src] || 0) + 1;
    degree[e.dst] = (degree[e.dst] || 0) + 1;
  }
  // Se van primero los nodos sin hipótesis, poco conectados y vistos hace más tiempo.
  const ranked = ids.sort((a, b) => {
    const na = graph.nodes[a];
    const nb = graph.nodes[b];
    return (nb.menard > 0) - (na.menard > 0) || (nb.known - na.known) || (degree[b] || 0) - (degree[a] || 0) || nb.seen - na.seen;
  });
  for (const id of ranked.slice(MAX_NODES)) {
    delete graph.nodes[id];
    for (const [key, e] of Object.entries(graph.edges)) if (e.src === id || e.dst === id) delete graph.edges[key];
  }
}

/** Nodos "principales" (destinos de arrastre): los que el caso ya tiene como entidad, por relevancia. */
export function mainEntities(graph, limit = 6) {
  const degree = {};
  for (const e of Object.values(graph.edges)) {
    degree[e.src] = (degree[e.src] || 0) + 1;
    degree[e.dst] = (degree[e.dst] || 0) + 1;
  }
  return Object.values(graph.nodes)
    .filter((n) => Number.isInteger(n.entityId))
    .sort((a, b) => b.menard - a.menard || (degree[b.id] || 0) - (degree[a.id] || 0) || b.seen - a.seen)
    .slice(0, limit)
    .map((n) => ({ id: n.id, label: n.label, entityId: n.entityId, menard: n.menard }));
}

// --- Disposición por fuerzas (determinista: la posición inicial sale del hash del id) ---

function seedPosition(id, width, height) {
  const h = parseInt(shortHash(id), 16);
  const angle = ((h % 3600) / 3600) * Math.PI * 2;
  const radius = 0.18 + (((h >>> 12) % 1000) / 1000) * 0.22;
  return { x: width / 2 + Math.cos(angle) * radius * width, y: height / 2 + Math.sin(angle) * radius * height };
}

/**
 * Calcula posiciones. `previous` (id -> {x, y}) conserva la ubicación de los nodos que ya estaban,
 * para que el grafo no salte cada vez que entra una cuenta nueva.
 */
export function layout(graph, { width = 320, height = 240, iterations = 140, previous = {} } = {}) {
  const nodes = Object.values(graph.nodes).map((n) => {
    const p = previous[n.id] || seedPosition(n.id, width, height);
    return { id: n.id, x: p.x, y: p.y, vx: 0, vy: 0 };
  });
  const index = Object.fromEntries(nodes.map((n, i) => [n.id, i]));
  const edges = Object.values(graph.edges).filter((e) => e.src in index && e.dst in index);
  const k = Math.sqrt((width * height) / Math.max(nodes.length, 1)) * 0.75;
  const margin = 18;

  for (let it = 0; it < iterations; it++) {
    const cool = 1 - it / iterations;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i];
        const b = nodes[j];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 0.01) { dx = ((i * 7 + j) % 5) - 2 || 1; dy = ((i + j * 3) % 5) - 2 || 1; d2 = dx * dx + dy * dy; }
        const d = Math.sqrt(d2);
        const f = (k * k) / d2;
        a.vx += (dx / d) * f * d; a.vy += (dy / d) * f * d;
        b.vx -= (dx / d) * f * d; b.vy -= (dy / d) * f * d;
      }
    }
    for (const e of edges) {
      const a = nodes[index[e.src]];
      const b = nodes[index[e.dst]];
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const f = ((d * d) / k) * (e.type === 'menard' ? 1.4 : 1);
      a.vx -= (dx / d) * f; a.vy -= (dy / d) * f;
      b.vx += (dx / d) * f; b.vy += (dy / d) * f;
    }
    for (const n of nodes) {
      n.vx += (width / 2 - n.x) * 0.06;
      n.vy += (height / 2 - n.y) * 0.06;
      const speed = Math.sqrt(n.vx * n.vx + n.vy * n.vy) || 0.01;
      const step = Math.min(speed, 14 * cool + 0.5);
      n.x = Math.min(width - margin, Math.max(margin, n.x + (n.vx / speed) * step));
      n.y = Math.min(height - margin, Math.max(margin, n.y + (n.vy / speed) * step));
      n.vx = 0; n.vy = 0;
    }
  }
  return Object.fromEntries(nodes.map((n) => [n.id, { x: Math.round(n.x * 10) / 10, y: Math.round(n.y * 10) / 10 }]));
}
