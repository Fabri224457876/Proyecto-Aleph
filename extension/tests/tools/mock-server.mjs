// Servidor Aleph simulado: implementa el contrato que usa la extensión, para tests y para probar a
// mano la extensión sin backend.   Uso: npm run mock-server   (escucha en http://127.0.0.1:8199)
// Usuario de prueba: analista / la clave que figura abajo en MOCK_LOGIN. Solo datos sintéticos.

import http from 'node:http';
import { fileURLToPath } from 'node:url';
import { validateCaptureBatch, validateFindingCreate, validateLookupRequest } from '../../src/lib/schema.js';

export const MOCK_LOGIN = { username: 'analista', secret: 'demo-lens-2026' };
export const MOCK_TOKEN = 'mock-token-aleph-lens';

export function createMockServer({ withSections = false, withAiChunks = true } = {}) {
  const db = {
    cases: [
      { id: 1, name: 'Operación Demo', description: '', legal_basis: 'Ejercicio con datos sintéticos', tlp: 'amber', status: 'open', created_by: 1, created_at: '2026-10-01T12:00:00Z' },
      { id: 2, name: 'Caso cerrado de ejemplo', description: '', legal_basis: 'Ejercicio', tlp: 'green', status: 'closed', created_by: 1, created_at: '2026-09-01T12:00:00Z' },
    ],
    captures: [], // { caseId, batch }
    findings: [], // { caseId, finding }
    sections: withSections ? [{ id: 10, name: 'Identidad', position: 0, findings: 0 }, { id: 11, name: 'Cuentas', position: 1, findings: 0 }] : [],
    // Lo que "el caso ya sabe": dos cuentas con una hipótesis MENARD entre ellas.
    // review_status con el vocabulario real del backend: pending | confirmed | rejected (AccountLink).
    known: {
      lau_ficticia: { known: true, entity_id: 101, posts_captured: 37, links: [{ link_id: 1, other: 'cuenta_demo_3', platform: 'x', score: 0.87, review_status: 'pending' }], relations: [{ type: 'usa_email', label: 'lau@ejemplo.com.ar', entity_id: 205 }], note: 'Cuenta semilla del caso.' },
      cuenta_demo_3: { known: true, entity_id: 102, posts_captured: 12, links: [{ link_id: 1, other: 'lau_ficticia', platform: 'x', score: 0.87, review_status: 'pending' }], relations: [], note: '' },
    },
    failNext: 0, // cantidad de respuestas 503 a devolver antes de volver a funcionar
    requests: [],
    nextSectionId: 20,
  };

  const send = (res, status, body) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
  };

  const server = http.createServer((req, res) => {
    let raw = '';
    req.on('data', (c) => { raw += c; });
    req.on('end', () => {
      const url = new URL(req.url, 'http://mock');
      let body = null;
      try {
        body = raw ? JSON.parse(raw) : null;
      } catch {
        return send(res, 422, { detail: 'JSON inválido.' });
      }
      db.requests.push({ method: req.method, path: url.pathname, auth: req.headers.authorization || '', cookie: req.headers.cookie || '', body });

      if (req.method === 'POST' && url.pathname === '/api/auth/login') {
        if (body && body.username === MOCK_LOGIN.username && body.password === MOCK_LOGIN.secret) {
          return send(res, 200, { access_token: MOCK_TOKEN, token_type: 'bearer', expires_in: 3600, user: { id: 1, username: 'analista', role: 'analyst', active: true, created_at: '2026-10-01T12:00:00Z' } });
        }
        return send(res, 401, { detail: 'Usuario o contraseña incorrectos.' });
      }
      if (req.headers.authorization !== `Bearer ${MOCK_TOKEN}`) return send(res, 401, { detail: 'No autenticado.' });
      if (db.failNext > 0) { db.failNext -= 1; return send(res, 503, { detail: 'Servicio no disponible.' }); }

      if (req.method === 'GET' && url.pathname === '/api/auth/me') return send(res, 200, { id: 1, username: 'analista', role: 'analyst', active: true });
      if (req.method === 'GET' && url.pathname === '/api/cases') return send(res, 200, { items: db.cases, total: db.cases.length, limit: 200, offset: 0 });

      const m = url.pathname.match(/^\/api\/cases\/(\d+)\/(captures|captures\/lookup|captures\/chunks|sections|findings)$/);
      if (!m) return send(res, 404, { detail: 'No encontrado.' });
      const caseId = Number(m[1]);
      if (!db.cases.some((c) => c.id === caseId)) return send(res, 404, { detail: 'Caso inexistente.' });
      const what = m[2];

      if (what === 'captures' && req.method === 'POST') {
        const errors = validateCaptureBatch(body);
        if (errors.length) return send(res, 422, { detail: 'Lote inválido.', errors });
        db.captures.push({ caseId, batch: body });
        return send(res, 201, { accounts: body.profiles.length, posts: body.profiles.reduce((n, p) => n + p.posts.length, 0) });
      }
      if (what === 'captures/lookup' && req.method === 'POST') {
        if (validateLookupRequest(body).length) return send(res, 422, { detail: 'Consulta inválida.' });
        const handles = {};
        for (const h of body.handles) handles[h] = db.known[h.toLowerCase()] || { known: false, entity_id: null, posts_captured: 0, links: [], relations: [], note: '' };
        return send(res, 200, { handles });
      }
      if (what === 'captures/chunks' && req.method === 'POST') {
        if (!withAiChunks) return send(res, 404, { detail: 'No encontrado.' });
        // Contrato real (ChunksIn): un solo "text" obligatorio. Respuesta: lista de Datachunk.
        if (!body || typeof body.text !== 'string' || !body.text.trim()) {
          return send(res, 422, { detail: 'Datos inválidos. body.text: Campo obligatorio.' });
        }
        // Simulado como regla de FUNES: "Nombre Apellido" con mayúsculas iniciales.
        const out = [];
        for (const mm of body.text.matchAll(/\b([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+ [A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)\b/g)) {
          out.push({ kind: 'person', value: mm[1], quote: mm[1], context: body.text.slice(Math.max(0, mm.index - 80), mm.index + 120), page_url: body.page_url || '', page_title: body.page_title || '', platform: body.platform || 'generic', author_handle: '', post_id: '', detected_by: 'funes', captured_at: new Date().toISOString() });
        }
        return send(res, 200, out);
      }
      if (what === 'sections' && req.method === 'GET') return send(res, 200, db.sections);
      if (what === 'sections' && req.method === 'POST') {
        if (!body || typeof body.name !== 'string' || !body.name.trim()) return send(res, 422, { detail: 'Falta el nombre.' });
        // Igual que la API real: sin distinguir mayúsculas, un nombre repetido es 409.
        if (db.sections.some((s) => s.name.toLowerCase() === body.name.trim().toLowerCase())) {
          return send(res, 409, { detail: 'Ya existe un inciso con ese nombre en el caso.' });
        }
        const section = { id: db.nextSectionId++, name: body.name.trim(), position: db.sections.length, findings: 0 };
        db.sections.push(section);
        return send(res, 201, section);
      }
      if (what === 'findings' && req.method === 'POST') {
        const errors = validateFindingCreate(body);
        if (errors.length) return send(res, 422, { detail: 'Hallazgo inválido.', errors });
        db.findings.push({ caseId, finding: body });
        const section = db.sections.find((s) => s.id === body.section_id);
        if (section) section.findings += 1;
        return send(res, 201, { id: db.findings.length });
      }
      return send(res, 405, { detail: 'Método no permitido.' });
    });
  });

  return {
    db,
    server,
    listen: (port = 0) => new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`))),
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const mock = createMockServer({ withSections: false });
  const base = await mock.listen(Number(process.env.PORT) || 8199);
  console.log(`Aleph simulado en ${base}`);
  console.log(`Usuario: ${MOCK_LOGIN.username}   Clave: ${MOCK_LOGIN.secret}`);
  console.log('Cuentas "conocidas" con hipótesis MENARD: lau_ficticia, cuenta_demo_3');
  mock.server.on('request', (req) => console.log(new Date().toISOString(), req.method, req.url));
}
