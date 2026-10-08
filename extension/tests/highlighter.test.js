import test from 'node:test';
import assert from 'node:assert/strict';
import { ChunkHighlighter, HIGHLIGHT_NAMES, CssHighlightRegistry } from '../src/content/chunk-highlighter.js';
import { LENS_HOST_TAG } from '../src/lib/dom.js';
import { chunkKey } from '../src/lib/chunks.js';
import { loadFixture, domFrom } from './tools/helpers.mjs';

// Registro falso: guarda lo que se pintaría, igual que CSS.highlights.
class FakeRegistry {
  constructor() { this.groups = { new: [], sent: [], known: [] }; this.replaced = 0; }
  replace(groups) { this.groups = groups; this.replaced += 1; }
  clear() { this.groups = { new: [], sent: [], known: [] }; }
  texts(status = 'new') { return this.groups[status].map((r) => r.toString()); }
  all() { return [...this.groups.new, ...this.groups.sent, ...this.groups.known]; }
}

function setup(html, options = {}) {
  const { document } = domFrom(`<body>${html}</body>`);
  const registry = new FakeRegistry();
  const highlighter = new ChunkHighlighter({ document, registry, ...options });
  return { document, registry, highlighter };
}

test('resalta solo el fragmento exacto, como rango de texto, sin tocar el DOM', () => {
  const { document, registry, highlighter } = setup('<p id="p">Escribime a <b>ventas@demo.com.ar</b> o al +54 9 341 555-0123, gracias.</p>');
  const before = document.body.innerHTML;
  const chunks = highlighter.rescan();
  assert.deepEqual(registry.texts(), ['ventas@demo.com.ar', '+54 9 341 555-0123']);
  assert.deepEqual(chunks.map((c) => c.kind), ['email', 'phone']);
  assert.equal(document.body.innerHTML, before, 'la página queda idéntica: no se envuelve nada en <span>');
  for (const range of registry.all()) {
    assert.equal(range.startContainer, range.endContainer);
    assert.equal(range.startContainer.nodeType, 3);
  }
});

test('no resalta dentro de inputs, textareas, scripts, estilos, zonas editables ni del propio UI', () => {
  const { registry, highlighter } = setup(`
    <p>visible@demo.com</p>
    <script>var a = "script@demo.com";</script>
    <style>.a::after{content:"style@demo.com"}</style>
    <noscript>noscript@demo.com</noscript>
    <textarea>textarea@demo.com</textarea>
    <input value="input@demo.com">
    <select><option>option@demo.com</option></select>
    <button>boton@demo.com</button>
    <div contenteditable="true">editable@demo.com <span>anidado@demo.com</span></div>
    <div role="textbox">textbox@demo.com</div>
    <${LENS_HOST_TAG}><div>ui-propio@demo.com</div></${LENS_HOST_TAG}>
    <form><span>form-clave@demo.com</span><input type="password"></form>
    <div data-testid="DMDrawer"><span>dm@demo.com</span></div>
  `);
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['visible@demo.com']);
});

test('fixture genérico: lo privado no se resalta y los falsos positivos tampoco', () => {
  const { document } = loadFixture('generic.html', 'https://foro-demo.example/hilo/1');
  const registry = new FakeRegistry();
  const highlighter = new ChunkHighlighter({ document, registry });
  highlighter.rescan();
  const texts = registry.texts();
  assert.ok(texts.includes('ventas@sitio-demo.com.ar'));
  assert.ok(texts.includes('CVE-2024-12345'));
  assert.ok(texts.includes('Av. Corrientes 1234'));
  for (const t of texts) {
    assert.ok(!/no-capturar|valor-de-input|textarea|borrador|secreto-en-script|estilo@/.test(t), t);
    assert.ok(!['1.2.3.4', 'informe.txt', 'Node.js', '3:2', 'Fin.Luego'].includes(t), `falso positivo: ${t}`);
  }
});

test('re-resaltado tras una mutación del DOM: sin duplicados y sin rangos huérfanos', () => {
  const { document, registry, highlighter } = setup('<div id="feed"><p class="post">Contacto: a@demo.com y @cuenta_demo</p></div>');
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['a@demo.com', '@cuenta_demo']);

  // Repetir la pasada sin cambios no acumula.
  highlighter.rescan();
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['a@demo.com', '@cuenta_demo']);
  assert.equal(highlighter.chunks.length, 2);

  // La plataforma re-renderiza: reemplaza el nodo por otro con el mismo texto y agrega uno nuevo.
  const feed = document.getElementById('feed');
  const old = feed.firstElementChild;
  const fresh = document.createElement('p');
  fresh.className = 'post';
  fresh.textContent = 'Contacto: a@demo.com y @cuenta_demo';
  feed.replaceChild(fresh, old);
  const extra = document.createElement('p');
  extra.textContent = 'Nuevo: b@demo.com';
  feed.appendChild(extra);
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['a@demo.com', '@cuenta_demo', 'b@demo.com']);
  for (const range of registry.all()) {
    assert.ok(range.startContainer.isConnected, 'ningún rango apunta a un nodo que ya no está en la página');
  }
  assert.ok(!registry.all().some((r) => r.startContainer.parentElement === old));

  // Cambia el texto de un nodo existente (characterData): se recalcula ese nodo.
  extra.firstChild.nodeValue = 'Nuevo: c@demo.com y d@demo.com';
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['a@demo.com', '@cuenta_demo', 'c@demo.com', 'd@demo.com']);

  // Se va todo: no queda nada pintado.
  feed.textContent = '';
  highlighter.rescan();
  assert.deepEqual(registry.all(), []);
});

