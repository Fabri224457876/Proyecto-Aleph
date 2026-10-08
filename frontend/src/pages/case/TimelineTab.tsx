import { useMemo, useState } from "react";
import * as endpoints from "../../api/endpoints";
import type { TimelineItem } from "../../api/types";
import { Badge, Btn, Empty, ErrorBox, Loading } from "../../components/ui";
import { fmtDay, fmtInt, fmtTime, truncate } from "../../lib/format";
import { POST_KIND_LABEL } from "../../lib/labels";
import { Link } from "../../lib/router";
import { useAsync } from "../../lib/useAsync";
import type { CaseTabProps } from "./tabProps";

const PAGE_SIZE = 60;

type Kind = "post" | "event";

function toIso(local: string): string | undefined {
  if (!local) return undefined;
  const date = new Date(local);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

function dayKey(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "sin fecha" : date.toISOString().slice(0, 10);
}

export function TimelineTab({ caseId }: CaseTabProps) {
  const [kinds, setKinds] = useState<Kind[]>(["post", "event"]);
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [since, setSince] = useState("");
  const [until, setUntil] = useState("");
  const [offset, setOffset] = useState(0);

  const timeline = useAsync(
    () =>
      endpoints.getTimeline(caseId, {
        kind: kinds.length === 2 ? undefined : kinds,
        order,
        since: toIso(since),
        until: toIso(until),
        limit: PAGE_SIZE,
        offset,
      }),
    [caseId, kinds, order, since, until, offset],
  );

  const groups = useMemo(() => {
    const map = new Map<string, TimelineItem[]>();
    for (const item of timeline.data?.items ?? []) {
      const key = dayKey(item.at);
      if (!map.has(key)) map.set(key, []);
      map.get(key)?.push(item);
    }
    return [...map.entries()];
  }, [timeline.data]);

  const total = timeline.data?.total ?? 0;

  function toggleKind(kind: Kind) {
    setOffset(0);
    setKinds((current) => {
      if (current.includes(kind)) return current.length === 1 ? current : current.filter((k) => k !== kind);
      return [...current, kind];
    });
  }

  return (
    <div className="timeline">
      <div className="toolbar tl-toolbar">
        <div className="chip-group" role="group" aria-label="Qué mostrar">
          <button type="button" className={`chip ${kinds.includes("post") ? "on" : ""}`} aria-pressed={kinds.includes("post")} onClick={() => toggleKind("post")}>
            Publicaciones
          </button>
          <button type="button" className={`chip ${kinds.includes("event") ? "on" : ""}`} aria-pressed={kinds.includes("event")} onClick={() => toggleKind("event")}>
            Eventos
          </button>
        </div>
        <div className="date-range">
          <label className="sr-only" htmlFor="tl-since">
            Desde
          </label>
          <input id="tl-since" className="input input-sm mono" type="date" value={since} onChange={(e) => { setSince(e.target.value); setOffset(0); }} />
          <span className="dim">→</span>
          <label className="sr-only" htmlFor="tl-until">
            Hasta
          </label>
          <input id="tl-until" className="input input-sm mono" type="date" value={until} onChange={(e) => { setUntil(e.target.value); setOffset(0); }} />
        </div>
        <Btn size="sm" variant="ghost" onClick={() => { setSince(""); setUntil(""); setOffset(0); }} disabled={!since && !until}>
          Todo el período
        </Btn>
        <Btn size="sm" onClick={() => { setOrder(order === "asc" ? "desc" : "asc"); setOffset(0); }}>
          {order === "asc" ? "Más antiguo primero" : "Más reciente primero"}
        </Btn>
        <span className="dim small mono">{fmtInt(total)} elementos</span>
      </div>

      {timeline.data && timeline.data.undated_events > 0 ? (
        <p className="notice notice-muted small">
          {timeline.data.undated_events} eventos sin fecha interpretable no aparecen en la línea. Agregá una fecha en sus datos (date, fecha u occurred_at).
        </p>
      ) : null}

      {timeline.loading && !timeline.data ? <Loading label="Armando la línea de tiempo…" rows={4} /> : null}
      {timeline.error ? <ErrorBox error={timeline.error} onRetry={timeline.reload} title="No se pudo armar la línea de tiempo" /> : null}
      {timeline.data && timeline.data.items.length === 0 ? (
        <Empty title="Nada para mostrar en este período">Ajustá las fechas o las categorías. Las publicaciones y los eventos con fecha aparecen acá.</Empty>
      ) : null}

      <ol className="tl-axis">
        {groups.map(([day, items]) => (
          <li key={day} className="tl-day">
            <h3 className="tl-day-title">{day === "sin fecha" ? "Sin fecha" : fmtDay(`${day}T12:00:00Z`)}</h3>
            <ul className="tl-items">
              {items.map((item) => (
                <li key={`${item.kind}-${item.post_id ?? item.entity_id}-${item.at}`} className={`tl-item tl-${item.kind}`}>
                  <span className="tl-time mono">{fmtTime(item.at)}</span>
                  <span className="tl-dot" aria-hidden="true" />
                  <div className="tl-card">
                    <div className="tl-card-head">
                      <Badge tone="neutral">{item.kind === "event" ? "evento" : POST_KIND_LABEL[item.post_kind] ?? (item.post_kind || "publicación")}</Badge>
                      <span className="mono small">{truncate(item.title, 70)}</span>
                      {item.platform ? <span className="dim small">{item.platform}</span> : null}
                    </div>
                    {item.text ? <p className="tl-text">{truncate(item.text, 240)}</p> : null}
                    <div className="tl-links">
                      {item.kind === "post" && item.account_id ? (
                        <Link className="link-inline" to={`/casos/${caseId}/cuentas?cuenta=${item.account_id}`}>
                          Ver la cuenta
                        </Link>
                      ) : null}
                      {item.entity_id ? (
                        <Link className="link-inline" to={`/casos/${caseId}/grafo?nodo=${item.entity_id}`}>
                          Ver en el grafo
                        </Link>
                      ) : null}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>

      {timeline.data && total > PAGE_SIZE ? (
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
      ) : null}
    </div>
  );
}
