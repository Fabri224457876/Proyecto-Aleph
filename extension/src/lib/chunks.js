// Detector de datachunks por reglas (detected_by: "rule") y armado de Datachunk / FindingCreate.
// Funciones puras sobre texto: no tocan el DOM.

import { cleanText, clip, trimUrl, toIsoUtc } from './normalize.js';

// TLD admitidos para detectar dominios "sueltos". Lista cerrada a propósito: evita que
// "archivo.txt", "Node.js", "script.py" o "fin.Luego" pasen por dominios.
const TLDS = new Set((
  'com net org edu gov gob mil int info biz io co me tv app dev xyz online site store tech ai cloud ' +
  'ar uy cl br mx es pe ve bo ec py_ co us uk ca au de fr it nl ch se no pt ru cn jp in eu ' +
  'onion cc to ly gg club top live news blog shop link social ws su cat pro mobi name tk ml ga cf icu vip ' +
  'win network world fm im is page media agency digital email finance money bet lat'
).split(/\s+/).filter((t) => !t.endsWith('_')));

const PLATFORM_ALIASES = {
  ig: 'instagram', insta: 'instagram', instagram: 'instagram',
  tw: 'x', twitter: 'x', x: 'x',
  tg: 'telegram', telegram: 'telegram',
  discord: 'discord', ds: 'discord', dc: 'discord',
  tiktok: 'tiktok', tt: 'tiktok',
  snap: 'snapchat', snapchat: 'snapchat',
  kick: 'kick', twitch: 'twitch', steam: 'steam', github: 'github', gh: 'github',
  fb: 'facebook', facebook: 'facebook', face: 'facebook',
  onlyfans: 'onlyfans', bluesky: 'bluesky', bsky: 'bluesky', reddit: 'reddit',
  youtube: 'youtube', yt: 'youtube', linkedin: 'linkedin', threads: 'threads', mastodon: 'mastodon',
  signal: 'signal', skype: 'skype', psn: 'psn', xbox: 'xbox', roblox: 'roblox', epic: 'epic',
};
// Alias largos: alcanzan con "mi <plataforma> es <usuario>". Los cortos ("x", "tt") exigen ":" o "=".
const LONG_ALIASES = Object.keys(PLATFORM_ALIASES).filter((a) => a.length >= 2 && !['x', 'tw', 'tt', 'ds', 'dc', 'gh', 'yt', 'fb'].includes(a));
const ALL_ALIASES = Object.keys(PLATFORM_ALIASES).sort((a, b) => b.length - a.length);

const MONTHS = {
  enero: 1, febrero: 2, marzo: 3, abril: 4, mayo: 5, junio: 6, julio: 7, agosto: 8, septiembre: 9, setiembre: 9,
  octubre: 10, noviembre: 11, diciembre: 12,
  january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7, august: 8, september: 9, october: 10,
  november: 11, december: 12,
};
const MONTH_ALT = Object.keys(MONTHS).join('|');

const pad = (n) => String(n).padStart(2, '0');

function isoDate(y, m, d) {
  y = Number(y); m = Number(m); d = Number(d);
  if (y < 100) y += y < 70 ? 2000 : 1900;
  if (y < 1900 || y > 2100 || m < 1 || m > 12 || d < 1 || d > 31) return '';
  const date = new Date(Date.UTC(y, m - 1, d));
  if (date.getUTCMonth() !== m - 1) return '';
  return `${y}-${pad(m)}-${pad(d)}`;
}

function digits(s) {
  return s.replace(/\D/g, '');
}

const mixed = (s) => /[a-z]/.test(s) && /[A-Z]/.test(s) && /\d/.test(s);

// Dígitos verificadores de un CBU/CVU argentino (22 dígitos: bloque de 8 + bloque de 14).
function validCbu(d) {
  if (!/^\d{22}$/.test(d)) return false;
  const check = (block, weights) => {
    const sum = weights.reduce((acc, w, i) => acc + w * Number(block[i]), 0);
    return (10 - (sum % 10)) % 10 === Number(block[weights.length]);
  };
  return check(d.slice(0, 8), [7, 1, 3, 9, 7, 1, 3]) && check(d.slice(8), [3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3]);
}

