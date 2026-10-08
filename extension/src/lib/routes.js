// Qué plataforma es una URL y qué rutas NO se leen nunca (mensajería privada, login, ajustes).

const PLATFORM_HOSTS = [
  ['x', /(^|\.)(x|twitter)\.com$/i],
  ['instagram', /(^|\.)instagram\.com$/i],
  ['bluesky', /(^|\.)bsky\.app$/i],
  ['reddit', /(^|\.)reddit\.com$/i],
];

export function parseUrl(url) {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

export function detectPlatform(url) {
  const u = parseUrl(url);
  if (!u) return 'generic';
  for (const [name, re] of PLATFORM_HOSTS) if (re.test(u.hostname)) return name;
  return 'generic';
}

// Rutas privadas por plataforma. Se evalúan sobre el pathname.
const PRIVATE_PATHS = {
  x: [/^\/messages(\/|$)/i, /^\/i\/chat(\/|$)/i, /^\/i\/flow\//i, /^\/login(\/|$)/i, /^\/settings(\/|$)/i,
    /^\/account(\/|$)/i, /^\/i\/keyboard_shortcuts/i],
  instagram: [/^\/direct(\/|$)/i, /^\/accounts(\/|$)/i, /^\/challenge(\/|$)/i],
  bluesky: [/^\/messages(\/|$)/i, /^\/settings(\/|$)/i],
  reddit: [/^\/message(\/|$)/i, /^\/chat(\/|$)/i, /^\/settings(\/|$)/i, /^\/login(\/|$)/i, /^\/account(\/|$)/i,
    /^\/prefs(\/|$)/i],
};

// Sitios que son mensajería, correo o banca por naturaleza: el extractor genérico no los lee.
const PRIVATE_HOSTS = [
  /(^|\.)chat\.reddit\.com$/i,
  /(^|\.)mail\.google\.com$/i, /(^|\.)outlook\.(live|office|office365)\.com$/i, /(^|\.)mail\.yahoo\.com$/i,
  /(^|\.)mail\.proton\.me$/i, /(^|\.)web\.whatsapp\.com$/i, /(^|\.)web\.telegram\.org$/i,
  /(^|\.)messenger\.com$/i, /(^|\.)messages\.google\.com$/i, /(^|\.)teams\.(microsoft|live)\.com$/i,
  /(^|\.)app\.slack\.com$/i, /(^|\.)signal\.org$/i, /(^|\.)accounts\.google\.com$/i,
  /(^|\.)login\.(microsoftonline|live)\.com$/i,
];
const PRIVATE_HOST_PATHS = [
  [/(^|\.)facebook\.com$/i, /^\/messages(\/|$)/i],
  [/(^|\.)discord\.com$/i, /^\/channels\/@me(\/|$)/i],
  [/(^|\.)linkedin\.com$/i, /^\/messaging(\/|$)/i],
  [/(^|\.)tiktok\.com$/i, /^\/messages(\/|$)/i],
];
// En cualquier sitio: pantallas de ingreso y de credenciales.
const GENERIC_PRIVATE_PATHS = [
  /(^|\/)(login|log-in|signin|sign-in|sign_in|logout|password|reset-password|2fa|mfa|oauth|sso|checkout)(\/|\.|$)/i,
];

/**
 * Devuelve el motivo (texto para mostrar al analista) si la URL no se puede capturar, o "" si se puede.
 */
export function excludedReason(url) {
  const u = parseUrl(url);
  if (!u) return 'Dirección inválida.';
  if (!/^https?:$/.test(u.protocol)) return 'Aleph Lens solo trabaja sobre páginas web (http/https).';
  const host = u.hostname;
  const path = u.pathname;
  if (PRIVATE_HOSTS.some((re) => re.test(host))) return 'Sitio de mensajería, correo o ingreso: Aleph Lens no lo lee.';
  for (const [hostRe, pathRe] of PRIVATE_HOST_PATHS) {
    if (hostRe.test(host) && pathRe.test(path)) return 'Mensajes privados: Aleph Lens no los lee.';
  }
  const platform = detectPlatform(url);
  if ((PRIVATE_PATHS[platform] || []).some((re) => re.test(path))) {
    return 'Mensajes privados o ajustes de la cuenta: Aleph Lens no los lee.';
  }
  if (GENERIC_PRIVATE_PATHS.some((re) => re.test(path))) return 'Pantalla de ingreso o credenciales: Aleph Lens no la lee.';
  return '';
}

export function isExcludedUrl(url) {
  return excludedReason(url) !== '';
}

// Contenedores de mensajería que pueden aparecer DENTRO de una página permitida (por ejemplo el cajón
// de mensajes de X sobre el timeline). Nada que esté adentro se extrae ni se resalta.
export const PRIVATE_CONTAINERS = [
  '[data-testid="DMDrawer"]',
  '[data-testid="DmActivityContainer"]',
  '[data-testid="DmScrollerContainer"]',
  '[data-testid="dm-conversation-panel"]',
  '[data-testid="messageEntry"]',
  '[data-testid^="chat-"]',
  '[aria-label="Direct messaging"]',
  'rs-app', // chat embebido de Reddit
  'reddit-chat',
].join(',');

// Contenedores de ingreso: un formulario o un diálogo (modal) que tenga un campo de contraseña.
const CREDENTIAL_BOXES = 'form, dialog, [role="dialog"], [role="alertdialog"]';

/** true si el nodo está dentro de mensajería privada o de un formulario/diálogo con campo de contraseña. */
export function insidePrivate(node) {
  const el = node && node.nodeType === 1 ? node : node && node.parentElement;
  if (!el || !el.closest) return false;
  if (el.closest(PRIVATE_CONTAINERS)) return true;
  // Se revisa todo el camino hacia arriba: un diálogo puede estar dentro de un formulario o al revés.
  for (let box = el.closest(CREDENTIAL_BOXES); box; box = box.parentElement && box.parentElement.closest(CREDENTIAL_BOXES)) {
    if (box.querySelector('input[type="password"]')) return true;
  }
  return false;
}

export function originPattern(url) {
  const u = parseUrl(url);
  return u && /^https?:$/.test(u.protocol) ? `${u.origin}/*` : '';
}
