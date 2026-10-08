// Extractor de X / Twitter. Funciones puras sobre el DOM ya renderizado.
// Los selectores se apoyan en los atributos data-testid de la web de X. NO están verificados contra
// la página real con sesión: ver "Selectores a validar en vivo" en el README.

import { q, qa, qaSelf, text, attr, richText, closest, pathOf } from '../lib/dom.js';
import { normalizeHandle, parseCount, absoluteUrl, toIsoUtc, uniq } from '../lib/normalize.js';

export const platform = 'x';

export const SELECTORS = {
  post: 'article[data-testid="tweet"]',
  postText: '[data-testid="tweetText"]',
  postUserName: '[data-testid="User-Name"]',
  postPermalink: 'a[href*="/status/"]',
  postTime: 'time[datetime]',
  socialContext: '[data-testid="socialContext"]',
  quoteContainer: 'div[role="link"][tabindex="0"]',
  replyCount: '[data-testid="reply"]',
  repostCount: '[data-testid="retweet"], [data-testid="unretweet"]',
  likeCount: '[data-testid="like"], [data-testid="unlike"]',
  viewCount: 'a[href$="/analytics"]',
  cardLink: '[data-testid="card.wrapper"] a[href]',
  photo: '[data-testid="tweetPhoto"] img',
  avatar: '[data-testid^="UserAvatar-Container-"]',
  profileName: '[data-testid="UserName"]',
  profileBio: '[data-testid="UserDescription"]',
  profileUrl: '[data-testid="UserUrl"]',
  profileLocation: '[data-testid="UserLocation"]',
  profileJoinDate: '[data-testid="UserJoinDate"]',
  profileBirthdate: '[data-testid="UserBirthdate"]',
  profileFollowing: 'a[href$="/following"]',
  profileFollowers: 'a[href$="/verified_followers"], a[href$="/followers"]',
  userCell: '[data-testid="UserCell"]',
  primaryColumn: '[data-testid="primaryColumn"]',
};

// Primer segmento de la ruta que NO es un handle.
const RESERVED = new Set([
  'home', 'explore', 'notifications', 'messages', 'i', 'search', 'settings', 'compose', 'hashtag', 'login',
  'logout', 'signup', 'tos', 'privacy', 'about', 'jobs', 'intent', 'share', 'account', 'communities',
]);
const REPLY_PREFIX = /^(replying to|en respuesta a|respondiendo a|em resposta a)\s/i;
const REPOST_HINT = /(reposted|reposteó|reposteaste|retweeted|retwitteó|repostou)/i;
const MONTHS_ES = { enero: 0, febrero: 1, marzo: 2, abril: 3, mayo: 4, junio: 5, julio: 6, agosto: 7, septiembre: 8, setiembre: 8, octubre: 9, noviembre: 10, diciembre: 11 };
const MONTHS_EN = { january: 0, february: 1, march: 2, april: 3, may: 4, june: 5, july: 6, august: 7, september: 8, october: 9, november: 10, december: 11 };

