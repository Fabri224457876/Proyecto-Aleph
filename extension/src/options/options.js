// Página de opciones: URL del servidor, inicio de sesión y prueba de conexión.
// La contraseña se usa una sola vez para pedir el token y no se guarda en ningún lado.

import { AlephClient, normalizeServerUrl } from '../lib/api.js';

const $ = (id) => document.getElementById(id);

function say(id, message, kind = '') {
  const node = $(id);
  node.textContent = message;
  node.className = `msg ${kind}`;
}

async function config() {
  const [local, session] = await Promise.all([
    chrome.storage.local.get(['serverUrl', 'token', 'user']),
    chrome.storage.session.get(['token', 'user']),
  ]);
  return {
    serverUrl: normalizeServerUrl(local.serverUrl || ''),
    token: session.token || local.token || '',
    user: session.user || local.user || '',
    remembered: Boolean(local.token),
  };
}

async function clearSession() {
  await chrome.storage.local.remove(['token', 'user']);
  await chrome.storage.session.remove(['token', 'user']);
}

function notify() {
  chrome.runtime.sendMessage({ type: 'options:changed' }).catch(() => {});
}

async function showSession() {
  const c = await config();
  const how = c.remembered ? ' (recordada en este equipo).' : ' (hasta cerrar el navegador).';
  $('session').textContent = c.token ? `Sesión iniciada${c.user ? ` como ${c.user}` : ''}${how}` : 'Sin sesión iniciada.';
  $('logout').disabled = !c.token;
}

/** Pide el permiso de red para el servidor (tiene que ser dentro de un clic del usuario). */
async function grantServer(serverUrl) {
  const origins = [`${new URL(serverUrl).origin}/*`];
  if (await chrome.permissions.contains({ origins })) return true;
  return chrome.permissions.request({ origins }).catch(() => false);
}

async function saveServer() {
  const serverUrl = normalizeServerUrl($('server').value);
  if (!serverUrl) {
    say('server-msg', 'Esa URL no es válida. Ejemplo: http://100.64.0.10:8100', 'bad');
    return '';
  }
  const granted = await grantServer(serverUrl);
  if (!granted) {
    say('server-msg', 'Sin el permiso para ese servidor la extensión no puede conectarse.', 'bad');
    return '';
  }
  const before = (await config()).serverUrl;
  await chrome.storage.local.set({ serverUrl });
  // Otro servidor: el token anterior no sirve, y no hay que mandárselo.
  if (before && before !== serverUrl) await clearSession();
  $('server').value = serverUrl;
  say('server-msg', 'Servidor guardado.', 'ok');
  notify();
  await showSession();
  return serverUrl;
}

async function login(button) {
  if (button) button.disabled = true;
  const secretField = $('password');
  try {
    const serverUrl = await saveServer();
    if (!serverUrl) return;
    say('login-msg', 'Iniciando sesión…');
    const username = $('username').value.trim();
    const client = new AlephClient({ baseUrl: serverUrl });
    const { token, user } = await client.login(username, secretField.value);
    await clearSession();
    const area = $('remember').checked ? chrome.storage.local : chrome.storage.session;
    await area.set({ token, user: (user && user.username) || username });
    say('login-msg', 'Sesión iniciada.', 'ok');
    notify();
  } catch (err) {
    say('login-msg', err && err.message ? err.message : 'No se pudo iniciar sesión.', 'bad');
  } finally {
    secretField.value = '';
    if (button) button.disabled = false;
    await showSession();
  }
}

async function testConnection() {
  const c = await config();
  if (!c.serverUrl) return say('test-msg', 'Primero guardá la URL del servidor.', 'bad');
  if (!c.token) return say('test-msg', 'Primero iniciá sesión.', 'bad');
  say('test-msg', 'Probando…');
  try {
    const client = new AlephClient({ baseUrl: c.serverUrl, token: c.token });
    const me = await client.me();
    const cases = await client.listCases();
    const who = me && me.username ? me.username : '—';
    const role = me && me.role ? me.role : 'sin rol';
    return say('test-msg', `Conexión correcta. Usuario: ${who} (${role}). Casos visibles: ${cases.length}.`, 'ok');
  } catch (err) {
    return say('test-msg', err && err.message ? err.message : 'No se pudo conectar.', 'bad');
  }
}

$('save-server').addEventListener('click', saveServer);
$('test').addEventListener('click', testConnection);
$('login').addEventListener('submit', (ev) => {
  ev.preventDefault();
  login(ev.submitter);
});
$('logout').addEventListener('click', async () => {
  await clearSession();
  say('login-msg', 'Sesión cerrada.');
  notify();
  await showSession();
});

(async () => {
  const c = await config();
  $('server').value = c.serverUrl;
  $('remember').checked = c.remembered;
  await showSession();
})();
