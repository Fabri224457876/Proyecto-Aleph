// Espejo en JS de los esquemas de backend/aleph/core/schemas.py que usa la extensión.
// Sirve para validar lo que sale (antes de enviar y en los tests). Si el contrato cambia, se cambia acá.

export const ENTITY_TYPES = [
  'person', 'account', 'email', 'phone', 'domain', 'ip', 'url', 'organization',
  'location', 'event', 'hash', 'wallet', 'document', 'vehicle', 'malware', 'vulnerability',
  'bank_account', 'alias',
];
export const POST_KINDS = ['original', 'reply', 'repost', 'quote'];
export const DETECTED_BY = ['rule', 'funes', 'manual'];
export const DEFAULT_SECTIONS = [
  'Identidad', 'Cuentas', 'Contactos', 'Ubicaciones', 'Actividad', 'Infraestructura', 'Sin clasificar',
];
export const UNCLASSIFIED = 'Sin clasificar';

const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;

const isStr = (v) => typeof v === 'string';
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const isIntOrNull = (v) => v === null || Number.isInteger(v);
const isDate = (v) => isStr(v) && ISO_UTC.test(v) && !Number.isNaN(Date.parse(v));
const isDateOrNull = (v) => v === null || isDate(v);
const isStrList = (v) => Array.isArray(v) && v.every(isStr);

function checkShape(obj, shape, path, errors) {
  if (!isObj(obj)) {
    errors.push(`${path}: se esperaba un objeto`);
    return false;
  }
  for (const [key, [check, what]] of Object.entries(shape)) {
    if (!(key in obj)) errors.push(`${path}.${key}: falta`);
    else if (!check(obj[key])) errors.push(`${path}.${key}: se esperaba ${what}`);
  }
  for (const key of Object.keys(obj)) if (!(key in shape)) errors.push(`${path}.${key}: clave fuera del esquema`);
  return true;
}

const POST_SHAPE = {
  platform_post_id: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  text: [isStr, 'texto'],
  created_at: [isDateOrNull, 'fecha ISO 8601 con zona o null'],
  lang: [isStr, 'texto'],
  kind: [(v) => POST_KINDS.includes(v), POST_KINDS.join('|')],
  reply_to: [(v) => isStr(v) && !v.startsWith('@'), 'handle sin @'],
  mentions: [(v) => isStrList(v) && v.every((m) => m === m.toLowerCase() && !m.startsWith('@')), 'handles en minúscula sin @'],
  hashtags: [(v) => isStrList(v) && v.every((h) => h === h.toLowerCase() && !h.startsWith('#')), 'hashtags en minúscula sin #'],
  urls: [isStrList, 'lista de texto'],
  client: [isStr, 'texto'],
  meta: [isObj, 'objeto'],
};

const ACCOUNT_SHAPE = {
  platform: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  handle: [(v) => isStr(v) && v !== '' && !v.startsWith('@') && !/\s/.test(v), 'handle sin @'],
  platform_uid: [isStr, 'texto'],
  display_name: [isStr, 'texto'],
  bio: [isStr, 'texto'],
  url: [isStr, 'texto'],
  created_at_platform: [isDateOrNull, 'fecha ISO 8601 con zona o null'],
  followers: [isIntOrNull, 'entero o null'],
  following: [isIntOrNull, 'entero o null'],
  avatar_url: [isStr, 'texto'],
  avatar_phash: [isStr, 'texto'],
  following_handles: [isStrList, 'lista de texto'],
  follower_handles: [isStrList, 'lista de texto'],
  meta: [isObj, 'objeto'],
};

const BATCH_SHAPE = {
  page_url: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  page_title: [isStr, 'texto'],
  platform: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  captured_at: [isDate, 'fecha ISO 8601 con zona'],
  profiles: [Array.isArray, 'lista'],
  interactions: [
    (v) => Array.isArray(v) && v.every((t) => Array.isArray(t) && t.length === 3 && t.every(isStr)),
    'lista de ternas [origen, destino, tipo]',
  ],
  text_snippets: [isStrList, 'lista de texto'],
  extension_version: [isStr, 'texto'],
};

const CHUNK_SHAPE = {
  kind: [(v) => v === 'text' || ENTITY_TYPES.includes(v), 'un ENTITY_TYPES o "text"'],
  value: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  quote: [isStr, 'texto'],
  context: [isStr, 'texto'],
  page_url: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  page_title: [isStr, 'texto'],
  platform: [(v) => isStr(v) && v !== '', 'texto no vacío'],
  author_handle: [(v) => isStr(v) && !v.startsWith('@'), 'handle sin @'],
  post_id: [isStr, 'texto'],
  detected_by: [(v) => DETECTED_BY.includes(v), DETECTED_BY.join('|')],
  captured_at: [isDate, 'fecha ISO 8601 con zona'],
};

const FINDING_SHAPE = {
  section_id: [isIntOrNull, 'entero o null'],
  chunk: [isObj, 'objeto'],
  attach_to_entity_id: [isIntOrNull, 'entero o null'],
  note: [isStr, 'texto'],
};

/** Devuelve la lista de errores (vacía si el lote cumple con CaptureBatch). */
export function validateCaptureBatch(batch) {
  const errors = [];
  if (!checkShape(batch, BATCH_SHAPE, 'batch', errors)) return errors;
  (Array.isArray(batch.profiles) ? batch.profiles : []).forEach((profile, i) => {
    const path = `batch.profiles[${i}]`;
    if (!checkShape(profile, { account: [isObj, 'objeto'], posts: [Array.isArray, 'lista'] }, path, errors)) return;
    checkShape(profile.account, ACCOUNT_SHAPE, `${path}.account`, errors);
    (Array.isArray(profile.posts) ? profile.posts : []).forEach((post, j) =>
      checkShape(post, POST_SHAPE, `${path}.posts[${j}]`, errors));
  });
  return errors;
}

export function validateDatachunk(chunk, path = 'chunk') {
  const errors = [];
  checkShape(chunk, CHUNK_SHAPE, path, errors);
  return errors;
}

export function validateFindingCreate(finding) {
  const errors = [];
  if (!checkShape(finding, FINDING_SHAPE, 'finding', errors)) return errors;
  if (isObj(finding.chunk)) errors.push(...validateDatachunk(finding.chunk, 'finding.chunk'));
  return errors;
}

export function validateLookupRequest(req) {
  const errors = [];
  checkShape(req, { platform: [(v) => isStr(v) && v !== '', 'texto no vacío'], handles: [isStrList, 'lista de texto'] }, 'lookup', errors);
  return errors;
}