// Cada regla: { kind, priority, re, map(match) -> { value, quote?, offset? } | null }.
// "offset"/"quote" permiten resaltar solo una parte del match (por ejemplo el usuario en "ig: pepe").
const RULES = [
  {
    name: 'url', kind: 'url', priority: 100,
    re: /\bhttps?:\/\/[^\s<>"'`]+/gi,
    map: (m) => {
      const quote = trimUrl(m[0]);
      return quote.length > 10 ? { value: quote, quote } : null;
    },
  },
  {
    name: 'email', kind: 'email', priority: 95,
    re: /(?<![\w.+-])[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,24}(?![\w-])/g,
    map: (m) => ({ value: m[0].toLowerCase() }),
  },
  {
    name: 'cve', kind: 'vulnerability', priority: 90,
    re: /\bCVE-(?:19|20)\d{2}-\d{4,7}\b/gi,
    map: (m) => ({ value: m[0].toUpperCase() }),
  },
  {
    name: 'eth', kind: 'wallet', priority: 88,
    re: /\b0x[a-fA-F0-9]{40}\b/g,
    map: (m) => ({ value: m[0] }),
  },
  {
    name: 'btc-bech32', kind: 'wallet', priority: 88,
    re: /\bbc1[ac-hj-np-z02-9]{25,62}\b/g,
    map: (m) => ({ value: m[0] }),
  },
  {
    name: 'btc-legacy', kind: 'wallet', priority: 87,
    re: /(?<![A-Za-z0-9])[13][a-km-zA-HJ-NP-Z1-9]{25,34}(?![A-Za-z0-9])/g,
    map: (m) => (mixed(m[0]) ? { value: m[0] } : null),
  },
  {
    name: 'cbu', kind: 'bank_account', priority: 86,
    re: /(?<![\w.-])\d{22}(?![\w-]|\.\d)/g,
    // Vale si los dígitos verificadores cierran, o si el texto dice que es un CBU/CVU.
    map: (m, text) => (validCbu(m[0]) || /\b(cbu|cvu)\b[^\n]{0,12}$/i.test(text.slice(Math.max(0, m.index - 24), m.index))
      ? { value: m[0] } : null),
  },
  {
    name: 'bank-alias', kind: 'alias', priority: 75,
    // Alias de CBU/CVU: 6 a 20 caracteres (letras, números, punto, guion). Se exige la palabra "alias"
    // y, si no hay ":" o "=", la forma típica palabra.palabra.palabra.
    re: /(?<![\p{L}\p{N}_])alias(?:\s+(?:cbu|cvu|mp|bancario|de\s+mercado\s*pago))?\s*(?:(:|=)\s*|\s+(?:es\s+)?)([A-Za-z0-9][A-Za-z0-9.-]{4,18}[A-Za-z0-9])(?![\p{L}\p{N}_-]|\.[\p{L}\p{N}])/giu,
    map: (m) => {
      const alias = m[2];
      if (!m[1] && (alias.match(/\./g) || []).length < 2) return null;
      if (!/[a-z]/i.test(alias)) return null;
      return { value: alias.toLowerCase(), quote: alias, offset: m[0].length - alias.length };
    },
  },
  {
    name: 'hash', kind: 'hash', priority: 85,
    re: /(?<![A-Za-z0-9])(?:[a-fA-F0-9]{64}|[a-fA-F0-9]{40}|[a-fA-F0-9]{32})(?![A-Za-z0-9])/g,
    // Un hash real mezcla letras y dígitos; "0000…0" o "aaaa…a" no lo son.
    map: (m) => (/[a-f]/i.test(m[0]) && /\d/.test(m[0]) && new Set(m[0].toLowerCase()).size >= 6
      ? { value: m[0].toLowerCase() } : null),
  },
  {
    name: 'ipv4', kind: 'ip', priority: 80,
    re: /(?<![\w.])(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(?::\d{1,5})?(?![\w]|\.\d)/g,
    map: (m, text) => {
      // "v1.2.3.4" o "versión 1.2.3.4" son números de versión, no direcciones.
      const before = text.slice(Math.max(0, m.index - 12), m.index).toLowerCase();
      if (/(v|ver\.?|versi[oó]n|version|build|release)\s*$/.test(before)) return null;
      return { value: m[0].replace(/:\d+$/, ''), quote: m[0] };
    },
  },
  {
    name: 'ipv6', kind: 'ip', priority: 80,
    re: /(?<![\w:])(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}(?![\w:])|(?<![\w:])(?:[A-Fa-f0-9]{1,4}:){2,6}:(?:[A-Fa-f0-9]{1,4}:){0,5}[A-Fa-f0-9]{1,4}(?![\w:])/g,
    map: (m) => ({ value: m[0].toLowerCase() }),
  },
  {
    name: 'coords', kind: 'location', priority: 78,
    re: /(?<![\w.,-])-?\d{1,2}\.\d{3,8}\s*,\s*-?\d{1,3}\.\d{3,8}(?![\w.])/g,
    map: (m) => {
      const [lat, lon] = m[0].split(',').map((p) => Number(p.trim()));
      return Math.abs(lat) <= 90 && Math.abs(lon) <= 180 ? { value: `${lat},${lon}` } : null;
    },
  },
  {
    name: 'address-es', kind: 'location', priority: 76,
    re: /(?<![\p{L}\p{N}])(?:Av(?:enida|da)?\.?|Calle|Bv\.?|Boulevard|Bulevar|Pasaje|Pje\.?|Diagonal|Diag\.?|Ruta|Camino|Autopista|Paseo)\s+(?:(?:de|del|la|las|los|el)\s+)*(?:[\p{Lu}\d][\p{L}\p{N}.'’-]*\s+){1,5}?(?:(?:N[°ºo]\.?|al|km\.?|#)\s*)?\d{1,5}(?![\p{L}\p{N}])/gu,
    map: (m) => ({ value: cleanText(m[0]) }),
  },
  {
    name: 'address-en', kind: 'location', priority: 76,
    re: /(?<![\p{L}\p{N}])\d{1,5}\s+(?:[A-Z][a-z]+\.?\s+){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln)\b\.?/gu,
    map: (m) => ({ value: cleanText(m[0]).replace(/\.$/, ''), quote: m[0].replace(/\.$/, '') }),
  },
  {
    name: 'platform-user', kind: 'account', priority: 74,
    re: new RegExp(
      `(?<![\\p{L}\\p{N}_@.])(?:(mi|my)\\s+)?(${ALL_ALIASES.join('|')})\\s*(?:(:|=|→|->)|\\s(es|is)\\s)\\s*@?([A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,30}[A-Za-z0-9_])?(?:#\\d{4})?)(?![\\p{L}\\p{N}_@])`,
      'giu',
    ),
    map: (m) => {
      const alias = m[2].toLowerCase();
      const user = m[5];
      const viaVerb = Boolean(m[4]);
      // "mi ig es pepe" vale; "x es una red" o "discord es genial" no.
      if (viaVerb && !(m[1] && LONG_ALIASES.includes(alias))) return null;
      if (!viaVerb && alias.length === 1 && !m[1] && m[3] !== ':' ) return null;
      if (/^(https?|www|una?|el|la|the|not?|si|yes|que|es|is)$/i.test(user)) return null;
      if (user.length < 2) return null;
      const offset = m[0].length - user.length;
      // El handle va solo en value (así lo espera el backend); la red viaja aparte en platform.
      return { value: user.toLowerCase(), platform: PLATFORM_ALIASES[alias], quote: user, offset };
    },
  },
  {
    name: 'phone-intl', kind: 'phone', priority: 72,
    re: /(?<![\w+])\+\d{1,3}[\s.-]?(?:\(\d{1,5}\)[\s.-]?)?\d(?:[\s.-]?\d){5,13}(?!\d)/g,
    map: (m) => {
      const d = digits(m[0]);
      return d.length >= 8 && d.length <= 15 ? { value: `+${d}` } : null;
    },
  },
  {
    name: 'phone-local', kind: 'phone', priority: 70,
    // Sin "+", se exige forma de teléfono: característica entre paréntesis o grupos separados por
    // espacio/guion, y 10 u 11 dígitos. Fechas ISO, montos y contadores no cumplen.
    re: /(?<![\w./-])(?:\(0?\d{2,4}\)\s?|0?\d{2,4}[\s-])(?:15[\s-]?)?\d{3,4}[\s-]\d{4}(?![\w/-]|\.\d)/g,
    map: (m) => {
      const d = digits(m[0]);
      if (d.length < 10 || d.length > 11) return null;
      if (/^\d{4}-\d{2}-\d{2}/.test(m[0])) return null;
      return { value: d };
    },
  },
  {
    name: 'date-iso', kind: 'event', priority: 66,
    re: /(?<![\w/-])(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::\d{2})?(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?(?![\w/-])/g,
    map: (m) => {
      const date = isoDate(m[1], m[2], m[3]);
      if (!date) return null;
      if (m[4] === undefined) return { value: date };
      if (Number(m[4]) > 23 || Number(m[5]) > 59) return null;
      const iso = m[6] ? toIsoUtc(m[0].replace(' ', 'T')) : null;
      return { value: iso || `${date}T${m[4]}:${m[5]}` };
    },
  },
  {
    name: 'date-dmy', kind: 'event', priority: 65,
    re: /(?<![\w/.-])(\d{1,2})([/.-])(\d{1,2})\2(\d{4}|\d{2})(?![\w/-]|\.\d)/g,
    map: (m) => {
      const value = isoDate(m[4], m[3], m[1]);
      return value ? { value } : null;
    },
  },
  {
    name: 'date-words-es', kind: 'event', priority: 65,
    re: new RegExp(`(?<![\\p{L}\\p{N}])(\\d{1,2})(?:º|°)?\\s+de\\s+(${MONTH_ALT})(?:\\s+(?:de|del)\\s+(\\d{4}))?(?![\\p{L}\\p{N}])`, 'giu'),
    map: (m) => {
      const month = MONTHS[m[2].toLowerCase()];
      if (!month || Number(m[1]) < 1 || Number(m[1]) > 31) return null;
      if (m[3]) {
        const value = isoDate(m[3], month, m[1]);
        return value ? { value } : null;
      }
      return { value: `--${pad(month)}-${pad(m[1])}` }; // fecha sin año (ISO 8601 truncada)
    },
  },
  {
    name: 'date-words-en', kind: 'event', priority: 65,
    re: new RegExp(`(?<![\\p{L}\\p{N}])(${MONTH_ALT})\\s+(\\d{1,2})(?:st|nd|rd|th)?,\\s*(\\d{4})(?![\\p{L}\\p{N}])`, 'giu'),
    map: (m) => {
      const month = MONTHS[m[1].toLowerCase()];
      const value = month ? isoDate(m[3], month, m[2]) : '';
      return value ? { value } : null;
    },
  },
  {
    name: 'time', kind: 'event', priority: 60,
    re: /(?<![\w:.,/-])([01]?\d|2[0-3])[:.]([0-5]\d)(?::[0-5]\d)?\s?(hs\.?|hrs\.?|h|a\.?\s?m\.?|p\.?\s?m\.?)(?![\p{L}\p{N}])|(?<![\w:.,/-])([01]?\d|2[0-3]):([0-5]\d)(?![\w:]|[.,/-]\d)/giu,
    map: (m) => {
      let hour = Number(m[1] ?? m[4]);
      const minute = m[2] ?? m[5];
      const suffix = (m[3] || '').toLowerCase().replace(/[.\s]/g, '');
      if (suffix === 'pm' && hour < 12) hour += 12;
      if (suffix === 'am' && hour === 12) hour = 0;
      return { value: `${pad(hour)}:${minute}` };
    },
  },
  {
    name: 'domain', kind: 'domain', priority: 50,
    re: /(?<![\w@./-])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}(?![\w@-]|\.[A-Za-z0-9])/g,
    map: (m, text) => {
      const value = m[0].toLowerCase();
      const labels = value.split('.');
      if (!TLDS.has(labels.at(-1))) return null;
      // "Hola.Com" (oración mal puntuada) vs "hola.com": un TLD con mayúscula inicial tras minúsculas es prosa.
      if (/[a-záéíóúñ]\.[A-ZÁÉÍÓÚÑ][a-z]+$/.test(m[0])) return null;
      if (labels.some((l) => l.length === 0)) return null;
      if (text[m.index + m[0].length] === '@') return null;
      return { value };
    },
  },
  {
    name: 'mention', kind: 'account', priority: 40,
    re: /(?<![\p{L}\p{N}_@/.])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,60}[A-Za-z0-9_])?)(?![\p{L}\p{N}_@])/gu,
    map: (m) => (m[1].length >= 2 ? { value: m[1].toLowerCase() } : null),
  },
];

export const CHUNK_KIND_LABELS = {
  account: 'Cuenta', email: 'Email', phone: 'Teléfono', domain: 'Dominio', url: 'URL', ip: 'IP',
  location: 'Lugar', event: 'Fecha / hora', hash: 'Hash', wallet: 'Billetera', vulnerability: 'CVE',
  person: 'Persona', organization: 'Organización', document: 'Documento', vehicle: 'Vehículo',
  malware: 'Malware', bank_account: 'Cuenta bancaria', alias: 'Alias bancario', text: 'Cita',
};

// Inciso sugerido por tipo (solo una sugerencia visual: el analista decide dónde va).
export const SUGGESTED_SECTION = {
  account: 'Cuentas', email: 'Contactos', phone: 'Contactos', domain: 'Infraestructura', url: 'Infraestructura',
  ip: 'Infraestructura', hash: 'Infraestructura', wallet: 'Infraestructura', vulnerability: 'Infraestructura',
  location: 'Ubicaciones', event: 'Actividad', person: 'Identidad', bank_account: 'Identidad', alias: 'Identidad',
  text: 'Sin clasificar',
};

export function chunkKey(kind, value) {
  return `${kind}|${String(value).toLowerCase()}`;
}

/**
 * Busca datachunks en un texto. Devuelve coincidencias sin superposición, ordenadas por posición:
 * [{ kind, value, quote, start, end, rule, platform? }]. `start`/`end` delimitan exactamente lo que se
 * resalta. `platform` aparece solo en cuentas de otra red ("mi ig es pepe"); el resto usa la de la página.
 */
export function detectChunks(text, { maxMatches = 200 } = {}) {
  const source = String(text ?? '');
  if (source.length < 3) return [];
  const found = [];
  for (const rule of RULES) {
    rule.re.lastIndex = 0;
    for (const m of source.matchAll(rule.re)) {
      const mapped = rule.map(m, source);
      if (!mapped || !mapped.value) continue;
      const offset = mapped.offset || 0;
      const quote = mapped.quote ?? m[0].slice(offset);
      const start = m.index + offset;
      const hit = { kind: rule.kind, value: mapped.value, quote, start, end: start + quote.length, rule: rule.name, priority: rule.priority };
      if (mapped.platform) hit.platform = mapped.platform;
      found.push(hit);
      if (found.length > maxMatches * 4) break;
    }
  }
  // A igual zona de texto gana la regla más específica; después, la coincidencia más larga.
  found.sort((a, b) => b.priority - a.priority || (b.end - b.start) - (a.end - a.start) || a.start - b.start);
  const taken = [];
  for (const c of found) {
    if (taken.some((t) => c.start < t.end && t.start < c.end)) continue;
    taken.push(c);
    if (taken.length >= maxMatches) break;
  }
  return taken.sort((a, b) => a.start - b.start).map(({ priority, ...rest }) => rest);
}

/** Busca apariciones literales de frases dadas (chunks de FUNES o manuales) dentro de un texto. */
export function findLiteral(text, phrases) {
  const out = [];
  const source = String(text ?? '');
  for (const phrase of phrases) {
    const needle = phrase.quote;
    if (!needle || needle.length < 2) continue;
    let from = 0;
    for (;;) {
      const i = source.indexOf(needle, from);
      if (i < 0) break;
      out.push({ ...phrase, start: i, end: i + needle.length });
      from = i + needle.length;
    }
  }
  return out;
}

/** Une coincidencias por regla con literales (los literales ganan si se superponen). */
export function mergeMatches(ruleMatches, literalMatches) {
  const out = [...literalMatches];
  for (const m of ruleMatches) if (!out.some((t) => m.start < t.end && t.start < m.end)) out.push(m);
  return out.sort((a, b) => a.start - b.start);
}

/** Datachunk completo (todas las claves del esquema). */
export function makeChunk(partial, page = {}) {
  const p = partial || {};
  const captured = toIsoUtc(p.captured_at) || new Date().toISOString();
  const by = ['rule', 'funes', 'manual'].includes(p.detected_by) ? p.detected_by : 'rule';
  return {
    kind: String(p.kind || 'text'),
    value: clip(cleanText(p.value), 2000),
    quote: clip(String(p.quote ?? p.value ?? ''), 2000),
    context: clip(cleanText(p.context), 600),
    page_url: String(p.page_url || page.url || ''),
    page_title: String(p.page_title || page.title || ''),
    platform: String(p.platform || page.platform || 'generic'),
    author_handle: String(p.author_handle || '').replace(/^@+/, ''),
    post_id: String(p.post_id || ''),
    detected_by: by,
    captured_at: captured,
  };
}

/** Chunk manual a partir de una selección de texto del analista. */
export function makeManualChunk(selectedText, page = {}, extra = {}) {
  const value = cleanText(selectedText);
  if (!value) return null;
  return makeChunk({ ...extra, kind: 'text', value, quote: String(selectedText).trim(), detected_by: 'manual' }, page);
}

/** FindingCreate: el analista soltó (o envió) un chunk a un inciso y/o a una entidad. */
export function buildFinding({ chunk, sectionId = null, entityId = null, note = '' }) {
  return {
    section_id: Number.isInteger(sectionId) ? sectionId : null,
    chunk: makeChunk(chunk),
    attach_to_entity_id: Number.isInteger(entityId) ? entityId : null,
    note: String(note || ''),
  };
}

export const CHUNK_MIME = 'application/x-aleph-datachunk+json';
