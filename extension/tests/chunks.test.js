import test from 'node:test';
import assert from 'node:assert/strict';
import { detectChunks, makeChunk, makeManualChunk, buildFinding, findLiteral, mergeMatches } from '../src/lib/chunks.js';
import { validateDatachunk, validateFindingCreate, ENTITY_TYPES } from '../src/lib/schema.js';

const kinds = (text) => detectChunks(text).map((c) => `${c.kind}:${c.value}`);

test('detector: cada coincidencia marca exactamente el fragmento (start/end/quote)', () => {
  const text = 'Escribile a Ventas@Sitio-Demo.com.ar o mirá https://sitio-demo.com.ar/x?y=1. Gracias.';
  for (const c of detectChunks(text)) assert.equal(text.slice(c.start, c.end), c.quote);
  assert.deepEqual(kinds(text), ['email:ventas@sitio-demo.com.ar', 'url:https://sitio-demo.com.ar/x?y=1']);
});

test('detector: todos los tipos son ENTITY_TYPES', () => {
  const text = 'a@b.com 203.0.113.9 CVE-2021-44228 @alguien sitio.com.ar 12/10/2026 +54 11 5555-0000 Av. Santa Fe 2100';
  for (const c of detectChunks(text)) assert.ok(ENTITY_TYPES.includes(c.kind), c.kind);
});

test('detector: handles, emails y dominios no se pisan entre sí', () => {
  assert.deepEqual(kinds('contacto: pepe@ejemplo.org'), ['email:pepe@ejemplo.org']);
  assert.deepEqual(kinds('seguí a @Pepe_Demo y a @otra.cuenta.'), ['account:pepe_demo', 'account:otra.cuenta']);
  assert.deepEqual(kinds('entrá a tienda-demo.com.ar hoy'), ['domain:tienda-demo.com.ar']);
  assert.deepEqual(kinds('https://tienda-demo.com.ar/a'), ['url:https://tienda-demo.com.ar/a']);
  assert.deepEqual(kinds('sitio oculto abcdefghij234567.onion'), ['domain:abcdefghij234567.onion']);
});

test('detector: teléfonos', () => {
  assert.deepEqual(kinds('llamá al +54 9 341 555-0123'), ['phone:+5493415550123']);
  assert.deepEqual(kinds('tel (011) 4555-0123'), ['phone:01145550123']);
  assert.deepEqual(kinds('wsp 341 555-0123'), ['phone:3415550123']);
  assert.deepEqual(kinds('+1 (202) 555-0142'), ['phone:+12025550142']);
});

test('detector: fechas y horas normalizadas', () => {
  assert.deepEqual(kinds('el 05/10/2026'), ['event:2026-10-05']);
  assert.deepEqual(kinds('fecha 2026-10-05'), ['event:2026-10-05']);
  assert.deepEqual(kinds('el 7 de octubre de 2026'), ['event:2026-10-07']);
  assert.deepEqual(kinds('el 7 de octubre'), ['event:--10-07']);
  assert.deepEqual(kinds('October 7, 2026'), ['event:2026-10-07']);
  assert.deepEqual(kinds('a las 21:30 hs'), ['event:21:30']);
  assert.deepEqual(kinds('a las 9:05 pm'), ['event:21:05']);
  assert.deepEqual(kinds('2026-10-05T14:03:00Z'), ['event:2026-10-05T14:03:00.000Z']);
});

test('detector: lugares con forma reconocible', () => {
  assert.deepEqual(kinds('nos vemos en Av. Corrientes 1234, CABA'), ['location:Av. Corrientes 1234']);
  assert.deepEqual(kinds('vive en Calle Falsa 123'), ['location:Calle Falsa 123']);
  assert.deepEqual(kinds('en Ruta 9 km 278'), ['location:Ruta 9 km 278']);
  assert.deepEqual(kinds('coordenadas -32.9468, -60.6393'), ['location:-32.9468,-60.6393']);
  assert.deepEqual(kinds('at 742 Evergreen Terrace Ave.'), ['location:742 Evergreen Terrace Ave']);
});

// [kind, value, platform] de cada coincidencia: "platform" vacío = la red de la página.
const accounts = (text) => detectChunks(text).map((c) => [c.kind, c.value, c.platform || '']);