export function matches(url) {
  try {
    return /(^|\.)(x|twitter)\.com$/i.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

function handleFromPath(path) {
  const seg = (path || '').split('/').filter(Boolean)[0] || '';
  if (!seg || RESERVED.has(seg.toLowerCase()) || !/^[A-Za-z0-9_]{1,15}$/.test(seg)) return '';
  return seg;
}

export function pageKind(url) {
  const path = pathOf(url);
  const parts = path.split('/').filter(Boolean);
  if (!parts.length || ['home', 'explore', 'search', 'hashtag'].includes(parts[0].toLowerCase())) return 'timeline';
  if (parts[1] === 'status' && parts[2]) return 'thread';
  if (handleFromPath(path)) {
    if (['following', 'followers', 'verified_followers'].includes(parts[1])) return 'list';
    return 'profile';
  }
  return 'other';
}

export function findItems(root) {
  return qaSelf(root, SELECTORS.post);
}

function statusRef(href) {
  const m = pathOf(href).match(/^\/([A-Za-z0-9_]{1,15})\/status\/(\d+)/);
  return m ? { handle: m[1], id: m[2] } : null;
}

// Nombre visible y handle a partir del bloque User-Name ("Nombre", "@handle", "· 3h").
function readUserName(block) {
  if (!block) return { handle: '', displayName: '' };
  let handle = '';
  for (const a of qa(block, 'a[href^="/"]')) {
    const t = text(a);
    if (t.startsWith('@')) { handle = normalizeHandle(t); break; }
  }
  if (!handle) {
    const m = text(block).match(/@([A-Za-z0-9_]{1,15})/);
    if (m) handle = m[1];
  }
  if (!handle) {
    const first = q(block, 'a[href^="/"]');
    handle = first ? handleFromPath(pathOf(attr(first, 'href'))) : '';
  }
  // El nombre visible es el primer tramo, antes del "@handle".
  const firstChild = block.firstElementChild || block;
  let displayName = richText(firstChild).split('\n')[0] || '';
  if (displayName.startsWith('@')) displayName = '';
  return { handle, displayName };
}

function countFrom(el) {
  if (!el) return null;
  const label = attr(el, 'aria-label');
  const fromLabel = label ? parseCount(label) : null;
  return fromLabel ?? parseCount(text(el));
}

/** Una publicación (article) -> registros normalizados e interacciones. null si no se puede leer. */
export function extractItem(article, ctx = {}) {
  if (!article) return null;
  const base = ctx.url || 'https://x.com/';

  // Las citas van dentro de un contenedor propio: se separa lo del autor de lo citado.
  const quoteBox = qa(article, SELECTORS.quoteContainer).find((el) => q(el, SELECTORS.postUserName)) || null;
  const own = (selector) => qa(article, selector).filter((el) => !quoteBox || !quoteBox.contains(el));

  const userBlock = own(SELECTORS.postUserName)[0];
  const { handle: nameHandle, displayName } = readUserName(userBlock);

  // El enlace permanente es el que envuelve al <time>.
  const timeEl = own(SELECTORS.postTime)[0] || null;
  const permalinkEl = (timeEl && closest(timeEl, 'a[href*="/status/"]')) ||
    own(SELECTORS.postPermalink).find((a) => statusRef(attr(a, 'href')));
  let ref = permalinkEl ? statusRef(attr(permalinkEl, 'href')) : null;
  // En la vista de hilo, la publicación principal no enlaza a sí misma: se usa la URL de la página.
  if (!ref && pageKind(base) === 'thread') {
    const pageRef = statusRef(base);
    if (pageRef && (!nameHandle || pageRef.handle.toLowerCase() === nameHandle.toLowerCase())) ref = pageRef;
  }
  const handle = nameHandle || (ref && ref.handle) || '';
  if (!handle || !ref) return null;

  const textEl = own(SELECTORS.postText)[0] || null;
  const body = richText(textEl, { blocks: false });
  const interactions = [];

  // Menciones, hashtags y URLs a partir de los enlaces del texto (más fiable que el texto truncado).
  const mentions = [];
  const hashtags = [];
  const urls = [];
  for (const a of textEl ? qa(textEl, 'a[href]') : []) {
    const href = attr(a, 'href');
    const label = text(a);
    if (label.startsWith('@')) mentions.push(normalizeHandle(label).toLowerCase());
    else if (label.startsWith('#') || /^\/hashtag\//.test(href)) hashtags.push(label.replace(/^#/, '').toLowerCase());
    else if (/^https?:\/\//.test(href)) urls.push(href);
  }
  for (const a of own(SELECTORS.cardLink)) {
    const href = absoluteUrl(attr(a, 'href'), base);
    if (href && !matches(href)) urls.push(href);
  }

  // Tipo de publicación.
  let kind = 'original';
  let replyTo = '';
  const meta = {};

  const replyLine = qa(article, 'div, span').find((el) =>
    (!quoteBox || !quoteBox.contains(el)) && (!textEl || !textEl.contains(el)) &&
    REPLY_PREFIX.test(text(el)) && text(el).length < 200 && q(el, 'a[href^="/"]'));
  if (replyLine) {
    kind = 'reply';
    const targets = qa(replyLine, 'a[href^="/"]').map((a) => normalizeHandle(text(a))).filter(Boolean);
    replyTo = targets[0] || '';
    if (targets.length > 1) meta.reply_to_all = uniq(targets.map((t) => t.toLowerCase()));
  } else if (pageKind(base) === 'thread') {
    // En la vista de hilo las respuestas no siempre dicen "En respuesta a". Los ids de X son
    // cronológicos: lo que es posterior a la publicación de la URL se toma como respuesta a ella.
    // Es una inferencia (puede ser respuesta a otra respuesta) y queda marcada en meta.
    const pageRef = statusRef(base);
    let later = false;
    try {
      later = Boolean(pageRef) && BigInt(ref.id) > BigInt(pageRef.id);
    } catch {
      later = false;
    }
    if (later && pageRef.handle.toLowerCase() !== handle.toLowerCase()) {
      kind = 'reply';
      replyTo = pageRef.handle;
      meta.reply_inferred = true;
    }
  }

  let quoted = null;
  if (quoteBox) {
    const qUser = readUserName(q(quoteBox, SELECTORS.postUserName));
    if (qUser.handle) {
      if (kind === 'original') kind = 'quote';
      const qText = richText(q(quoteBox, SELECTORS.postText), { blocks: false });
      const qTime = q(quoteBox, SELECTORS.postTime);
      meta.quoted_handle = qUser.handle;
      quoted = { handle: qUser.handle, displayName: qUser.displayName, text: qText, time: qTime ? attr(qTime, 'datetime') : '' };
      interactions.push([handle, qUser.handle, 'quote']);
    }
  }

  const social = own(SELECTORS.socialContext)[0] || null;
  let reposter = null;
  if (social && REPOST_HINT.test(text(social))) {
    const link = closest(social, 'a[href^="/"]') || q(social, 'a[href^="/"]');
    const rh = link ? handleFromPath(pathOf(attr(link, 'href'))) : '';
    if (rh && rh.toLowerCase() !== handle.toLowerCase()) {
      reposter = { handle: rh, displayName: text(social).replace(REPOST_HINT, '').trim() };
    }
  } else if (social && /pinned|fijad/i.test(text(social))) {
    meta.pinned = true;
  }

  const replies = countFrom(own(SELECTORS.replyCount)[0]);
  const reposts = countFrom(own(SELECTORS.repostCount)[0]);
  const likes = countFrom(own(SELECTORS.likeCount)[0]);
  const views = countFrom(own(SELECTORS.viewCount)[0]);
  if (replies !== null) meta.replies = replies;
  if (reposts !== null) meta.reposts = reposts;
  if (likes !== null) meta.likes = likes;
  if (views !== null) meta.views = views;
  const media = own(SELECTORS.photo).map((img) => attr(img, 'src')).filter(Boolean);
  if (media.length) meta.media = media;
  meta.permalink = `https://x.com/${ref.handle}/status/${ref.id}`;

  const avatarImg = q(own(SELECTORS.avatar)[0] || null, 'img[src]');
  const account = {
    platform, handle, display_name: displayName, url: `https://x.com/${handle}`,
    avatar_url: avatarImg ? attr(avatarImg, 'src') : '',
  };
  const post = {
    platform_post_id: ref.id,
    text: body,
    created_at: timeEl ? toIsoUtc(attr(timeEl, 'datetime')) : null,
    lang: textEl ? attr(textEl, 'lang') : '',
    kind, reply_to: replyTo, mentions, hashtags, urls, meta,
  };

  const records = [{ account, post }];
  for (const m of uniq(mentions)) if (m !== handle.toLowerCase()) interactions.push([handle, m, 'mention']);
  if (kind === 'reply' && replyTo) interactions.push([handle, replyTo, 'reply']);

  if (reposter) {
    // El repost se anota en la cuenta que reposteó (kind "repost": MENARD no lo usa para estilometría).
    records.push({
      account: { platform, handle: reposter.handle, display_name: reposter.displayName, url: `https://x.com/${reposter.handle}` },
      post: {
        platform_post_id: ref.id, text: body, created_at: null, lang: post.lang, kind: 'repost',
        mentions, hashtags, urls,
        meta: { reposted_from: handle, original_created_at: post.created_at, permalink: meta.permalink },
      },
    });
    interactions.push([reposter.handle, handle, 'repost']);
  }
  if (quoted) {
    records.push({ account: { platform, handle: quoted.handle, display_name: quoted.displayName, url: `https://x.com/${quoted.handle}` }, post: null });
  }
  return { records, interactions, primary: { handle, postId: ref.id, text: body } };
}

function parseJoinDate(label) {
  // "Joined March 2019" / "Se unió en marzo de 2019"
  const m = String(label).toLowerCase().match(/([a-záéíóú]+)\s+(?:de\s+)?(\d{4})/);
  if (!m) return null;
  const month = MONTHS_EN[m[1]] ?? MONTHS_ES[m[1]];
  return month === undefined ? null : new Date(Date.UTC(Number(m[2]), month, 1)).toISOString();
}

/** Datos de página: cabecera de perfil y, en /following o /followers, la lista visible de cuentas. */
export function extractPage(doc, ctx = {}) {
  const url = ctx.url || (doc.location && doc.location.href) || '';
  const out = { records: [], interactions: [], snippets: [] };
  const kind = pageKind(url);
  const pageHandle = handleFromPath(pathOf(url));
  const column = q(doc, SELECTORS.primaryColumn) || doc;

  if (kind === 'profile' && pageHandle) {
    const nameBlock = q(column, SELECTORS.profileName);
    if (nameBlock) {
      const shown = text(nameBlock).match(/@([A-Za-z0-9_]{1,15})/);
      const handle = shown ? shown[1] : pageHandle;
      // Solo se confía en la cabecera si corresponde al perfil de la URL (la SPA puede ir atrasada).
      if (handle.toLowerCase() === pageHandle.toLowerCase()) {
        const meta = {};
        const location = text(q(column, SELECTORS.profileLocation));
        if (location) meta.location = location;
        const birth = text(q(column, SELECTORS.profileBirthdate));
        if (birth) meta.birthdate_label = birth;
        const urlEl = q(column, SELECTORS.profileUrl);
        const avatar = q(column, `${SELECTORS.avatar} img[src]`);
        out.records.push({
          account: {
            platform, handle,
            display_name: richText(nameBlock.firstElementChild || nameBlock).split('\n')[0].replace(/^@.*/, ''),
            bio: richText(q(column, SELECTORS.profileBio), { blocks: false }),
            url: `https://x.com/${handle}`,
            created_at_platform: parseJoinDate(text(q(column, SELECTORS.profileJoinDate))),
            followers: countFrom(q(column, SELECTORS.profileFollowers)),
            following: countFrom(q(column, SELECTORS.profileFollowing)),
            avatar_url: avatar ? attr(avatar, 'src') : '',
            meta: urlEl ? { ...meta, website: text(urlEl) } : meta,
          },
          post: null,
        });
      }
    }
  }

  if (kind === 'list' && pageHandle) {
    const which = /followers$/.test(pathOf(url).replace(/\/$/, '')) ? 'follower_handles' : 'following_handles';
    const handles = [];
    for (const cell of qa(column, SELECTORS.userCell)) {
      const m = text(cell).match(/@([A-Za-z0-9_]{1,15})/);
      if (!m) continue;
      handles.push(m[1]);
      out.records.push({ account: { platform, handle: m[1], url: `https://x.com/${m[1]}` }, post: null });
    }
    if (handles.length) out.records.push({ account: { platform, handle: pageHandle, [which]: uniq(handles) }, post: null });
  }
  return out;
}

/** Dónde colgar las insignias: elementos que muestran un handle. */
export function findHandleTargets(root) {
  const out = [];
  for (const block of qaSelf(root, `${SELECTORS.postUserName}, ${SELECTORS.profileName}`)) {
    const { handle } = readUserName(block);
    if (handle) out.push({ element: block, handle });
  }
  for (const cell of qaSelf(root, SELECTORS.userCell)) {
    const m = text(cell).match(/@([A-Za-z0-9_]{1,15})/);
    if (m) out.push({ element: cell, handle: m[1] });
  }
  return out;
}
