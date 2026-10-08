// Ayudas de lectura del DOM. Solo lectura: nada de acá modifica la página.

import { cleanText } from './normalize.js';

export const LENS_HOST_TAG = 'aleph-lens-root';

const BLOCK_TAGS = new Set([
  'ADDRESS', 'ARTICLE', 'ASIDE', 'BLOCKQUOTE', 'DD', 'DIV', 'DL', 'DT', 'FIGCAPTION', 'FIGURE', 'FOOTER',
  'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HEADER', 'LI', 'MAIN', 'NAV', 'OL', 'P', 'PRE', 'SECTION', 'TABLE',
  'TR', 'UL',
]);
const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'TEXTAREA', 'INPUT', 'SELECT', 'OPTION']);

/**
 * Texto de un elemento respetando emojis renderizados como <img alt="..."> y saltos de bloque.
 * Con { blocks: false } ningun elemento corta linea (X envuelve las menciones en <div> en linea).
 */
export function richText(el, { blocks = true } = {}) {
  if (!el) return '';
  let out = '';
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) out += child.nodeValue;
      else if (child.nodeType === 1) {
        const tag = child.tagName.toUpperCase();
        if (SKIP_TAGS.has(tag)) continue;
        if (tag === 'IMG') out += child.getAttribute('alt') || '';
        else if (tag === 'BR') out += '\n';
        else {
          const block = blocks && BLOCK_TAGS.has(tag);
          if (block && out && !out.endsWith('\n')) out += '\n';
          walk(child);
          if (block && !out.endsWith('\n')) out += '\n';
        }
      }
    }
  };
  walk(el);
  return cleanText(out);
}

export function text(el) {
  return el ? cleanText(el.textContent) : '';
}

export function attr(el, name) {
  return el ? (el.getAttribute(name) || '').trim() : '';
}

export function q(root, selector) {
  if (!root || !selector) return null;
  try {
    return root.querySelector(selector);
  } catch {
    return null;
  }
}

export function qa(root, selector) {
  if (!root || !selector) return [];
  try {
    return [...root.querySelectorAll(selector)];
  } catch {
    return [];
  }
}

/** Como qa(), pero incluye a la raíz si ella misma coincide. */
export function qaSelf(root, selector) {
  const out = qa(root, selector);
  try {
    if (root && root.nodeType === 1 && root.matches(selector)) out.unshift(root);
  } catch {
    /* selector inválido: se ignora */
  }
  return out;
}

export function closest(el, selector) {
  try {
    return el && el.closest ? el.closest(selector) : null;
  } catch {
    return null;
  }
}

export function pathOf(href, base) {
  try {
    return new URL(href, base || 'https://invalid.example/').pathname;
  } catch {
    return '';
  }
}
