// Content script de Aleph Lens. Solo corre en la pestaña donde el analista encendió la captura.
// Es PASIVO: lee el DOM ya renderizado. No navega, no hace clics, no hace scroll, no llama a APIs de la
// plataforma, no lee cookies, almacenamiento de la página, campos de formulario ni mensajes privados.
// No tiene el token ni habla con el servidor: todo pasa por el service worker.

import { extractorFor } from '../extractors/index.js';
import { excludedReason, insidePrivate } from '../lib/routes.js';
import { handleKey, cleanText, clip } from '../lib/normalize.js';
import { makeChunk, makeManualChunk, chunkKey } from '../lib/chunks.js';
import { CaptureSession } from './session.js';
import { ChunkHighlighter, CssHighlightRegistry, SKIP_SELECTOR } from './chunk-highlighter.js';
import { Overlay } from './overlay.js';

const FLUSH_MS = 2000;
const MUTATION_MS = 250;
const LOOKUP_MS = 600;
const LOOKUP_TTL_MS = 120000;
const URL_POLL_MS = 500;

let lens = null;
let listening = false;

function send(message) {
  try {
    return chrome.runtime.sendMessage(message).catch(() => null);
  } catch {
    // La extensión se recargó o se desinstaló: este script quedó huérfano.
    if (lens) lens.stop();
    return Promise.resolve(null);
  }
}

class Lens {
  constructor(config) {
    this.caseName = config.caseName || '';
    this.sent = new Map(Object.entries(config.sent || {})); // "kind|value" -> nombre del inciso
    this.infos = new Map(); // handle en minúscula -> { info, at }
    this.pendingLookup = new Set();
    this.aiEnabled = config.aiChunks !== false;
    this.timers = {};
    this.focusedChunk = null;
    this.url = location.href;
    this.version = chrome.runtime.getManifest().version;

    this.overlay = new Overlay({
      onSend: (chunk, section) => this.sendChunk(chunk, section),
      onDragStart: (chunk) => {
        const data = this.toDatachunk(chunk);
        this.focusedChunk = chunk;
        send({ type: 'lens:dragStart', chunk: data });
        return data;
      },
      onDragEnd: () => send({ type: 'lens:dragEnd' }),
      onManual: () => this.manualFromSelection(),
      getSections: async () => (await send({ type: 'lens:getSections' }))?.sections || [],
    });
    this.registry = new CssHighlightRegistry(window);
    this.highlighter = new ChunkHighlighter({
      document,
      registry: this.registry,
      isNearViewport: (el) => this.nearViewport(el),
      statusOf: (kind, value) => this.chunkStatus(kind, value),
    });
    this.buildSession();
  }

  buildSession() {
    this.extractor = extractorFor(location.href);
    this.session = new CaptureSession({
      document,
      extractor: this.extractor,
      getUrl: () => location.href,
      version: this.version,
      observeVisibility: (el, cb) => {
        this.visibleCallbacks.set(el, cb);
        this.io.observe(el);
      },
    });
  }

  start() {
    this.overlay.mount();
    this.visibleCallbacks = new WeakMap();
    this.io = new IntersectionObserver((entries) => {
      const vh = window.innerHeight || 800;
      let any = false;
      for (const entry of entries) {
        // "En pantalla": un cuarto del elemento, o un elemento alto que ocupa buena parte del visor.
        if (!entry.isIntersecting || (entry.intersectionRatio < 0.25 && entry.intersectionRect.height < vh * 0.4)) continue;
        const cb = this.visibleCallbacks.get(entry.target);
        this.io.unobserve(entry.target);
        if (cb) { cb(); any = true; }
      }
      if (any) this.schedule('flush', () => this.flush(), FLUSH_MS);
    }, { threshold: [0, 0.25, 0.6] });

    this.mo = new MutationObserver(() => this.schedule('scan', () => this.scan(), MUTATION_MS));
    this.mo.observe(document.documentElement, { childList: true, subtree: true, characterData: true });

    this.onScroll = () => this.requestFrame();
    this.onMove = (ev) => { this.pointer = { x: ev.clientX, y: ev.clientY, target: ev.target }; this.requestFrame(); };
    this.onSelection = () => this.schedule('selection', () => this.updateSelectionButton(), 200);
    this.onDocClick = (ev) => { if (!this.overlay.contains(ev.target)) this.overlay.closeMenu(); };
    this.onHide = () => this.flush();
    window.addEventListener('scroll', this.onScroll, { capture: true, passive: true });
    window.addEventListener('resize', this.onScroll, { passive: true });
    document.addEventListener('mousemove', this.onMove, { capture: true, passive: true });
    document.addEventListener('selectionchange', this.onSelection, { passive: true });
    document.addEventListener('click', this.onDocClick, { capture: true, passive: true });
    document.addEventListener('visibilitychange', this.onHide, { passive: true });
    window.addEventListener('pagehide', this.onHide, { passive: true });

    // La navegación de las SPA no recarga la página: se detecta comparando la URL.
    this.urlTimer = setInterval(() => this.checkUrl(), URL_POLL_MS);
    this.applyRoute();
    this.scan();
  }