test('detector: usuarios de otras plataformas (el handle va solo y la red en platform)', () => {
  assert.deepEqual(accounts('mi ig es pepe.demo'), [['account', 'pepe.demo', 'instagram']]);
  assert.deepEqual(accounts('Discord: pepe#0042'), [['account', 'pepe#0042', 'discord']]);
  assert.deepEqual(accounts('tg: @pepe_demo'), [['account', 'pepe_demo', 'telegram']]);
  assert.deepEqual(accounts('telegram = pepedemo'), [['account', 'pepedemo', 'telegram']]);
  assert.deepEqual(accounts('seguí a @Pepe_Demo'), [['account', 'pepe_demo', '']], 'una mención común no trae red');
  const c = detectChunks('mi ig es pepe.demo')[0];
  assert.equal(c.quote, 'pepe.demo', 'se resalta solo el usuario, no la frase');
});

test('datachunk de cuenta de otra red: value es el handle y platform la red (contrato FindingCreate)', () => {
  const [hit] = detectChunks('mi ig es pepe.demo');
  const chunk = makeChunk({ kind: hit.kind, value: hit.value, quote: hit.quote, platform: hit.platform }, { url: 'https://x.com/home', platform: 'x' });
  assert.equal(chunk.value, 'pepe.demo', 'nada de "instagram:" dentro del handle');
  assert.equal(chunk.platform, 'instagram');
  assert.deepEqual(validateDatachunk(chunk), []);
  const plain = makeChunk({ kind: 'account', value: 'pepe_demo' }, { url: 'https://x.com/home', platform: 'x' });
  assert.equal(plain.platform, 'x', 'sin red propia, vale la de la página');
});

test('detector: indicadores técnicos (hash, IP, billetera, CVE)', () => {
  assert.deepEqual(kinds('md5 d41d8cd98f00b204e9800998ecf8427e'), ['hash:d41d8cd98f00b204e9800998ecf8427e']);
  assert.deepEqual(kinds('ip 198.51.100.7:8080'), ['ip:198.51.100.7']);
  assert.deepEqual(kinds('ipv6 2001:db8::8a2e:370:7334'), ['ip:2001:db8::8a2e:370:7334']);
  assert.deepEqual(kinds('eth 0x52908400098527886E0F7030069857D2E4169EE7'), ['wallet:0x52908400098527886E0F7030069857D2E4169EE7']);
  assert.deepEqual(kinds('btc bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq'), ['wallet:bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq']);
  assert.deepEqual(kinds('btc 1BoatSLRHtKNngkdXEeobR76b53LETtpyT'), ['wallet:1BoatSLRHtKNngkdXEeobR76b53LETtpyT']);
  assert.deepEqual(kinds('ver cve-2021-44228'), ['vulnerability:CVE-2021-44228']);
});

test('detector: cuentas bancarias (CBU/CVU) y alias', () => {
  // CBU inventado con dígitos verificadores válidos.
  assert.deepEqual(kinds('transferí al 2850590940090418135201'), ['bank_account:2850590940090418135201']);
  assert.deepEqual(kinds('CVU: 0000003100010000000009'), ['bank_account:0000003100010000000009']);
  assert.deepEqual(kinds('número 1234567890123456789012 cualquiera'), [], '22 dígitos sin verificador válido ni contexto');
  assert.deepEqual(kinds('mi alias es perro.gato.sol'), ['alias:perro.gato.sol']);
  assert.deepEqual(kinds('Alias MP: Lau.Demo-01'), ['alias:lau.demo-01']);
  assert.deepEqual(kinds('usa un alias falso en redes'), []);
  assert.deepEqual(kinds('alias conocido como pepe'), []);
});

test('detector: falsos positivos que NO tienen que resaltarse', () => {
  const clean = [
    'la versión 1.2.3.4 ya salió', 'v10.0.19045.3 build', 'abrí informe.txt y script.py', 'uso Node.js y README.md',
    'terminó la frase.Luego siguió', 'tuvo 1.234.567 visitas', 'ganaron 3:2 en el clásico', 'cuesta $ 1.500,50',
    'x es una red social', 'discord es genial', 'el id es 1234567890123456', 'código 0000000000000000000000000000000000000000',
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'el 31/02/2026 no existe', 'ip 999.1.1.1 inválida', 'arroba suelta @ y nada más',
    'el 2026-13-45', 'ratio 16:9', 'mail roto pepe@ sin dominio', 'e.g. esto', 'a las 25:99',
  ];
  for (const text of clean) assert.deepEqual(kinds(text), [], `"${text}"`);
});

