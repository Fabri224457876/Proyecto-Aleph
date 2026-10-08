// Extractor de Reddit. Cubre la web actual ("shreddit", componentes web con los datos en atributos)
// y old.reddit.com (div.thing con data-*). NO verificado en vivo: ver README.

import { q, qa, qaSelf, text, attr, richText, pathOf } from '../lib/dom.js';
import { normalizeHandle, parseCount, toIsoUtc, absoluteUrl } from '../lib/normalize.js';

export const platform = 'reddit';

export const SELECTORS = {
  // Web actual
  post: 'shreddit-post',
  comment: 'shreddit-comment',
  postTitle: '[slot="title"], h1[id^="post-title"]',
  postBody: '[slot="text-body"], [data-post-click-location="text-body"]',
  commentBody: '[slot="comment"]',
  time: 'time[datetime], faceplate-timeago[ts]',
  // old.reddit.com
  oldThing: '.thing[data-fullname][data-author]',
  oldTitle: 'a.title',
  oldBody: '.usertext-body .md',
  oldTime: 'time[datetime]',
  // Perfil
  profileHeader: 'shreddit-profile-header, [data-testid="profile-main"]',
  profileKarma: '[data-testid="karma-number"]',
  profileCakeDay: '[data-testid="cake-day"], time[data-testid="cake-day"]',
  profileBio: '[data-testid="profile-description"]',
  authorLink: 'a[href^="/user/"], a[href*="reddit.com/user/"]',
};

const ITEM = `${SELECTORS.post}, ${SELECTORS.comment}, ${SELECTORS.oldThing}`;
const GONE = new Set(['', '[deleted]', '[removed]', 'automoderator']);

export function matches(url) {
  try {
    return /(^|\.)reddit\.com$/i.test(new URL(url).hostname);
  } catch {
    return false;
  }
}

export function pageKind(url) {
  const path = pathOf(url);
  if (/^\/(user|u)\/[^/]+/.test(path)) return 'profile';
  if (/\/comments\/[a-z0-9]+/i.test(path)) return 'thread';
  if (/^\/(message|chat|settings)/.test(path)) return 'other';
  return 'timeline';
}

export function findItems(root) {
  return qaSelf(root, ITEM);
}

function userUrl(handle) {
  return `https://www.reddit.com/user/${handle}`;
}

function linksIn(el, base) {
  return qa(el, 'a[href]').map((a) => absoluteUrl(attr(a, 'href'), base)).filter((u) => u && !matches(u));
}

function userMentions(body) {
  return [...String(body).matchAll(/(?<![\w/])\/?u\/([A-Za-z0-9_-]{3,20})/g)].map((m) => m[1].toLowerCase());
}

// El autor del comentario padre, buscando el contenedor que lo envuelve.
function parentAuthor(el) {
  let node = el.parentElement;
  while (node) {
    if (node.matches && node.matches(`${SELECTORS.comment}, ${SELECTORS.oldThing}`)) {
      const a = attr(node, 'author') || attr(node, 'data-author');
      if (a) return a;
    }
    node = node.parentElement;
  }
  return '';
}

function threadAuthor(el) {
  const doc = el.ownerDocument;
  const post = doc && (q(doc, SELECTORS.post) || q(doc, '.thing.link[data-author]'));
  return post ? attr(post, 'author') || attr(post, 'data-author') : '';
}

