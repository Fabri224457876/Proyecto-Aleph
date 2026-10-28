import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useAuth } from "../lib/auth";
import { Link, navigate, useLocation } from "../lib/router";
import { applyTheme, readTheme, type Theme } from "../lib/theme";
import { ROLE_LABEL } from "../lib/labels";
import * as endpoints from "../api/endpoints";

type ApiState = "checking" | "up" | "down";

export function Shell({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [api, setApi] = useState<ApiState>("checking");
  const [query, setQuery] = useState("");
  const location = useLocation();
  // Dentro de un caso, la búsqueda arranca limitada a ese caso.
  const inCase = /^\/casos\/(\d+)/.exec(location.pathname)?.[1];

  function search(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (q.length < 2) return;
    const params = new URLSearchParams({ q });
    if (inCase) params.set("caso", inCase);
    navigate(`/buscar?${params.toString()}`);
    setQuery("");
  }

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    let alive = true;
    endpoints
      .health()
      .then(() => alive && setApi("up"))
      .catch(() => alive && setApi("down"));
    return () => {
      alive = false;
    };
  }, []);

  const user = auth.user;
  const nextTheme: Theme = theme === "light" ? "dark" : "light";

  return (
    <div className="app">
      <a className="skip-link" href="#contenido">
        Saltar al contenido
      </a>
      <header className="topbar">
        <div className="topbar-inner">
          <Link to="/" className="brand" aria-label="Aleph, ir al inicio">
            <span className="brand-name">Aleph</span>
            <span className="brand-sub">Investigación de fuentes abiertas</span>
          </Link>
          <form className="topsearch" role="search" onSubmit={search}>
            <input
              className="input"
              type="search"
              aria-label={inCase ? "Buscar en este caso" : "Buscar en mis casos"}
              placeholder={inCase ? "Buscar en este caso…" : "Buscar en mis casos…"}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </form>
          <nav className="topnav" aria-label="Principal">
            <Link to="/">Mis casos</Link>
            {auth.canAudit && <Link to="/actividad">Registro de actividad</Link>}
            {auth.isAdmin && <Link to="/usuarios">Usuarios</Link>}
            <Link to="/cuenta">Mi contraseña</Link>
          </nav>
          <div className="topbar-user">
            <button
              type="button"
              className="btn btn-ghost btn-small"
              onClick={() => setTheme(nextTheme)}
              aria-label={`Cambiar a tema ${nextTheme === "dark" ? "oscuro" : "claro"}`}
            >
              Tema {theme === "light" ? "claro" : "oscuro"}
            </button>
            {user && (
              <span className="userbox">
                <span className="userbox-name">{user.username}</span>
                <span className="userbox-role">{ROLE_LABEL[user.role] ?? user.role}</span>
              </span>
            )}
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={() => auth.logout("manual")}
            >
              Salir
            </button>
          </div>
        </div>
      </header>
      <main id="contenido" className="main" tabIndex={-1}>
        {children}
      </main>
      <footer className="footer">
        <span>
          Cada acción queda registrada en la auditoría.{" "}
          {api === "up" && <span className="dot dot-ok">API en línea</span>}
          {api === "down" && (
            <span className="dot dot-bad">La API no responde: revisá que esté levantada.</span>
          )}
        </span>
        <button type="button" className="link-button" onClick={() => navigate("/")}>
          Ir a mis casos
        </button>
      </footer>
    </div>
  );
}
