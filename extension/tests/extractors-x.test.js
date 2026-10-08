import test from 'node:test';
import assert from 'node:assert/strict';
import * as x from '../src/extractors/x.js';
import { makeAccount, makePost } from '../src/lib/normalize.js';
import { loadFixture, extractAll, postsOf } from './tools/helpers.mjs';

const TIMELINE = 'https://x.com/home';

test('x: reconoce el tipo de página por la URL', () => {
  assert.equal(x.pageKind('https://x.com/home'), 'timeline');
  assert.equal(x.pageKind('https://x.com/lau_ficticia'), 'profile');
  assert.equal(x.pageKind('https://x.com/lau_ficticia/status/1840000000000000001'), 'thread');
  assert.equal(x.pageKind('https://x.com/lau_ficticia/following'), 'list');
  assert.equal(x.pageKind('https://x.com/messages/123-456'), 'other');
  assert.ok(x.matches('https://twitter.com/algo'));
  assert.ok(!x.matches('https://ejemplo.com/x.com'));
});

test('x timeline: publicación original con mención, hashtag, URL, emoji y contadores', () => {
  const { document } = loadFixture('x-timeline.html', TIMELINE);
  const first = x.extractItem(x.findItems(document)[0], { url: TIMELINE });
  const account = makeAccount(first.records[0].account);
  const post = makePost(first.records[0].post);

  assert.equal(account.platform, 'x');
  assert.equal(account.handle, 'lau_ficticia');
  assert.equal(account.display_name, 'Laura Ficticia🌻');
  assert.equal(account.avatar_url, 'https://pbs.twimg.com/profile_images/1/lau_normal.jpg');

  assert.equal(post.platform_post_id, '1840000000000000001');
  assert.equal(post.created_at, '2026-10-05T14:03:22.000Z');
  assert.equal(post.kind, 'original');
  assert.equal(post.lang, 'es');
  assert.deepEqual(post.mentions, ['tomi_ejemplo']);
  assert.deepEqual(post.hashtags, ['buenosaires']);
  assert.ok(post.urls.includes('https://t.co/abc123XYZ'));
  assert.match(post.text, /^Holaaa, hoy en la marcha con @Tomi_Ejemplo 🔥 #BuenosAires más info:/);
  assert.equal(post.meta.replies, 3);
  assert.equal(post.meta.reposts, 12);
  assert.equal(post.meta.likes, 1200);
  assert.equal(post.meta.views, 45678);
  assert.deepEqual(first.interactions, [['lau_ficticia', 'tomi_ejemplo', 'mention']]);
});

test('x timeline: un repost queda en la cuenta original (original) y en la que reposteó (repost)', () => {
  const { document } = loadFixture('x-timeline.html', TIMELINE);
  const item = x.extractItem(x.findItems(document)[1], { url: TIMELINE });
  assert.equal(item.records[0].account.handle, 'Tomi_Ejemplo');
  assert.equal(makePost(item.records[0].post).kind, 'original');
  assert.equal(item.records[0].post.platform_post_id, '1839999999999999990');

  const repost = item.records[1];
  assert.equal(repost.account.handle, 'lau_ficticia');
  assert.equal(repost.post.kind, 'repost');
  assert.equal(repost.post.platform_post_id, '1839999999999999990');
  assert.equal(repost.post.meta.reposted_from, 'Tomi_Ejemplo');
  assert.deepEqual(item.interactions, [['lau_ficticia', 'Tomi_Ejemplo', 'repost']]);
});

test('x timeline: respuesta con "En respuesta a" y cita', () => {
  const { document } = loadFixture('x-timeline.html', TIMELINE);
  const items = x.findItems(document);
  const reply = x.extractItem(items[2], { url: TIMELINE });
  const post = makePost(reply.records[0].post);
  assert.equal(post.kind, 'reply');
  assert.equal(post.reply_to, 'lau_ficticia');
  assert.deepEqual(post.meta.reply_to_all, ['lau_ficticia', 'tomi_ejemplo']);
  assert.ok(reply.interactions.some(([a, b, t]) => a === 'cuenta_demo_3' && b === 'lau_ficticia' && t === 'reply'));

  const quote = x.extractItem(items[3], { url: TIMELINE });
  const qpost = makePost(quote.records[0].post);
  assert.equal(quote.records[0].account.handle, 'Tomi_Ejemplo');
  assert.equal(qpost.kind, 'quote');
  assert.equal(qpost.meta.quoted_handle, 'lau_ficticia');
  // El texto de la cita no se mezcla con el del autor.
  assert.equal(qpost.text, 'Miren esto #Demo');
  assert.deepEqual(qpost.hashtags, ['demo']);
  assert.deepEqual(qpost.mentions, []);
  assert.ok(quote.interactions.some(([a, b, t]) => a === 'Tomi_Ejemplo' && b === 'lau_ficticia' && t === 'quote'));
});

test('x perfil: cabecera con bio, fecha de alta, seguidores y seguidos', () => {
  const url = 'https://x.com/lau_ficticia';
  const { document } = loadFixture('x-profile.html', url);
  const page = x.extractPage(document, { url });
  assert.equal(page.records.length, 1);
  const account = makeAccount(page.records[0].account);
  assert.equal(account.handle, 'lau_ficticia');
  assert.equal(account.display_name, 'Laura Ficticia🌻');
  assert.match(account.bio, /^Docente\. Opiniones propias\./);
  assert.equal(account.created_at_platform, '2019-03-01T00:00:00.000Z');
  assert.equal(account.following, 312);
  assert.equal(account.followers, 12400);
  assert.equal(account.meta.location, 'Rosario, Santa Fe');
  assert.equal(account.meta.website, 'lau.ejemplo.com.ar');
  assert.equal(account.avatar_url, 'https://pbs.twimg.com/profile_images/1/lau_400x400.jpg');

  const pinned = x.extractItem(x.findItems(document)[0], { url });
  assert.equal(pinned.records[0].post.meta.pinned, true);
  assert.equal(pinned.records.length, 1, 'una publicación fijada no es un repost');
});

test('x perfil: no confía en la cabecera si no corresponde a la URL (SPA atrasada)', () => {
  const url = 'https://x.com/otra_cuenta';
  const { document } = loadFixture('x-profile.html', url);
  assert.equal(x.extractPage(document, { url }).records.length, 0);
});

test('x hilo: publicación principal y respuestas inferidas por la URL', () => {
  const url = 'https://x.com/lau_ficticia/status/1840000000000000001';
  const { document } = loadFixture('x-thread.html', url);
  const all = extractAll(x, document, url);
  const main = postsOf(all, 'lau_ficticia').find((p) => p.platform_post_id === '1840000000000000001');
  assert.ok(main, 'la publicación principal se identifica');
  assert.equal(makePost(main).kind, 'original');

  const reply = makePost(postsOf(all, 'cuenta_demo_3')[0]);
  assert.equal(reply.kind, 'reply');
  assert.equal(reply.reply_to, 'lau_ficticia');
  assert.equal(reply.meta.reply_inferred, true);

  // La autora siguiendo su propio hilo no es una "respuesta a sí misma".
  const self = makePost(postsOf(all, 'lau_ficticia').find((p) => p.platform_post_id === '1840000000000000600'));
  assert.equal(self.kind, 'original');
});

test('x: findHandleTargets ubica los bloques de nombre', () => {
  const { document } = loadFixture('x-timeline.html', TIMELINE);
  const handles = x.findHandleTargets(document.querySelector('[data-testid="primaryColumn"]')).map((t) => t.handle);
  assert.deepEqual(handles, ['lau_ficticia', 'Tomi_Ejemplo', 'cuenta_demo_3', 'Tomi_Ejemplo', 'lau_ficticia']);
});

test('x: un article sin enlace permanente ni handle devuelve null (no inventa datos)', () => {
  const { document } = loadFixture('x-timeline.html', TIMELINE);
  const article = document.createElement('article');
  article.setAttribute('data-testid', 'tweet');
  article.innerHTML = '<div data-testid="tweetText">Publicidad sin autor</div>';
  assert.equal(x.extractItem(article, { url: TIMELINE }), null);
});
