// Cliente del servidor Aleph. Es el ÚNICO lugar de la extensión que hace pedidos de red,
// y solo los hace contra la URL que configuró el usuario. Nunca envía cookies (credentials: "omit").

export class ApiError extends Error {
  constructor(message, { status = 0, retriable = false, auth = false } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.retriable = retriable;
    this.auth = auth;
  }
}

/** "http://host:8100/", "http://host:8100/api" -> "http://host:8100". Devuelve "" si no es válida. */
export function normalizeServerUrl(value) {
  let s = String(value ?? '').trim();
  if (!s) return '';
  if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = `http://${s}`;
  try {
    const u = new URL(s);
    if (!/^https?:$/.test(u.protocol)) return '';
    const path = u.pathname.replace(/\/+$/, '').replace(/\/api$/i, '');
    return `${u.origin}${path}`;
  } catch {
    return '';
  }
}

const RETRIABLE = new Set([408, 425, 429, 500, 502, 503, 504]);
// Límite de texto por pedido de captures/chunks (MAX_TEXT_CHARS en backend/aleph/api/routers/cti.py).
const CHUNKS_TEXT_MAX = 200000;

export class AlephClient {
  constructor({ baseUrl, token = '', fetchImpl = globalThis.fetch.bind(globalThis), timeoutMs = 15000 }) {
    this.baseUrl = normalizeServerUrl(baseUrl);
    this.token = token;
    this.fetch = fetchImpl;
    this.timeoutMs = timeoutMs;
  }

  async request(method, path, body, { auth = true } = {}) {
    if (!this.baseUrl) throw new ApiError('Falta configurar la URL del servidor Aleph.');
    if (auth && !this.token) throw new ApiError('Falta iniciar sesión en el servidor Aleph.', { status: 401, auth: true });
    const headers = { Accept: 'application/json' };
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (auth) headers.Authorization = `Bearer ${this.token}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    let response;
    try {
      response = await this.fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: 'omit',
        cache: 'no-store',
        signal: controller.signal,
      });
    } catch (err) {
      const why = err && err.name === 'AbortError' ? 'el servidor no respondió a tiempo' : 'no se pudo conectar';
      throw new ApiError(`Sin conexión con Aleph: ${why}.`, { retriable: true });
    } finally {
      clearTimeout(timer);
    }
    let data = null;
    const raw = await response.text().catch(() => '');
    if (raw) {
      try {
        data = JSON.parse(raw);
      } catch {
        data = null;
      }
    }
    if (!response.ok) {
      const detail = data && typeof data.detail === 'string' ? data.detail : `Error ${response.status} del servidor.`;
      throw new ApiError(detail, {
        status: response.status,
        retriable: RETRIABLE.has(response.status),
        auth: response.status === 401,
      });
    }
    return data;
  }

  /** POST /api/auth/login -> { access_token, token_type, expires_in, user } */
  async login(username, password) {
    const data = await this.request('POST', '/api/auth/login', { username, password }, { auth: false });
    const token = data && (data.access_token || data.token);
    if (!token) throw new ApiError('El servidor no devolvió un token.');
    return { token, expiresIn: Number(data.expires_in) || 0, user: data.user || null };
  }

  me() {
    return this.request('GET', '/api/auth/me');
  }

  /** GET /api/cases: acepta una lista simple o el sobre paginado { items, total, ... }. */
  async listCases() {
    const data = await this.request('GET', '/api/cases?limit=200');
    const items = Array.isArray(data) ? data : (data && Array.isArray(data.items) ? data.items : []);
    return items
      .filter((c) => c && Number.isInteger(c.id))
      .map((c) => ({ id: c.id, name: String(c.name || `Caso ${c.id}`), tlp: String(c.tlp || ''), status: String(c.status || '') }));
  }

  sendCapture(caseId, batch) {
    return this.request('POST', `/api/cases/${encodeURIComponent(caseId)}/captures`, batch);
  }

  async lookup(caseId, platform, handles) {
    const data = await this.request('POST', `/api/cases/${encodeURIComponent(caseId)}/captures/lookup`, { platform, handles });
    return data && typeof data.handles === 'object' && data.handles ? data.handles : {};
  }

  /**
   * POST /api/cases/{id}/captures/chunks: datachunks de un texto (IOCs y reglas de FUNES, sin LLM y sin guardar
   * nada). El endpoint recibe un solo `text` (ChunksIn), así que acá se unen los textos del pedido.
   * Devuelve list[Datachunk]. Sin texto no hace ningún pedido.
   */
  async aiChunks(caseId, { texts = [], page_url = '', page_title = '', platform = 'generic' } = {}) {
    const text = (Array.isArray(texts) ? texts : []).map((t) => String(t)).join('\n\n').slice(0, CHUNKS_TEXT_MAX);
    if (!text.trim()) return [];
    const body = {
      text,
      page_url: String(page_url || '').slice(0, 2000),
      page_title: String(page_title || '').slice(0, 500),
      platform: String(platform || 'generic').slice(0, 100),
    };
    const data = await this.request('POST', `/api/cases/${encodeURIComponent(caseId)}/captures/chunks`, body);
    return Array.isArray(data) ? data : (data && Array.isArray(data.chunks) ? data.chunks : []);
  }

  async listSections(caseId) {
    const data = await this.request('GET', `/api/cases/${encodeURIComponent(caseId)}/sections`);
    const items = Array.isArray(data) ? data : (data && Array.isArray(data.items) ? data.items : []);
    return items.filter((s) => s && Number.isInteger(s.id)).map((s) => ({
      id: s.id, name: String(s.name || ''), position: Number(s.position) || 0, findings: Number(s.findings) || 0,
    }));
  }

  createSection(caseId, name) {
    return this.request('POST', `/api/cases/${encodeURIComponent(caseId)}/sections`, { name });
  }

  createFinding(caseId, finding) {
    return this.request('POST', `/api/cases/${encodeURIComponent(caseId)}/findings`, finding);
  }
}
