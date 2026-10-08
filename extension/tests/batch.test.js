import test from 'node:test';
import assert from 'node:assert/strict';
import { CaptureAccumulator, LIMITS } from '../src/lib/batch.js';
import { validateCaptureBatch } from '../src/lib/schema.js';
import { normalizeHandle, parseCount, toIsoUtc, extractHashtags, extractMentions, makePost, makeAccount } from '../src/lib/normalize.js';
import * as x from '../src/extractors/x.js';
import { loadFixture } from './tools/helpers.mjs';

const PAGE = { pageUrl: 'https://x.com/home', pageTitle: 'Inicio / X', platform: 'x', now: new Date('2026-10-07T12:00:00Z'), version: '0.1.0' };

test('normalización: handles, hashtags, menciones, fechas y contadores', () => {
  assert.equal(normalizeHandle('@Lau_Ficticia'), 'Lau_Ficticia');
  assert.equal(normalizeHandle('/u/usuario_demo/'), 'usuario_demo');
  assert.equal(normalizeHandle('https://x.com/lau_ficticia?s=20'), 'lau_ficticia');
  assert.equal(normalizeHandle('dos palabras'), '');
  assert.deepEqual(extractHashtags('Hola #BuenosAires y #Paraná, #2026 no; a&#38; tampoco'), ['buenosaires', 'paraná']);
  assert.deepEqual(extractMentions('con @Tomi_Ejemplo, mail a@b.com y @otra.cuenta.'), ['tomi_ejemplo', 'otra.cuenta']);
  assert.equal(toIsoUtc('2026-10-05T11:03:22-03:00'), '2026-10-05T14:03:22.000Z');
  assert.equal(toIsoUtc('2026-10-05T14:03:22'), '2026-10-05T14:03:22.000Z', 'sin zona se toma como UTC');
  assert.equal(toIsoUtc(1791051611), '2026-10-03T18:20:11.000Z');
  assert.equal(toIsoUtc('no es fecha'), null);
  assert.equal(parseCount('12,3 mil'), 12300);
  assert.equal(parseCount('4.5K'), 4500);
  assert.equal(parseCount('1.234'), 1234);
  assert.equal(parseCount('1,234,567'), 1234567);
  assert.equal(parseCount('2 M'), 2000000);
  assert.equal(parseCount('312 Siguiendo'), 312);
  assert.equal(parseCount('Responder'), null);
});

test('makePost / makeAccount: siempre todas las claves del esquema, con valores por defecto', () => {
  assert.deepEqual(Object.keys(makePost({ platform_post_id: '1' })).sort(),
    ['client', 'created_at', 'hashtags', 'kind', 'lang', 'mentions', 'meta', 'platform_post_id', 'reply_to', 'text', 'urls']);
  assert.deepEqual(Object.keys(makeAccount({ platform: 'x', handle: 'a' })).sort(),
    ['avatar_phash', 'avatar_url', 'bio', 'created_at_platform', 'display_name', 'follower_handles', 'followers', 'following', 'following_handles', 'handle', 'meta', 'platform', 'platform_uid', 'url']);
  assert.equal(makePost({ platform_post_id: '1', kind: 'inventado' }).kind, 'original');
  assert.equal(makePost({ platform_post_id: '1', reply_to: '@Alguien' }).reply_to, 'Alguien');
  assert.equal(makeAccount({ platform: 'x', handle: '@A', followers: 1.5 }).followers, null);
});

test('acumulador: no duplica cuentas ni publicaciones (ni con distinta capitalización del handle)', () => {
  const acc = new CaptureAccumulator();
  const a = acc.add({ platform: 'x', handle: 'Lau_Ficticia' }, { platform_post_id: '1', text: 'hola' });
  const b = acc.add({ platform: 'x', handle: 'lau_ficticia' }, { platform_post_id: '1', text: 'hola' });
  const c = acc.add({ platform: 'x', handle: '@LAU_FICTICIA' }, { platform_post_id: '2', text: 'chau' });
  assert.deepEqual([a.newAccount, a.newPost], [true, true]);
  assert.deepEqual([b.newAccount, b.newPost], [false, false]);
  assert.deepEqual([c.newAccount, c.newPost], [false, true]);
  assert.deepEqual(acc.totals, { accounts: 1, posts: 2, interactions: 0, snippets: 0 });

  const batch = acc.drain(PAGE);
  assert.equal(batch.profiles.length, 1);
  assert.equal(batch.profiles[0].account.handle, 'Lau_Ficticia', 'conserva el handle como lo mostró la plataforma');
  assert.deepEqual(batch.profiles[0].posts.map((p) => p.platform_post_id), ['1', '2']);
});

