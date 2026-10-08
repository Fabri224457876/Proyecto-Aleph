import test from 'node:test';
import assert from 'node:assert/strict';
import * as bluesky from '../src/extractors/bluesky.js';
import * as reddit from '../src/extractors/reddit.js';
import * as instagram from '../src/extractors/instagram.js';
import * as generic from '../src/extractors/generic.js';
import { extractorFor } from '../src/extractors/index.js';
import { makeAccount, makePost } from '../src/lib/normalize.js';
import { loadFixture, extractAll, postsOf } from './tools/helpers.mjs';

test('registro: elige el extractor por dominio y cae en el genérico', () => {
  assert.equal(extractorFor('https://x.com/home').platform, 'x');
  assert.equal(extractorFor('https://mobile.twitter.com/a').platform, 'x');
  assert.equal(extractorFor('https://www.instagram.com/sofi/').platform, 'instagram');
  assert.equal(extractorFor('https://bsky.app/profile/a.b').platform, 'bluesky');
  assert.equal(extractorFor('https://old.reddit.com/r/a').platform, 'reddit');
  assert.equal(extractorFor('https://foro.example/hilo').platform, 'generic');
});

// --- Bluesky ---

test('bluesky feed: autor, id estable (rkey), DID, menciones, hashtags y URLs', () => {
  const url = 'https://bsky.app/';
  const { document } = loadFixture('bluesky-feed.html', url);
  const items = bluesky.findItems(document);
  assert.equal(items.length, 3);
  const first = bluesky.extractItem(items[0], { url });
  const account = makeAccount(first.records[0].account);
  const post = makePost(first.records[0].post);
  assert.equal(account.handle, 'marina.ejemplo.bsky.social');
  assert.equal(account.display_name, 'Marina Ejemplo');
  assert.equal(account.platform_uid, 'did:plc:abcd1234efgh5678ijkl9012');
  assert.equal(post.platform_post_id, '3lb2xyzabcd2k');
  assert.equal(post.meta.at_uri, 'at://did:plc:abcd1234efgh5678ijkl9012/app.bsky.feed.post/3lb2xyzabcd2k');
  assert.deepEqual(post.mentions, ['nico.demo.bsky.social']);
  assert.deepEqual(post.hashtags, ['rosario']);
  assert.deepEqual(post.urls, ['https://ejemplo.org/articulo']);
  assert.equal(post.kind, 'original');
  assert.equal(post.meta.replies, 4);
  assert.equal(post.meta.reposts, 11);
  assert.equal(post.meta.likes, 1200);
  assert.ok(post.created_at && post.created_at.startsWith('2026-10-0'), 'la fecha sale de la etiqueta del enlace');
});

test('bluesky feed: repost y respuesta', () => {
  const url = 'https://bsky.app/';
  const { document } = loadFixture('bluesky-feed.html', url);
  const items = bluesky.findItems(document);
  const repost = bluesky.extractItem(items[1], { url });
  assert.equal(repost.records[0].account.handle, 'nico.demo.bsky.social');
  assert.equal(repost.records[1].account.handle, 'marina.ejemplo.bsky.social');
  assert.equal(repost.records[1].post.kind, 'repost');
  assert.deepEqual(repost.interactions, [['marina.ejemplo.bsky.social', 'nico.demo.bsky.social', 'repost']]);

  const reply = bluesky.extractItem(items[2], { url });
  const post = makePost(reply.records[0].post);
  assert.equal(post.kind, 'reply');
  assert.equal(post.reply_to, 'marina.ejemplo.bsky.social');
});

test('bluesky perfil: nombre, bio y contadores', () => {
  const url = 'https://bsky.app/profile/marina.ejemplo.bsky.social';
  const { document } = loadFixture('bluesky-profile.html', url);
  const account = makeAccount(bluesky.extractPage(document, { url }).records[0].account);
  assert.equal(account.handle, 'marina.ejemplo.bsky.social');
  assert.equal(account.display_name, 'Marina Ejemplo');
  assert.equal(account.followers, 2300);
  assert.equal(account.following, 187);
  assert.match(account.bio, /Periodista de datos\.\nRosario/);
  assert.equal(account.platform_uid, 'did:plc:abcd1234efgh5678ijkl9012');
});

// --- Reddit ---

