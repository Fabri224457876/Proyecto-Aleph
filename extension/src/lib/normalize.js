// Normalización compartida por todos los extractores. Tiene que dar lo mismo que el backend:
// handle sin "@", menciones y hashtags en minúscula y sin prefijo, fechas ISO 8601 en UTC.

export function cleanText(value) {
  return String(value ?? '')
    .replace(/ /g, ' ')
    .replace(/[ \t\f\v]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/** Handle tal como lo muestra la plataforma, sin "@" ni prefijos "u/". Devuelve "" si no parece un handle. */
export function normalizeHandle(value) {
  let h = String(value ?? '').trim();
  h = h.replace(/^https?:\/\/[^/]+\//i, '');
  h = h.replace(/^\/+/, '').replace(/^(u|user)\//i, '');
  h = h.replace(/^@+/, '').replace(/[/?#].*$/, '').trim();
  if (!h || /\s/.test(h) || h.length > 253) return '';
  return h;
}

export function handleKey(value) {
  return normalizeHandle(value).toLowerCase();
}

export function uniq(list) {
  const seen = new Set();
  const out = [];
  for (const item of list) {
    if (item === '' || item == null || seen.has(item)) continue;
    seen.add(item);
    out.push(item);
  }
  return out;
}

const MENTION_RE = /(?<![\p{L}\p{N}_@/.])@([A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,60}[A-Za-z0-9_])?)/gu;
const HASHTAG_RE = /(?<![\p{L}\p{N}_&/])[#＃]([\p{L}\p{N}_]*[\p{L}_][\p{L}\p{N}_]*)/gu;
const URL_RE = /https?:\/\/[^\s<>"'`]+/gi;

export function extractMentions(text) {
  return uniq([...String(text ?? '').matchAll(MENTION_RE)].map((m) => m[1].toLowerCase()));
}

export function extractHashtags(text) {
  return uniq([...String(text ?? '').matchAll(HASHTAG_RE)].map((m) => m[1].toLowerCase()));
}

export function trimUrl(url) {
  let u = String(url ?? '').trim();
  // Puntuación de cierre que casi nunca es parte de la URL.
  for (;;) {
    const last = u.slice(-1);
    if (/[.,;:!?…'"»]/.test(last)) u = u.slice(0, -1);
    else if (last === ')' && !u.includes('(')) u = u.slice(0, -1);
    else if (last === ']' && !u.includes('[')) u = u.slice(0, -1);
    else break;
  }
  return u;
}

export function extractUrls(text) {
  return uniq([...String(text ?? '').matchAll(URL_RE)].map((m) => trimUrl(m[0])));
}

export function absoluteUrl(href, base) {
  if (!href) return '';
  try {
    const u = new URL(href, base || undefined);
    return /^https?:$/.test(u.protocol) ? u.href : '';
  } catch {
    return '';
  }
}

/** Cualquier fecha interpretable -> ISO 8601 UTC ("2026-10-07T12:00:00.000Z") o null. */
export function toIsoUtc(value) {
  if (value == null || value === '') return null;
  let d;
  if (value instanceof Date) d = value;
  else if (typeof value === 'number') d = new Date(value < 1e11 ? value * 1000 : value);
  else {
    const s = String(value).trim();
    if (/^\d{10}$/.test(s)) d = new Date(Number(s) * 1000);
    else if (/^\d{13}$/.test(s)) d = new Date(Number(s));
    // Una fecha ISO sin zona se toma como UTC, no como hora local del navegador.
    else if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(s)) d = new Date(`${s}Z`);
    else d = new Date(s);
  }
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}

const MULTIPLIERS = [
  [/^(k|mil)$/i, 1e3],
  [/^(m|mm|mill\.?|millones|millón|million)$/i, 1e6],
  [/^(b|bn|mil millones|billion)$/i, 1e9],
];

/** "12,3 mil", "1.234", "4.5K", "2 M", "1,234,567" -> entero, o null si no se entiende. */
export function parseCount(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? Math.round(value) : null;
  const s = cleanText(value).toLowerCase();
  const m = s.match(/(\d[\d.,\s]*\d|\d)\s*(mil millones|millones|millón|million|billion|mill\.?|mil|mm|bn|k|m|b)?(?![a-z])/i);
  if (!m) return null;
  let num = m[1].replace(/\s/g, '');
  const suffix = m[2];
  let n;
  if (suffix) {
    // Con sufijo, el separador es decimal ("12,3 mil", "4.5K").
    const parts = num.split(/[.,]/);
    if (parts.length > 2) return null;
    n = Number(parts.join('.'));
    const mult = MULTIPLIERS.find(([re]) => re.test(suffix));
    n *= mult ? mult[1] : 1;
  } else {
    // Sin sufijo, puntos y comas son separadores de miles ("1.234", "1,234,567").
    const groups = num.split(/[.,]/);
    const thousands = groups.length > 1 && groups.slice(1).every((g) => g.length === 3);
    if (groups.length > 1 && !thousands) n = Number(groups.slice(0, -1).join('') + '.' + groups.at(-1));
    else n = Number(groups.join(''));
  }
  return Number.isFinite(n) ? Math.round(n) : null;
}

export function clip(text, max) {
  const s = String(text ?? '');
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** Hash corto y estable (FNV-1a de 32 bits) para deduplicar y armar ids sintéticos. */
export function shortHash(text) {
  let h = 0x811c9dc5;
  const s = String(text ?? '');
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

/** AccountRecord completo (todas las claves del esquema) a partir de datos parciales. */
export function makeAccount(partial) {
  const p = partial || {};
  return {
    platform: String(p.platform || ''),
    handle: normalizeHandle(p.handle),
    platform_uid: String(p.platform_uid || ''),
    display_name: cleanText(p.display_name),
    bio: cleanText(p.bio),
    url: String(p.url || ''),
    created_at_platform: toIsoUtc(p.created_at_platform),
    followers: Number.isInteger(p.followers) ? p.followers : null,
    following: Number.isInteger(p.following) ? p.following : null,
    avatar_url: String(p.avatar_url || ''),
    avatar_phash: '',
    following_handles: uniq((p.following_handles || []).map(normalizeHandle)),
    follower_handles: uniq((p.follower_handles || []).map(normalizeHandle)),
    meta: { ...(p.meta || {}) },
  };
}

const KINDS = new Set(['original', 'reply', 'repost', 'quote']);

/** PostRecord completo. Menciones, hashtags y URLs se deducen del texto si no vienen dados. */
export function makePost(partial) {
  const p = partial || {};
  const text = cleanText(p.text);
  return {
    platform_post_id: String(p.platform_post_id || ''),
    text,
    created_at: toIsoUtc(p.created_at),
    lang: String(p.lang || ''),
    kind: KINDS.has(p.kind) ? p.kind : 'original',
    reply_to: normalizeHandle(p.reply_to),
    mentions: uniq([...(p.mentions || []).map(handleKey), ...extractMentions(text)]),
    hashtags: uniq([
      ...(p.hashtags || []).map((h) => String(h).replace(/^[#＃]/, '').toLowerCase()),
      ...extractHashtags(text),
    ]),
    urls: uniq([...(p.urls || []), ...extractUrls(text)]),
    client: String(p.client || ''),
    meta: { ...(p.meta || {}) },
  };
}
