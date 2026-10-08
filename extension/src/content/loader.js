// Cargador del content script. Lo inyecta el service worker (chrome.scripting.executeScript) SOLO en
// la pestaña donde el analista encendió la captura. Es un script clásico: importa el módulo real.
(() => {
  if (globalThis.__alephLensLoader) return;
  globalThis.__alephLensLoader = true;
  import(chrome.runtime.getURL('src/content/main.js'))
    .then((mod) => mod.boot())
    .catch((err) => {
      globalThis.__alephLensLoader = false;
      console.warn('[Aleph Lens] no se pudo iniciar:', err && err.message);
    });
})();
