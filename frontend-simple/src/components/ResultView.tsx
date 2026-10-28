import { useState } from "react";
import * as ep from "../api/endpoints";
import type { Datachunk } from "../api/contract";
import type { SectionOut } from "../api/types";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { errorMessage, plural } from "../lib/format";
import { ENTITY_TYPE_LABEL } from "../lib/labels";
import { Link } from "../lib/router";
import type { Outcome } from "../lib/reunirActions";
import { sectionFor } from "../lib/reunirActions";
import { Button, Notice } from "./ui";
import { useCase } from "./CaseLayout";

export interface ResultItem {
  id: number;
  actionTitle: string;
  outcome?: Outcome;
  error?: string;
}

function kindLabel(kind: string): string {
  return (ENTITY_TYPE_LABEL as Record<string, string>)[kind] ?? (kind === "text" ? "Texto" : kind);
}

/** Vista previa de datachunks: la persona elige cuáles incorporar al expediente. */
function ChunkPicker({ chunks }: { chunks: Datachunk[] }) {
  const ctx = useCase();
  const toast = useToast();
  const sections = useAsync(() => ep.listSections(ctx.caseId), [ctx.caseId]);
  const [picks, setPicks] = useState<Record<number, { on: boolean; section: number | null }>>({});
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<Set<number>>(new Set());

  const list: SectionOut[] = sections.data ?? [];
  const sectionByName = (name: string): number | null => {
    const found = list.find((s) => s.name.toLowerCase() === name.toLowerCase());
    if (found) return found.id;
    return list.find((s) => s.name.toLowerCase() === "sin clasificar")?.id ?? null;
  };
  const pickFor = (index: number, chunk: Datachunk) =>
    picks[index] ?? { on: true, section: sectionByName(sectionFor(chunk.kind)) };

  const selected = chunks.map((chunk, index) => ({ chunk, index, pick: pickFor(index, chunk) })).filter(
    (row) => row.pick.on && !done.has(row.index),
  );

  async function addSelected() {
    setBusy(true);
    let added = 0;
    const failed: string[] = [];
    for (const row of selected) {
      try {
        await ep.createFinding(ctx.caseId, { chunk: row.chunk, section_id: row.pick.section });
        added += 1;
        setDone((prev) => new Set(prev).add(row.index));
      } catch (error) {
        failed.push(errorMessage(error));
      }
    }
    setBusy(false);
    if (added) {
      toast.notify(`Se agregaron ${plural(added, "dato", "datos")} al expediente.`, "ok");
      sections.reload();
    }
    if (failed.length) toast.notify(failed[0], "bad");
  }

  if (!chunks.length) return null;
  return (
    <div className="stack">
      <p className="small muted">
        Nada se agrega solo: marcá lo que quieras sumar al expediente y en qué inciso va.
      </p>
      <div className="result-list">
        {chunks.map((chunk, index) => {
          const pick = pickFor(index, chunk);
          const added = done.has(index);
          return (
            <div className="card-found" key={`${chunk.kind}-${chunk.value}-${index}`}>
              <div className="row">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={pick.on}
                    disabled={added || busy}
                    onChange={(e) => setPicks((prev) => ({ ...prev, [index]: { ...pick, on: e.target.checked } }))}
                  />
                  <span className="sr-only">Sumar {chunk.value} al expediente</span>
                </label>
                <span className="chip">{kindLabel(chunk.kind)}</span>
                <strong className="mono">{chunk.value}</strong>
                {added && <span className="chip chip-ok">Agregado</span>}
              </div>
              {chunk.quote && <blockquote>“{chunk.quote}”</blockquote>}
              <div className="row small">
                <label className="row" style={{ gap: 6 }}>
                  <span className="muted">Inciso</span>
                  <select
                    className="select"
                    value={pick.section ?? ""}
                    disabled={added || busy}
                    onChange={(e) =>
                      setPicks((prev) => ({
                        ...prev,
                        [index]: { on: pick.on, section: e.target.value ? Number(e.target.value) : null },
                      }))
                    }
                  >
                    <option value="">Sin inciso</option>
                    {list.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            </div>
          );
        })}
      </div>
      <div className="btn-row">
        <Button
          variant="primary"
          disabled={!ctx.canWrite || busy || selected.length === 0}
          onClick={addSelected}
        >
          {busy ? "Agregando…" : `Agregar ${selected.length} al expediente`}
        </Button>
        {!ctx.canWrite && <span className="small muted">{ctx.readOnlyReason}</span>}
      </div>
    </div>
  );
}

export function ResultView({ item }: { item: ResultItem }) {
  const ctx = useCase();
  const outcome = item.outcome;

  if (item.error) {
    return (
      <Notice tone="bad" title={`No se pudo: ${item.actionTitle}`}>
        <p>{item.error}</p>
      </Notice>
    );
  }
  if (!outcome) return null;

  const hasChunks = Boolean(outcome.chunks && outcome.chunks.length);
  return (
    <article className="panel result" aria-label={`Resultado: ${outcome.title}`}>
      <header className="panel-head">
        <h2 className="panel-title">Esto encontré: {outcome.title}</h2>
        {outcome.proposals > 0 && (
          <Link to={`/casos/${ctx.caseId}/revisar`} className="btn btn-secondary btn-small">
            Revisar {plural(outcome.proposals, "propuesta", "propuestas")}
          </Link>
        )}
      </header>
      <ul className="stack" style={{ margin: 0, paddingLeft: "1.2em" }}>
        {outcome.lines.map((line) => (
          <li key={line}>{line}</li>
        ))}
      </ul>
      {outcome.warnings.length > 0 && (
        <Notice tone="warn" title="Avisos">
          <ul style={{ margin: 0, paddingLeft: "1.2em" }}>
            {outcome.warnings.map((w) => (
              <li key={w}>{w}</li>
            ))}
          </ul>
        </Notice>
      )}
      {hasChunks && (
        <div style={{ marginTop: 14 }}>
          <ChunkPicker chunks={outcome.chunks ?? []} />
        </div>
      )}
      <details style={{ marginTop: 12 }}>
        <summary className="small">Detalle técnico</summary>
        <pre style={{ marginTop: 8 }}>{JSON.stringify(outcome.details, null, 2)}</pre>
      </details>
    </article>
  );
}