test('estados: nuevo (amarillo), enviado al expediente y ya conocido por el caso', () => {
  const sent = new Map([[chunkKey('email', 'a@demo.com'), 'Contactos']]);
  const known = new Set(['cuenta_demo']);
  const { registry, highlighter } = setup('<p>a@demo.com, @cuenta_demo y sitio-demo.com</p>', {
    statusOf: (kind, value) => {
      if (sent.has(chunkKey(kind, value))) return { status: 'sent', section: sent.get(chunkKey(kind, value)) };
      if (kind === 'account' && known.has(value)) return { status: 'known' };
      return { status: 'new' };
    },
  });
  const chunks = highlighter.rescan();
  assert.deepEqual(registry.texts('sent'), ['a@demo.com']);
  assert.deepEqual(registry.texts('known'), ['@cuenta_demo']);
  assert.deepEqual(registry.texts('new'), ['sitio-demo.com']);
  assert.equal(chunks.find((c) => c.status === 'sent').section, 'Contactos');

  // Al enviar otro chunk cambia de grupo en la pasada siguiente (no queda en los dos).
  sent.set(chunkKey('domain', 'sitio-demo.com'), 'Infraestructura');
  highlighter.rescan();
  assert.deepEqual(registry.texts('new'), []);
  assert.deepEqual(registry.texts('sent'), ['a@demo.com', 'sitio-demo.com']);
});

test('chunks manuales y de FUNES: se resaltan por texto exacto y sobreviven al re-render', () => {
  const { document, registry, highlighter } = setup('<p id="p">Anoche Laura Ficticia dijo que no iba a volver nunca más.</p>');
  highlighter.rescan();
  assert.deepEqual(registry.texts(), []);
  assert.equal(highlighter.addLiteral({ kind: 'person', value: 'Laura Ficticia', quote: 'Laura Ficticia', detected_by: 'funes' }), true);
  assert.equal(highlighter.addLiteral({ kind: 'person', value: 'Laura Ficticia', quote: 'Laura Ficticia', detected_by: 'funes' }), false, 'no se agrega dos veces');
  highlighter.addLiteral({ kind: 'text', value: 'no iba a volver nunca más', quote: 'no iba a volver nunca más', detected_by: 'manual' });
  const chunks = highlighter.rescan();
  assert.deepEqual(registry.texts(), ['Laura Ficticia', 'no iba a volver nunca más']);
  assert.deepEqual(chunks.map((c) => c.detected_by), ['funes', 'manual']);

  const p = document.getElementById('p');
  const clone = p.cloneNode(true);
  p.replaceWith(clone);
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['Laura Ficticia', 'no iba a volver nunca más']);
});

test('solo trabaja sobre lo que está en pantalla o cerca, y encuentra el chunk bajo el cursor', () => {
  const { document, registry, highlighter } = setup('<p id="cerca">cerca@demo.com</p><p id="lejos">lejos@demo.com</p>', {});
  highlighter.isNearViewport = (el) => el.id !== 'lejos';
  highlighter.rescan();
  assert.deepEqual(registry.texts(), ['cerca@demo.com']);
  const node = document.getElementById('cerca').firstChild;
  assert.equal(highlighter.chunkAtOffset(node, 3).value, 'cerca@demo.com');
  assert.equal(highlighter.chunkAtOffset(document.getElementById('lejos').firstChild, 3), null);
  highlighter.clear();
  assert.deepEqual(registry.all(), []);
});

test('registro real: usa CSS.highlights con un nombre por estado y los borra al limpiar', () => {
  const store = new Map();
  class Highlight { constructor(...ranges) { this.ranges = ranges; } }
  const win = { CSS: { highlights: store }, Highlight };
  const registry = new CssHighlightRegistry(win);
  registry.replace({ new: ['r1', 'r2'], sent: [], known: ['r3'] });
  assert.deepEqual(store.get(HIGHLIGHT_NAMES.new).ranges, ['r1', 'r2']);
  assert.ok(!store.has(HIGHLIGHT_NAMES.sent));
  assert.deepEqual(store.get(HIGHLIGHT_NAMES.known).ranges, ['r3']);
  registry.replace({ new: [], sent: [], known: [] });
  assert.equal(store.size, 0);
  // Sin soporte del navegador no rompe.
  new CssHighlightRegistry({}).replace({ new: ['r'], sent: [], known: [] });
});
