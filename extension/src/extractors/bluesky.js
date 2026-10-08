// Extractor de Bluesky (bsky.app). Se apoya en los data-testid de la app web oficial.
// NO verificado contra la página real: ver "Selectores a validar en vivo" en el README.

import { q, qa, qaSelf, text, attr, richText, pathOf } from '../lib/dom.js';
import { normalizeHandle, parseCount, toIsoUtc, absoluteUrl, uniq } from '../lib/normalize.js';

export const platform = 'bluesky';

export const SELECTORS = {
  // El handle del autor viene en el propio data-testid: "feedItem-by-<handle>".
  feedItem: '[data-testid^="feedItem-by-"]',
  threadItem: '[data-testid^="postThreadItem-by-"]',
  postText: '[data-testid="postText"]',
  postLink: 'a[href*="/post/"]',
  profileLink: 'a[href^="/profile/"]',
  avatarImg: '[data-testid="userAvatarImage"] img, img[src*="/img/avatar"]',
  replyCount: '[data-testid="replyBtn"]',
  repostCount: '[data-testid="repostCount"]',
  likeCount: '[data-testid="likeCount"]',
  profileView: '[data-testid="profileView"], [data-testid="profileScreen"]',
  profileDisplayName: '[data-testid="profileHeaderDisplayName"]',
  profileDescription: '[data-testid="profileHeaderDescription"]',
  profileFollowers: '[data-testid="profileHeaderFollowersButton"]',
  profileFollows: '[data-testid="profileHeaderFollowsButton"]',
  profileAvatar: '[data-testid="profileHeaderAviButton"] img, [data-testid="userAvatarImage"] img',
  quoteEmbed: '[aria-label^="Post by"], [aria-label^="Publicación de"], [data-testid="quoteEmbed"]',
};

const POST_ITEM = `${SELECTORS.feedItem}, ${SELECTORS.threadItem}`;
const REPOST_LABEL = /^(reposted by|republicado por|reposteado por)\s+/i;
const REPLY_LABEL = /^(reply to|replied to|respuesta a|en respuesta a|respondió a)\s+/i;

