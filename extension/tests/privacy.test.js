// Reglas de comportamiento: rutas privadas excluidas, nada de mensajería ni de formularios con clave,
// y la sesión de captura no lee ni envía nada de ahí.

import test from 'node:test';
import assert from 'node:assert/strict';
import { excludedReason, isExcludedUrl, detectPlatform, insidePrivate, originPattern } from '../src/lib/routes.js';
import { CaptureSession } from '../src/content/session.js';
import * as x from '../src/extractors/x.js';
import * as generic from '../src/extractors/generic.js';
import { validateCaptureBatch } from '../src/lib/schema.js';
import { loadFixture, domFrom } from './tools/helpers.mjs';

test('rutas de mensajes directos y de credenciales quedan excluidas', () => {
  const excluded = [
    'https://x.com/messages', 'https://x.com/messages/123-456', 'https://twitter.com/messages/compose',
    'https://x.com/i/chat/abc', 'https://x.com/i/flow/login', 'https://x.com/settings/account',
    'https://www.instagram.com/direct/inbox/', 'https://www.instagram.com/direct/t/1234567/',
    'https://www.instagram.com/accounts/login/', 'https://www.instagram.com/accounts/password/change/',
    'https://bsky.app/messages', 'https://bsky.app/messages/3kabc', 'https://bsky.app/settings',
    'https://www.reddit.com/message/inbox', 'https://www.reddit.com/chat/room/abc', 'https://chat.reddit.com/room/x',
    'https://old.reddit.com/message/messages/abc', 'https://www.reddit.com/login/',
    'https://mail.google.com/mail/u/0/', 'https://web.whatsapp.com/', 'https://web.telegram.org/k/',
    'https://www.messenger.com/t/123', 'https://www.facebook.com/messages/t/123', 'https://discord.com/channels/@me/123',
    'https://outlook.live.com/mail/0/', 'https://www.linkedin.com/messaging/thread/1/',
    'https://banco-demo.example/login', 'https://tienda-demo.example/cuenta/password/reset', 'https://sso.example/oauth/authorize',
    'chrome://extensions/', 'file:///C:/Users/x/doc.html', 'about:blank', 'no es una url',
  ];
  for (const url of excluded) assert.ok(isExcludedUrl(url), `debería excluir ${url}`);
});

test('rutas públicas siguen permitidas (sin falsos bloqueos)', () => {
  const allowed = [
    'https://x.com/home', 'https://x.com/lau_ficticia', 'https://x.com/lau_ficticia/status/1840000000000000001',
    'https://x.com/search?q=messages', 'https://x.com/messages_fan', 'https://x.com/i/lists/123',
    'https://www.instagram.com/sofi.demo_ok/', 'https://www.instagram.com/p/CxDemo12345/', 'https://www.instagram.com/directo.tv/',
    'https://bsky.app/profile/marina.ejemplo.bsky.social', 'https://bsky.app/profile/a.b/post/3lb2',
    'https://www.reddit.com/r/ejemplo/comments/1abcde/titulo/', 'https://www.reddit.com/user/usuario_demo_uno/',
    'https://www.reddit.com/r/chat/', 'https://foro-demo.example/hilo/1', 'https://discord.com/channels/123/456',
    'https://blog.example/2026/como-hacer-login-seguro-en-tu-app', 'http://sitio-demo.example/',
  ];
  for (const url of allowed) assert.equal(excludedReason(url), '', `no debería excluir ${url}`);
});

test('plataforma y patrón de permiso por origen', () => {
  assert.equal(detectPlatform('https://mobile.twitter.com/a'), 'x');
  assert.equal(detectPlatform('https://old.reddit.com/r/a'), 'reddit');
  assert.equal(detectPlatform('https://notx.com/'), 'generic');
  assert.equal(originPattern('https://x.com/lau_ficticia?a=1'), 'https://x.com/*');
  assert.equal(originPattern('chrome://extensions'), '');
});

function immediateSession(document, extractor, getUrl) {
  // Observador de visibilidad falso: todo lo que se observa "entra en pantalla" al instante.
  const session = new CaptureSession({
    document, extractor, getUrl, version: 'test', now: () => new Date('2026-10-07T12:00:00Z'),
    observeVisibility: (el, cb) => cb(),
  });
  return session;
}

test('sesión: el cajón de mensajes directos sobre el timeline no se captura', () => {
  const url = 'https://x.com/home';
  const { document } = loadFixture('x-timeline.html', url);
  assert.ok(insidePrivate(document.querySelector('[data-testid="DMDrawer"] article')));
  assert.ok(!insidePrivate(document.querySelector('[data-testid="primaryColumn"] article')));

  const session = immediateSession(document, x, () => url);
  assert.equal(session.scan(), 4, 'solo las 4 publicaciones del timeline');
  const batch = session.flush('Inicio / X');
  assert.deepEqual(validateCaptureBatch(batch), []);
  const wire = JSON.stringify(batch);
  assert.ok(!wire.includes('privado_demo'));
  assert.ok(!wire.includes('mensaje privado'));
  assert.ok(!wire.includes('secreto@privado-demo.com'));
});