test('reddit (web actual): publicación y comentarios anidados desde los atributos', () => {
  const url = 'https://www.reddit.com/r/ejemplo/comments/1abcde/consulta_de_ejemplo/';
  const { document } = loadFixture('reddit-thread.html', url);
  const all = extractAll(reddit, document, url);
  assert.equal(all.items.filter(Boolean).length, 3, 'el comentario [deleted] se descarta');

  const post = makePost(postsOf(all, 'usuario_demo_uno').find((p) => p.platform_post_id === 't3_1abcde'));
  assert.equal(post.kind, 'original');
  assert.equal(post.created_at, '2026-10-03T18:20:11.000Z');
  assert.match(post.text, /^Consulta de ejemplo sobre un dominio raro\n\nMe llegó un mail/);
  assert.deepEqual(post.urls, ['https://banco-falso-demo.com/ingreso']);
  assert.deepEqual(post.mentions, ['moderador_demo']);
  assert.equal(post.meta.subreddit, 'ejemplo');
  assert.equal(post.meta.score, 128);

  const c1 = makePost(postsOf(all, 'usuario_demo_dos')[0]);
  assert.equal(c1.platform_post_id, 't1_k1aaaa');
  assert.equal(c1.kind, 'reply');
  assert.equal(c1.reply_to, 'usuario_demo_uno', 'un comentario de primer nivel responde al autor de la publicación');
  assert.equal(c1.text, 'Es phishing, la IP es 203.0.113.45 y está reportada.', 'no arrastra el texto de las respuestas anidadas');
  assert.equal(c1.created_at, '2026-10-03T18:45:00.000Z');

  const c2 = makePost(postsOf(all, 'usuario_demo_uno').find((p) => p.platform_post_id === 't1_k1bbbb'));
  assert.equal(c2.reply_to, 'usuario_demo_dos');
  assert.equal(c2.created_at, '2026-10-03T19:02:30.000Z');
  assert.ok(all.interactions.some(([a, b, t]) => a === 'usuario_demo_uno' && b === 'usuario_demo_dos' && t === 'reply'));
});

test('reddit (old): div.thing con data-*', () => {
  const url = 'https://old.reddit.com/r/ejemplo/comments/1abcde/consulta_de_ejemplo/';
  const { document } = loadFixture('reddit-old.html', url);
  const all = extractAll(reddit, document, url);
  assert.equal(all.items.filter(Boolean).length, 3);
  const post = makePost(postsOf(all, 'usuario_demo_uno').find((p) => p.platform_post_id === 't3_1abcde'));
  assert.match(post.text, /^Consulta de ejemplo sobre un dominio raro\n\nMe llegó un mail raro/);
  assert.equal(post.created_at, '2026-10-03T18:20:11.000Z');
  const c1 = makePost(postsOf(all, 'usuario_demo_dos')[0]);
  assert.equal(c1.text, 'Es phishing.');
  assert.equal(c1.reply_to, 'usuario_demo_uno');
  const c2 = makePost(postsOf(all, 'usuario_demo_uno').find((p) => p.platform_post_id === 't1_k1bbbb'));
  assert.equal(c2.reply_to, 'usuario_demo_dos');
  assert.equal(makeAccount(all.records[0].account).platform_uid, 't2_aaa111');
});

// --- Instagram ---

test('instagram perfil: datos de las meta og:* y número exacto del atributo title', () => {
  const url = 'https://www.instagram.com/sofi.demo_ok/';
  const { document } = loadFixture('instagram-profile.html', url);
  const account = makeAccount(instagram.extractPage(document, { url }).records[0].account);
  assert.equal(account.handle, 'sofi.demo_ok');
  assert.equal(account.display_name, 'Sofi Demo');
  assert.equal(account.followers, 12345, 'el title="12.345" gana sobre el "12,3 mil" visible y sobre og:description');
  assert.equal(account.following, 567);
  assert.equal(account.meta.posts, 89);
  assert.equal(account.url, 'https://www.instagram.com/sofi.demo_ok/');
  assert.ok(account.avatar_url.includes('sofi_demo_320.jpg'));
  // En el perfil no hay unidades de texto: la grilla no trae ni texto ni fecha.
  assert.equal(instagram.findItems(document).length, 0);
});

