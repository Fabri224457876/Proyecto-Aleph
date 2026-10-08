// Cliente, cola de envío y service worker contra un servidor Aleph simulado (HTTP real en 127.0.0.1).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { AlephClient, ApiError, normalizeServerUrl } from '../src/lib/api.js';
import { SendQueue, backoffDelay } from '../src/lib/queue.js';
import { CaptureAccumulator } from '../src/lib/batch.js';
import { makeChunk } from '../src/lib/chunks.js';
import { createMockServer, MOCK_LOGIN, MOCK_TOKEN } from './tools/mock-server.mjs';
import { ROOT } from './tools/helpers.mjs';

function sampleBatch(id = '1') {
  const acc = new CaptureAccumulator();
  acc.add({ platform: 'x', handle: 'lau_ficticia', display_name: 'Laura' }, { platform_post_id: id, text: `hola ${id} @tomi #demo` });
  acc.addInteraction('lau_ficticia', 'tomi', 'mention');
  return acc.drain({ pageUrl: 'https://x.com/home', pageTitle: 'X', platform: 'x', now: new Date('2026-10-07T12:00:00Z'), version: '0.1.0' });
}

test('normalizeServerUrl', () => {
  assert.equal(normalizeServerUrl('http://100.64.0.10:8100/'), 'http://100.64.0.10:8100');
  assert.equal(normalizeServerUrl('https://aleph.example/api/'), 'https://aleph.example');
  assert.equal(normalizeServerUrl('localhost:8100'), 'http://localhost:8100');
  assert.equal(normalizeServerUrl('https://aleph.example/base'), 'https://aleph.example/base');
  assert.equal(normalizeServerUrl('ftp://x'), '');
  assert.equal(normalizeServerUrl(''), '');
});

test('cliente: login, casos, captura, lookup, incisos y hallazgos según el contrato', async (t) => {
  const mock = createMockServer();
  const base = await mock.listen();
  t.after(() => mock.close());

  const anon = new AlephClient({ baseUrl: base });
  await assert.rejects(() => anon.login(MOCK_LOGIN.username, 'incorrecta'), (e) => e instanceof ApiError && e.status === 401 && /incorrectos/.test(e.message));
  const { token, user } = await anon.login(MOCK_LOGIN.username, MOCK_LOGIN.secret);
  assert.equal(token, MOCK_TOKEN);
  assert.equal(user.username, 'analista');

  const client = new AlephClient({ baseUrl: `${base}/api/`, token });
  assert.equal((await client.me()).role, 'analyst');
  const cases = await client.listCases();
  assert.deepEqual(cases.map((c) => [c.id, c.name, c.status]), [[1, 'Operación Demo', 'open'], [2, 'Caso cerrado de ejemplo', 'closed']]);

  await client.sendCapture(1, sampleBatch());
  assert.equal(mock.db.captures.length, 1);
  assert.equal(mock.db.captures[0].batch.profiles[0].posts[0].mentions[0], 'tomi');

  const found = await client.lookup(1, 'x', ['lau_ficticia', 'nadie_demo']);
  assert.equal(found.lau_ficticia.known, true);
  assert.equal(found.lau_ficticia.links[0].score, 0.87);
  assert.equal(found.nadie_demo.known, false);

  assert.deepEqual(await client.listSections(1), []);
  const section = await client.createSection(1, 'Cuentas');
  assert.equal(section.name, 'Cuentas');
  const chunk = makeChunk({ kind: 'email', value: 'a@demo.com' }, { url: 'https://x.com/home', platform: 'x' });
  await client.createFinding(1, { section_id: section.id, chunk, attach_to_entity_id: null, note: '' });
  assert.equal(mock.db.findings[0].finding.chunk.value, 'a@demo.com');
  assert.equal((await client.listSections(1))[0].findings, 1);

  const ai = await client.aiChunks(1, { page_url: 'https://x.com/home', page_title: '', platform: 'x', texts: ['Ayer vi a Laura Ficticia en la plaza'] });
  assert.deepEqual(ai.map((c) => [c.kind, c.value, c.detected_by]), [['person', 'Laura Ficticia', 'funes']]);

  // Cada pedido autenticado lleva Bearer y ninguno lleva cookies.
  const authed = mock.db.requests.filter((r) => r.path !== '/api/auth/login');
  assert.ok(authed.every((r) => r.auth === `Bearer ${MOCK_TOKEN}`));
  assert.ok(mock.db.requests.every((r) => r.cookie === ''));
  assert.equal(mock.db.requests.find((r) => r.path === '/api/auth/login').auth, '', 'el login no manda token');
});

