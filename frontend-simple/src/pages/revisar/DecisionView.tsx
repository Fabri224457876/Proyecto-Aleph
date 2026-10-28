import { Fragment, useEffect, useState } from "react";
import * as ep from "../../api/endpoints";
import type { Contradiction } from "../../api/contract";
import type { EntityOut, LinkOut, RelationOut, SignalResult } from "../../api/types";
import { useCase } from "../../components/CaseLayout";
import { Button, Chip, ErrorBox, Help, Notice, Loading, Field } from "../../components/ui";
import { useAsync } from "../../lib/useAsync";
import { useToast } from "../../lib/toast";
import { errorMessage, propText, pct } from "../../lib/format";
import {
  entityTypeLabel,
  relationLabel,
  SIGNAL_FAMILY_LABEL,
  signalLabel,
  ITEM_STATUS_LABEL,
} from "../../lib/labels";
import { Link } from "../../lib/router";

export type QueueItem =
  | { kind: "hypothesis"; key: string; link: LinkOut }
  | { kind: "entity"; key: string; entity: EntityOut }
  | { kind: "relation"; key: string; relation: RelationOut }
  | { kind: "contradiction"; key: string; contradiction: Contradiction };

export interface Decision {
  message: string;
}

interface Props {
  item: QueueItem;
  position: number;
  total: number;
  onPrev?: () => void;
  onNext?: () => void;
  onClose: () => void;
  onDecided: (decision: Decision) => void;
  onDiscardLocal: (message: string) => void;
}

function fmtScore(value: number): string {
  return value.toFixed(2).replace(".", ",");
}

function strength(score: number): string {
  if (score >= 0.7) return "Fuerte";
  if (score >= 0.4) return "Moderada";
  return "Débil";
}

function topSignals(signals: SignalResult[]): SignalResult[] {
  return signals
    .filter((s) => s.available)
    .slice()
    .sort((a, b) => b.score * b.weight - a.score * a.weight)
    .slice(0, 4);
}

function AccountCard({ account }: { account: LinkOut["account_a"] }) {
  return (
    <div className="account-card">
      <span className="chip">{account.platform}</span>
      <span className="handle">@{account.handle}</span>
      {account.display_name && <span className="muted">{account.display_name}</span>}
    </div>
  );
}

