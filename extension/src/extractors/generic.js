// Extractor genérico: cualquier página. Texto visible por bloques, enlaces, y handles / emails /
// dominios detectados por patrón. No arma cuentas (no se sabe de qué plataforma es un "@handle" suelto):
// manda el texto al caso como text_snippets y deja las detecciones para el panel.

import { qa, qaSelf, attr, richText } from '../lib/dom.js';
import { LENS_HOST_TAG } from '../lib/dom.js';
import { absoluteUrl, uniq, cleanText } from '../lib/normalize.js';
import { detectChunks } from '../lib/chunks.js';
import { insidePrivate } from '../lib/routes.js';

export const platform = 'generic';

export const SELECTORS = {
  // Bloques de texto que se capturan cuando entran en pantalla.
  block: 'article, p, li, blockquote, h1, h2, h3, h4, td, dd, pre, figcaption',
  // Nunca se lee nada de acá adentro.
  skip: `script, style, noscript, template, nav, footer, form, input, textarea, select, button, [contenteditable=""], [contenteditable="true"], [aria-hidden="true"], [hidden], ${LENS_HOST_TAG}`,
  link: 'a[href]',
};

const MIN_BLOCK_CHARS = 25;

export function matches() {
  return true;
}

export function pageKind() {
  return 'page';
}

function skipped(el) {
  return Boolean(el.closest(SELECTORS.skip)) || insidePrivate(el);
}

/** Bloques de texto "hoja": los que no contienen a su vez otro bloque (evita capturar dos veces). */
export function findItems(root) {
  return qaSelf(root, SELECTORS.block).filter((el) => {
    if (skipped(el)) return false;
    if (el.querySelector(SELECTORS.block)) return false;
    return cleanText(el.textContent).length >= MIN_BLOCK_CHARS;
  });
}

export function extractItem(el, ctx = {}) {
  if (!el || skipped(el)) return null;
  const body = richText(el);
  if (body.length < MIN_BLOCK_CHARS) return null;
  const base = ctx.url || '';
  const links = uniq(qa(el, SELECTORS.link).map((a) => absoluteUrl(attr(a, 'href'), base)).filter(Boolean));
  const detections = detectChunks(body).map(({ kind, value, quote, platform }) => ({ kind, value, quote, ...(platform ? { platform } : {}) }));
  return { records: [], interactions: [], snippets: [body], links, detections, primary: { handle: '', postId: '', text: body } };
}

/** Lectura de página completa (sin esperar a que cada bloque entre en pantalla). Útil para pruebas. */
export function extractPage(doc, ctx = {}) {
  const out = { records: [], interactions: [], snippets: [], links: [], detections: [] };
  if (!ctx.full) return out;
  const seen = new Set();
  for (const el of findItems(doc.body || doc)) {
    const item = extractItem(el, ctx);
    if (!item) continue;
    out.snippets.push(...item.snippets);
    out.links.push(...item.links);
    for (const d of item.detections) {
      const k = `${d.kind}|${d.value}`;
      if (!seen.has(k)) { seen.add(k); out.detections.push(d); }
    }
  }
  out.links = uniq(out.links);
  return out;
}

export function findHandleTargets() {
  return [];
}
