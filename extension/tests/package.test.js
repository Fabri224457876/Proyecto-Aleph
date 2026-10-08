// La extensión se puede cargar descomprimida, y el código cumple las reglas de comportamiento que se
// pueden verificar leyéndolo (pasiva, sin red fuera del cliente, sin tocar cookies ni campos).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { checkAll, listFiles } from './tools/check-syntax.mjs';
import { ROOT } from './tools/helpers.mjs';
import { DEFAULT_SECTIONS, ENTITY_TYPES } from '../src/lib/schema.js';

const sources = listFiles(join(ROOT, 'src'), ['.js']).map((file) => ({
  rel: relative(ROOT, file).replace(/\\/g, '/'),
  // Sin comentarios, para no confundir una explicación con código.
  code: readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1'),
}));

test('sintaxis de todos los JS, manifest MV3 y archivos referenciados', () => {
  const { problems, scripts } = checkAll();
  assert.deepEqual(problems, []);
  assert.ok(scripts > 25);
});

test('pasiva: lo que corre en la página no hace clics, scroll, navegación ni pedidos de red', () => {
  const inPage = sources.filter((s) => /^src\/(content|extractors)\//.test(s.rel) || /^src\/lib\/(dom|normalize|routes|chunks|batch|schema|graph)\.js$/.test(s.rel));
  assert.ok(inPage.length >= 14);
  const forbidden = [
    [/\.click\(\)/, 'clics sintéticos'], [/dispatchEvent\(/, 'eventos sintéticos'],
    [/\b(scrollTo|scrollBy|scrollIntoView)\(/, 'scroll automático'],
    [/location\.(assign|replace|reload)\(|location\.href\s*=[^=]/, 'navegación'], [/history\.(pushState|replaceState|back|go)\(/, 'navegación'],
    [/\bfetch\(|XMLHttpRequest|WebSocket|sendBeacon|EventSource/, 'pedidos de red'],
    [/document\.cookie|localStorage|sessionStorage|indexedDB/, 'cookies o almacenamiento de la página'],
    [/chrome\.storage/, 'almacenamiento de la extensión (ahí está el token)'],
    [/[\w)\]]\.value\b(?!\s*[:,)}\]])/, 'lectura de campos de formulario'],
    [/window\.open\(|\.submit\(\)/, 'abrir o enviar'],
  ];
  for (const { rel, code } of inPage) {
    for (const [re, what] of forbidden) {
      const lines = code.split('\n').filter((l) => re.test(l));
      // Excepciones revisadas a mano: `.value` de objetos propios (chunks, detecciones), no del DOM.
      const real = lines.filter((l) => !(what === 'lectura de campos de formulario' && /\b(chunk|data|d|c|m|mapped|l|p|info|message\.chunk|focused|partial)\.value\b/.test(l)));
      assert.deepEqual(real, [], `${rel}: ${what}`);
    }
  }
});

test('la red sale de un solo lugar (lib/api.js) y sin cookies', () => {
  const withFetch = sources.filter((s) => /\bfetch\(|this\.fetch\(/.test(s.code)).map((s) => s.rel);
  assert.deepEqual(withFetch, ['src/lib/api.js']);
  const api = sources.find((s) => s.rel === 'src/lib/api.js').code;
  assert.match(api, /credentials: 'omit'/);
  for (const s of sources) assert.ok(!/https?:\/\/(?!x\.com|bsky\.app|www\.reddit\.com|www\.instagram\.com|invalid\.example)[a-z0-9.-]+\.[a-z]{2,}/i.test(s.code.replace(/'http:\/\/www\.w3\.org\/2000\/svg'/, '')), `${s.rel}: URL externa fija en el código`);
});

test('el token no se escribe en la página ni viaja al content script', () => {
  for (const s of sources.filter((x) => /^src\/(content|extractors)\//.test(x.rel))) {
    assert.ok(!/token|Authorization|Bearer/i.test(s.code), `${s.rel} no tiene que conocer el token`);
  }
});

test('api.js: cada método y ruta que usa existe en los routers reales del backend', (t) => {
  const routers = join(ROOT, '..', 'backend', 'aleph', 'api', 'routers');
  let files;
  try {
    files = {
      auth: readFileSync(join(routers, 'auth.py'), 'utf8'), cases: readFileSync(join(routers, 'cases.py'), 'utf8'),
      lens: readFileSync(join(routers, 'lens.py'), 'utf8'), cti: readFileSync(join(routers, 'cti.py'), 'utf8'),
    };
  } catch {
    t.skip('no está el backend al lado (extensión distribuida sola)');
    return;
  }
  // Prefijo de cada router (make_router(prefix=...), montado en /api) y variable con la que decora sus rutas.
  const mounts = [
    [files.auth, 'auth', '/api/auth'], [files.cases, 'router', '/api/cases'],
    [files.lens, 'router', '/api/cases/{case_id}'], [files.cti, 'router', '/api/cases/{case_id}'],
  ];
  const real = new Set();
  for (const [py, variable, prefix] of mounts) {
    for (const m of py.matchAll(new RegExp(`@${variable}\\.(get|post|patch|delete)\\(\\s*"([^"]*)"`, 'g'))) {
      real.add(`${m[1].toUpperCase()} ${`${prefix}${m[2]}`.replace(/\{[^}]*\}/g, '{}')}`);
    }
  }
  const api = readFileSync(join(ROOT, 'src/lib/api.js'), 'utf8');
  const used = [...api.matchAll(/this\.request\(\s*'(GET|POST|PATCH|DELETE)',\s*[`'"]([^`'"]+)[`'"]/g)]
    .map((m) => `${m[1]} ${m[2].replace(/\$\{[^}]*\}/g, '{}').replace(/\?.*$/, '')}`);
  assert.ok(used.length >= 9, `se esperaban al menos 9 llamadas en api.js y hay ${used.length}`);
  for (const route of used) assert.ok(real.has(route), `${route} no existe en backend/aleph/api/routers`);
});

test('el espejo JS del contrato coincide con core/schemas.py', (t) => {
  let py;
  try {
    py = readFileSync(join(ROOT, '..', 'backend', 'aleph', 'core', 'schemas.py'), 'utf8');
  } catch {
    t.skip('no está el backend al lado (extensión distribuida sola)');
    return;
  }
  const tuple = (name) => [...py.match(new RegExp(`${name} = \\(([\\s\\S]*?)\\)\\n`))[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.deepEqual(DEFAULT_SECTIONS, tuple('DEFAULT_SECTIONS'));
  assert.deepEqual(ENTITY_TYPES, tuple('ENTITY_TYPES'));
  const fields = (cls) => [...py.match(new RegExp(`class ${cls}\\(BaseModel\\):([\\s\\S]*?)(?=\\nclass |\\n# ---|\\n[A-Z_]+ = |$)`))[1].matchAll(/^ {4}([a-z_]+):/gm)].map((m) => m[1]).sort();
  const schema = readFileSync(join(ROOT, 'src/lib/schema.js'), 'utf8');
  const shape = (name) => [...schema.match(new RegExp(`const ${name} = \\{([\\s\\S]*?)\\n\\};`))[1].matchAll(/^ {2}([a-z_]+):/gm)].map((m) => m[1]).sort();
  assert.deepEqual(shape('POST_SHAPE'), fields('PostRecord'));
  assert.deepEqual(shape('ACCOUNT_SHAPE'), fields('AccountRecord'));
  assert.deepEqual(shape('BATCH_SHAPE'), fields('CaptureBatch'));
  assert.deepEqual(shape('CHUNK_SHAPE'), fields('Datachunk'));
  assert.deepEqual(shape('FINDING_SHAPE'), fields('FindingCreate'));
});