test('acumulador: el mismo id en dos cuentas distintas no es duplicado (original + repost)', () => {
  const acc = new CaptureAccumulator();
  acc.add({ platform: 'x', handle: 'tomi' }, { platform_post_id: '9', text: 't' });
  const r = acc.add({ platform: 'x', handle: 'lau' }, { platform_post_id: '9', text: 't', kind: 'repost' });
  assert.equal(r.newPost, true);
  assert.equal(acc.totals.posts, 2);
});

test('acumulador: lo ya enviado no se reenvía; una cuenta solo vuelve a salir si trae datos nuevos', () => {
  const acc = new CaptureAccumulator();
  acc.add({ platform: 'x', handle: 'lau' }, { platform_post_id: '1', text: 'a' });
  assert.ok(acc.drain(PAGE));
  assert.equal(acc.drain(PAGE), null, 'sin novedades no hay lote');

  acc.add({ platform: 'x', handle: 'lau' }, { platform_post_id: '1', text: 'a' });
  assert.equal(acc.drain(PAGE), null, 'ver de nuevo lo mismo no genera lote');

  acc.add({ platform: 'x', handle: 'lau', bio: 'Docente', followers: 10 });
  const second = acc.drain(PAGE);
  assert.equal(second.profiles.length, 1);
  assert.equal(second.profiles[0].account.bio, 'Docente');
  assert.deepEqual(second.profiles[0].posts, [], 'la publicación ya enviada no se repite');

  acc.add({ platform: 'x', handle: 'lau', bio: '' });
  assert.equal(acc.drain(PAGE), null, 'un valor vacío no pisa uno bueno ni ensucia la cuenta');
});

test('acumulador: interacciones y fragmentos sin duplicar', () => {
  const acc = new CaptureAccumulator();
  assert.equal(acc.addInteraction('@Lau', 'Tomi', 'mention'), true);
  assert.equal(acc.addInteraction('lau', 'tomi', 'MENTION'), false);
  assert.equal(acc.addInteraction('lau', 'tomi', 'reply'), true);
  assert.equal(acc.addInteraction('lau', 'lau', 'reply'), false, 'no hay interacción con uno mismo');
  assert.equal(acc.addSnippet('Un párrafo de ejemplo.'), true);
  assert.equal(acc.addSnippet('Un   párrafo de ejemplo. '), false);
  const batch = acc.drain({ ...PAGE, platform: 'generic' });
  assert.deepEqual(batch.interactions, [['lau', 'tomi', 'mention'], ['lau', 'tomi', 'reply']]);
  assert.deepEqual(batch.text_snippets, ['Un párrafo de ejemplo.']);
});

test('acumulador: reparte lotes grandes sin perder ni repetir publicaciones', () => {
  const acc = new CaptureAccumulator();
  const total = LIMITS.postsPerBatch * 2 + 7;
  for (let i = 0; i < total; i++) acc.add({ platform: 'x', handle: `cuenta${i % 5}` }, { platform_post_id: String(i), text: `t${i}` });
  const seen = new Set();
  let batches = 0;
  for (let batch = acc.drain(PAGE); batch; batch = acc.drain(PAGE)) {
    batches += 1;
    assert.deepEqual(validateCaptureBatch(batch), []);
    const count = batch.profiles.reduce((n, p) => n + p.posts.length, 0);
    assert.ok(count <= LIMITS.postsPerBatch);
    for (const p of batch.profiles) for (const post of p.posts) {
      assert.ok(!seen.has(post.platform_post_id));
      seen.add(post.platform_post_id);
    }
    assert.ok(batches < 10);
  }
  assert.equal(seen.size, total);
  assert.equal(batches, 3);
});

