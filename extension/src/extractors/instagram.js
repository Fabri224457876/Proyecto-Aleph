// Extractor de Instagram. Instagram ofusca las clases CSS y casi no usa atributos estables, así que
// este extractor se apoya en lo poco que sí es estable: las etiquetas <meta property="og:*">, la forma
// de las URL (/p/<código>/, /reel/<código>/, /<usuario>/) y los <time datetime>.
// Es el extractor MENOS fiable de todos: validar en vivo (ver README).

import { q, qa, text, attr, richText, pathOf, closest } from '../lib/dom.js';
import { normalizeHandle, parseCount, toIsoUtc, cleanText } from '../lib/normalize.js';

export const platform = 'instagram';

export const SELECTORS = {
  ogTitle: 'meta[property="og:title"]',
  ogDescription: 'meta[property="og:description"], meta[name="description"]',
  ogImage: 'meta[property="og:image"]',
  ogUrl: 'meta[property="og:url"], link[rel="canonical"]',
  profileHeader: 'main header, header section',
  profileHandle: 'header h2, header h1',
  profileBio: 'header section > div:last-child, header [data-testid="user-bio"]',
  profileCounts: 'header ul li, header a[href$="/followers/"], header a[href$="/following/"]',
  profileAvatar: 'header img[alt*="profile picture"], header img[alt*="foto del perfil"], header img',
  postRoot: 'article, main [role="presentation"], main',
  time: 'time[datetime]',
  userLink: 'a[href^="/"][role="link"], a[href^="/"]',
  caption: 'h1',
  commentList: 'ul',
  postLink: 'a[href*="/p/"], a[href*="/reel/"]',
};

const RESERVED = new Set([
  'p', 'reel', 'reels', 'explore', 'direct', 'accounts', 'stories', 'tv', 'about', 'legal', 'developer',
  'challenge', 'web', 'api', 'graphql', 'static', 'emails', 'privacy', 'session', 'your_activity',
]);
const HANDLE_RE = /^[A-Za-z0-9._]{1,30}$/;

