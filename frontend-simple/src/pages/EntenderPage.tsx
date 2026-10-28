import * as ep from "../api/endpoints";
import { useCase } from "../components/CaseLayout";
import { ErrorBox, Loading, Notice, PageTitle, Panel, Stat, Help } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { plural } from "../lib/format";
import { Link, useLocation } from "../lib/router";
import { ExpedienteView } from "./entender/ExpedienteView";
import { PeopleView } from "./entender/PeopleView";
import { TimelineView } from "./entender/TimelineView";
import { ConnectionsView } from "./entender/ConnectionsView";
import { ReportView } from "./entender/ReportView";
import { AskView } from "./entender/AskView";
import { TlonView } from "./entender/TlonView";

type View = "expediente" | "personas" | "tiempo" | "conexiones" | "preguntar" | "tlon" | "informe";

const VIEWS: Array<{ key: View; label: string }> = [
  { key: "expediente", label: "Expediente" },
  { key: "personas", label: "Personas y cuentas" },
  { key: "tiempo", label: "Línea de tiempo" },
  { key: "conexiones", label: "Conexiones" },
  { key: "preguntar", label: "Preguntarle al caso" },
  { key: "tlon", label: "Vínculos posibles" },
  { key: "informe", label: "Informe y exportar" },
];

interface Summary {
  entities: number;
  relations: number;
  findings: number;
  sources: number;
  accounts: number;
  hypotheses: number;
}

async function loadSummary(caseId: number): Promise<Summary> {
  const [entities, relations, findings, sources, accounts, hypotheses] = await Promise.all([
    ep.listEntities(caseId, { status: ["confirmed"], limit: 1 }),
    ep.listRelations(caseId, { status: ["confirmed"], limit: 1 }),
    ep.listFindings(caseId, { limit: 1 }),
    ep.listSources(caseId, { limit: 1 }),
    ep.listAccounts(caseId, { limit: 1 }),
    ep.listLinks(caseId, { status: "confirmed", limit: 1 }),
  ]);
  return {
    entities: entities.total,
    relations: relations.total,
    findings: findings.total,
    sources: sources.total,
    accounts: accounts.total,
    hypotheses: hypotheses.total,
  };
}

export function EntenderPage() {
  const ctx = useCase();
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const requested = params.get("vista") as View | null;
  const view: View = VIEWS.some((v) => v.key === requested) ? (requested as View) : "expediente";
  const summary = useAsync(() => loadSummary(ctx.caseId), [ctx.caseId, ctx.pending]);
  const base = `/casos/${ctx.caseId}/entender`;

  return (
    <div className="page">
      <PageTitle lead="El caso ya ordenado: qué se sabe, qué falta revisar, y cómo contarlo.">Entender y contar</PageTitle>

      <Panel
        title="Resumen"
        actions={
          ctx.pending > 0 ? (
            <Link to={`/casos/${ctx.caseId}/revisar`} className="btn btn-primary btn-small">
              Revisar {plural(ctx.pending, "pendiente", "pendientes")}
            </Link>
          ) : (
            <span className="chip chip-ok">Nada pendiente de revisión</span>
          )
        }
      >
        {summary.error ? <ErrorBox error={summary.error} onRetry={summary.reload} /> : null}
        {!summary.data && !summary.error && <Loading what="Contando" />}
        {summary.data && (
          <div className="stack">
            <p>
              Hay <strong>{plural(summary.data.entities, "entidad confirmada", "entidades confirmadas")}</strong>,{" "}
              <strong>{plural(summary.data.relations, "relación", "relaciones")}</strong> y{" "}
              <strong>{plural(summary.data.findings, "hallazgo", "hallazgos")}</strong> en el expediente.{" "}
              {ctx.pending > 0
                ? `Faltan ${plural(ctx.pending, "decisión", "decisiones")} en Revisar.`
                : "No falta decidir nada."}
            </p>
            <div className="stats">
              <Stat label="Entidades confirmadas" value={summary.data.entities} />
              <Stat label="Relaciones confirmadas" value={summary.data.relations} />
              <Stat label="Hallazgos" value={summary.data.findings} />
              <Stat label="Cuentas" value={summary.data.accounts} />
              <Stat label="Fuentes" value={summary.data.sources} />
              <Stat label="Hipótesis confirmadas" value={summary.data.hypotheses} hint="Hipótesis de MENARD que confirmó una persona" />
            </div>
            <p className="small muted">
              Una hipótesis confirmada no es una identificación: es una decisión humana sobre una sugerencia con evidencia.{" "}
              <Help term="hipotesis" />
            </p>
          </div>
        )}
      </Panel>

      <nav className="subnav" aria-label="Vistas del caso">
        {VIEWS.map((v) => (
          <Link
            key={v.key}
            to={v.key === "expediente" ? base : `${base}?vista=${v.key}`}
            className={v.key === view ? "is-current" : ""}
            aria-current={v.key === view ? "page" : undefined}
          >
            {v.label}
          </Link>
        ))}
      </nav>

      {view === "expediente" && <ExpedienteView />}
      {view === "personas" && <PeopleView />}
      {view === "tiempo" && <TimelineView />}
      {view === "conexiones" && <ConnectionsView />}
      {view === "preguntar" && <AskView />}
      {view === "tlon" && <TlonView />}
      {view === "informe" && <ReportView />}

      {ctx.pending > 0 && view !== "informe" && (
        <Notice tone="info">
          <p>
            Todavía hay cosas sin decidir. Lo que confirmes en Revisar aparece acá.{" "}
            <Link to={`/casos/${ctx.caseId}/revisar`}>Ir a Revisar</Link>
          </p>
        </Notice>
      )}
    </div>
  );
}
