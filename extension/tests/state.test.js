import test from 'node:test';
import assert from 'node:assert/strict';
import { newTabState, applyBatch, applyLookup, badgeFor } from '../src/background/state.js';
import { emptyGraph, addAccount, addEdge, applyHandleInfo, layout, mainEntities } from '../src/lib/graph.js';
import { CaptureAccumulator } from '../src/lib/batch.js';

function batchOf(posts) {
  const acc = new CaptureAccumulator();
  for (const [handle, id, text] of posts) acc.add({ platform: 'x', handle }, { platform_post_id: id, text });
  acc.addInteraction('lau_ficticia', 'tomi_ejemplo', 'mention');
  acc.addInteraction('cuenta_demo_3', 'lau_ficticia', 'reply');
  return acc.drain({ pageUrl: 'https://x.com/home', platform: 'x', now: new Date('2026-10-07T12:00:00Z') });
}

test('estado: contadores y hallazgos por lote, sin contar dos veces lo mismo', () => {
  const state = newTabState();
  const batch = batchOf([['lau_ficticia', '1', 'a'], ['cuenta_demo_3', '2', 'b']]);
  const fresh = applyBatch(state, batch, [{ kind: 'email', value: 'a@demo.com', quote: 'a@demo.com' }]);
  assert.deepEqual([state.counts.accounts, state.counts.posts], [2, 2]);
  assert.deepEqual(fresh.map((f) => f.kind), ['account', 'account', 'connection', 'detection']);
  assert.equal(state.findings[0].kind, 'detection', 'lo más nuevo va primero');

  // Recarga de la página: llega el mismo lote otra vez.
  const again = applyBatch(state, batch, [{ kind: 'email', value: 'a@demo.com' }]);
  assert.deepEqual(again, []);
  assert.deepEqual([state.counts.accounts, state.counts.posts], [2, 2]);
  assert.deepEqual(Object.keys(state.graph.nodes).sort(), ['cuenta_demo_3', 'lau_ficticia', 'tomi_ejemplo']);
  assert.ok(JSON.stringify(state).length > 0, 'el estado se puede guardar en chrome.storage');
});

test('estado: lookup marca cuentas conocidas e hipótesis MENARD (como hipótesis, y una sola vez)', () => {
  const state = newTabState();
  applyBatch(state, batchOf([['lau_ficticia', '1', 'a'], ['cuenta_demo_3', '2', 'b']]));
  const link = (other) => [{ other, platform: 'x', score: 0.87, review_status: 'proposed' }];
  const response = {
    lau_ficticia: { known: true, entity_id: 101, posts_captured: 37, links: link('cuenta_demo_3'), relations: [], note: '' },
    cuenta_demo_3: { known: true, entity_id: 102, posts_captured: 12, links: link('lau_ficticia'), relations: [], note: '' },
    tomi_ejemplo: { known: false, entity_id: null, posts_captured: 0, links: [], relations: [], note: '' },
  };
  const fresh = applyLookup(state, 'x', response);
  const kinds = fresh.map((f) => f.kind);
  assert.equal(kinds.filter((k) => k === 'known').length, 2);
  assert.equal(kinds.filter((k) => k === 'menard').length, 1, 'la hipótesis del par se avisa una sola vez');
  assert.equal(kinds.filter((k) => k === 'connection').length, 1, 'y se avisa que las dos cuentas están en pantalla');
  const menard = fresh.find((f) => f.kind === 'menard');
  assert.match(menard.detail, /Hipótesis MENARD/);
  assert.match(menard.detail, /0\.87/);
  assert.match(menard.detail, /para revisar/);
  assert.equal(state.graph.nodes.lau_ficticia.menard, 0.87);
  assert.equal(state.graph.nodes.lau_ficticia.entityId, 101);
  assert.equal(state.graph.nodes.tomi_ejemplo.known, false);
  assert.ok(Object.values(state.graph.edges).some((e) => e.type === 'menard' && e.weight === 0.87));
  assert.deepEqual(applyLookup(state, 'x', response), [], 'repetir la consulta no repite hallazgos');
});

test('estado: una hipótesis rechazada por un analista no se resalta', () => {
  const state = newTabState();
  const fresh = applyLookup(state, 'x', { a_demo: { known: false, links: [{ other: 'b_demo', score: 0.9, review_status: 'rejected' }] } });
  assert.deepEqual(fresh, []);
  assert.deepEqual(state.graph.nodes, {});
});

test('insignia del ícono: REC solo con captura activa', () => {
  assert.equal(badgeFor(newTabState()).text, '');
  assert.equal(badgeFor({ ...newTabState(), active: true }).text, 'REC');
  assert.equal(badgeFor({ ...newTabState(), active: true, paused: 'mensajes' }).text, '❚❚');
  assert.equal(badgeFor({ ...newTabState(), active: true, error: 'x' }).text, '!');
});

test('grafo: aristas sin duplicar, MENARD sin dirección, límite de nodos y destinos de arrastre', () => {
  const g = emptyGraph();
  assert.ok(addEdge(g, 'A', 'b', 'mention'));
  assert.equal(addEdge(g, 'a', 'B', 'mention'), null);
  assert.equal(g.edges['a|b|mention'].weight, 2);
  assert.equal(addEdge(g, 'a', 'a', 'reply'), null);
  assert.ok(addEdge(g, 'b', 'a', 'menard', 0.6));
  assert.equal(addEdge(g, 'a', 'b', 'menard', 0.9), null);
  assert.equal(g.edges['a|b|menard'].weight, 0.9);

  applyHandleInfo(g, 'a', { known: true, entity_id: 7, links: [{ other: 'b', score: 0.9 }] });
  assert.deepEqual(mainEntities(g).map((e) => [e.id, e.entityId]), [['a', 7]]);

  for (let i = 0; i < 150; i++) addAccount(g, `relleno${i}`);
  assert.ok(Object.keys(g.nodes).length <= 60);
  assert.ok(g.nodes.a && g.nodes.b, 'los nodos con hipótesis no se descartan');
});

test('grafo: la disposición es determinista, queda dentro del lienzo y no mueve de más lo que ya estaba', () => {
  const g = emptyGraph();
  for (const [a, b] of [['a', 'b'], ['b', 'c'], ['c', 'd'], ['a', 'd'], ['e', 'f']]) addEdge(g, a, b, 'mention');
  addEdge(g, 'a', 'c', 'menard', 0.8);
  const size = { width: 340, height: 230 };
  const one = layout(g, size);
  assert.deepEqual(layout(g, size), one);
  for (const p of Object.values(one)) {
    assert.ok(p.x >= 18 && p.x <= size.width - 18 && p.y >= 18 && p.y <= size.height - 18);
    assert.ok(Number.isFinite(p.x) && Number.isFinite(p.y));
  }
  const pts = Object.values(one);
  for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
    assert.ok(Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y) > 8, 'los nodos no se enciman');
  }
  addAccount(g, 'nuevo');
  const two = layout(g, { ...size, previous: one, iterations: 30 });
  assert.ok(two.nuevo);
  assert.deepEqual(layout(emptyGraph(), size), {});
});