test('CaptureBatch: lo extraído del fixture de X cumple el esquema campo por campo', () => {
  const url = 'https://x.com/home';
  const { document } = loadFixture('x-timeline.html', url);
  const acc = new CaptureAccumulator();
  for (const el of x.findItems(document.querySelector('[data-testid="primaryColumn"]'))) {
    const item = x.extractItem(el, { url });
    for (const { account, post } of item.records) acc.add(account, post);
    for (const [a, b, t] of item.interactions) acc.addInteraction(a, b, t);
  }
  const batch = acc.drain({ ...PAGE, pageUrl: url });
  assert.deepEqual(validateCaptureBatch(batch), []);
  assert.deepEqual(Object.keys(batch).sort(), ['captured_at', 'extension_version', 'interactions', 'page_title', 'page_url', 'platform', 'profiles', 'text_snippets']);
  assert.equal(batch.captured_at, '2026-10-07T12:00:00.000Z');
  assert.equal(batch.platform, 'x');
  assert.deepEqual(batch.profiles.map((p) => p.account.handle).sort(), ['Tomi_Ejemplo', 'cuenta_demo_3', 'lau_ficticia']);
  // Sobrevive a JSON (lo que realmente viaja): ternas como listas, fechas como texto ISO.
  const wire = JSON.parse(JSON.stringify(batch));
  assert.deepEqual(validateCaptureBatch(wire), []);
  assert.ok(wire.interactions.every((t) => Array.isArray(t) && t.length === 3));
  for (const p of wire.profiles) for (const post of p.posts) {
    assert.ok(post.mentions.every((m) => m === m.toLowerCase() && !m.startsWith('@')));
    assert.ok(post.hashtags.every((h) => h === h.toLowerCase() && !h.startsWith('#')));
    assert.ok(post.created_at === null || post.created_at.endsWith('Z'));
  }
});

test('validador de CaptureBatch: rechaza lo que pydantic rechazaría', () => {
  const acc = new CaptureAccumulator();
  acc.add({ platform: 'x', handle: 'lau' }, { platform_post_id: '1', text: 'a' });
  const good = acc.drain(PAGE);
  const bad = (mutate) => { const b = structuredClone(good); mutate(b); return validateCaptureBatch(b); };
  assert.deepEqual(validateCaptureBatch(good), []);
  assert.ok(bad((b) => { delete b.captured_at; }).length);
  assert.ok(bad((b) => { b.captured_at = '07/10/2026'; }).length);
  assert.ok(bad((b) => { b.interactions = [['a', 'b']]; }).length);
  assert.ok(bad((b) => { b.profiles[0].posts[0].kind = 'retweet'; }).length);
  assert.ok(bad((b) => { b.profiles[0].posts[0].mentions = ['@Lau']; }).length);
  assert.ok(bad((b) => { b.profiles[0].account.followers = '10'; }).length);
  assert.ok(bad((b) => { b.profiles[0].account.handle = '@lau'; }).length);
  assert.ok(bad((b) => { b.profiles[0].posts[0].created_at = '2026-10-05T14:03:22'; }).length, 'fecha sin zona');
  assert.ok(bad((b) => { b.token = 'x'; }).length, 'ninguna clave fuera del contrato');
});

test('acumulador: un nombre visto al pasar no pisa al ya conocido; la cabecera del perfil sí', () => {
  const acc = new CaptureAccumulator();
  acc.add({ platform: 'x', handle: 'lau', display_name: 'Laura Ficticia🌻' });
  acc.drain(PAGE);
  acc.add({ platform: 'x', handle: 'lau', display_name: 'Laura Ficticia' });
  assert.equal(acc.drain(PAGE), null);
  acc.add({ platform: 'x', handle: 'lau', display_name: 'Laura F.', bio: 'Docente' }, null, { authoritative: true });
  const batch = acc.drain(PAGE);
  assert.equal(batch.profiles[0].account.display_name, 'Laura F.');
});
