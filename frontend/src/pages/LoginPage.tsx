import { useState, type FormEvent } from "react";
import { useAuth } from "../lib/auth";
import { useLocation } from "../lib/router";
import { Btn, InlineError } from "../components/ui";
import { Icon } from "../components/Icon";

export function LoginPage() {
  const { login } = useAuth();
  const { search } = useLocation();
  const expired = new URLSearchParams(search).get("motivo") === "vencida";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError(new Error("Completá usuario y contraseña para ingresar."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err);
      setPassword("");
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <svg className="brand-mark" viewBox="0 0 32 32" width="26" height="26" aria-hidden="true">
            <path d="M8 24 16 8l8 16" fill="none" stroke="currentColor" strokeWidth="2.6" />
            <circle cx="16" cy="19" r="2.2" fill="currentColor" />
          </svg>
          <div>
            <p className="login-title">ALEPH</p>
            <p className="login-sub">P.R.O.A. · Plataforma de Reconocimiento y Operaciones Analíticas</p>
          </div>
        </div>

        <form className="login-form" onSubmit={onSubmit} noValidate>
          {expired ? (
            <p className="notice notice-amber" role="status">
              <Icon name="ban" /> Tu sesión venció. Volvé a ingresar para seguir.
            </p>
          ) : null}

          <label className="field-label" htmlFor="login-user">
            Usuario
          </label>
          <input
            id="login-user"
            className="input"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            disabled={busy}
            autoFocus
          />

          <label className="field-label" htmlFor="login-pass">
            Contraseña
          </label>
          <input
            id="login-pass"
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={busy}
          />

          <InlineError error={error} />

          <Btn type="submit" variant="primary" className="login-submit" disabled={busy}>
            {busy ? "Ingresando…" : "Ingresar"}
          </Btn>
        </form>

        <footer className="login-foot">
          <p>
            Solo datos públicos, APIs oficiales o exportaciones aportadas por el operador. Las salidas de MENARD y
            FUNES son hipótesis con evidencia para revisión humana, no veredictos.
          </p>
        </footer>
      </div>
    </div>
  );
}
