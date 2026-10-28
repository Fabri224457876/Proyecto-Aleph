// Tema claro (por defecto) u oscuro. La preferencia vive en el navegador de cada persona.
export type Theme = "light" | "dark";
const KEY = "aleph.simple.theme";

export function readTheme(): Theme {
  try {
    return window.localStorage.getItem(KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    window.localStorage.setItem(KEY, theme);
  } catch {
    /* sin almacenamiento: el tema queda solo para esta carga */
  }
}
