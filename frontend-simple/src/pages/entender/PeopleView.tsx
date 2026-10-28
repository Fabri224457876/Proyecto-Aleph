import { useState } from "react";
import * as ep from "../../api/endpoints";
import type { AccountOut, PostOut } from "../../api/types";
import { useCase } from "../../components/CaseLayout";
import { EntityPanel } from "../../components/EntityPanel";
import { Button, Empty, ErrorBox, Loading, Notice, Panel, Help } from "../../components/ui";
import { useAsync } from "../../lib/useAsync";
import { fmtDate, fmtDateTime, fmtInt, plural, truncate } from "../../lib/format";
import { POST_KIND_LABEL } from "../../lib/labels";

async function loadPeople(caseId: number) {
  const [persons, accounts] = await Promise.all([
    ep.listEntities(caseId, { type: ["person", "organization", "alias"], status: ["confirmed", "proposed"], limit: 200 }),
    ep.listAccounts(caseId, { limit: 200 }),
  ]);
  const sorted = [...accounts.items].sort((a, b) => b.post_count - a.post_count || a.id - b.id);
  return { persons: persons.items, accounts: sorted, accountsTotal: accounts.total };
}

function AccountDetail({ account }: { account: AccountOut }) {
  const ctx = useCase();
  const [posts, setPosts] = useState<PostOut[] | null>(null);
  const [loadingPosts, setLoadingPosts] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function loadPosts() {
    setLoadingPosts(true);
    setError(null);
    try {
      const page = await ep.listPosts(ctx.caseId, account.id, { order: "desc", limit: 20 });
      setPosts(page.items);
    } catch (err) {
      setError(err);
    } finally {
      setLoadingPosts(false);
    }
  }

  return (
    <div className="stack">
      <div>
        <p className="eyebrow">{account.platform}</p>
        <h3 style={{ wordBreak: "break-word" }}>@{account.handle}</h3>
        {account.display_name && <p className="muted">{account.display_name}</p>}
      </div>
      {account.bio && <p className="small">{truncate(account.bio, 300)}</p>}
      <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "6px 12px", margin: 0 }}>
        <dt className="muted">Publicaciones</dt>
        <dd style={{ margin: 0 }}>{fmtInt(account.post_count)}</dd>
        <dt className="muted">Seguidores</dt>
        <dd style={{ margin: 0 }}>{account.followers === null ? "sin dato" : fmtInt(account.followers)}</dd>
        <dt className="muted">Seguidos</dt>
        <dd style={{ margin: 0 }}>{account.following === null ? "sin dato" : fmtInt(account.following)}</dd>
        <dt className="muted">Creada</dt>
        <dd style={{ margin: 0 }}>{account.created_at_platform ? fmtDate(account.created_at_platform) : "sin dato"}</dd>
      </dl>
      {account.url && (
        <p className="small mono" style={{ wordBreak: "break-all" }}>
          {account.url}
        </p>
      )}
      {posts === null ? (
        <div className="stack">
          <Button onClick={() => void loadPosts()} disabled={loadingPosts}>
            {loadingPosts ? "Cargando…" : "Ver publicaciones"}
          </Button>
          <p className="small muted">Abrir las publicaciones queda registrado en la auditoría.</p>
          {error ? <ErrorBox error={error} /> : null}
        </div>
      ) : (
        <div className="stack">
          {posts.length === 0 && <p className="small muted">Esta cuenta no tiene publicaciones guardadas.</p>}
          {posts.map((post) => (
            <div className="finding" key={post.id}>
              <div className="row small">
                <span className="chip">{POST_KIND_LABEL[post.kind] ?? post.kind}</span>
                <span className="muted">{fmtDateTime(post.created_at)}</span>
              </div>
              <p style={{ wordBreak: "break-word" }}>{truncate(post.text, 360)}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function PeopleView() {
  const ctx = useCase();
  const data = useAsync(() => loadPeople(ctx.caseId), [ctx.caseId]);
  const [selected, setSelected] = useState<{ kind: "person"; id: number } | { kind: "account"; id: number } | null>(null);
  const [accountDetail, setAccountDetail] = useState<AccountOut | null>(null);
  const [accountError, setAccountError] = useState<unknown>(null);

  if (data.error) return <ErrorBox error={data.error} onRetry={data.reload} />;
  if (!data.data) return <Loading what="Buscando personas y cuentas" />;
  const { persons, accounts, accountsTotal } = data.data;

  async function pickAccount(id: number) {
    setSelected({ kind: "account", id });
    setAccountError(null);
    try {
      setAccountDetail(await ep.getAccount(ctx.caseId, id));
    } catch (err) {
      setAccountError(err);
    }
  }

  return (
    <div className="split">
      <Panel title="Personas y cuentas">
        <div className="stack">
          <p className="small muted">
            Las cuentas se ordenan por actividad. Hacé clic en una para ver su detalle.{" "}
            <Help text="Una cuenta es un perfil en una red social o servicio. Que aparezca acá no significa que pertenezca a alguien en particular." />
          </p>

          <h3>Personas ({persons.length})</h3>
          {persons.length === 0 && <p className="small muted">Todavía no hay personas. Aparecen cuando FUNES o vos las cargan.</p>}
          <div className="person-grid">
            {persons.map((p) => (
              <button
                key={p.id}
                type="button"
                className="person-card"
                aria-pressed={selected?.kind === "person" && selected.id === p.id}
                onClick={() => setSelected({ kind: "person", id: p.id })}
              >
                <span>{p.label}</span>
                <span className="small muted">{p.status === "proposed" ? "Propuesta" : "Confirmada"}</span>
              </button>
            ))}
          </div>

          <h3>Cuentas ({fmtInt(accountsTotal)})</h3>
          {accounts.length === 0 ? (
            <Empty title="Todavía no hay cuentas en el caso.">
              <p>Podés buscar un usuario en Reunir y recolectar su actividad pública.</p>
            </Empty>
          ) : (
            <div className="person-grid">
              {accounts.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className="person-card"
                  aria-pressed={selected?.kind === "account" && selected.id === a.id}
                  onClick={() => void pickAccount(a.id)}
                >
                  <span style={{ wordBreak: "break-all" }}>@{a.handle}</span>
                  <span className="small muted">
                    {a.platform} · {plural(a.post_count, "publicación", "publicaciones")}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Detalle">
        {!selected && <p className="muted">Elegí una persona o una cuenta para ver su detalle.</p>}
        {selected?.kind === "person" && <EntityPanel key={selected.id} entityId={selected.id} onChanged={data.reload} onDeleted={() => { setSelected(null); data.reload(); }} />}
        {selected?.kind === "account" && accountError ? <ErrorBox error={accountError} /> : null}
        {selected?.kind === "account" && !accountDetail && !accountError && <Loading what="Abriendo la cuenta" />}
        {selected?.kind === "account" && accountDetail && <AccountDetail key={accountDetail.id} account={accountDetail} />}
        {accounts.length === 0 && persons.length === 0 && <Notice tone="info">Nada que mostrar todavía.</Notice>}
      </Panel>
    </div>
  );
}
