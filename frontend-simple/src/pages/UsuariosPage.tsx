import { useState } from "react";
import * as ep from "../api/endpoints";
import type { User } from "../api/types";
import { Button, ErrorBox, Field, Loading, PageTitle, Panel } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { errorMessage, fmtDateTime } from "../lib/format";
import { ROLE_LABEL } from "../lib/labels";
import { useAuth } from "../lib/auth";
import { Notice } from "../components/ui";

const ROLES = ["analyst", "auditor", "admin"] as const;

function UserRow({ user, self, onChanged }: { user: User; self: boolean; onChanged: () => void }) {
  const toast = useToast();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function update(body: { role?: string; active?: boolean; password?: string }, message: string) {
    setBusy(true);
    setError(null);
    try {
      await ep.updateUser(user.id, body);
      toast.notify(message, "ok");
      setPassword("");
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr>
      <td>
        <strong>{user.username}</strong>
        {self && <span className="muted small"> (vos)</span>}
        <div className="small muted">Creado {fmtDateTime(user.created_at)}</div>
      </td>
      <td>
        <select
          className="select"
          aria-label={`Rol de ${user.username}`}
          value={user.role}
          disabled={busy || self}
          onChange={(e) => void update({ role: e.target.value }, `Rol de ${user.username} actualizado.`)}
        >
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {ROLE_LABEL[r]}
            </option>
          ))}
        </select>
      </td>
      <td>
        <label className="check small">
          <input
            type="checkbox"
            checked={user.active}
            disabled={busy || self}
            onChange={(e) => void update({ active: e.target.checked }, e.target.checked ? "Usuario activado." : "Usuario desactivado.")}
          />
          Activo
        </label>
      </td>
      <td>
        <div className="row">
          <input
            className="input"
            style={{ width: 200 }}
            type="password"
            autoComplete="new-password"
            aria-label={`Nueva contraseña para ${user.username}`}
            placeholder="Nueva contraseña"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button disabled={!password || busy} onClick={() => void update({ password }, "Contraseña cambiada.")}>
            Cambiar
          </Button>
        </div>
        {error ? <p className="field-error">{errorMessage(error)}</p> : null}
      </td>
    </tr>
  );
}

export function UsuariosPage() {
  const auth = useAuth();
  const toast = useToast();
  const users = useAsync(() => ep.listUsers(), []);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<(typeof ROLES)[number]>("analyst");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  if (!auth.isAdmin) return <Notice tone="warn">Esta pantalla es solo para administradores.</Notice>;

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const created = await ep.createUser({ username: username.trim(), password, role });
      toast.notify(`Usuario ${created.username} creado.`, "ok");
      setUsername("");
      setPassword("");
      users.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageTitle lead="Quién puede entrar a Aleph y con qué rol. Un analista ve solo los casos donde es miembro; un auditor ve todos, sin cambiarlos.">
        Usuarios
      </PageTitle>

      <Panel title="Usuarios">
        {users.error ? <ErrorBox error={users.error} onRetry={users.reload} /> : null}
        {!users.data && !users.error && <Loading what="Cargando usuarios" />}
        {users.data && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Usuario</th>
                  <th scope="col">Rol</th>
                  <th scope="col">Estado</th>
                  <th scope="col">Contraseña</th>
                </tr>
              </thead>
              <tbody>
                {users.data.items.map((u) => (
                  <UserRow key={u.id} user={u} self={u.id === auth.user?.id} onChanged={users.reload} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel title="Crear usuario">
        <div className="stack">
          <div className="grid-2">
            <Field label="Nombre de usuario">
              {(id) => <input id={id} className="input" value={username} onChange={(e) => setUsername(e.target.value)} maxLength={100} />}
            </Field>
            <Field label="Rol">
              {(id) => (
                <select id={id} className="select" value={role} onChange={(e) => setRole(e.target.value as (typeof ROLES)[number])}>
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
              )}
            </Field>
          </div>
          <Field label="Contraseña inicial" hint="La persona la cambia después en Mi contraseña.">
            {(id) => (
              <input id={id} className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
            )}
          </Field>
          {error ? <ErrorBox error={error} /> : null}
          <div className="btn-row">
            <Button variant="primary" disabled={busy || !username.trim() || !password} onClick={() => void create()}>
              Crear usuario
            </Button>
          </div>
        </div>
      </Panel>
    </div>
  );
}