function HypothesisBody({ link }: { link: LinkOut }) {
  const signals = link.signals?.signals ?? [];
  const top = topSignals(signals);
  return (
    <div className="stack">
      <p className="decide-kind">Hipótesis de MENARD</p>
      <h2 className="decide-title">Estas dos cuentas podrían ser del mismo operador</h2>
      <p className="hypothesis-warning">
        Es una hipótesis con evidencia, no una identificación. La decisión es tuya y queda registrada.
      </p>

      <div className="accounts-pair">
        <AccountCard account={link.account_a} />
        <span className="account-pair-mark" aria-hidden="true">≈</span>
        <AccountCard account={link.account_b} />
      </div>

      <div className="score-box">
        <div>
          <p className="small muted">
            Puntaje de similitud <Help text="Mide cuántas señales coinciden entre las dos cuentas. Va de 0 a 1 y no es una probabilidad." />
          </p>
          <p className="score-number">{fmtScore(link.score)}</p>
        </div>
        <div className="grow">
          <div className="bar" role="img" aria-label={`Puntaje ${fmtScore(link.score)} de 1`}>
            <span style={{ width: `${Math.round(link.score * 100)}%` }} />
          </div>
          <p className="small" style={{ marginTop: 8 }}>
            Confianza: <strong>{link.confidence || "sin dato"}</strong>{" "}
            <Help term="confianza" />
          </p>
        </div>
      </div>

      {link.summary && <p>{link.summary}</p>}

      <section aria-labelledby="senales-top" className="stack">
        <h3 id="senales-top">Las señales que más pesan</h3>
        {top.length === 0 && <p className="muted">No hay señales suficientes para explicar esta hipótesis.</p>}
        {top.map((signal) => (
          <div className="signal" key={signal.name}>
            <div className="signal-head">
              <strong>{signalLabel(signal.name)}</strong>
              <span className="row small">
                <Chip>{SIGNAL_FAMILY_LABEL[signal.family] ?? signal.family}</Chip>
                <Chip tone={signal.score >= 0.7 ? "accent" : "neutral"}>
                  {strength(signal.score)} ({fmtScore(signal.score)})
                </Chip>
              </span>
            </div>
            <p className="small">{signal.explanation}</p>
            {signal.evidence[0] && (
              <p className="signal-evidence">
                Ejemplo: {signal.evidence[0].description}. A: “{signal.evidence[0].a}”. B: “{signal.evidence[0].b}”.
              </p>
            )}
          </div>
        ))}
      </section>

      <details>
        <summary className="small" style={{ cursor: "pointer", color: "var(--accent)" }}>
          Ver todas las señales ({signals.length})
        </summary>
        <div className="table-wrap" style={{ marginTop: 10 }}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Señal</th>
                <th scope="col">Familia</th>
                <th scope="col">Puntaje</th>
                <th scope="col">Peso</th>
                <th scope="col">Detalle</th>
              </tr>
            </thead>
            <tbody>
              {signals.map((signal) => (
                <tr key={signal.name}>
                  <td>{signalLabel(signal.name)}</td>
                  <td>{SIGNAL_FAMILY_LABEL[signal.family] ?? signal.family}</td>
                  <td>{signal.available ? fmtScore(signal.score) : "sin datos suficientes"}</td>
                  <td>{fmtScore(signal.weight)}</td>
                  <td className="small">{signal.explanation}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="small muted" style={{ marginTop: 8 }}>
          Dato técnico: <span className="mono">{link.signals?.confidence ?? "—"}</span> · hipótesis {link.id}
        </p>
      </details>
    </div>
  );
}

function EntityBody({ entity }: { entity: EntityOut }) {
  const source = useAsync(
    () => (entity.source_id ? ep.getSource(entity.case_id, entity.source_id) : Promise.resolve(null)),
    [entity.id],
  );
  const props = Object.entries(entity.props ?? {});
  return (
    <div className="stack">
      <p className="decide-kind">Entidad propuesta</p>
      <h2 className="decide-title">
        {entityTypeLabel(entity.type)}: {entity.label}
      </h2>
      <div className="row">
        <Chip>{entityTypeLabel(entity.type)}</Chip>
        <Chip>{ITEM_STATUS_LABEL[entity.status] ?? entity.status}</Chip>
      </div>
      <section className="stack" aria-labelledby="origen">
        <h3 id="origen">De dónde sale</h3>
        {entity.source_id === null && (
          <p>No tiene una fuente registrada. Pudo cargarse a mano, o el sistema no la asoció a un texto.</p>
        )}
        {entity.source_id !== null && source.loading && <Loading what="Buscando la fuente" />}
        {entity.source_id !== null && source.error ? <ErrorBox error={source.error} /> : null}
        {source.data && (
          <p>
            Fuente {source.data.id}: {source.data.reference || source.data.kind}. Fiabilidad {source.data.reliability},
            credibilidad {source.data.credibility}. <Link to={`/casos/${entity.case_id}/mas?seccion=fuentes`}>Ver la fuente</Link>
          </p>
        )}
        {entity.confidence !== null && entity.confidence !== undefined && (
          <p className="small muted">Confianza que le dio el sistema: {pct(entity.confidence)}.</p>
        )}
      </section>
      {props.length > 0 && (
        <section className="stack" aria-labelledby="datos">
          <h3 id="datos">Datos que trae</h3>
          <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "6px 14px", margin: 0 }}>
            {props.map(([key, value]) => (
              <Fragment key={key}>
                <dt className="muted">{key.replace(/_/g, " ")}</dt>
                <dd style={{ margin: 0, wordBreak: "break-word" }}>{propText(value)}</dd>
              </Fragment>
            ))}
          </dl>
        </section>
      )}
    </div>
  );
}

function RelationBody({ relation }: { relation: RelationOut }) {
  const caseId = relation.case_id;
  const src = useAsync(() => ep.getEntity(caseId, relation.src_id), [relation.id]);
  const dst = useAsync(() => ep.getEntity(caseId, relation.dst_id), [relation.id]);
  const label = (e: { data?: EntityOut; error?: unknown; loading: boolean }, fallback: number) =>
    e.data ? e.data.label : e.loading ? "…" : `entidad ${fallback}`;
  return (
    <div className="stack">
      <p className="decide-kind">Relación propuesta</p>
      <h2 className="decide-title">
        {label(src, relation.src_id)} → {relationLabel(relation.type)} → {label(dst, relation.dst_id)}
      </h2>
      <p>
        El sistema propone que estas dos entidades estén conectadas así. Confirmala solo si el texto o la fuente lo respalda.
      </p>
      <div className="row">
        <Chip>{relationLabel(relation.type)}</Chip>
        <Chip>{pct(relation.confidence)} de confianza</Chip>
      </div>
      {relation.source_id === null ? (
        <p className="small muted">Sin fuente registrada.</p>
      ) : (
        <p className="small muted">Fuente {relation.source_id}.</p>
      )}
    </div>
  );
}

function ContradictionBody({ c }: { c: Contradiction }) {
  return (
    <div className="stack">
      <p className="decide-kind">Posible contradicción entre fuentes</p>
      <h2 className="decide-title">{c.subject || "Dos fuentes dicen cosas que no coinciden"}</h2>
      <div className="row">
        <Chip tone={c.severity === "alta" ? "bad" : "warn"}>Severidad {c.severity}</Chip>
        <Chip>{c.kind.replace(/_/g, " ")}</Chip>
      </div>
      <p>{c.summary}</p>
      {c.readings && c.readings.length > 0 && (
        <section className="stack" aria-labelledby="lecturas">
          <h3 id="lecturas">Lo que dice cada fuente</h3>
          <ul className="stack" style={{ margin: 0, paddingLeft: "1.2em" }}>
            {c.readings.map((r, i) => (
              <li key={`${r.kind}-${i}`}>{r.description}</li>
            ))}
          </ul>
        </section>
      )}
      {c.sources.length > 0 && <p className="small muted">Fuentes: {c.sources.join(" · ")}</p>}
      {c.explanation && (
        <section className="stack" aria-labelledby="explica">
          <h3 id="explica">Explicación</h3>
          <p>{c.explanation}</p>
          <p className="small muted">
            {c.explanation_source === "llm" ? "Redactada por la IA local." : "Texto de plantilla, sin IA."}
          </p>
        </section>
      )}
    </div>
  );
}

export function DecisionView({ item, position, total, onPrev, onNext, onClose, onDecided, onDiscardLocal }: Props) {
  const ctx = useCase();
  const toast = useToast();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const sections = useAsync(() => ep.listSections(ctx.caseId), [ctx.caseId]);
  // Se vuelve a leer la hipótesis con su evidencia actual (GET /menard/links/{id}).
  const freshLink = useAsync(
    () => (item.kind === "hypothesis" ? ep.getLink(ctx.caseId, item.link.id) : Promise.resolve(null)),
    [item.key],
  );

  useEffect(() => {
    setNote("");
    setError(null);
  }, [item.key]);

  async function decide(confirm: boolean) {
    if (busy || !ctx.canWrite) return;
    setBusy(true);
    setError(null);
    try {
      if (item.kind === "hypothesis") {
        await ep.reviewLink(ctx.caseId, item.link.id, confirm ? "confirm" : "reject", note.trim());
        onDecided({
          message: confirm
            ? "Hipótesis confirmada. Quedó una relación «mismo operador» en el grafo."
            : "Hipótesis descartada.",
        });
      } else if (item.kind === "entity") {
        await ep.reviewEntity(ctx.caseId, item.entity.id, confirm ? "accept" : "reject", note.trim());
        onDecided({ message: confirm ? "Entidad confirmada." : "Entidad descartada." });
      } else if (item.kind === "relation") {
        await ep.reviewRelation(ctx.caseId, item.relation.id, confirm ? "accept" : "reject", note.trim());
        onDecided({ message: confirm ? "Relación confirmada." : "Relación descartada." });
      } else {
        if (!confirm) {
          onDiscardLocal("Descartada en esta sesión. La API no guarda el descarte de una contradicción.");
          return;
        }
        const c = item.contradiction;
        const actividad = (sections.data ?? []).find((s) => s.name.toLowerCase() === "actividad");
        await ep.createFinding(ctx.caseId, {
          section_id: actividad?.id ?? null,
          chunk: {
            kind: "text",
            value: `${c.subject}: ${c.summary}`.slice(0, 2000),
            quote: "",
            context: c.explanation ?? "",
            page_url: "FUNES · contradicciones del caso",
            page_title: "Contradicción entre fuentes (FUNES)",
            platform: "generic",
            detected_by: "funes",
            captured_at: new Date().toISOString(),
          },
          note: note.trim(),
        });
        onDecided({ message: "Contradicción incorporada al expediente." });
      }
    } catch (err) {
      const message = errorMessage(err);
      setError(err);
      toast.notify(message, "bad");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "c" || event.key === "C") void decide(true);
      else if (event.key === "d" || event.key === "D") void decide(false);
      else if (event.key === "ArrowRight" && onNext) onNext();
      else if (event.key === "ArrowLeft" && onPrev) onPrev();
      else if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const confirmLabel =
    item.kind === "hypothesis"
      ? "Confirmar hipótesis"
      : item.kind === "contradiction"
        ? "Incorporar al expediente"
        : item.kind === "relation"
          ? "Confirmar relación"
          : "Confirmar entidad";
  const discardLabel = "Descartar";
  const consequence =
    item.kind === "hypothesis"
      ? "Al confirmar se agrega al grafo una relación «mismo operador» entre las dos cuentas. Al descartar, la hipótesis queda archivada."
      : item.kind === "entity"
        ? "Al confirmar, la entidad entra al grafo como confirmada. Al descartar, no entra."
        : item.kind === "relation"
          ? "Al confirmar, la relación entra al grafo. Al descartar, no entra."
          : "Al incorporarla, queda en el expediente como un hallazgo con su origen. Al descartarla, sale de la lista por esta sesión.";

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <p className="small muted">
          Decisión {position} de {total}
        </p>
        <div className="btn-row">
          <Button variant="ghost" onClick={onClose}>
            Volver a la lista
          </Button>
        </div>
      </div>

      <div className="decide">
        <section className="panel" aria-label="Lo que propone el sistema">
          {item.kind === "hypothesis" && <HypothesisBody link={freshLink.data ?? item.link} />}
          {item.kind === "entity" && <EntityBody entity={item.entity} />}
          {item.kind === "relation" && <RelationBody relation={item.relation} />}
          {item.kind === "contradiction" && <ContradictionBody c={item.contradiction} />}
        </section>

        <aside className="decide-side" aria-label="Tu decisión">
          <section className="panel">
            <h2 className="panel-title" style={{ marginBottom: 8 }}>
              Tu decisión
            </h2>
            <p className="small muted" style={{ marginBottom: 12 }}>
              {consequence}
            </p>
            {!ctx.canWrite && <Notice tone="warn">{ctx.readOnlyReason}</Notice>}
            <Field label={<>Nota <span className="muted">(opcional)</span></>} hint="Queda en la auditoría junto con tu decisión.">
              {(id) => (
                <textarea
                  id={id}
                  className="textarea"
                  rows={3}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  disabled={!ctx.canWrite || busy}
                  maxLength={2000}
                />
              )}
            </Field>
            {error ? <p className="field-error">{errorMessage(error)}</p> : null}
            <div className="decision-buttons" style={{ marginTop: 12 }}>
              <Button variant="primary" big disabled={!ctx.canWrite || busy} onClick={() => void decide(true)}>
                {confirmLabel}
              </Button>
              <Button variant="danger" big disabled={!ctx.canWrite || busy} onClick={() => void decide(false)}>
                {discardLabel}
              </Button>
            </div>
            <p className="keyhint" style={{ marginTop: 12 }}>
              Atajos (fuera de la nota): <kbd>C</kbd> confirma · <kbd>D</kbd> descarta · <kbd>→</kbd> siguiente · <kbd>←</kbd>{" "}
              anterior · <kbd>Esc</kbd> vuelve a la lista.
            </p>
          </section>
          <div className="btn-row">
            <Button disabled={!onPrev} onClick={onPrev}>
              ← Anterior
            </Button>
            <Button disabled={!onNext} onClick={onNext}>
              Siguiente →
            </Button>
          </div>
        </aside>
      </div>
    </div>
  );
}