export function extractItem(el, ctx = {}) {
  if (!el) return null;
  const base = ctx.url || 'https://www.reddit.com/';
  const tag = el.tagName.toLowerCase();
  const isOld = tag !== 'shreddit-post' && tag !== 'shreddit-comment';
  const isComment = tag === 'shreddit-comment' || (isOld && /^t1_/.test(attr(el, 'data-fullname')));

  const handle = normalizeHandle(isOld ? attr(el, 'data-author') : attr(el, 'author'));
  const id = isOld ? attr(el, 'data-fullname') : (isComment ? attr(el, 'thingid') : attr(el, 'id'));
  if (GONE.has(handle.toLowerCase()) || !/^t[13]_[a-z0-9]+$/i.test(id)) return null;

  const meta = {};
  let body = '';
  let created = null;
  let urls = [];

  if (isOld) {
    const bodyEl = q(el, `:scope > .entry ${SELECTORS.oldBody}`) || q(el, SELECTORS.oldBody);
    const title = isComment ? '' : text(q(el, SELECTORS.oldTitle));
    body = [title, richText(bodyEl)].filter(Boolean).join('\n\n');
    const t = q(el, `:scope > .entry ${SELECTORS.oldTime}`) || q(el, SELECTORS.oldTime);
    created = t ? toIsoUtc(attr(t, 'datetime')) : toIsoUtc(attr(el, 'data-timestamp'));
    urls = bodyEl ? linksIn(bodyEl, base) : [];
    if (attr(el, 'data-subreddit')) meta.subreddit = attr(el, 'data-subreddit');
    if (attr(el, 'data-permalink')) meta.permalink = absoluteUrl(attr(el, 'data-permalink'), 'https://www.reddit.com/');
    if (!isComment && /^https?:/.test(attr(el, 'data-url'))) urls.push(attr(el, 'data-url'));
    const score = parseCount(attr(el, 'data-score'));
    if (score !== null) meta.score = score;
  } else if (isComment) {
    const bodyEl = qa(el, SELECTORS.commentBody).find((b) => b.closest(SELECTORS.comment) === el) || null;
    body = richText(bodyEl);
    const t = qa(el, SELECTORS.time).find((x) => x.closest(SELECTORS.comment) === el);
    created = t ? toIsoUtc(attr(t, 'datetime') || attr(t, 'ts')) : toIsoUtc(attr(el, 'created'));
    urls = bodyEl ? linksIn(bodyEl, base) : [];
    if (attr(el, 'permalink')) meta.permalink = absoluteUrl(attr(el, 'permalink'), 'https://www.reddit.com/');
    if (attr(el, 'depth')) meta.depth = Number(attr(el, 'depth')) || 0;
    if (attr(el, 'parentid')) meta.parent_id = attr(el, 'parentid');
    if (attr(el, 'postid')) meta.post_id = attr(el, 'postid');
    const score = parseCount(attr(el, 'score'));
    if (score !== null) meta.score = score;
  } else {
    const title = attr(el, 'post-title') || text(q(el, SELECTORS.postTitle));
    const bodyEl = q(el, SELECTORS.postBody);
    body = [title, richText(bodyEl)].filter(Boolean).join('\n\n');
    created = toIsoUtc(attr(el, 'created-timestamp'));
    urls = bodyEl ? linksIn(bodyEl, base) : [];
    const content = attr(el, 'content-href');
    if (/^https?:/.test(content) && !matches(content)) urls.push(content);
    if (attr(el, 'subreddit-prefixed-name')) meta.subreddit = attr(el, 'subreddit-prefixed-name').replace(/^r\//, '');
    if (attr(el, 'permalink')) meta.permalink = absoluteUrl(attr(el, 'permalink'), 'https://www.reddit.com/');
    if (attr(el, 'post-type')) meta.post_type = attr(el, 'post-type');
    if (attr(el, 'domain')) meta.domain = attr(el, 'domain');
    const score = parseCount(attr(el, 'score'));
    if (score !== null) meta.score = score;
    const comments = parseCount(attr(el, 'comment-count'));
    if (comments !== null) meta.comments = comments;
  }

  let kind = 'original';
  let replyTo = '';
  if (isComment) {
    kind = 'reply';
    const target = parentAuthor(el) || threadAuthor(el);
    if (target && !GONE.has(target.toLowerCase())) replyTo = target;
  }

  const mentions = userMentions(body);
  const uid = isOld ? attr(el, 'data-author-fullname') : attr(el, 'author-id');
  const account = { platform, handle, platform_uid: uid, url: userUrl(handle) };
  const post = { platform_post_id: id, text: body, created_at: created, kind, reply_to: replyTo, mentions, urls, meta };
  const interactions = [];
  if (replyTo && replyTo.toLowerCase() !== handle.toLowerCase()) interactions.push([handle, replyTo, 'reply']);
  for (const m of mentions) if (m !== handle.toLowerCase()) interactions.push([handle, m, 'mention']);
  return { records: [{ account, post }], interactions, primary: { handle, postId: id, text: body } };
}

export function extractPage(doc, ctx = {}) {
  const url = ctx.url || (doc.location && doc.location.href) || '';
  const out = { records: [], interactions: [], snippets: [] };
  const m = pathOf(url).match(/^\/(?:user|u)\/([A-Za-z0-9_-]{3,20})/);
  if (!m) return out;
  const header = q(doc, SELECTORS.profileHeader) || doc;
  const meta = {};
  const karma = parseCount(text(q(header, SELECTORS.profileKarma)) || attr(header, 'karma'));
  if (karma !== null) meta.karma = karma;
  const cake = q(header, SELECTORS.profileCakeDay);
  out.records.push({
    account: {
      platform, handle: m[1], url: userUrl(m[1]),
      display_name: attr(header, 'display-name') || '',
      bio: text(q(header, SELECTORS.profileBio)),
      created_at_platform: cake ? toIsoUtc(attr(cake, 'datetime') || text(cake)) : null,
      meta,
    },
    post: null,
  });
  return out;
}

export function findHandleTargets(root) {
  const out = [];
  for (const a of qaSelf(root, SELECTORS.authorLink)) {
    const m = pathOf(attr(a, 'href'), 'https://www.reddit.com/').match(/^\/user\/([A-Za-z0-9_-]{3,20})\/?$/);
    if (m && text(a)) out.push({ element: a, handle: m[1] });
  }
  return out;
}
