import { useEffect, useMemo, useState } from "react";
import * as endpoints from "../../api/endpoints";
import type { AccountOut, ClusterSummary, LinkOut, MenardRunSummary, Page, SignalResult } from "../../api/types";
import { Badge, Bar, Btn, Empty, ErrorBox, InlineError, Loading, ReviewBadge, Segmented } from "../../components/ui";
import { Icon } from "../../components/Icon";
import { classNames, errorMessage, fmtDateTime, fmtInt, pct, score2, truncate } from "../../lib/format";
import { REVIEW_LABEL, SIGNAL_FAMILY_LABEL, SIGNAL_FAMILY_ORDER, signalLabel } from "../../lib/labels";
import { Link } from "../../lib/router";
import { useToast } from "../../lib/toast";
import { useAsync } from "../../lib/useAsync";
import type { CaseTabProps } from "./tabProps";

const PAGE_SIZE = 40;
const MIN_NOTE = 10;

type ReviewFilter = "pending" | "confirmed" | "rejected" | "all";

const CONFIDENCE_LABEL: Record<string, string> = { alta: "Confianza alta", media: "Confianza media", baja: "Confianza baja" };

export function MenardTab({ caseId, writable, refreshCase }: CaseTabProps) {
  const toast = useToast();
  const [filter, setFilter] = useState<ReviewFilter>("pending");
  const [offset, setOffset] = useState(0);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [runSummary, setRunSummary] = useState<MenardRunSummary | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<unknown>(null);

  const links = useAsync<Page<LinkOut>>(
    () =>
      endpoints.listLinks(caseId, {
        status: filter === "all" ? undefined : filter === "pending" ? "pending" : filter,
        limit: PAGE_SIZE,
        offset,
      }),
    [caseId, filter, offset],
  );

  const lastRun = useAsync(() => endpoints.listJobs({ case_id: caseId, kind: "menard", limit: 1 }), [caseId]);
  const lastJob = lastRun.data?.items[0];

  const items = links.data?.items ?? [];
  const selected = items.find((link) => link.id === selectedId) ?? items[0] ?? null;

  useEffect(() => {
    if (selectedId === null && items.length > 0) setSelectedId(items[0].id);
  }, [items, selectedId]);

  async function runAnalysis() {
    setRunning(true);
    setRunError(null);
    try {
      const summary = await endpoints.runMenard(caseId, { min_score: 0 });
      setRunSummary(summary);
      setOffset(0);
      setFilter("pending");
      links.reload();
      lastRun.reload();
      refreshCase();
      toast.notify(
        `MENARD analizó ${summary.accounts_analyzed} cuentas: ${summary.links_created} hipótesis nuevas.`,
        "ok",
      );
    } catch (err) {
      setRunError(err);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="menard">
      <div className="menard-head">
        <div className="menard-intro">
          <p className="menard-warning">
            <Icon name="shield" />
            <span>
              Son <strong>hipótesis de mismo operador</strong> con evidencia para revisión humana. MENARD no identifica a
              nadie: cada vínculo se confirma o se descarta con una nota, y queda auditado.
            </span>
          </p>
          <p className="dim small mono">
            {lastJob
              ? `Última corrida ${fmtDateTime(lastJob.finished_at ?? lastJob.created_at)} · ${fmtInt(Number(lastJob.result?.accounts_analyzed ?? 0))} cuentas · ${fmtInt(Number(lastJob.result?.pairs_returned ?? 0))} pares`
              : "Todavía no se corrió MENARD en este caso."}
          </p>
        </div>
        <div className="menard-run">
          <Btn
            variant="primary"
            icon="refresh"
            onClick={runAnalysis}
            disabled={!writable || running}
            title={writable ? "Analiza las cuentas del caso y crea o actualiza las hipótesis" : "Solo con el caso abierto y un rol de escritura"}
          >
            {running ? "Corriendo MENARD…" : "Correr MENARD"}
          </Btn>
          {running ? <span className="dim small">Puede tardar unos segundos según la cantidad de cuentas.</span> : null}
        </div>
      </div>

      <InlineError error={runError} />
      {runSummary ? <RunSummary summary={runSummary} onClose={() => setRunSummary(null)} /> : null}

      <div className="menard-body">
        <section className="menard-list" aria-label="Hipótesis">
          <div className="menard-list-tools">
            <Segmented<ReviewFilter>
              label="Estado de revisión"
              value={filter}
              onChange={(value) => {
                setFilter(value);
                setOffset(0);
                setSelectedId(null);
              }}
              options={[
                { value: "pending", label: "Pendientes" },
                { value: "confirmed", label: "Confirmadas" },
                { value: "rejected", label: "Rechazadas" },
                { value: "all", label: "Todas" },
              ]}
            />
          </div>

          {links.loading && !links.data ? <Loading label="Cargando hipótesis…" rows={4} /> : null}
          {links.error ? <ErrorBox error={links.error} onRetry={links.reload} title="No se pudieron cargar las hipótesis" /> : null}
          {links.data && items.length === 0 ? (
            <Empty
              title={filter === "pending" ? "No hay hipótesis pendientes" : "No hay hipótesis en esta vista"}
              action={
                writable ? (
                  <Btn variant="primary" icon="refresh" onClick={runAnalysis} disabled={running}>
                    Correr MENARD
                  </Btn>
                ) : undefined
              }
            >
              {filter === "pending"
                ? "MENARD compara las cuentas del caso de a pares. Necesita al menos dos cuentas con publicaciones."
                : "Cambiá el filtro para ver otras hipótesis."}
            </Empty>
          ) : null}

          <ol className="link-list">
            {items.map((link) => (
              <li key={link.id}>
                <button
                  type="button"
                  className={classNames("link-item", selected?.id === link.id && "on")}
                  onClick={() => setSelectedId(link.id)}
                  aria-pressed={selected?.id === link.id}
                >
                  <div className="link-item-top">
                    <span className="link-score mono">{score2(link.score)}</span>
                    <ReviewBadge status={link.review_status} />
                  </div>
                  <div className="link-pair">
                    <span className="mono">{truncate(link.account_a.handle, 22)}</span>
                    <span className="dim">↔</span>
                    <span className="mono">{truncate(link.account_b.handle, 22)}</span>
                  </div>
                  <div className="link-meta">
                    <span className="small dim">
                      {link.account_a.platform} · {link.account_b.platform}
                    </span>
                    <span className="small">{CONFIDENCE_LABEL[link.confidence] ?? link.confidence}</span>
                  </div>
                  <Bar value={link.score} tone={link.review_status === "confirmed" ? "ok" : link.review_status === "rejected" ? "bad" : "amber"} label="Puntaje" />
                </button>
              </li>
            ))}
          </ol>

          {links.data && links.data.total > PAGE_SIZE ? (
            <div className="pager">
              <span className="dim mono small">
                {offset + 1}–{Math.min(offset + PAGE_SIZE, links.data.total)} de {links.data.total}
              </span>
              <div className="pager-buttons">
                <Btn size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  Anterior
                </Btn>
                <Btn size="sm" disabled={offset + PAGE_SIZE >= links.data.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  Siguiente
                </Btn>
              </div>
            </div>
          ) : null}
        </section>

        <section className="menard-detail" aria-label="Detalle de la hipótesis">
          {selected ? (
            <LinkDetail key={selected.id} caseId={caseId} link={selected} writable={writable} onReviewed={() => { links.reload(); refreshCase(); }} />
          ) : links.data ? (
            <Empty
              title={filter === "pending" ? "No quedan hipótesis pendientes" : "Nada para mostrar en esta vista"}
            >
              {filter === "pending"
                ? "Todas las hipótesis de este caso ya tienen una decisión. Podés revisarlas en «Confirmadas» o «Rechazadas»."
                : "Cambiá el filtro para ver otras hipótesis."}
            </Empty>
          ) : (
            <Loading label="Cargando la comparación…" rows={3} />
          )}
        </section>
      </div>
    </div>
  );
}

function RunSummary({ summary, onClose }: { summary: MenardRunSummary; onClose: () => void }) {
  return (
    <div className="run-summary" role="status">
      <div className="run-summary-head">
        <strong>Corrida de MENARD</strong>
        <button type="button" className="link-btn" onClick={onClose}>
          cerrar
        </button>
      </div>
      <div className="run-stats mono">
        <span>{summary.accounts_analyzed} cuentas analizadas</span>
        <span>{summary.pairs_returned} pares</span>
        <span className="hot">{summary.links_created} hipótesis nuevas</span>
        <span>{summary.links_updated} actualizadas</span>
        <span>{summary.clusters.length} agrupamientos</span>
      </div>
      {summary.warnings.length ? <p className="small dim">{summary.warnings.join(" ")}</p> : null}
      {summary.clusters.length ? <Clusters clusters={summary.clusters} /> : null}
      <p className="small dim">{summary.disclaimer}</p>
    </div>
  );
}

function Clusters({ clusters }: { clusters: ClusterSummary[] }) {
  return (
    <details className="clusters">
      <summary>Agrupamientos sugeridos ({clusters.length})</summary>
      <ul>
        {clusters.map((cluster, index) => (
          <li key={index}>
            <span className="mono small">{cluster.members.join(" · ")}</span>
            <span className="dim small"> cohesión {pct(cluster.cohesion)}</span>
            {cluster.summary ? <p className="small dim">{cluster.summary}</p> : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

function familyScore(signals: SignalResult[]) {
  return SIGNAL_FAMILY_ORDER.map((family) => {
    const items = signals.filter((signal) => signal.family === family);
    const available = items.filter((signal) => signal.available);
    const weightSum = available.reduce((sum, signal) => sum + Math.max(signal.weight, 0), 0);
    let score: number | null = null;
    if (available.length > 0) {
      score =
        weightSum > 0
          ? available.reduce((sum, signal) => sum + signal.score * Math.max(signal.weight, 0), 0) / weightSum
          : available.reduce((sum, signal) => sum + signal.score, 0) / available.length;
    }
    return { family, total: items.length, available: available.length, score };
  });
}

function LinkDetail({
  caseId,
  link,
  writable,
  onReviewed,
}: {
  caseId: number;
  link: LinkOut;
  writable: boolean;
  onReviewed: () => void;
}) {
  const toast = useToast();
  const accountA = useAsync<AccountOut>(() => endpoints.getAccount(caseId, link.account_a.id), [caseId, link.account_a.id]);
  const accountB = useAsync<AccountOut>(() => endpoints.getAccount(caseId, link.account_b.id), [caseId, link.account_b.id]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"confirm" | "reject" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [lastRelation, setLastRelation] = useState<number | null>(null);

  const signals = useMemo(() => link.signals?.signals ?? [], [link.signals]);
  const families = useMemo(() => familyScore(signals), [signals]);
  const pending = link.review_status === "pending";
  const noteOk = note.trim().length >= MIN_NOTE;

  async function decide(decision: "confirm" | "reject") {
    if (!noteOk) {
      setError(new Error(`La nota es obligatoria: explicá la decisión en al menos ${MIN_NOTE} caracteres.`));
      return;
    }
    setBusy(decision);
    setError(null);
    try {
      const result = await endpoints.reviewLink(caseId, link.id, decision, note.trim());
      setLastRelation(result.relation?.id ?? null);
      toast.notify(
        decision === "confirm"
          ? result.relation
            ? `Hipótesis confirmada: relación «mismo operador» #${result.relation.id} creada en el grafo.`
            : "Hipótesis confirmada."
          : "Hipótesis rechazada.",
        "ok",
      );
      setNote("");
      onReviewed();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(null);
    }
  }

  const overall = link.score;
  const decisionTone = link.review_status === "confirmed" ? "ok" : link.review_status === "rejected" ? "bad" : "amber";

  return (
    <div className="link-detail">
      <div className="ld-head">
        <div>
          <p className="ld-kicker mono">HIPÓTESIS #{link.id} · MISMO OPERADOR</p>
          <div className="ld-score">
            <span className="ld-score-value mono">{score2(overall)}</span>
            <span className="ld-score-unit dim">puntaje 0–1</span>
            <Badge tone={decisionTone} dashed={link.review_status === "pending"}>
              {REVIEW_LABEL[link.review_status] ?? link.review_status}
            </Badge>
            <Badge tone="neutral">{CONFIDENCE_LABEL[link.confidence] ?? link.confidence}</Badge>
          </div>
        </div>
      </div>

      <div className="compare">
        <AccountCard side="A" brief={link.account_a} account={accountA.data} loading={accountA.loading} error={accountA.error} caseId={caseId} />
        <div className="compare-mid" aria-hidden="true">
          <span>↔</span>
        </div>
        <AccountCard side="B" brief={link.account_b} account={accountB.data} loading={accountB.loading} error={accountB.error} caseId={caseId} />
      </div>

      {link.summary ? <p className="ld-summary">{link.summary}</p> : null}

      <h3 className="block-title">Desglose por familia</h3>
      <div className="family-list">
        {families.map((row) => (
          <div className="family-row" key={row.family}>
            <span className="family-name">{SIGNAL_FAMILY_LABEL[row.family] ?? row.family}</span>
            {row.score === null ? (
              <span className="family-na dim small">
                {row.total === 0 ? "no disponible en esta instalación" : "sin datos suficientes"}
              </span>
            ) : (
              <>
                <Bar value={row.score} tone={row.score >= 0.7 ? "amber" : "neutral"} label={`${SIGNAL_FAMILY_LABEL[row.family]} ${pct(row.score)}`} />
                <span className="family-value mono">{pct(row.score)}</span>
              </>
            )}
            <span className="family-count dim small mono">
              {row.available}/{row.total}
            </span>
          </div>
        ))}
      </div>
      <p className="dim small">Cada barra promedia las señales disponibles de su familia, ponderadas por su peso.</p>

      <h3 className="block-title">Señales y evidencia</h3>
      <div className="signal-list">
        {SIGNAL_FAMILY_ORDER.map((family) => {
          const items = signals.filter((signal) => signal.family === family);
          if (items.length === 0) return null;
          return (
            <details key={family} className="signal-family" open={family === "stylometry" || family === "profile"}>
              <summary>
                {SIGNAL_FAMILY_LABEL[family]} <span className="dim mono small">({items.filter((s) => s.available).length}/{items.length})</span>
              </summary>
              {items.map((signal) => (
                <SignalRow key={signal.name} signal={signal} />
              ))}
            </details>
          );
        })}
      </div>

      <h3 className="block-title">Decisión del analista</h3>
      {pending ? (
        <div className="review-box">
          <label className="field-label" htmlFor="review-note">
            Nota de revisión (obligatoria)
          </label>
          <textarea
            id="review-note"
            className="textarea"
            rows={3}
            maxLength={2000}
            value={note}
            onChange={(event) => setNote(event.target.value)}
            disabled={!writable || busy !== null}
            placeholder="Qué miraste y por qué lo confirmás o lo descartás. Ej.: horario y estilo coincidentes en 40 publicaciones."
          />
          <p className="field-hint">{noteOk ? "Lista para registrar." : `Mínimo ${MIN_NOTE} caracteres.`}</p>
          <div className="review-actions">
            <Btn variant="ok" icon="check" disabled={!writable || busy !== null || !noteOk} onClick={() => void decide("confirm")} title={writable ? undefined : "Solo con el caso abierto y un rol de escritura"}>
              {busy === "confirm" ? "Confirmando…" : "Confirmar hipótesis"}
            </Btn>
            <Btn variant="danger" icon="ban" disabled={!writable || busy !== null || !noteOk} onClick={() => void decide("reject")} title={writable ? undefined : "Solo con el caso abierto y un rol de escritura"}>
              {busy === "reject" ? "Rechazando…" : "Rechazar"}
            </Btn>
          </div>
          <p className="dim small">
            Confirmar crea una relación «mismo operador» entre las entidades de ambas cuentas, con el puntaje como confianza.
          </p>
          <InlineError error={error} />
        </div>
      ) : (
        <div className="review-done">
          <p>
            <ReviewBadge status={link.review_status} /> por el usuario #{link.reviewed_by ?? "—"} el {fmtDateTime(link.created_at)}
          </p>
          {link.review_note ? <blockquote className="finding-quote">{link.review_note}</blockquote> : null}
          {lastRelation ? <p className="small dim">Relación creada: #{lastRelation}</p> : null}
          <p className="dim small">Las decisiones quedan registradas en la auditoría.</p>
        </div>
      )}
    </div>
  );
}

function AccountCard({
  side,
  brief,
  account,
  loading,
  error,
  caseId,
}: {
  side: "A" | "B";
  brief: LinkOut["account_a"];
  account: AccountOut | undefined;
  loading: boolean;
  error: unknown;
  caseId: number;
}) {
  return (
    <article className="account-card" aria-label={`Cuenta ${side}`}>
      <header className="account-card-head">
        <span className="side-tag mono">{side}</span>
        <span className="badge badge-neutral">{brief.platform}</span>
      </header>
      <p className="account-handle mono">@{brief.handle}</p>
      <p className="account-name">{brief.display_name || <span className="dim">sin nombre visible</span>}</p>
      {loading && !account ? <p className="dim small">Leyendo el perfil…</p> : null}
      {error ? <p className="inline-error small">{errorMessage(error)}</p> : null}
      {account ? (
        <>
          {account.bio ? <p className="account-bio">{truncate(account.bio, 180)}</p> : null}
          <dl className="kv kv-compact">
            <dt>Seguidores</dt>
            <dd className="mono">{account.followers ?? "—"}</dd>
            <dt>Seguidos</dt>
            <dd className="mono">{account.following ?? "—"}</dd>
            <dt>Publicaciones</dt>
            <dd className="mono">{fmtInt(account.post_count)}</dd>
            <dt>Creada</dt>
            <dd className="mono">{account.created_at_platform ? fmtDateTime(account.created_at_platform) : "—"}</dd>
          </dl>
        </>
      ) : null}
      <div className="account-links">
        <Link className="link-inline" to={`/casos/${caseId}/cuentas?cuenta=${brief.id}`}>
          Ver publicaciones
        </Link>
        {brief.entity_id ? (
          <Link className="link-inline" to={`/casos/${caseId}/grafo?nodo=${brief.entity_id}`}>
            Ver en el grafo
          </Link>
        ) : null}
      </div>
    </article>
  );
}

function SignalRow({ signal }: { signal: SignalResult }) {
  return (
    <details className={classNames("signal", !signal.available && "signal-na")}>
      <summary>
        <span className="signal-name">{signalLabel(signal.name)}</span>
        {signal.available ? <Bar value={signal.score} tone="amber" label={signalLabel(signal.name)} /> : <span className="dim small">sin datos</span>}
        <span className="mono small">{signal.available ? score2(signal.score) : "—"}</span>
        <span className="mono dim small">peso {signal.weight.toFixed(2)}</span>
      </summary>
      {signal.explanation ? <p className="signal-explain">{signal.explanation}</p> : null}
      {signal.evidence.length > 0 ? (
        <ul className="evidence">
          {signal.evidence.slice(0, 6).map((item, index) => (
            <li key={index}>
              <p className="evidence-desc">{item.description}</p>
              {item.a || item.b ? (
                <div className="evidence-pair">
                  <blockquote>
                    <span className="mono dim small">A</span> {truncate(item.a || "—", 140)}
                  </blockquote>
                  <blockquote>
                    <span className="mono dim small">B</span> {truncate(item.b || "—", 140)}
                  </blockquote>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </details>
  );
}