  stop() {
    this.flush();
    for (const t of Object.values(this.timers)) clearTimeout(t);
    this.timers = {};
    clearInterval(this.urlTimer);
    if (this.io) this.io.disconnect();
    if (this.mo) this.mo.disconnect();
    window.removeEventListener('scroll', this.onScroll, { capture: true });
    window.removeEventListener('resize', this.onScroll);
    document.removeEventListener('mousemove', this.onMove, { capture: true });
    document.removeEventListener('selectionchange', this.onSelection);
    document.removeEventListener('click', this.onDocClick, { capture: true });
    document.removeEventListener('visibilitychange', this.onHide);
    window.removeEventListener('pagehide', this.onHide);
    this.highlighter.clear();
    this.overlay.unmount();
    if (lens === this) lens = null;
  }

  schedule(name, fn, ms) {
    if (this.timers[name]) return;
    this.timers[name] = setTimeout(() => {
      delete this.timers[name];
      fn();
    }, ms);
  }

  requestFrame() {
    if (this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.renderMarks();
      this.hoverChunk();
    });
  }

  // --- Rutas: en mensajería privada, login o ajustes la captura se pone en pausa sola ---
  applyRoute() {
    this.pausedReason = excludedReason(location.href);
    this.overlay.setIndicator({ caseName: this.caseName, paused: Boolean(this.pausedReason), reason: this.pausedReason });
    if (this.pausedReason) {
      this.highlighter.clear();
      this.overlay.setAccountMarks([]);
      this.overlay.hideGrip(true);
      this.overlay.hideSelectionButton();
    }
    send({ type: 'lens:route', url: location.href, paused: this.pausedReason });
  }

  checkUrl() {
    if (location.href === this.url) return;
    // Lo capturado hasta acá pertenece a la URL anterior: se envía antes de cambiar de página.
    this.flush();
    this.url = location.href;
    this.targets = [];
    this.overlay.hideGrip(true);
    this.applyRoute();
    this.schedule('scan', () => this.scan(), MUTATION_MS);
  }

  nearViewport(el) {
    const r = el.getBoundingClientRect();
    if (!r.width && !r.height) return false;
    const vh = window.innerHeight || 800;
    return r.bottom > -vh * 0.5 && r.top < vh * 1.5;
  }

  // --- Lectura ---
  scan() {
    if (this.pausedReason) return;
    this.session.scan(document);
    this.session.scanPage();
    this.targets = this.extractor.findHandleTargets(document).filter((t) => !insidePrivate(t.element));
    this.queueLookups();
    this.highlighter.rescan(document.body);
    this.requestFrame();
    if (this.session.accumulator.hasPending()) this.schedule('flush', () => this.flush(), FLUSH_MS);
  }

  flush() {
    if (!this.session) return;
    const batch = this.session.flush(document.title);
    const detections = this.session.takeDetections();
    if (batch || detections.length) send({ type: 'lens:batch', batch, detections, totals: this.session.totals });
    this.askAiChunks();
  }

  // Detector del servidor (captures/chunks: IOCs y reglas de FUNES, sin IA). Si no existe, se apaga solo.
  async askAiChunks() {
    if (!this.aiEnabled || this.pausedReason) return;
    const texts = this.session.takeTexts();
    if (!texts.length) return;
    const reply = await send({ type: 'lens:aiChunks', texts, url: location.href, title: document.title, platform: this.extractor.platform });
    if (!reply || reply.disabled) { this.aiEnabled = false; return; }
    let added = false;
    for (const c of reply.chunks || []) {
      // Se conserva el origen que dice el servidor: "rule" (extractor de IOCs) o "funes" (reglas de FUNES). Sin IA.
      const detectedBy = c && ['rule', 'funes'].includes(c.detected_by) ? c.detected_by : 'rule';
      if (c && c.kind && (c.quote || c.value)) added = this.highlighter.addLiteral({ ...c, detected_by: detectedBy }) || added;
    }
    if (added) this.schedule('scan', () => this.scan(), MUTATION_MS);
  }

  // --- Qué sabe el caso de las cuentas en pantalla ---
  queueLookups() {
    const now = Date.now();
    for (const t of this.targets || []) {
      const key = handleKey(t.handle);
      const cached = this.infos.get(key);
      if (key && (!cached || now - cached.at > LOOKUP_TTL_MS)) this.pendingLookup.add(key);
    }
    if (this.pendingLookup.size) this.schedule('lookup', () => this.lookup(), LOOKUP_MS);
  }

  async lookup() {
    const handles = [...this.pendingLookup].slice(0, 50);
    for (const h of handles) this.pendingLookup.delete(h);
    if (!handles.length) return;
    // Mientras se espera la respuesta se anotan como consultados, para no pedirlos de nuevo.
    for (const h of handles) if (!this.infos.has(h)) this.infos.set(h, { info: null, at: Date.now() });
    const reply = await send({ type: 'lens:lookup', platform: this.extractor.platform, handles });
    const found = (reply && reply.handles) || {};
    const byKey = new Map(Object.entries(found).map(([k, v]) => [handleKey(k), v]));
    for (const h of handles) this.infos.set(h, { info: byKey.get(h) || null, at: Date.now() });
    if (this.pendingLookup.size) this.schedule('lookup', () => this.lookup(), LOOKUP_MS);
    this.highlighter.rescan(document.body);
    this.requestFrame();
  }

  accountLines(handle, info) {
    const lines = [{ text: `@${handle}`, bold: true }];
    if (info.known) lines.push({ text: `En el caso · ${info.posts_captured || 0} publicaciones capturadas` });
    for (const link of (info.links || []).filter((l) => l && l.review_status !== 'rejected').slice(0, 5)) {
      const status = { confirmed: 'confirmada por un analista', proposed: 'pendiente de revisión', pending: 'pendiente de revisión' }[link.review_status] || (link.review_status || 'pendiente de revisión');
      const where = link.platform && link.platform !== this.extractor.platform ? ` (${link.platform})` : '';
      lines.push({ text: `Hipótesis MENARD de mismo operador ↔ @${link.other}${where} · puntaje ${Number(link.score || 0).toFixed(2)} · ${status}` });
    }
    for (const rel of (info.relations || []).slice(0, 5)) lines.push({ text: `${rel.type || 'relación'} → ${rel.label || ''}`, dim: true });
    if (info.note) lines.push({ text: clip(info.note, 200), dim: true });
    if ((info.links || []).length) lines.push({ text: 'Es una hipótesis para revisar, no una conclusión.', dim: true });
    return lines;
  }

  renderMarks() {
    if (this.pausedReason || !this.overlay.layer) return;
    const vh = window.innerHeight || 800;
    const marks = [];
    for (const t of this.targets || []) {
      const entry = this.infos.get(handleKey(t.handle));
      const info = entry && entry.info;
      if (!info || !t.element.isConnected) continue;
      const links = (info.links || []).filter((l) => l && l.review_status !== 'rejected');
      if (!info.known && !links.length) continue;
      const rect = t.element.getBoundingClientRect();
      if (!rect.width || rect.bottom < 0 || rect.top > vh) continue;
      const score = links.reduce((max, l) => Math.max(max, Number(l.score) || 0), 0);
      marks.push({ rect, handle: t.handle, level: links.length ? 'menard' : 'known', score, lines: this.accountLines(t.handle, info) });
      if (marks.length >= 80) break;
    }
    this.overlay.setAccountMarks(marks);
  }

  // --- Datachunks ---
  chunkStatus(kind, value) {
    const section = this.sent.get(chunkKey(kind, value));
    if (section !== undefined) return { status: 'sent', section };
    if (kind === 'account') {
      const entry = this.infos.get(handleKey(value));
      if (entry && entry.info && entry.info.known) return { status: 'known' };
    }
    return { status: 'new' };
  }

  rectsOf(chunk) {
    try {
      return [...chunk.range.getClientRects()].filter((r) => r.width > 0 && r.height > 0);
    } catch {
      return [];
    }
  }

  hoverChunk() {
    const p = this.pointer;
    if (!p || this.pausedReason || this.overlay.contains(p.target)) return;
    let chunk = null;
    const pos = document.caretPositionFromPoint
      ? document.caretPositionFromPoint(p.x, p.y)
      : (document.caretRangeFromPoint ? document.caretRangeFromPoint(p.x, p.y) : null);
    const node = pos && (pos.offsetNode || pos.startContainer);
    const offset = pos ? (pos.offset ?? pos.startOffset) : 0;
    if (node && node.nodeType === 3) {
      const candidate = this.highlighter.chunkAtOffset(node, offset);
      // El "caret" más cercano puede estar lejos del puntero: se confirma con los rectángulos reales.
      if (candidate && this.rectsOf(candidate).some((r) => p.x >= r.left - 1 && p.x <= r.right + 1 && p.y >= r.top - 1 && p.y <= r.bottom + 1)) chunk = candidate;
    }
    if (chunk) {
      this.focusedChunk = chunk;
      this.overlay.showGrip(chunk, this.rectsOf(chunk));
    } else if (this.overlay.gripChunk) {
      this.overlay.hideGrip();
    }
  }

  contextOf(node) {
    const block = node.parentElement && node.parentElement.closest('article, p, li, blockquote, td, [data-testid="tweetText"], [data-testid="postText"], div');
    return clip(cleanText(block ? block.textContent : node.nodeValue), 500);
  }

  toDatachunk(chunk) {
    const info = this.session.infoFor(chunk.node) || {};
    return makeChunk({
      kind: chunk.kind, value: chunk.value, quote: chunk.quote, detected_by: chunk.detected_by,
      platform: chunk.platform || '', // cuentas de otra red ("mi ig es ..."); vacío = plataforma de la página
      context: info.text ? clip(info.text, 500) : this.contextOf(chunk.node),
      author_handle: info.handle || '', post_id: info.postId || '',
    }, { url: location.href, title: document.title, platform: this.extractor.platform });
  }

  /** Envía un chunk al expediente. Solo se llama por una acción explícita del analista. */
  async sendChunk(chunk, section) {
    const data = chunk.range ? this.toDatachunk(chunk) : chunk;
    const rects = chunk.range ? this.rectsOf(chunk) : [];
    const reply = await send({ type: 'lens:sendFinding', chunk: data, section: section || null });
    if (!reply || !reply.ok) {
      this.overlay.toast((reply && reply.error) || 'No se pudo guardar el hallazgo.', 'error');
      return false;
    }
    this.markSent(data.kind, data.value, reply.section);
    if (rects.length) this.overlay.fly(rects);
    this.overlay.toast(`Guardado en «${reply.section}»`);
    this.overlay.hideGrip(true);
    return true;
  }

  markSent(kind, value, sectionName) {
    this.sent.set(chunkKey(kind, value), sectionName || 'Sin clasificar');
    this.highlighter.rescan(document.body);
  }

  selectionInfo() {
    const sel = document.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
    const range = sel.getRangeAt(0);
    const anchor = range.commonAncestorContainer;
    const el = anchor.nodeType === 1 ? anchor : anchor.parentElement;
    // Nunca desde campos editables, formularios con contraseña ni mensajería privada.
    if (!el || el.closest(SKIP_SELECTOR) || insidePrivate(el) || this.overlay.contains(el)) return null;
    const textValue = sel.toString();
    if (cleanText(textValue).length < 2) return null;
    return { range, text: textValue, node: range.startContainer };
  }

  updateSelectionButton() {
    const info = this.pausedReason ? null : this.selectionInfo();
    if (!info) return this.overlay.hideSelectionButton();
    const rects = [...info.range.getClientRects()];
    if (!rects.length) return this.overlay.hideSelectionButton();
    return this.overlay.showSelectionButton(rects[rects.length - 1]);
  }

  /** Selección manual -> datachunk "text" (detected_by: manual). Abre el menú para elegir inciso. */
  manualFromSelection() {
    const info = this.pausedReason ? null : this.selectionInfo();
    if (!info) {
      this.overlay.toast('Seleccioná un texto de la página primero.', 'error');
      return;
    }
    const post = this.session.infoFor(info.node) || {};
    const data = makeManualChunk(info.text, { url: location.href, title: document.title, platform: this.extractor.platform }, {
      context: post.text ? clip(post.text, 500) : this.contextOf(info.node),
      author_handle: post.handle || '', post_id: post.postId || '',
    });
    if (!data) return;
    const rects = [...info.range.getClientRects()];
    const anchor = rects[rects.length - 1] || { left: 20, top: 40, bottom: 60, right: 120 };
    // Si la selección cabe en un solo nodo de texto, queda resaltada como un chunk más.
    if (!info.text.includes('\n')) this.highlighter.addLiteral({ kind: 'text', value: data.value, quote: info.text.trim(), detected_by: 'manual' });
    this.highlighter.rescan(document.body);
    this.overlay.hideSelectionButton();
    this.focusedChunk = data;
    this.overlay.openMenu(data, anchor);
  }

  /** Atajo: manda el chunk enfocado al último inciso usado. */
  async sendFocused() {
    const chunk = this.overlay.gripChunk || this.focusedChunk;
    if (!chunk) {
      this.overlay.toast('No hay un datachunk enfocado: pasá el mouse por uno resaltado.', 'error');
      return;
    }
    const reply = await send({ type: 'lens:getSections' });
    const last = ((reply && reply.sections) || []).find((s) => s.last) || null;
    this.sendChunk(chunk, last || { id: null, name: 'Sin clasificar' });
  }
}

