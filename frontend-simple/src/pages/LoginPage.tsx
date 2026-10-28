import { useEffect, useState, type FormEvent } from "react";
import * as ep from "../api/endpoints";
import { useAuth } from "../lib/auth";
import { navigate } from "../lib/router";
import { applyTheme, readTheme } from "../lib/theme";
import { errorMessage } from "../lib/format";
import { Button, Field, Notice } from "../components/ui";

// Solo se vuelve a rutas internas de la consola.
function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}

export function LoginPage() {
  const auth = useAuth();
  const params = new URLSearchParams(window.location.search);
  const motivo = params.get("motivo");
  const next = safeNext(params.get("next"));
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [api, setApi] = useState<"checking" | "up" | "down">("checking");

  useEffect(() => {
    applyTheme(readTheme());
    let alive = true;
    ep.health()
      .then(() => alive && setApi("up"))
      .catch(() => alive && setApi("down"));
    return () => {
      alive = false;
    };
  }, []);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!username.trim() || !password) {
      setError("Escribí tu usuario y tu contraseña.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await auth.login(username.trim(), password);
      setPassword("");
      navigate(next, { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <main className="login-card" aria-labelledby="login-title">
        <div>
          <p className="login-brand" id="login-title">
            Aleph
          </p>
          <p className="login-sub">Investigación de fuentes abiertas · versión simple</p>
        </div>
        {motivo === "vencida" && <Notice tone="warn">Tu sesión venció. Volvé a entrar para seguir.</Notice>}
        <form className="stack" onSubmit={(e) => void submit(e)} noValidate>
          <Field label="Usuario">
            {(id) => (
              <input
                id={id}
                className="input"
                autoComplete="username"
                autoFocus
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            )}
          </Field>
          <Field label="Contraseña">
            {(id) => (
              <input
                id={id}
                className="input"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            )}
          </Field>
          {error && (
            <Notice tone="bad">
              <p>{error}</p>
            </Notice>
          )}
          <Button variant="primary" big type="submit" disabled={busy}>
            {busy ? "Entrando…" : "Entrar"}
          </Button>
        </form>
        <p className="small muted">
          Solo para personal autorizado. Cada acción queda registrada en la auditoría.
        </p>
        <p className="small">
          {api === "up" && <span className="dot dot-ok">API en línea</span>}
          {api === "down" && <span className="dot dot-bad">La API no responde: revisá que esté levantada.</span>}
          {api === "checking" && <span className="muted">Revisando la API…</span>}
        </p>
      </main>
    </div>
  );
}