export function matches(url) {
  try {
    return /(^|\.)instagram\.com$/i.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

function userFromPath(path) {
  const parts = (path || '').split('/').filter(Boolean);
  if (parts.length !== 1) return '';
  return HANDLE_RE.test(parts[0]) && !RESERVED.has(parts[0].toLowerCase()) ? parts[0] : '';
}

function shortcode(url) {
  const m = pathOf(url).match(/\/(?:p|reel|tv)\/([A-Za-z0-9_-]{5,})/);
  return m ? m[1] : '';
}

export function pageKind(url) {
  const path = pathOf(url);
  if (shortcode(url)) return 'thread';
  if (/^\/direct(\/|$)/.test(path)) return 'other';
  if (userFromPath(path)) return 'profile';
  if (path === '/' || /^\/explore/.test(path)) return 'timeline';
  return 'other';
}

// Una "unidad" es el bloque más chico que contiene un <time> y exactamente un autor: el pie de foto
// o un comentario. Se sube desde el <time> hasta encontrar un enlace de usuario.
function unitFor(timeEl, root) {
  let node = timeEl.parentElement;
  for (let depth = 0; node && node !== root && depth < 8; depth++, node = node.parentElement) {
    const authors = new Set(qa(node, 'a[href^="/"]').map((a) => userFromPath(pathOf(attr(a, 'href')))).filter(Boolean)
      .map((h) => h.toLowerCase()));
    if (authors.size >= 1 && cleanText(node.textContent).length > text(timeEl).length + 2) return node;
  }
  return null;
}

export function findItems(root) {
  const doc = root.ownerDocument || root;
  const url = (doc.location && doc.location.href) || '';
  // Solo en la vista de una publicación: en el perfil la grilla no trae texto ni fecha.
  if (url && pageKind(url) !== 'thread') return [];
  const seen = new Set();
  const out = [];
  for (const t of qa(root, SELECTORS.time)) {
    const unit = unitFor(t, root);
    if (unit && !seen.has(unit)) { seen.add(unit); out.push(unit); }
  }
  return out;
}

function ownerFromMeta(doc) {
  // og:title: "Nombre en Instagram: «texto»"; og:description: "123 likes, 4 comments - usuario on October 5, 2026: ..."
  const desc = attr(q(doc, SELECTORS.ogDescription), 'content');
  const m = desc.match(/-\s*([A-Za-z0-9._]{1,30})\s+(?:on|el)\s/i) || desc.match(/\(@([A-Za-z0-9._]{1,30})\)/);
  return m ? m[1] : '';
}

/** Una unidad (pie de foto o comentario) de la vista de publicación. */
export function extractItem(unit, ctx = {}) {
  if (!unit) return null;
  const url = ctx.url || (unit.ownerDocument.location && unit.ownerDocument.location.href) || '';
  const code = shortcode(url);
  if (!code) return null;
  const doc = unit.ownerDocument;

  const authorLink = qa(unit, 'a[href^="/"]').find((a) => userFromPath(pathOf(attr(a, 'href'))) && text(a));
  const handle = authorLink ? userFromPath(pathOf(attr(authorLink, 'href'))) : '';
  const timeEl = q(unit, SELECTORS.time);
  if (!handle || !timeEl) return null;
  const created = toIsoUtc(attr(timeEl, 'datetime'));

  // Texto: todo lo de la unidad menos el nombre del autor, la hora y los botones.
  const skip = new Set([authorLink, timeEl, closest(timeEl, 'a')].filter(Boolean));
  const parts = [];
  const mentions = [];
  const hashtags = [];
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) parts.push(child.nodeValue);
      else if (child.nodeType === 1) {
        if (skip.has(child) || /^(BUTTON|SVG|TIME|SCRIPT|STYLE)$/i.test(child.tagName)) continue;
        if (child.getAttribute('role') === 'button') continue;
        if (child.tagName === 'A') {
          const href = attr(child, 'href');
          const label = text(child);
          if (label.startsWith('@')) mentions.push(normalizeHandle(label).toLowerCase());
          else if (/^\/explore\/tags\//.test(href)) hashtags.push(label.replace(/^#/, '').toLowerCase());
        }
        if (child.tagName === 'IMG') parts.push(attr(child, 'alt'));
        else if (child.tagName === 'BR') parts.push('\n');
        else walk(child);
      }
    }
  };
  walk(unit);
  let body = cleanText(parts.join(''));
  // Restos de interfaz que quedan pegados al texto ("Reply", "Responder", "12 likes", "Ver traducción").
  body = body.replace(/\s*(\d+\s+(likes?|me gusta)|Reply|Responder|See translation|Ver traducción)\s*$/gi, '').trim();
  if (body.toLowerCase().startsWith(handle.toLowerCase())) body = body.slice(handle.length).trim();
  if (!body) return null;

  const owner = ownerFromMeta(doc);
  const isOwner = Boolean(owner) && owner.toLowerCase() === handle.toLowerCase();
  // El pie de foto es la unidad con <h1>, o si no, la primera unidad del autor de la publicación.
  const isCaption = Boolean(q(unit, SELECTORS.caption)) || (isOwner && findItems(doc)[0] === unit);
  // En los comentarios, el enlace de la hora suele apuntar a /p/<código>/c/<id del comentario>/.
  const timeLink = closest(timeEl, 'a[href*="/c/"]');
  const commentId = timeLink ? (pathOf(attr(timeLink, 'href')).match(/\/c\/(\d+)/) || [])[1] : '';
  const meta = { permalink: `https://www.instagram.com/p/${code}/`, shortcode: code };
  let post;
  const interactions = [];
  if (isCaption) {
    post = { platform_post_id: code, text: body, created_at: created, kind: 'original', mentions, hashtags, meta };
  } else {
    // Instagram no expone el id del comentario en el DOM: se arma uno estable con código + autor + fecha.
    const stamp = created ? created.replace(/[-:.TZ]/g, '').slice(0, 14) : 'sinfecha';
    post = {
      platform_post_id: commentId ? `${code}:c:${commentId}` : `${code}:c:${handle.toLowerCase()}:${stamp}`,
      text: body, created_at: created,
      kind: 'reply', reply_to: owner && !isOwner ? owner : '',
      mentions, hashtags, meta: commentId ? { ...meta, comment_id: commentId } : { ...meta, synthetic_id: true },
    };
    if (post.reply_to) interactions.push([handle, post.reply_to, 'reply']);
  }
  for (const m of mentions) if (m !== handle.toLowerCase()) interactions.push([handle, m, 'mention']);
  const account = { platform, handle, url: `https://www.instagram.com/${handle}/` };
  return { records: [{ account, post }], interactions, primary: { handle, postId: post.platform_post_id, text: body } };
}

