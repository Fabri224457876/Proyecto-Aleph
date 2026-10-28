import { useState } from "react";
import * as ep from "../api/endpoints";
import { useCase } from "../components/CaseLayout";
import { Button, ConfirmButton, ErrorBox, Loading, Notice, PageTitle, Panel, Help } from "../components/ui";
import { useAuth } from "../lib/auth";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { fmtDateTime } from "../lib/format";
import { ROLE_LABEL } from "../lib/labels";

export function EquipoPage() {
  const ctx = useCase();
  const auth = useAuth();
  const toast = useToast();
  const members = useAsync(() => ep.listMembers(ctx.caseId), [ctx.caseId]);
  const [username, setUsername] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const mine = members.data?.items.find((m) => m.username === auth.user?.username);
  const canManage = ctx.canWrite && (auth.isAdmin || Boolean(mine));

  async function add() {
    setBusy(true);
    setError(null);
    try {
      const created = await ep.addMember(ctx.caseId, { username: username.trim() });
      toast.notify(`${created.username} ahora tiene acceso al caso.`, "ok");
      setUsername("");
      members.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  async function remove(userId: number, name: string) {
    setBusy(true);
    setError(null);
    try {
      await ep.removeMember(ctx.caseId, userId);
      toast.notify(`${name} ya no tiene acceso al caso.`, "ok");
      members.reload();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageTitle lead="Quién tiene acceso a este caso. Solo los analistas que están en el caso (y los administradores) lo trabajan. El auditor ve todos los casos, pero no los gestiona.">
        Equipo del caso
      </PageTitle>

      {error ? <ErrorBox error={error} /> : null}

      <Panel title="Miembros">
        {!members.data && !members.error && <Loading what="Buscando el equipo" />}
        {members.error ? <ErrorBox error={members.error} onRetry={members.reload} /> : null}
        {members.data && (
          <div className="list">
            {members.data.items.map((m) => (
              <div className="item" key={m.user_id}>
                <div className="item-main">
                  <p className="item-title">
                    {m.username}
                    {m.username === auth.user?.username && <span className="muted small"> (vos)</span>}
                  </p>
                  <p className="item-meta">
                    {ROLE_LABEL[m.role] ?? m.role} · agregado el {fmtDateTime(m.added_at)}
                  </p>
                </div>
                {canManage && (
                  <ConfirmButton
                    label="Quitar del caso"
                    confirmLabel="Confirmar"
                    disabled={busy}
                    onConfirm={() => void remove(m.user_id, m.username)}
                  />
                )}
              </div>
            ))}
            {members.data.items.length === 0 && <p className="muted">Todavía no hay miembros en este caso.</p>}
          </div>
        )}
      </Panel>

      {canManage ? (
        <Panel title="Agregar a un analista" actions={<Help text="Solo se agregan analistas. Un administrador o un auditor ya ven todos los casos." />}>
          <div className="row">
            <input
              className="input grow"
              aria-label="Nombre de usuario del analista"
              placeholder="Nombre de usuario"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              maxLength={100}
            />
            <Button variant="primary" disabled={!username.trim() || busy} onClick={() => void add()}>
              Agregar
            </Button>
          </div>
        </Panel>
      ) : (
        <Notice tone="info">
          <p>
            {ctx.canWrite
              ? "Solo un analista miembro del caso o un administrador puede cambiar el equipo."
              : ctx.readOnlyReason || "Solo lectura."}
          </p>
        </Notice>
      )}
    </div>
  );
}
