// Resaltado de datachunks sobre rangos de texto, SIN modificar el DOM de la página.
// Cada pasada (rescan) reconstruye la lista completa de rangos y reemplaza la anterior: por eso no
// hay duplicados aunque la plataforma vuelva a renderizar. El pintado lo hace un "registro" inyectado
// (en el navegador, la CSS Custom Highlight API); en los tests, uno falso.

import { detectChunks, findLiteral, mergeMatches, chunkKey } from '../lib/chunks.js';
import { LENS_HOST_TAG } from '../lib/dom.js';
import { insidePrivate } from '../lib/routes.js';

export const HIGHLIGHT_NAMES = { new: 'aleph-chunk-new', sent: 'aleph-chunk-sent', known: 'aleph-chunk-known' };

// Nunca se resalta (ni se lee) adentro de esto.
export const SKIP_SELECTOR = [
  'script', 'style', 'noscript', 'template', 'textarea', 'input', 'select', 'option', 'button', 'svg', 'code > pre',
  '[contenteditable=""]', '[contenteditable="true"]', '[role="textbox"]', '[aria-hidden="true"]', LENS_HOST_TAG,
].join(',');

const MAX_NODE_CHARS = 20000;

export class ChunkHighlighter {
  /**
   * @param registry      { replace({ new: Range[], sent: Range[], known: Range[] }), clear() }
   * @param isNearViewport (element) => boolean. Limita el trabajo a lo que está en pantalla o cerca.
   * @param statusOf      (kind, value) => { status: "new"|"sent"|"known", section?: string }
   */
  constructor({ document, registry, isNearViewport = () => true, statusOf = () => ({ status: 'new' }), maxChunks = 1500 }) {
    this.document = document;
    this.registry = registry;
    this.isNearViewport = isNearViewport;
    this.statusOf = statusOf;
    this.maxChunks = maxChunks;
    this.cache = new WeakMap(); // nodo de texto -> { text, version, matches }
    this.literals = []; // chunks manuales o de FUNES que se buscan por su texto exacto
    this.literalVersion = 0;
    this.chunks = [];
    this.byNode = new Map();
  }

  /** Suma una frase a resaltar literalmente (selección manual o chunk devuelto por el servidor). */
  addLiteral({ kind, value, quote, detected_by }) {
    const q = String(quote || value || '');
    if (q.trim().length < 2) return false;
    if (this.literals.some((l) => l.quote === q && l.kind === kind)) return false;
    this.literals.push({ kind, value: String(value || q), quote: q, detected_by: detected_by || 'manual' });
    this.literalVersion += 1;
    return true;
  }

  #skip(node) {
    const parent = node.parentElement;
    if (!parent) return true;
    if (parent.closest(SKIP_SELECTOR)) return true;
    return insidePrivate(parent);
  }

  #matchesFor(node) {
    const textValue = node.nodeValue || '';
    const cached = this.cache.get(node);
    if (cached && cached.text === textValue && cached.version === this.literalVersion) return cached.matches;
    let matches = [];
    if (textValue.length >= 3 && textValue.length <= MAX_NODE_CHARS && /\S/.test(textValue)) {
      const rules = detectChunks(textValue).map((m) => ({ ...m, detected_by: 'rule' }));
      const literal = this.literals.length ? findLiteral(textValue, this.literals) : [];
      matches = mergeMatches(rules, literal);
    }
    this.cache.set(node, { text: textValue, version: this.literalVersion, matches });
    return matches;
  }

  /** Recorre el texto visible, arma los rangos y reemplaza el resaltado anterior. Devuelve los chunks. */
  rescan(root = this.document.body) {
    const chunks = [];
    const byNode = new Map();
    const groups = { new: [], sent: [], known: [] };
    if (root) {
      const walker = this.document.createTreeWalker(root, 4 /* NodeFilter.SHOW_TEXT */);
      const nearCache = new Map();
      for (let node = walker.nextNode(); node && chunks.length < this.maxChunks; node = walker.nextNode()) {
        if ((node.nodeValue || '').length < 3 || this.#skip(node)) continue;
        const parent = node.parentElement;
        let near = nearCache.get(parent);
        if (near === undefined) { near = this.isNearViewport(parent); nearCache.set(parent, near); }
        if (!near) continue;
        const matches = this.#matchesFor(node);
        if (!matches.length) continue;
        const list = [];
        for (const m of matches) {
          const range = this.document.createRange();
          try {
            range.setStart(node, m.start);
            range.setEnd(node, m.end);
          } catch {
            continue;
          }
          const state = this.statusOf(m.kind, m.value) || { status: 'new' };
          const chunk = {
            key: chunkKey(m.kind, m.value), kind: m.kind, value: m.value, quote: m.quote,
            detected_by: m.detected_by || 'rule', platform: m.platform || '', node, start: m.start, end: m.end, range,
            status: state.status || 'new', section: state.section || '',
          };
          chunks.push(chunk);
          list.push(chunk);
          (groups[chunk.status] || groups.new).push(range);
        }
        if (list.length) byNode.set(node, list);
      }
    }
    this.chunks = chunks;
    this.byNode = byNode;
    this.registry.replace(groups);
    return chunks;
  }

  /** Chunk que contiene la posición (nodo de texto, desplazamiento), o null. */
  chunkAtOffset(node, offset) {
    const list = this.byNode.get(node);
    if (!list) return null;
    return list.find((c) => offset >= c.start && offset <= c.end) || null;
  }

  clear() {
    this.chunks = [];
    this.byNode = new Map();
    this.registry.clear();
  }
}

/** Registro real: CSS Custom Highlight API. Los estilos ::highlight() los inyecta el service worker. */
export class CssHighlightRegistry {
  constructor(win) {
    this.win = win;
    this.supported = Boolean(win.CSS && win.CSS.highlights && win.Highlight);
  }

  replace(groups) {
    if (!this.supported) return;
    for (const [status, name] of Object.entries(HIGHLIGHT_NAMES)) {
      const ranges = groups[status] || [];
      if (ranges.length) this.win.CSS.highlights.set(name, new this.win.Highlight(...ranges));
      else this.win.CSS.highlights.delete(name);
    }
  }

  clear() {
    if (!this.supported) return;
    for (const name of Object.values(HIGHLIGHT_NAMES)) this.win.CSS.highlights.delete(name);
  }
}