/** Perfil: se arma con las etiquetas og:* (lo más estable) y se completa con la cabecera visible. */
export function extractPage(doc, ctx = {}) {
  const url = ctx.url || (doc.location && doc.location.href) || '';
  const out = { records: [], interactions: [], snippets: [] };
  if (pageKind(url) !== 'profile') return out;
  const handle = userFromPath(pathOf(url));
  if (!handle) return out;

  const title = attr(q(doc, SELECTORS.ogTitle), 'content');
  const desc = attr(q(doc, SELECTORS.ogDescription), 'content');
  // La SPA no siempre actualiza las <meta> al navegar: solo se usan si nombran al perfil de la URL.
  const metaOk = new RegExp(`@${handle.replace(/[.]/g, '\\.')}\\b`, 'i').test(`${title} ${desc}`);

  let followers = null;
  let following = null;
  let posts = null;
  let displayName = '';
  if (metaOk) {
    // "1,234 Followers, 567 Following, 89 Posts - See Instagram photos and videos from Nombre (@usuario)"
    const f = desc.match(/([\d.,]+\s*(?:k|m|mil|mill\.?)?)\s+(followers|seguidores)/i);
    const g = desc.match(/([\d.,]+\s*(?:k|m|mil|mill\.?)?)\s+(following|seguidos)/i);
    const p = desc.match(/([\d.,]+\s*(?:k|m|mil|mill\.?)?)\s+(posts|publicaciones)/i);
    followers = f ? parseCount(f[1]) : null;
    following = g ? parseCount(g[1]) : null;
    posts = p ? parseCount(p[1]) : null;
    const n = title.match(/^(.*?)\s*\(@/);
    displayName = n ? n[1].trim() : '';
  }

  const header = q(doc, SELECTORS.profileHeader);
  const shown = text(q(doc, SELECTORS.profileHandle));
  const headerOk = header && shown.toLowerCase() === handle.toLowerCase();
  let bio = '';
  let avatar = '';
  if (headerOk) {
    // El número exacto suele venir en el atributo title del <span> cuando el visible está abreviado.
    for (const a of qa(header, 'a[href$="/followers/"], a[href$="/following/"]')) {
      const exact = q(a, 'span[title]');
      const n = parseCount(exact ? attr(exact, 'title') : text(a));
      if (n === null) continue;
      if (/followers\/$/.test(attr(a, 'href'))) followers = n;
      else following = n;
    }
    bio = richText(q(doc, SELECTORS.profileBio));
    const img = q(doc, SELECTORS.profileAvatar);
    avatar = img ? attr(img, 'src') : '';
  }
  if (!avatar && metaOk) avatar = attr(q(doc, SELECTORS.ogImage), 'content');
  if (!metaOk && !headerOk) return out;

  const meta = {};
  if (posts !== null) meta.posts = posts;
  out.records.push({
    account: {
      platform, handle, display_name: displayName, bio, url: `https://www.instagram.com/${handle}/`,
      followers, following, avatar_url: avatar, meta,
    },
    post: null,
  });
  return out;
}

export function findHandleTargets(root) {
  const out = [];
  const seen = new Set();
  for (const a of qa(root, 'a[href^="/"]')) {
    const handle = userFromPath(pathOf(attr(a, 'href')));
    // Solo enlaces cuyo texto ES el handle (así no se marcan fotos ni íconos).
    if (!handle || text(a).replace(/^@/, '').toLowerCase() !== handle.toLowerCase() || seen.has(a)) continue;
    seen.add(a);
    out.push({ element: a, handle });
  }
  for (const h of qa(root, SELECTORS.profileHandle)) {
    const t = text(h);
    if (HANDLE_RE.test(t)) out.push({ element: h, handle: t });
  }
  return out;
}