test('detector: texto vacío, corto o enorme no rompe', () => {
  assert.deepEqual(detectChunks(''), []);
  assert.deepEqual(detectChunks(null), []);
  assert.deepEqual(detectChunks('ab'), []);
  const big = 'a@b.com '.repeat(5000);
  assert.ok(detectChunks(big, { maxMatches: 50 }).length <= 50);
});

test('literales (FUNES / manual): se ubican por texto exacto y ganan si se superponen', () => {
  const text = 'Reunión con Laura Ficticia en laura.com.ar';
  const literal = findLiteral(text, [{ kind: 'person', value: 'Laura Ficticia', quote: 'Laura Ficticia', detected_by: 'funes' }]);
  assert.equal(literal.length, 1);
  assert.equal(text.slice(literal[0].start, literal[0].end), 'Laura Ficticia');
  const merged = mergeMatches(detectChunks(text), literal);
  assert.deepEqual(merged.map((m) => m.kind), ['person', 'domain']);
});

test('Datachunk: cumple el esquema con todos los campos', () => {
  const chunk = makeChunk(
    { kind: 'email', value: 'a@b.com', quote: 'A@B.com', context: 'escribime a A@B.com', author_handle: '@lau_ficticia', post_id: '184' },
    { url: 'https://x.com/lau_ficticia/status/184', title: 'X', platform: 'x' },
  );
  assert.deepEqual(validateDatachunk(chunk), []);
  assert.equal(chunk.author_handle, 'lau_ficticia');
  assert.equal(chunk.detected_by, 'rule');
  assert.deepEqual(Object.keys(chunk).sort(), ['author_handle', 'captured_at', 'context', 'detected_by', 'kind', 'page_title', 'page_url', 'platform', 'post_id', 'quote', 'value']);
});

test('Datachunk manual: kind "text" y detected_by "manual"', () => {
  const chunk = makeManualChunk('  una frase   seleccionada\npor el analista ', { url: 'https://foro.example/h', title: 'Foro', platform: 'generic' });
  assert.equal(chunk.kind, 'text');
  assert.equal(chunk.detected_by, 'manual');
  assert.equal(chunk.value, 'una frase seleccionada\npor el analista');
  assert.deepEqual(validateDatachunk(chunk), []);
  assert.equal(makeManualChunk('   ', { url: 'https://foro.example/h' }), null);
});

test('FindingCreate: armado contra el esquema (inciso, sin clasificar y entidad)', () => {
  const chunk = makeChunk({ kind: 'domain', value: 'sitio-demo.com.ar' }, { url: 'https://foro.example/h', platform: 'generic' });
  const inSection = buildFinding({ chunk, sectionId: 12 });
  assert.deepEqual(validateFindingCreate(inSection), []);
  assert.deepEqual(Object.keys(inSection).sort(), ['attach_to_entity_id', 'chunk', 'note', 'section_id']);
  assert.equal(inSection.section_id, 12);
  assert.equal(inSection.attach_to_entity_id, null);

  const unclassified = buildFinding({ chunk });
  assert.equal(unclassified.section_id, null, 'None = "Sin clasificar"');
  assert.deepEqual(validateFindingCreate(unclassified), []);

  const onEntity = buildFinding({ chunk, entityId: 101, note: 'visto en el foro' });
  assert.equal(onEntity.attach_to_entity_id, 101);
  assert.equal(onEntity.note, 'visto en el foro');
  assert.deepEqual(validateFindingCreate(onEntity), []);

  // Un id que no es entero (por ejemplo un inciso predeterminado sin crear) no viaja como id.
  assert.equal(buildFinding({ chunk, sectionId: 'Cuentas' }).section_id, null);
});

test('FindingCreate: el validador detecta lo que el backend rechazaría', () => {
  const chunk = makeChunk({ kind: 'domain', value: 'sitio-demo.com.ar' }, { url: 'https://foro.example/h' });
  assert.ok(validateFindingCreate({ ...buildFinding({ chunk }), section_id: '3' }).length > 0);
  assert.ok(validateFindingCreate(buildFinding({ chunk: { ...chunk, detected_by: 'ia' } })).length === 0, 'makeChunk corrige detected_by inválido');
  assert.ok(validateDatachunk({ ...chunk, kind: 'cosa' }).length > 0);
  assert.ok(validateDatachunk({ ...chunk, captured_at: '2026-10-07 12:00' }).length > 0);
  assert.ok(validateDatachunk({ ...chunk, page_url: '' }).length > 0);
});
