import { useState } from "react";
import * as ep from "../../api/endpoints";
import type { TlonHypothesis, TlonRunOut } from "../../api/contract";
import { useCase } from "../../components/CaseLayout";
import { Button, Chip, Notice, Panel, Stat, Help } from "../../components/ui";
import { Link } from "../../lib/router";
import { useAsync } from "../../lib/useAsync";
import { ApiError } from "../../api/client";
import { errorMessage, plural } from "../../lib/format";

const VERDICT_TONE = { plausible: "ok", dudosa: "warn", descartable: "bad" } as const;

function pct(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)} %`;
}

function Hypothesis({ h, caseId }: { h: TlonHypothesis; caseId: number }) {
  return (
    <li className="item">
      <div className="item-main">
        <div className="item-title">
          {h.src_label} <span className="muted">—{h.type}→</span> {h.dst_label}
        </div>
        <div className="item-meta">{h.evidence.explanation}</div>
        {h.review && (
          <div className="stack small" style={{ marginTop: 8 }}>
            <div>
              <strong>FUNES:</strong> {h.review.razon}
            </div>
            {h.review.verificar && (
              <div>
                <strong>Para verificar:</strong> {h.review.verificar}
              </div>
            )}
          </div>
        )}
      </div>
      <div className="item-actions">
        <Chip title="Qué tan fuerte es frente a los vínculos reales del mismo tipo">fuerza {pct(h.percentile)}</Chip>
        {h.review && <Chip tone={VERDICT_TONE[h.review.veredicto]}>{h.review.veredicto}</Chip>}
        {h.relation_id ? (
          <Link to={`/casos/${caseId}/revisar`} className="btn btn-secondary btn-small">
            Decidir en Revisar
          </Link>
        ) : (
          <Chip tone="neutral" title="FUNES la descartó: no se agregó al grafo">no se propuso</Chip>
        )}
      </div>
    </li>
  );
}

export function TlonView() {
  const ctx = useCase();
  const latest = useAsync<TlonRunOut | null>(
    () => ep.tlonLatest(ctx.caseId).catch((err) => (err instanceof ApiError && err.status === 404 ? null : Promise.reject(err))),
    [ctx.caseId],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const result = latest.data;

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const out = await ep.tlonRun(ctx.caseId);
      latest.setData(out);
      ctx.reloadPending();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stack">
      <Panel
        title={
          <>
            Vínculos posibles (TLÖN){" "}
            <Help text="TLÖN aprende cómo está armado este caso (qué se conecta con qué) con un modelo predictivo estilo JEPA, y señala los vínculos que ese patrón sugiere y todavía nadie registró. FUNES, el modelo de lenguaje, revisa cada uno y explica cómo verificarlo. Son hipótesis: entran a Revisar para que decidas." />
          </>
        }
        actions={
          ctx.canWrite ? (
            <Button variant="primary" className="btn-small" disabled={busy} onClick={() => void run()}>
              {busy ? "Aprendiendo el caso…" : result ? "Volver a correr" : "Buscar vínculos que faltan"}
            </Button>
          ) : undefined
        }
      >
        <div className="stack">
          {!ctx.canWrite && <p className="small muted">{ctx.readOnlyReason || "Tu rol no permite correr TLÖN."}</p>}
          {error && (
            <Notice tone="warn">
              <p>{error}</p>
            </Notice>
          )}
          {latest.error ? (
            <Notice tone="warn">
              <p>{errorMessage(latest.error)}</p>
            </Notice>
          ) : null}
          {result === null && !busy && <p className="muted">Todavía no se corrió en este caso. Tarda unos segundos.</p>}
          {result && result.status !== "ok" && (
            <Notice tone="info">
              <p>{result.message}</p>
            </Notice>
          )}
          {result && result.status === "ok" && result.quality && (
            <>
              <div className="stats">
                <Stat label="Entidades y vínculos" value={`${result.nodes} · ${result.edges}`} />
                <Stat
                  label="Acierto en vínculos ocultos"
                  value={pct(result.quality.hits_at_5)}
                  hint="Se le ocultaron vínculos reales al entrenar: proporción en que puso el extremo correcto entre sus 5 primeras opciones"
                />
                <Stat label="Al azar sería" value={pct(result.quality.baseline_hits_at_5)} hint="Para comparar con el acierto del modelo" />
                <Stat label="Propuestas nuevas" value={result.created_relations} />
              </div>
              <p className="small muted">
                Describió las entidades con {result.feature_source === "ngramas" ? "n-gramas de texto" : "embeddings locales y n-gramas"}.
                {result.llm_status === "usado"
                  ? " FUNES revisó las hipótesis."
                  : " FUNES no revisó las hipótesis (el modelo de lenguaje no estaba disponible)."}
              </p>
              {result.warnings.map((w) => (
                <Notice key={w} tone="warn">
                  <p>{w}</p>
                </Notice>
              ))}
              {result.message && <p>{result.message}</p>}
            </>
          )}
        </div>
      </Panel>

      {result && result.lectura.length > 0 && (
        <Panel title="Lectura del caso (FUNES)">
          <ul className="stack">
            {result.lectura.map((s, i) => (
              <li key={i}>
                {s.texto} {s.ids.length > 0 && <span className="small muted">[{s.ids.join(", ")}]</span>}
              </li>
            ))}
          </ul>
          <p className="small muted">Borrador de la IA local. Las T son hipótesis de TLÖN; las E y R, datos del grafo.</p>
        </Panel>
      )}

      {result && result.hypotheses.length > 0 && (
        <Panel title={`Hipótesis (${plural(result.hypotheses.length, "vínculo posible", "vínculos posibles")})`}>
          <ul className="list">
            {result.hypotheses.map((h) => (
              <Hypothesis key={h.ref} h={h} caseId={ctx.caseId} />
            ))}
          </ul>
        </Panel>
      )}
    </div>
  );
}