export function matches(url) {
  try {
    return /(^|\.)bsky\.app$/i.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

function postRef(href) {
  const m = pathOf(href).match(/^\/profile\/([^/]+)\/post\/([A-Za-z0-9]+)/);
  return m ? { actor: decodeURIComponent(m[1]), rkey: m[2] } : null;
}

function profileActor(href) {
  const m = pathOf(href).match(/^\/profile\/([^/]+)\/?$/);
  return m ? decodeURIComponent(m[1]) : '';
}

export function pageKind(url) {
  const path = pathOf(url);
  if (/^\/profile\/[^/]+\/post\//.test(path)) return 'thread';
  if (/^\/profile\/[^/]+\/(follows|followers)\/?$/.test(path)) return 'list';
  if (/^\/profile\/[^/]+\/?$/.test(path)) return 'profile';
  if (/^\/(messages|settings)/.test(path)) return 'other';
  return 'timeline';
}

export function findItems(root) {
  return qaSelf(root, POST_ITEM);
}

// El DID aparece en la URL del avatar: .../img/avatar/plain/did:plc:xxxx/<cid>@jpeg
function didFromAvatar(src) {
  const m = String(src || '').match(/(did:(?:plc|web):[A-Za-z0-9.%:_-]+?)\//);
  return m ? m[1] : '';
}

export function extractItem(item, ctx = {}) {
  if (!item) return null;
  const base = ctx.url || 'https://bsky.app/';
  const testid = attr(item, 'data-testid');
  let handle = normalizeHandle(testid.replace(/^(feedItem|postThreadItem)-by-/, ''));

  const quoteBox = q(item, SELECTORS.quoteEmbed);
  const own = (selector) => qa(item, selector).filter((el) => !quoteBox || !quoteBox.contains(el));

  // Enlace permanente del propio autor.
  let ref = null;
  let linkEl = null;
  for (const a of own(SELECTORS.postLink)) {
    const r = postRef(attr(a, 'href'));
    if (r && (!handle || r.actor.toLowerCase() === handle.toLowerCase() || r.actor.startsWith('did:'))) { ref = r; linkEl = a; break; }
  }
  if (!ref && pageKind(base) === 'thread') {
    const r = postRef(base);
    if (r && (!handle || r.actor.toLowerCase() === handle.toLowerCase())) ref = r;
  }
  if (!handle && ref && !ref.actor.startsWith('did:')) handle = ref.actor;
  if (!handle || !ref) return null;

  const textEl = own(SELECTORS.postText)[0] || null;
  const body = richText(textEl);
  const avatar = own(SELECTORS.avatarImg)[0] || null;
  const did = didFromAvatar(avatar ? attr(avatar, 'src') : '') || (ref.actor.startsWith('did:') ? ref.actor : '');

  let displayName = '';
  for (const a of own(SELECTORS.profileLink)) {
    if (profileActor(attr(a, 'href')).toLowerCase() !== handle.toLowerCase()) continue;
    const t = text(a);
    if (t && !t.startsWith('@') && t.toLowerCase() !== handle.toLowerCase()) { displayName = t; break; }
  }

  const mentions = [];
  const hashtags = [];
  const urls = [];
  for (const a of textEl ? qa(textEl, 'a[href]') : []) {
    const href = attr(a, 'href');
    const label = text(a);
    const actor = profileActor(href);
    if (actor && label.startsWith('@')) mentions.push(normalizeHandle(label).toLowerCase());
    else if (/^\/hashtag\//.test(href) || label.startsWith('#')) hashtags.push(label.replace(/^#/, '').toLowerCase());
    else if (/^https?:\/\//.test(href)) urls.push(href);
  }

  const interactions = [];
  const meta = {};
  let kind = 'original';
  let replyTo = '';
  let reposter = '';

  for (const el of own('[aria-label], a, span, div')) {
    if (textEl && textEl.contains(el)) continue;
    const label = attr(el, 'aria-label') || '';
    const shown = el.children.length <= 2 ? text(el) : '';
    if (!reposter && (REPOST_LABEL.test(label) || REPOST_LABEL.test(shown))) {
      const link = el.matches('a[href^="/profile/"]') ? el : q(el, 'a[href^="/profile/"]');
      reposter = link ? profileActor(attr(link, 'href')) : '';
    }
    if (!replyTo && (REPLY_LABEL.test(label) || REPLY_LABEL.test(shown)) && shown.length < 160) {
      const link = el.matches('a[href^="/profile/"]') ? el : q(el, 'a[href^="/profile/"]');
      const actor = link ? profileActor(attr(link, 'href')) : '';
      kind = 'reply';
      // Sin enlace solo se ve el nombre visible, no el handle: se guarda aparte y reply_to queda vacío.
      if (actor && !actor.startsWith('did:')) replyTo = actor;
      else meta.reply_to_display_name = (label || shown).replace(REPLY_LABEL, '').trim();
    }
  }

  if (quoteBox) {
    const qLink = qa(quoteBox, SELECTORS.postLink).map((a) => postRef(attr(a, 'href'))).find(Boolean) ||
      qa(quoteBox, SELECTORS.profileLink).map((a) => ({ actor: profileActor(attr(a, 'href')) })).find((r) => r.actor);
    if (qLink && qLink.actor && !qLink.actor.startsWith('did:')) {
      if (kind === 'original') kind = 'quote';
      meta.quoted_handle = qLink.actor;
      interactions.push([handle, qLink.actor, 'quote']);
    }
  }

  // Fecha: el enlace de la hora lleva la fecha completa en aria-label o data-tooltip (hora local del navegador).
  let created = null;
  const timeEl = own('time[datetime]')[0];
  if (timeEl) created = toIsoUtc(attr(timeEl, 'datetime'));
  if (!created && linkEl) {
    const label = attr(linkEl, 'data-tooltip') || attr(linkEl, 'aria-label') || attr(linkEl, 'title');
    created = toIsoUtc(label.replace(/\s+at\s+/i, ' ').replace(/\s+a las?\s+/i, ' '));
    if (created) meta.created_at_source = 'etiqueta en hora local del navegador';
  }

  for (const [key, selector] of [['replies', SELECTORS.replyCount], ['reposts', SELECTORS.repostCount], ['likes', SELECTORS.likeCount]]) {
    const el = own(selector)[0];
    const n = el ? parseCount(text(el) || attr(el, 'aria-label')) : null;
    if (n !== null) meta[key] = n;
  }
  meta.permalink = absoluteUrl(`/profile/${handle}/post/${ref.rkey}`, 'https://bsky.app/');
  if (did) meta.at_uri = `at://${did}/app.bsky.feed.post/${ref.rkey}`;

  const account = {
    platform, handle, platform_uid: did, display_name: displayName,
    url: `https://bsky.app/profile/${handle}`, avatar_url: avatar ? attr(avatar, 'src') : '',
  };
  const post = {
    platform_post_id: ref.rkey, text: body, created_at: created, lang: textEl ? attr(textEl, 'lang') : '',
    kind, reply_to: replyTo, mentions, hashtags, urls, meta,
  };
  const records = [{ account, post }];
  for (const m of uniq(mentions)) if (m !== handle.toLowerCase()) interactions.push([handle, m, 'mention']);
  if (kind === 'reply' && replyTo) interactions.push([handle, replyTo, 'reply']);
  if (reposter && !reposter.startsWith('did:') && reposter.toLowerCase() !== handle.toLowerCase()) {
    records.push({
      account: { platform, handle: reposter, url: `https://bsky.app/profile/${reposter}` },
      post: {
        platform_post_id: ref.rkey, text: body, kind: 'repost', lang: post.lang, mentions, hashtags, urls,
        meta: { reposted_from: handle, original_created_at: created, permalink: meta.permalink },
      },
    });
    interactions.push([reposter, handle, 'repost']);
  }
  if (meta.quoted_handle) {
    records.push({ account: { platform, handle: meta.quoted_handle, url: `https://bsky.app/profile/${meta.quoted_handle}` }, post: null });
  }
  return { records, interactions, primary: { handle, postId: ref.rkey, text: body } };
}

export function extractPage(doc, ctx = {}) {
  const url = ctx.url || (doc.location && doc.location.href) || '';
  const out = { records: [], interactions: [], snippets: [] };
  if (pageKind(url) !== 'profile') return out;
  const actor = profileActor(pathOf(url));
  const view = q(doc, SELECTORS.profileView) || doc;
  const nameEl = q(view, SELECTORS.profileDisplayName);
  if (!actor || !nameEl) return out;
  // El handle visible ("@nombre.bsky.social") está cerca del nombre; si la URL usa un DID se toma de ahí.
  let handle = actor.startsWith('did:') ? '' : actor;
  if (!handle) {
    const m = text(view).match(/@([a-z0-9][a-z0-9.-]+\.[a-z]{2,})/i);
    handle = m ? m[1] : '';
  }
  if (!handle) return out;
  const avatar = q(view, SELECTORS.profileAvatar);
  const avatarUrl = avatar ? attr(avatar, 'src') : '';
  out.records.push({
    account: {
      platform, handle,
      platform_uid: didFromAvatar(avatarUrl) || (actor.startsWith('did:') ? actor : ''),
      display_name: text(nameEl),
      bio: richText(q(view, SELECTORS.profileDescription)),
      url: `https://bsky.app/profile/${handle}`,
      followers: parseCount(text(q(view, SELECTORS.profileFollowers))),
      following: parseCount(text(q(view, SELECTORS.profileFollows))),
      avatar_url: avatarUrl,
    },
    post: null,
  });
  return out;
}

export function findHandleTargets(root) {
  const out = [];
  for (const item of qaSelf(root, POST_ITEM)) {
    const handle = normalizeHandle(attr(item, 'data-testid').replace(/^(feedItem|postThreadItem)-by-/, ''));
    const anchor = qa(item, SELECTORS.profileLink).find((a) => text(a)) || item;
    if (handle) out.push({ element: anchor, handle });
  }
  for (const el of qaSelf(root, SELECTORS.profileDisplayName)) {
    const m = text(el.parentElement || el).match(/@([a-z0-9][a-z0-9.-]+\.[a-z]{2,})/i);
    if (m) out.push({ element: el, handle: m[1] });
  }
  return out;
}