test('captures/chunks: el cuerpo es el de ChunksIn (un solo text) y sin texto no hay pedido', async (t) => {
  let sent = null;
  const fake = async (url, init) => {
    sent = { url, body: JSON.parse(init.body) };
    return { ok: true, status: 200, text: async () => JSON.stringify([{ kind: 'ip', value: '203.0.113.9', quote: '203.0.113.9', detected_by: 'rule' }]) };
  };
  const client = new AlephClient({ baseUrl: 'http://servidor.test', token: 'x', fetchImpl: fake });
  const chunks = await client.aiChunks(3, { page_url: 'https://x.com/home', page_title: 'X', platform: 'x', texts: ['uno', 'dos'] });
  assert.equal(sent.url, 'http://servidor.test/api/cases/3/captures/chunks');
  assert.equal(sent.body.text, 'uno\n\ndos');
  assert.deepEqual(Object.keys(sent.body).sort(), ['page_title', 'page_url', 'platform', 'text']);
  assert.equal(chunks[0].value, '203.0.113.9');
  const body = sent.body;
  sent = null;
  assert.deepEqual(await client.aiChunks(3, { texts: ['   '] }), []);
  assert.equal(sent, null, 'sin texto no se hace ningún pedido');

  let py = null;
  try {
    py = readFileSync(join(ROOT, '..', 'backend', 'aleph', 'api', 'routers', 'cti.py'), 'utf8');
  } catch {
    t.skip('no está el backend al lado (extensión distribuida sola)');
    return;
  }
  const fields = [...py.match(/class ChunksIn\(BaseModel\):([\s\S]*?)\n\n/)[1].matchAll(/^ {4}([a-z_]+):/gm)].map((m) => m[1]);
  for (const key of Object.keys(body)) assert.ok(fields.includes(key), `${key} no existe en ChunksIn`);
});

test('cliente: errores clasificados (sin servidor, 401, 422, 503)', async (t) => {
  const mock = createMockServer();
  const base = await mock.listen();
  t.after(() => mock.close());
  await assert.rejects(() => new AlephClient({ baseUrl: 'http://127.0.0.1:9', token: 'x', timeoutMs: 1500 }).listCases(), (e) => e.retriable && !e.auth);
  await assert.rejects(() => new AlephClient({ baseUrl: base, token: 'vencido' }).listCases(), (e) => e.auth && e.status === 401 && !e.retriable);
  await assert.rejects(() => new AlephClient({ baseUrl: base }).listCases(), (e) => e.auth, 'sin token ni siquiera sale el pedido');
  await assert.rejects(() => new AlephClient({ baseUrl: '' }).listCases(), /URL del servidor/);
  const client = new AlephClient({ baseUrl: base, token: MOCK_TOKEN });
  await assert.rejects(() => client.sendCapture(1, { page_url: 'x' }), (e) => e.status === 422 && !e.retriable);
  mock.db.failNext = 1;
  await assert.rejects(() => client.listCases(), (e) => e.status === 503 && e.retriable);
});

// Cola con reloj y temporizador falsos: los reintentos se disparan a mano.
function fakeQueue(send, options = {}) {
  const env = { now: 0, timers: [], events: [], saved: null };
  const queue = new SendQueue({
    send,
    persist: async (items) => { env.saved = structuredClone(items); },
    onEvent: (e) => env.events.push(e.type),
    now: () => env.now,
    setTimer: (fn, ms) => { const t = { fn, at: env.now + ms }; env.timers.push(t); return t; },
    clearTimer: (t) => { env.timers = env.timers.filter((x) => x !== t); },
    options,
  });
  env.advance = async (ms) => {
    env.now += ms;
    const due = env.timers.filter((t) => t.at <= env.now);
    env.timers = env.timers.filter((t) => t.at > env.now);
    for (const t of due) t.fn();
    await queue.running;
  };
  return { queue, env };
}

