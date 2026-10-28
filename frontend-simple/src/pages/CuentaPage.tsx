import { useState } from "react";
import * as ep from "../api/endpoints";
import { Button, Field, PageTitle, Panel, Notice } from "../components/ui";
import { useToast } from "../lib/toast";
import { errorMessage } from "../lib/format";

export function CuentaPage() {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [repeat, setRepeat] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const mismatch = repeat.length > 0 && next !== repeat;

  async function submit() {
    if (next !== repeat) {
      setError("La nueva contraseña y su repetición no coinciden.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await ep.changePassword(current, next);
      setCurrent("");
      setNext("");
      setRepeat("");
      setDone(true);
      toast.notify("Contraseña cambiada.", "ok");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageTitle lead="Cambiá tu contraseña. Queda registrado en la auditoría.">Mi contraseña</PageTitle>
      <Panel>
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            void submit();
          }}
        >
          <Field label="Contraseña actual">
            {(id) => (
              <input id={id} className="input" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
            )}
          </Field>
          <Field label="Contraseña nueva">
            {(id) => (
              <input id={id} className="input" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
            )}
          </Field>
          <Field label="Repetí la contraseña nueva" error={mismatch ? "No coincide con la nueva." : undefined}>
            {(id) => (
              <input id={id} className="input" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
            )}
          </Field>
          {error ? <Notice tone="bad"><p>{error}</p></Notice> : null}
          {done && <Notice tone="ok">Listo: la contraseña se cambió.</Notice>}
          <div className="btn-row">
            <Button variant="primary" type="submit" disabled={busy || !current || !next || mismatch}>
              {busy ? "Guardando…" : "Cambiar contraseña"}
            </Button>
          </div>
        </form>
      </Panel>
    </div>
  );
}
