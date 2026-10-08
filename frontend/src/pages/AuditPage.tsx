import { useMemo, useState, type FormEvent } from "react";
import * as endpoints from "../api/endpoints";
import type { AuditEventOut } from "../api/types";
import { Badge, Btn, Empty, ErrorBox, Loading, Modal, Segmented } from "../components/ui";
import { Icon } from "../components/Icon";
import { fmtDateTime, fmtInt, isoTitle, shortHash, truncate } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";

const PAGE_SIZE = 50;

interface Filters {
  caseId: string;
  action: string;
  userId: string;
  since: string;
  until: string;
  order: "desc" | "asc";
}

const EMPTY_FILTERS: Filters = { caseId: "", action: "", userId: "", since: "", until: "", order: "desc" };

function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const date = new Date(local);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function AuditPage() {
  const toast = useToast();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [applied, setApplied] = useState<Filters>(EMPTY_FILTERS);
  const [offset, setOffset] = useState(0);
  const [detail, setDetail] = useState<AuditEventOut | null>(null);

  const verify = useAsync(() => endpoints.verifyAudit(), []);
  const cases = useAsync(() => endpoints.listCases({ limit: 200 }), []);

  const events = useAsync(
    () =>
      endpoints.listAuditEvents({
        case_id: applied.caseId ? Number(applied.caseId) : undefined,
        user_id: applied.userId ? Number(applied.userId) : undefined,
        action: applied.action.trim() || undefined,
        since: toIso(applied.since),
        until: toIso(applied.until),
        order: applied.order,
        limit: PAGE_SIZE,
        offset,
      }),
    [applied, offset],
  );

  const caseOptions = useMemo(() => cases.data?.items ?? [], [cases.data]);

  function onApply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (filters.userId && !/^\d+$/.test(filters.userId)) {
      toast.notify("El ID de usuario tiene que ser un número.", "bad");
      return;
    }
    setOffset(0);
    setApplied(filters);
  }

  function onClear() {
    setFilters(EMPTY_FILTERS);
    setApplied(EMPTY_FILTERS);
    setOffset(0);
  }

  const total = events.data?.total ?? 0;
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);

  return (
    <div className="page audit-page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Auditoría</h1>
          <p className="page-sub">
            Cada acción queda en una cadena de hashes: cada evento incluye el hash del anterior. Si alguien altera o borra un
            evento, la verificación lo detecta.
          </p>
        </div>
      </div>

      <section className={`chain-status ${verify.data ? (verify.data.ok ? "chain-ok" : "chain-broken") : ""}`} aria-live="polite">
        {verify.loading && !verify.data ? (
          <Loading label="Verificando la cadena…" rows={1} />
        ) : verify.error ? (
          <ErrorBox error={verify.error} onRetry={verify.reload} title="No se pudo verificar la cadena" />
        ) : verify.data ? (
          <>
            <div className="chain-mark">
              <Icon name={verify.data.ok ? "check" : "ban"} />
            </div>
            <div className="chain-text">
              <p className="chain-kicker mono">VERIFICACIÓN DE INTEGRIDAD</p>
              <p className="chain-title">{verify.data.ok ? "Cadena íntegra" : `Cadena rota en el evento ${verify.data.broken_event_id}`}</p>
              <p className="chain-detail">{verify.data.detail}</p>
              <p className="chain-meta mono">{fmtInt(verify.data.total_events)} eventos verificados</p>
            </div>
            <Btn size="sm" icon="refresh" onClick={verify.reload} disabled={verify.loading}>
              {verify.loading ? "Verificando…" : "Verificar de nuevo"}
            </Btn>
          </>
        ) : null}
      </section>

      <form className="audit-filters" onSubmit={onApply}>
        <div className="filter-field">
          <label className="field-label" htmlFor="af-case">
            Caso
          </label>
          <select id="af-case" className="select" value={filters.caseId} onChange={(e) => setFilters({ ...filters, caseId: e.target.value })}>
            <option value="">Todos los casos</option>
            {caseOptions.map((item) => (
              <option key={item.id} value={item.id}>
                #{item.id} · {truncate(item.name, 48)}
              </option>
            ))}
          </select>
        </div>
        <div className="filter-field">
          <label className="field-label" htmlFor="af-action">
            Acción
          </label>
          <input
            id="af-action"
            className="input mono"
            placeholder="p. ej. menard. o graph.view"
            value={filters.action}
            onChange={(e) => setFilters({ ...filters, action: e.target.value })}
          />
        </div>
        <div className="filter-field filter-narrow">
          <label className="field-label" htmlFor="af-user">
            ID de usuario
          </label>
          <input id="af-user" className="input input-narrow mono" inputMode="numeric" value={filters.userId} onChange={(e) => setFilters({ ...filters, userId: e.target.value })} />
        </div>
        <div className="filter-field">
          <label className="field-label" htmlFor="af-since">
            Desde
          </label>
          <input id="af-since" className="input mono" type="datetime-local" value={filters.since} onChange={(e) => setFilters({ ...filters, since: e.target.value })} />
        </div>
        <div className="filter-field">
          <label className="field-label" htmlFor="af-until">
            Hasta
          </label>
          <input id="af-until" className="input mono" type="datetime-local" value={filters.until} onChange={(e) => setFilters({ ...filters, until: e.target.value })} />
        </div>
        <div className="filter-actions">
          <Segmented<"desc" | "asc">
            label="Orden"
            value={filters.order}
            onChange={(value) => setFilters({ ...filters, order: value })}
            options={[
              { value: "desc", label: "Más nuevos" },
              { value: "asc", label: "Más viejos" },
            ]}
          />
          <Btn type="submit" variant="primary" size="sm">
            Aplicar
          </Btn>
          <Btn type="button" size="sm" variant="ghost" onClick={onClear}>
            Limpiar
          </Btn>
        </div>
      </form>

      {events.loading && !events.data ? <Loading label="Cargando eventos…" rows={5} /> : null}
      {events.error ? <ErrorBox error={events.error} onRetry={events.reload} title="No se pudo cargar la auditoría" /> : null}

      {events.data && events.data.items.length === 0 ? (
        <Empty title="No hay eventos con estos filtros">Probá con otro rango de fechas o quitá el filtro de acción.</Empty>
      ) : null}

      {events.data && events.data.items.length > 0 ? (
        <>
          <div className="table-wrap">
            <table className="table table-dense audit-table">
              <thead>
                <tr>
                  <th className="col-id">#</th>
                  <th className="col-date">Fecha</th>
                  <th className="col-user">Usuario</th>
                  <th className="col-id">Caso</th>
                  <th>Acción</th>
                  <th>Objetivo</th>
                  <th className="col-hash">Hash</th>
                </tr>
              </thead>
              <tbody>
                {events.data.items.map((event) => (
                  <tr key={event.id} className="audit-row" onClick={() => setDetail(event)}>
                    <td className="mono dim">{event.id}</td>
                    <td className="mono" title={isoTitle(event.ts)}>
                      {fmtDateTime(event.ts)}
                    </td>
                    <td className="mono">{event.user_id ? `#${event.user_id}` : <span className="dim">sistema</span>}</td>
                    <td className="mono">{event.case_id ? `#${event.case_id}` : <span className="dim">—</span>}</td>
                    <td>
                      <Badge tone={event.action.startsWith("auth.denied") || event.action.endsWith(".failed") ? "bad" : "neutral"}>{event.action}</Badge>
                    </td>
                    <td className="mono dim">{truncate(event.target, 40)}</td>
                    <td className="mono dim" title={event.hash}>
                      {shortHash(event.hash, 14)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="dim mono">
              {from}–{to} de {fmtInt(total)} eventos
            </span>
            <div className="pager-buttons">
              <Btn size="sm" disabled={offset === 0 || events.loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Anterior
              </Btn>
              <Btn size="sm" disabled={offset + PAGE_SIZE >= total || events.loading} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Siguiente
              </Btn>
            </div>
          </div>
        </>
      ) : null}

      {detail ? (
        <Modal title={`Evento #${detail.id} · ${detail.action}`} onClose={() => setDetail(null)} width={680}>
          <dl className="kv">
            <dt>Fecha (UTC)</dt>
            <dd className="mono">{detail.ts}</dd>
            <dt>Usuario</dt>
            <dd className="mono">{detail.user_id ? `#${detail.user_id}` : "sistema"}</dd>
            <dt>Caso</dt>
            <dd className="mono">{detail.case_id ? `#${detail.case_id}` : "—"}</dd>
            <dt>Objetivo</dt>
            <dd className="mono">{detail.target || "—"}</dd>
            <dt>Hash anterior</dt>
            <dd className="mono hash-full">{detail.prev_hash || "—"}</dd>
            <dt>Hash</dt>
            <dd className="mono hash-full">{detail.hash}</dd>
          </dl>
          <h3 className="block-title">Detalle</h3>
          <pre className="json-view">{JSON.stringify(detail.detail, null, 2)}</pre>
        </Modal>
      ) : null}
    </div>
  );
}