test('cola: espera exponencial con tope', () => {
  assert.deepEqual([1, 2, 3, 4, 5].map((n) => backoffDelay(n, { baseDelayMs: 2000, maxDelayMs: 20000 })), [2000, 4000, 8000, 16000, 20000]);
});

test('cola: reintenta fallas transitorias contra el servidor y termina entregando, en orden y sin duplicar', async (t) => {
  const mock = createMockServer();
  const base = await mock.listen();
  t.after(() => mock.close());
  const client = new AlephClient({ baseUrl: base, token: MOCK_TOKEN });
  const { queue, env } = fakeQueue((item) => client.sendCapture(item.caseId, item.payload));

  mock.db.failNext = 2;
  await queue.enqueue({ tabId: 7, caseId: 1, payload: sampleBatch('1') });
  await queue.enqueue({ tabId: 7, caseId: 1, payload: sampleBatch('2') });
  assert.equal(env.saved.length, 2, 'la cola se persiste al encolar');
  await queue.process();
  assert.equal(mock.db.captures.length, 0, 'las dos primeras respuestas fueron 503');
  assert.equal(queue.pendingFor(7), 2);
  assert.equal(queue.pendingFor(8), 0);
  assert.equal(env.timers.length, 1, 'queda programado el reintento');

  await env.advance(2000);
  assert.equal(mock.db.captures.length, 2);
  assert.deepEqual(mock.db.captures.map((c) => c.batch.profiles[0].posts[0].platform_post_id), ['1', '2']);
  assert.equal(queue.size, 0);
  assert.deepEqual(env.saved, []);
  assert.deepEqual(env.events, ['retry', 'retry', 'sent', 'sent']);
});

test('cola: descarta lo que el servidor rechaza (4xx) y lo que agota los intentos', async () => {
  let calls = 0;
  const { queue, env } = fakeQueue(async (item) => {
    calls += 1;
    throw new ApiError('x', item.payload === 'malo' ? { status: 422 } : { status: 503, retriable: true });
  }, { maxAttempts: 3, baseDelayMs: 10, maxDelayMs: 40 });
  await queue.enqueue({ tabId: 1, caseId: 1, payload: 'malo' });
  await queue.enqueue({ tabId: 1, caseId: 1, payload: 'caido' });
  await queue.process();
  await env.advance(10);
  await env.advance(20);
  await env.advance(1000);
  assert.equal(queue.size, 0);
  assert.equal(calls, 4, '1 intento para el 422 y 3 para el 503');
  assert.deepEqual(env.events, ['dropped', 'retry', 'retry', 'dropped']);
});

test('cola: con 401 se pausa sin perder nada y sigue al volver a iniciar sesión', async () => {
  let token = 'vencido';
  const sent = [];
  const { queue, env } = fakeQueue(async (item) => {
    if (token === 'vencido') throw new ApiError('No autenticado.', { status: 401, auth: true });
    sent.push(item.payload);
  });
  await queue.enqueue({ tabId: 1, caseId: 1, payload: 'a' });
  await queue.enqueue({ tabId: 1, caseId: 1, payload: 'b' });
  await queue.process();
  assert.equal(queue.paused, true);
  assert.equal(queue.size, 2);
  assert.equal(env.timers.length, 0, 'en pausa no insiste');
  assert.deepEqual(env.events, ['auth']);
  token = 'nuevo';
  await queue.resume();
  assert.deepEqual(sent, ['a', 'b']);
  assert.equal(queue.items.length, 0);
});

test('cola: se restaura tras reiniciarse el service worker y tiene un tope de tamaño', async () => {
  const sent = [];
  const { queue } = fakeQueue(async (item) => { sent.push(item.payload); }, { maxItems: 3 });
  queue.restore([{ id: 5, tabId: 1, caseId: 1, payload: 'viejo', attempts: 2, nextAt: 0 }, null, { id: 6 }]);
  assert.equal(queue.size, 1);
  for (const p of ['a', 'b', 'c']) await queue.enqueue({ tabId: 1, caseId: 1, payload: p });
  assert.equal(queue.size, 3, 'se descartó el más viejo');
  await queue.process();
  assert.deepEqual(sent, ['a', 'b', 'c']);
  assert.ok(queue.items.length === 0);
});
