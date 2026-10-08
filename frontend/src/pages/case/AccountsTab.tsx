import { useEffect, useState, type FormEvent } from "react";
import * as endpoints from "../../api/endpoints";
import type { AccountOut, PostOut } from "../../api/types";
import { Badge, Btn, Empty, ErrorBox, Loading, Segmented } from "../../components/ui";
import { Icon } from "../../components/Icon";
import { fmtDate, fmtDateTime, fmtInt, truncate } from "../../lib/format";
import { POST_KIND_LABEL } from "../../lib/labels";
import { Link, useLocation } from "../../lib/router";
import { useAsync } from "../../lib/useAsync";
import type { CaseTabProps } from "./tabProps";

const PAGE_SIZE = 50;
const POSTS_PAGE = 20;

export function AccountsTab({ caseId }: CaseTabProps) {
  const { search } = useLocation();
  const [query, setQuery] = useState("");
  const [applied, setApplied] = useState("");
  const [platform, setPlatform] = useState("");
  const [offset, setOffset] = useState(0);
  const initialOpen = Number(new URLSearchParams(search).get("cuenta")) || null;
  const [openId, setOpenId] = useState<number | null>(initialOpen);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setApplied(query.trim());
      setOffset(0);
    }, 260);
    return () => window.clearTimeout(timer);
  }, [query]);

  const accounts = useAsync(
    () => endpoints.listAccounts(caseId, { q: applied || undefined, platform: platform || undefined, limit: PAGE_SIZE, offset }),
    [caseId, applied, platform, offset],
  );
  const total = accounts.data?.total ?? 0;

  return (
    <div className="accounts">
      <div className="toolbar">
        <div className="search-box">
          <Icon name="search" />
          <input className="input" type="search" placeholder="Handle, nombre o biografía" aria-label="Buscar cuentas" value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <select className="select select-compact" aria-label="Plataforma" value={platform} onChange={(e) => { setPlatform(e.target.value); setOffset(0); }}>
          <option value="">Todas las plataformas</option>
          {["x", "bluesky", "mastodon", "reddit", "telegram", "github", "instagram", "discord", "generic"].map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <span className="dim small mono">{fmtInt(total)} cuentas</span>
      </div>

      {accounts.loading && !accounts.data ? <Loading label="Cargando cuentas…" rows={4} /> : null}
      {accounts.error ? <ErrorBox error={accounts.error} onRetry={accounts.reload} title="No se pudieron cargar las cuentas" /> : null}
      {accounts.data && accounts.data.items.length === 0 ? (
        <Empty title="No hay cuentas en el caso">
          Las cuentas llegan desde los conectores, las importaciones de exportaciones o la extensión Aleph Lens.
        </Empty>
      ) : null}

      {accounts.data && accounts.data.items.length > 0 ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Plataforma</th>
                  <th>Handle</th>
                  <th>Nombre visible</th>
                  <th className="col-num">Seguidores</th>
                  <th className="col-num">Publicaciones</th>
                  <th className="col-date">Creada en la plataforma</th>
                </tr>
              </thead>
              <tbody>
                {accounts.data.items.map((account) => (
                  <tr key={account.id} className={`clickable ${openId === account.id ? "row-on" : ""}`} onClick={() => setOpenId(account.id)}>
                    <td>
                      <Badge tone="neutral">{account.platform}</Badge>
                    </td>
                    <td className="mono">
                      <button type="button" className="row-link mono" onClick={(e) => { e.stopPropagation(); setOpenId(account.id); }}>
                        @{account.handle}
                      </button>
                    </td>
                    <td>{truncate(account.display_name, 40)}</td>
                    <td className="col-num mono">{account.followers ?? "—"}</td>
                    <td className="col-num mono">{fmtInt(account.post_count)}</td>
                    <td className="mono dim">{account.created_at_platform ? fmtDate(account.created_at_platform) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="dim mono small">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} de {total}
            </span>
            <div className="pager-buttons">
              <Btn size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Anterior
              </Btn>
              <Btn size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Siguiente
              </Btn>
            </div>
          </div>
        </>
      ) : null}

      {openId ? <AccountDrawer caseId={caseId} accountId={openId} onClose={() => setOpenId(null)} /> : null}
    </div>
  );
}

function AccountDrawer({ caseId, accountId, onClose }: { caseId: number; accountId: number; onClose: () => void }) {
  const account = useAsync<AccountOut>(() => endpoints.getAccount(caseId, accountId), [caseId, accountId]);
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  const [kind, setKind] = useState<"" | "original" | "reply" | "repost" | "quote">("");
  const [text, setText] = useState("");
  const [appliedText, setAppliedText] = useState("");
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setAppliedText(text.trim());
      setOffset(0);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [text]);

  // Cada consulta de publicaciones queda auditada en la API: se pide solo al abrir o al filtrar.
  const posts = useAsync(
    () =>
      endpoints.listPosts(caseId, accountId, {
        order,
        q: appliedText || undefined,
        kind: kind || undefined,
        limit: POSTS_PAGE,
        offset,
      }),
    [caseId, accountId, order, kind, appliedText, offset],
  );

  function onSubmit(event: FormEvent) {
    event.preventDefault();
  }

  return (
    <aside className="drawer" aria-label="Detalle de la cuenta">
      <header className="drawer-head">
        <div>
          <p className="mono dim small">cuenta #{accountId}</p>
          <h2 className="drawer-title mono">{account.data ? `@${account.data.handle}` : "Cargando…"}</h2>
          {account.data ? <Badge tone="neutral">{account.data.platform}</Badge> : null}
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Cerrar">
          <Icon name="x" />
        </button>
      </header>

      {account.loading && !account.data ? <Loading rows={2} /> : null}
      {account.error ? <ErrorBox error={account.error} onRetry={account.reload} /> : null}

      {account.data ? (
        <>
          <p className="drawer-name">{account.data.display_name || <span className="dim">sin nombre visible</span>}</p>
          {account.data.bio ? <p className="account-bio">{truncate(account.data.bio, 240)}</p> : null}
          <dl className="kv kv-compact">
            <dt>Seguidores</dt>
            <dd className="mono">{account.data.followers ?? "—"}</dd>
            <dt>Seguidos</dt>
            <dd className="mono">{account.data.following ?? "—"}</dd>
            <dt>Publicaciones</dt>
            <dd className="mono">{fmtInt(account.data.post_count)}</dd>
            <dt>Creada</dt>
            <dd className="mono">{account.data.created_at_platform ? fmtDateTime(account.data.created_at_platform) : "—"}</dd>
            <dt>URL</dt>
            <dd className="mono small break">{account.data.url ? truncate(account.data.url, 60) : "—"}</dd>
          </dl>
          {account.data.entity_id ? (
            <Link className="link-inline" to={`/casos/${caseId}/grafo?nodo=${account.data.entity_id}`}>
              Ver la entidad en el grafo
            </Link>
          ) : null}
        </>
      ) : null}

      <h3 className="block-title">Publicaciones</h3>
      <form className="drawer-filters" onSubmit={onSubmit}>
        <input className="input input-sm" type="search" placeholder="Buscar en el texto" aria-label="Buscar en publicaciones" value={text} onChange={(e) => setText(e.target.value)} />
        <select className="select select-xs" aria-label="Tipo" value={kind} onChange={(e) => { setKind(e.target.value as typeof kind); setOffset(0); }}>
          <option value="">Todas</option>
          <option value="original">Originales</option>
          <option value="reply">Respuestas</option>
          <option value="repost">Reposteos</option>
          <option value="quote">Citas</option>
        </select>
        <Segmented<"desc" | "asc">
          label="Orden de las publicaciones"
          value={order}
          onChange={(value) => {
            setOrder(value);
            setOffset(0);
          }}
          options={[
            { value: "desc", label: "Recientes" },
            { value: "asc", label: "Antiguas" },
          ]}
        />
      </form>

      {posts.loading && !posts.data ? <Loading label="Leyendo publicaciones…" rows={3} /> : null}
      {posts.error ? <ErrorBox error={posts.error} onRetry={posts.reload} title="No se pudieron leer las publicaciones" /> : null}
      {posts.data && posts.data.items.length === 0 ? <p className="dim small">Ninguna publicación coincide.</p> : null}
      <ol className="post-list">
        {(posts.data?.items ?? []).map((post: PostOut) => (
          <li key={post.id} className="post">
            <header className="post-head">
              <span className="mono small dim">{post.created_at ? fmtDateTime(post.created_at) : "sin fecha"}</span>
              <Badge tone="neutral">{POST_KIND_LABEL[post.kind] ?? post.kind}</Badge>
            </header>
            <p className="post-text">{truncate(post.text, 420)}</p>
          </li>
        ))}
      </ol>
      {posts.data && posts.data.total > POSTS_PAGE ? (
        <div className="pager">
          <span className="dim mono small">
            {offset + 1}–{Math.min(offset + POSTS_PAGE, posts.data.total)} de {posts.data.total}
          </span>
          <div className="pager-buttons">
            <Btn size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - POSTS_PAGE))}>
              Anterior
            </Btn>
            <Btn size="sm" disabled={offset + POSTS_PAGE >= posts.data.total} onClick={() => setOffset(offset + POSTS_PAGE)}>
              Siguiente
            </Btn>
          </div>
        </div>
      ) : null}
      <p className="dim small">Leer publicaciones queda registrado en la auditoría.</p>
    </aside>
  );
}
