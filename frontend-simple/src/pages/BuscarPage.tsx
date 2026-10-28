import { useEffect, useState, type FormEvent } from "react";
import * as ep from "../api/endpoints";
import type { HitKind, HitOut, LookupAction, LookupPlan } from "../api/contract";
import { Button, Chip, Empty, ErrorBox, Loading, Notice, PageTitle, Panel, Help } from "../components/ui";
import { Link, navigate, useLocation } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { errorMessage, plural } from "../lib/format";

export const KIND_LABEL: Record<HitKind, string> = {
  case: "Casos",
  entity: "Entidades",
  account: "Cuentas",
  post: "Publicaciones",
  source: "Fuentes",
  finding: "Hallazgos del expediente",
  link: "Hipótesis de MENARD",
};

const ORDER: HitKind[] = ["entity", "account", "finding", "link", "post", "source", "case"];

/** A dónde lleva cada resultado dentro de la consola. */
export function hitLink(hit: HitOut): string {
  const base = `/casos/${hit.case_id}`;
  switch (hit.kind) {
    case "case":
      return base;
    case "entity":
      return hit.status === "proposed" ? `${base}/revisar` : `${base}/entender?vista=conexiones`;
    case "account":
    case "post":
      return `${base}/entender?vista=personas`;
    case "finding":
      return `${base}/entender`;
    case "link":
      return `${base}/revisar`;
    case "source":
      return `${base}/mas`;
  }
}

function buildUrl(q: string, caseId: number | null, semantic: boolean): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (caseId) params.set("caso", String(caseId));
  if (semantic) params.set("sentido", "1");
  return `/buscar?${params.toString()}`;
}