function onMessage(message, _sender, respond) {
  if (!message || typeof message.type !== 'string') return undefined;
  switch (message.type) {
    case 'lens:start':
      startLens(message);
      respond({ ok: true });
      break;
    case 'lens:stop':
      if (lens) lens.stop();
      respond({ ok: true });
      break;
    case 'lens:command':
      if (lens && message.command === 'send-focused-chunk') lens.sendFocused();
      if (lens && message.command === 'chunk-from-selection') lens.manualFromSelection();
      respond({ ok: Boolean(lens) });
      break;
    case 'lens:findingSaved':
      // El hallazgo se guardó desde el panel (arrastre): se refleja en la página.
      if (lens && message.chunk) {
        const focused = lens.focusedChunk;
        if (focused && focused.range && chunkKey(focused.kind, focused.value) === chunkKey(message.chunk.kind, message.chunk.value)) {
          lens.overlay.fly(lens.rectsOf(focused));
        }
        lens.markSent(message.chunk.kind, message.chunk.value, message.section);
        lens.overlay.toast(`Guardado en «${message.section}»`);
      }
      respond({ ok: true });
      break;
    default:
      return undefined;
  }
  return undefined;
}

function startLens(config) {
  if (lens) {
    lens.caseName = config.caseName || lens.caseName;
    lens.applyRoute();
    return;
  }
  lens = new Lens(config);
  lens.start();
}

/** Punto de entrada: pregunta al service worker si la captura está encendida para esta pestaña. */
export async function boot() {
  if (!listening) {
    chrome.runtime.onMessage.addListener(onMessage);
    listening = true;
  }
  const state = await send({ type: 'lens:hello', url: location.href });
  if (state && state.active) startLens(state);
}