test('sesión: en una ruta de mensajes no observa, no extrae y no arma lote', () => {
  let url = 'https://x.com/messages/123-456';
  const { document } = loadFixture('x-timeline.html', url);
  const session = immediateSession(document, x, () => url);
  assert.equal(session.scan(), 0);
  assert.deepEqual(session.scanPage(), { accounts: [], posts: 0 });
  assert.equal(session.flush('Mensajes'), null);
  assert.deepEqual(session.totals, { accounts: 0, posts: 0, interactions: 0, snippets: 0 });

  // Navegación SPA: de un perfil a mensajes. Lo pendiente no sale con la página privada.
  url = 'https://x.com/home';
  session.scan();
  assert.ok(session.accumulator.hasPending());
  url = 'https://x.com/messages';
  assert.equal(session.flush('Mensajes'), null, 'estando en mensajes no se envía nada');
});

test('sesión: deduplica cuando la plataforma vuelve a renderizar la misma publicación', () => {
  const url = 'https://x.com/home';
  const { document } = loadFixture('x-timeline.html', url);
  const session = immediateSession(document, x, () => url);
  session.scan();
  const first = session.flush('t');
  const posts = first.profiles.reduce((n, p) => n + p.posts.length, 0);
  assert.equal(posts, 5, '4 publicaciones + 1 repost');

  // X recicla los nodos al hacer scroll: aparece un <article> nuevo con el mismo contenido.
  const column = document.querySelector('[data-testid="primaryColumn"] section > div');
  const original = column.querySelector('article');
  const clone = original.cloneNode(true);
  column.appendChild(clone);
  assert.equal(session.scan(), 1, 'el nodo nuevo se observa');
  assert.equal(session.flush('t'), null, 'pero no aporta nada nuevo');
  assert.equal(session.scan(), 0, 'y no se vuelve a observar');
  assert.equal(session.totals.posts, 5);

  // El contexto de la publicación queda disponible para los datachunks de adentro.
  const info = session.infoFor(original.querySelector('[data-testid="tweetText"] span'));
  assert.equal(info.handle, 'lau_ficticia');
  assert.equal(info.postId, '1840000000000000001');
});

test('sesión genérica: no lee formularios con contraseña, campos ni zonas editables', () => {
  const url = 'https://foro-demo.example/hilo/1';
  const { document } = loadFixture('generic.html', url);
  const session = immediateSession(document, generic, () => url);
  session.scan();
  const batch = session.flush('Foro');
  assert.deepEqual(validateCaptureBatch(batch), []);
  assert.equal(batch.platform, 'generic');
  assert.equal(batch.profiles.length, 0);
  const wire = JSON.stringify(batch);
  for (const forbidden of ['NoLeerEstoNunca123', 'valor-de-input', 'texto-en-textarea', 'no-capturar@demo.com', 'borrador@demo.com', 'secreto-en-script']) {
    assert.ok(!wire.includes(forbidden), `no debe salir "${forbidden}"`);
  }
  assert.ok(session.takeDetections().some((d) => d.kind === 'email' && d.value === 'ventas@sitio-demo.com.ar'));
  assert.deepEqual(session.takeDetections(), [], 'las detecciones se avisan una sola vez');
});

test('insidePrivate: cualquier formulario con campo de contraseña', () => {
  const { document } = domFrom('<form><p id="a">Texto del formulario de ingreso</p><input type="password"></form><form><p id="b">Buscador</p><input type="search"></form>');
  assert.ok(insidePrivate(document.getElementById('a')));
  assert.ok(!insidePrivate(document.getElementById('b')));
  assert.ok(insidePrivate(document.getElementById('a').firstChild), 'también para nodos de texto');
});

test('insidePrivate: diálogos (modales) con campo de contraseña, aunque no sean formulario', () => {
  const { document } = domFrom(
    '<main><p id="pub">Publicación pública</p></main>' +
    '<div role="dialog"><p id="modal">Ingresá a tu cuenta</p><div><input type="password"></div></div>' +
    '<div role="dialog"><p id="otro">Diálogo sin contraseña</p></div>' +
    '<form><div role="dialog"><p id="anidado">Dentro de un formulario con clave</p></div><input type="password"></form>',
  );
  assert.ok(insidePrivate(document.getElementById('modal')), 'diálogo con clave');
  assert.ok(!insidePrivate(document.getElementById('pub')));
  assert.ok(!insidePrivate(document.getElementById('otro')), 'diálogo sin clave no se excluye');
  assert.ok(insidePrivate(document.getElementById('anidado')), 'diálogo dentro de un formulario con clave');
});