export function BuscarPage() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const q = (params.get("q") ?? "").trim();
  const caseId = Number(params.get("caso")) || null;
  const semantic = params.get("sentido") === "1";
  const [text, setText] = useState(q);

  useEffect(() => setText(q), [q]);

  const results = useAsync(
    () => (q.length >= 2 ? ep.search(q, { caseId: caseId ?? undefined, semantic, limit: 60 }) : Promise.resolve(null)),
    [q, caseId, semantic],
  );

  function submit(e: FormEvent) {
    e.preventDefault();
    navigate(buildUrl(text.trim(), caseId, semantic));
  }

  const items = results.data?.items ?? [];
  const groups = ORDER.map((kind) => ({ kind, hits: items.filter((h) => h.kind === kind) })).filter((g) => g.hits.length);

  return (
    <div className="page">
      <PageTitle lead="Encontrá cualquier cosa en tus casos: entidades, cuentas, publicaciones, fuentes, hallazgos e hipótesis. Sin importar tildes ni mayúsculas.">
        Buscar
      </PageTitle>

      <Panel>
        <form className="stack" onSubmit={submit} role="search">
          <div className="row">
            <input
              className="input grow"
              aria-label="Qué buscar"
              placeholder="Un nombre, un usuario, un dominio, una frase…"
              value={text}
              autoFocus
              onChange={(e) => setText(e.target.value)}
            />
            <Button variant="primary" type="submit" disabled={text.trim().length < 2}>
              Buscar
            </Button>
          </div>
          <div className="row small">
            <label className="check">
              <input
                type="checkbox"
                checked={semantic}
                onChange={(e) => navigate(buildUrl(q, caseId, e.target.checked))}
              />
              También por significado{" "}
              <Help text="Usa la IA local (embeddings) para encontrar cosas relacionadas aunque no compartan palabras: «pago con criptomonedas» encuentra una billetera." />
            </label>
            {caseId && (
              <span className="chip chip-accent">
                Solo en el caso {caseId}{" "}
                <button type="button" className="link-button" aria-label="Buscar en todos los casos" onClick={() => navigate(buildUrl(q, null, semantic))}>
                  ×
                </button>
              </span>
            )}
          </div>
        </form>
      </Panel>

      {q.length < 2 && <Empty title="Escribí al menos dos letras para buscar." />}
      {results.error ? <ErrorBox error={results.error} onRetry={results.reload} /> : null}
      {q.length >= 2 && results.loading && <Loading what={semantic ? "Buscando por texto y por significado" : "Buscando"} />}

      {results.data && (
        <div className="stack">
          {results.data.warnings.map((w) => (
            <Notice key={w} tone="warn">
              <p>{w}</p>
            </Notice>
          ))}
          <p className="small muted">
            {items.length === 0
              ? "Sin resultados."
              : `${plural(items.length, "resultado", "resultados")}${results.data.semantic_status === "usado" ? ", ordenados por texto y significado" : ""}.`}
          </p>
          {groups.map((g) => (
            <Panel key={g.kind} title={`${KIND_LABEL[g.kind]} (${g.hits.length})`}>
              <ul className="list">
                {g.hits.map((h) => (
                  <li key={h.ref}>
                    <Link to={hitLink(h)} className="item item-link">
                      <div className="item-main">
                        <div className="item-title">{h.title}</div>
                        <div className="item-meta">{h.snippet}</div>
                      </div>
                      <div className="item-actions">
                        {!caseId && <Chip>caso {h.case_id}</Chip>}
                        {h.status === "proposed" && <Chip tone="warn">propuesta</Chip>}
                        {h.status === "pending" && <Chip tone="warn">pendiente</Chip>}
                        {h.semantic !== null && h.lexical === 0 && <Chip tone="accent" title="Encontrado por significado, no por palabras">por significado</Chip>}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </Panel>
          ))}
        </div>
      )}

      {caseId && q.length >= 1 && <ExternalLookup caseId={caseId} q={q} />}
    </div>
  );
}

/** Búsqueda en fuentes públicas: la API reconoce el dato y arma el plan; cada acción se corre aparte y queda auditada. */
function ExternalLookup({ caseId, q }: { caseId: number; q: string }) {
  const toast = useToast();
  const plan = useAsync<LookupPlan>(() => ep.lookupPlan(caseId, q), [caseId, q]);
  const [state, setState] = useState<Record<string, "corriendo" | "listo" | string>>({});

  async function run(action: LookupAction) {
    setState((s) => ({ ...s, [action.id]: "corriendo" }));
    try {
      await ep.runLookupAction(action);
      setState((s) => ({ ...s, [action.id]: "listo" }));
      toast.notify(`${action.title}: listo. Lo encontrado quedó como propuesta en el caso.`, "ok");
    } catch (err) {
      setState((s) => ({ ...s, [action.id]: errorMessage(err) }));
    }
  }

  async function runAll() {
    for (const action of plan.data?.actions ?? []) {
      if (action.available && state[action.id] !== "listo") await run(action);
    }
  }

  return (
    <Panel
      title={
        <>
          Buscar afuera, en fuentes públicas{" "}
          <Help text="Consulta conectores y herramientas OSINT en solo lectura. Lo que encuentran entra al caso como propuesta, para que lo revises." />
        </>
      }
      actions={
        plan.data && plan.data.actions.some((a) => a.available) ? (
          <Button variant="primary" className="btn-small" onClick={() => void runAll()}>
            Correr todas
          </Button>
        ) : undefined
      }
    >
      {plan.error ? <ErrorBox error={plan.error} onRetry={plan.reload} /> : null}
      {!plan.data && !plan.error && <Loading what="Reconociendo el dato" />}
      {plan.data && (
        <div className="stack">
          <p>
            Detecté <strong>{plan.data.kind_label}</strong>: <code>{plan.data.value}</code>. <span className="muted small">{plan.data.note}</span>
          </p>
          <ul className="list">
            {plan.data.actions.map((a) => {
              const st = state[a.id];
              return (
                <li key={a.id} className="item">
                  <div className="item-main">
                    <div className="item-title">{a.title}</div>
                    <div className="item-meta">
                      {a.description}
                      {!a.available && ` — ${a.unavailable_reason}`}
                    </div>
                    {st && st !== "corriendo" && st !== "listo" && <p className="small bad-text">{st}</p>}
                  </div>
                  <div className="item-actions">
                    {!a.network && <Chip>sin conexión externa</Chip>}
                    {st === "listo" ? (
                      <Chip tone="ok">listo</Chip>
                    ) : (
                      <Button className="btn-small" disabled={!a.available || st === "corriendo"} onClick={() => void run(a)}>
                        {st === "corriendo" ? "Consultando…" : "Consultar"}
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
          {Object.values(state).includes("listo") && (
            <p>
              <Link to={`/casos/${caseId}/revisar`}>Revisar lo que se encontró</Link>
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
