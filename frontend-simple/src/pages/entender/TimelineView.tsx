import { useMemo, useState } from "react";
import * as ep from "../../api/endpoints";
import type { TimelineItem } from "../../api/types";
import { useCase } from "../../components/CaseLayout";
import { Chip, Empty, ErrorBox, Loading, Panel, Button } from "../../components/ui";
import { useAsync } from "../../lib/useAsync";
import { fmtDay, fmtTime, plural, truncate } from "../../lib/format";

type Kind = "all" | "post" | "event";

export function TimelineView() {
  const ctx = useCase();
  const [kind, setKind] = useState<Kind>("all");
  const [limit, setLimit] = useState(200);
  const data = useAsync(
    () =>
      ep.getTimeline(ctx.caseId, {
        order: "asc",
        limit,
        kind: kind === "all" ? undefined : [kind],
      }),
    [ctx.caseId, kind, limit],
  );

  const groups = useMemo(() => {
    const map = new Map<string, TimelineItem[]>();
    for (const item of data.data?.items ?? []) {
      const key = item.at ? fmtDay(item.at) : "Sin fecha";
      const list = map.get(key) ?? [];
      list.push(item);
      map.set(key, list);
    }
    return [...map.entries()];
  }, [data.data]);

  return (
    <div className="stack">
      <Panel
        title="Línea de tiempo"
        actions={
          <div className="btn-row">
            {(["all", "post", "event"] as Kind[]).map((k) => (
              <button
                key={k}
                type="button"
                className={`btn btn-small ${kind === k ? "btn-primary" : "btn-secondary"}`}
                aria-pressed={kind === k}
                onClick={() => setKind(k)}
              >
                {k === "all" ? "Todo" : k === "post" ? "Publicaciones" : "Eventos"}
              </button>
            ))}
          </div>
        }
      >
        <p className="small muted">
          Lo que pasó, en orden. Las publicaciones vienen de las cuentas del caso; los eventos son fechas que cargaste o que extrajo FUNES.
          {data.data && data.data.undated_events ? ` Hay ${plural(data.data.undated_events, "evento sin fecha", "eventos sin fecha")} que no aparecen acá.` : ""}
        </p>
      </Panel>

      {data.error ? <ErrorBox error={data.error} onRetry={data.reload} /> : null}
      {!data.data && !data.error && <Loading what="Ordenando la línea de tiempo" />}
      {data.data && data.data.items.length === 0 && (
        <Empty title="No hay nada fechado todavía.">
          <p>Cuando haya publicaciones o eventos con fecha, van a aparecer acá en orden.</p>
        </Empty>
      )}

      {groups.map(([day, items]) => (
        <section key={day} aria-label={day}>
          <p className="timeline-day">{day}</p>
          <ol className="timeline">
            {items.map((item, index) => (
              <li key={`${item.kind}-${item.post_id ?? item.entity_id ?? index}-${item.at}`} className={`event ${item.kind === "event" ? "is-event" : ""}`}>
                <div className="row small" style={{ justifyContent: "space-between" }}>
                  <span className="timeline-time">{fmtTime(item.at)}</span>
                  <span className="row" style={{ gap: 6 }}>
                    <Chip>{item.kind === "post" ? "Publicación" : "Evento"}</Chip>
                    {item.platform && <span className="muted">{item.platform}{item.handle ? ` · @${item.handle}` : ""}</span>}
                  </span>
                </div>
                <p style={{ fontWeight: 600, wordBreak: "break-word" }}>{item.title}</p>
                {item.text && <p className="small muted" style={{ wordBreak: "break-word" }}>{truncate(item.text, 240)}</p>}
              </li>
            ))}
          </ol>
        </section>
      ))}

      {data.data && data.data.total > data.data.items.length && (
        <Button onClick={() => setLimit((n) => n + 200)}>Mostrar más</Button>
      )}
    </div>
  );
}