test('instagram perfil: si las meta son de otra cuenta (SPA atrasada) y no hay cabecera, no devuelve nada', () => {
  const url = 'https://www.instagram.com/otra.cuenta/';
  const { document } = loadFixture('instagram-profile.html', url);
  assert.equal(instagram.extractPage(document, { url }).records.length, 0);
});

test('instagram publicación: pie de foto y comentarios con id estable', () => {
  const url = 'https://www.instagram.com/p/CxDemo12345/';
  const { document } = loadFixture('instagram-post.html', url);
  const all = extractAll(instagram, document, url);
  const caption = makePost(postsOf(all, 'sofi.demo_ok')[0]);
  assert.equal(caption.platform_post_id, 'CxDemo12345');
  assert.equal(caption.kind, 'original');
  assert.equal(caption.created_at, '2026-10-02T21:15:00.000Z');
  assert.match(caption.text, /^Atardecer en el río 🌅 con @lu\.ejemplo #Paraná$/);
  assert.deepEqual(caption.mentions, ['lu.ejemplo']);
  assert.deepEqual(caption.hashtags, ['paraná']);

  const c1 = makePost(postsOf(all, 'lu.ejemplo')[0]);
  assert.equal(c1.platform_post_id, 'CxDemo12345:c:17900000000000001');
  assert.equal(c1.kind, 'reply');
  assert.equal(c1.reply_to, 'sofi.demo_ok');
  assert.equal(c1.text, 'Qué lindo día fue!! 😍', 'sin botones ni contadores pegados');

  const c2 = makePost(postsOf(all, 'otra_cuenta.demo')[0]);
  assert.equal(c2.platform_post_id, 'CxDemo12345:c:17900000000000002');
  assert.deepEqual(c2.mentions, ['sofi.demo_ok']);
});

// --- Genérico ---

test('genérico: texto visible, enlaces y detecciones; nada de formularios, nav, pie, script o edición', () => {
  const url = 'https://foro-demo.example/hilo/1';
  const { document } = loadFixture('generic.html', url);
  const page = generic.extractPage(document, { url, full: true });
  const joined = page.snippets.join('\n');
  assert.match(joined, /Encontré que la cuenta @operador_demo/);
  for (const forbidden of ['no-capturar@demo.com', 'valor-de-input', 'NoLeerEstoNunca123', 'texto-en-textarea', 'borrador@demo.com', 'pie@demo.com', 'contacto-nav', 'secreto-en-script', 'estilo@demo.com']) {
    assert.ok(!joined.includes(forbidden), `no debe leer "${forbidden}"`);
  }
  const found = new Set(page.detections.map((d) => `${d.kind}:${d.value}`));
  for (const expected of [
    'account:operador_demo', 'url:https://sitio-demo.com.ar/promo?id=7', 'email:ventas@sitio-demo.com.ar',
    'phone:+5493415550123', 'ip:203.0.113.45', 'vulnerability:CVE-2024-12345',
    'hash:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08',
    'event:2026-10-07', 'event:21:30', 'location:Av. Corrientes 1234',
    'wallet:0x52908400098527886E0F7030069857D2E4169EE7',
  ]) assert.ok(found.has(expected), `falta ${expected} en ${[...found].join(' | ')}`);
  // "mi ig es ..." y "discord: ...": el handle va solo y la red aparte.
  const otherNetworks = page.detections.filter((d) => d.kind === 'account' && d.platform).map((d) => `${d.platform}:${d.value}`);
  assert.deepEqual(otherNetworks.sort(), ['discord:operador#1234', 'instagram:operador.demo_ok']);
  // Falsos positivos del párrafo 4.
  for (const d of page.detections) {
    assert.ok(!['1.2.3.4', 'informe.txt', 'node.js', 'fin.luego', '03:02'].includes(d.value), `falso positivo: ${d.kind} ${d.value}`);
  }
});

test('genérico: solo toma bloques hoja con texto suficiente', () => {
  const url = 'https://foro-demo.example/hilo/1';
  const { document } = loadFixture('generic.html', url);
  const items = generic.findItems(document.body);
  const ids = items.map((el) => el.id || el.tagName);
  assert.ok(ids.includes('p1') && ids.includes('li1'));
  assert.ok(!ids.includes('post-1'), 'el <article> contiene bloques: no se captura dos veces');
});
