// Sesión de captura de una pestaña: decide qué elementos mirar, extrae los que entran en pantalla,
// deduplica y arma los lotes. No usa chrome.* ni observadores reales (se inyectan), así se prueba en Node.

import { CaptureAccumulator } from '../lib/batch.js';
import { excludedReason, insidePrivate } from '../lib/routes.js';
import { handleKey } from '../lib/normalize.js';

export class CaptureSession {
  /**
   * @param document          documento a leer
   * @param extractor         módulo de extractores/ (x, bluesky, ...)
   * @param getUrl            () => URL actual de la pestaña
   * @param observeVisibility (element, onVisible) => void. En el navegador usa IntersectionObserver.
   * @param version           versión de la extensión, para extension_version
   */
  constructor({ document, extractor, getUrl, observeVisibility, version = '', now = () => new Date() }) {
    this.document = document;
    this.extractor = extractor;
    this.getUrl = getUrl;
    this.observeVisibility = observeVisibility;
    this.version = version;
    this.now = now;
    this.accumulator = new CaptureAccumulator();
    this.watched = new WeakSet(); // elementos ya entregados al observador de visibilidad
    this.extracted = new WeakSet(); // elementos ya leídos
    this.itemInfo = new WeakMap(); // elemento -> { handle, postId, text } (contexto para los datachunks)
    this.detections = new Map(); // "kind|value" -> detección pendiente de avisar al panel
    this.detectionKeys = new Set();
    this.newTexts = []; // textos nuevos, para el detector del servidor (captures/chunks, opcional)
    this.pageUrl = getUrl();
  }

  excluded() {
    return excludedReason(this.getUrl());
  }

  /** Busca unidades capturables nuevas y las pone a observar. Devuelve cuántas sumó. */
  scan(root = this.document) {
    if (this.excluded()) return 0;
    let added = 0;
    for (const el of this.extractor.findItems(root)) {
      if (this.watched.has(el) || insidePrivate(el)) continue;
      this.watched.add(el);
      added += 1;
      this.observeVisibility(el, () => this.capture(el));
    }
    return added;
  }

  /** Lee un elemento que ya está en pantalla. Devuelve lo nuevo que aportó. */
  capture(el) {
    const fresh = { accounts: [], posts: 0 };
    if (this.extracted.has(el) || !el.isConnected || this.excluded() || insidePrivate(el)) return fresh;
    let item = null;
    try {
      item = this.extractor.extractItem(el, { url: this.getUrl() });
    } catch {
      item = null; // un cambio de HTML de la plataforma no tiene que romper la captura del resto
    }
    if (!item) return fresh;
    this.extracted.add(el);
    this.pageUrl = this.getUrl();
    if (item.primary) this.itemInfo.set(el, item.primary);
    this.#absorb(item, fresh);
    if (item.primary && item.primary.text) this.newTexts.push(item.primary.text);
    return fresh;
  }

  /** Datos de página (cabecera de perfil, listas). Se puede llamar muchas veces: solo suma lo nuevo. */
  scanPage() {
    const fresh = { accounts: [], posts: 0 };
    if (this.excluded()) return fresh;
    let page = null;
    try {
      page = this.extractor.extractPage(this.document, { url: this.getUrl() });
    } catch {
      page = null;
    }
    if (page) this.#absorb(page, fresh, { authoritative: true });
    return fresh;
  }

  #absorb(item, fresh, options = {}) {
    for (const { account, post } of item.records || []) {
      const r = this.accumulator.add(account, post, options);
      if (r.newAccount) fresh.accounts.push(handleKey(account.handle));
      if (r.newPost) fresh.posts += 1;
    }
    for (const [src, dst, type] of item.interactions || []) this.accumulator.addInteraction(src, dst, type);
    for (const s of item.snippets || []) this.accumulator.addSnippet(s);
    for (const d of item.detections || []) {
      const k = `${d.kind}|${d.value}`;
      if (this.detectionKeys.has(k)) continue;
      this.detectionKeys.add(k);
      this.detections.set(k, d);
    }
  }

  /** Contexto de un nodo: la publicación que lo contiene, si ya fue leída. */
  infoFor(node) {
    let el = node && node.nodeType === 1 ? node : node && node.parentElement;
    for (let depth = 0; el && depth < 40; depth++, el = el.parentElement) {
      const info = this.itemInfo.get(el);
      if (info) return info;
    }
    return null;
  }

  takeDetections() {
    const out = [...this.detections.values()];
    this.detections.clear();
    return out;
  }

  takeTexts(maxItems = 20, maxChars = 8000) {
    const out = [];
    let used = 0;
    while (this.newTexts.length && out.length < maxItems) {
      const t = this.newTexts[0];
      if (out.length && used + t.length > maxChars) break;
      out.push(this.newTexts.shift().slice(0, maxChars));
      used += t.length;
    }
    return out;
  }

  /** Lote pendiente (CaptureBatch) o null. Si la página quedó excluida, lo pendiente se descarta. */
  flush(pageTitle = '') {
    if (this.excluded()) return null;
    return this.accumulator.drain({
      pageUrl: this.pageUrl || this.getUrl(),
      pageTitle,
      platform: this.extractor.platform,
      now: this.now(),
      version: this.version,
    });
  }

  get totals() {
    return this.accumulator.totals;
  }
}
