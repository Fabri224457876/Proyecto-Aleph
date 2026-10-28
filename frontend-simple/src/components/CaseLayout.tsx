import { createContext, useContext, useEffect, type ReactNode } from "react";
import * as endpoints from "../api/endpoints";
import type { CaseDetail } from "../api/types";
import { useAuth } from "../lib/auth";
import { Link } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { CASE_STATUS_LABEL } from "../lib/labels";
import { ErrorBox, Help, Loading, Notice, StatusChip, TlpChip } from "./ui";

export type CaseSection = "reunir" | "revisar" | "entender" | "equipo" | "mas";

export interface CaseCtx {
  caseId: number;
  detail: CaseDetail;
  reloadCase: () => void;
  /** Cosas que esperan una decisión humana: propuestas, hipótesis de MENARD pendientes. */
  pending: number;
  reloadPending: () => void;
  /** Rol de escritura y caso abierto. */
  canWrite: boolean;
  /** Por qué no se puede escribir (vacío si sí se puede). */
  readOnlyReason: string;
}

const CaseContext = createContext<CaseCtx | null>(null);

export function useCase(): CaseCtx {
  const value = useContext(CaseContext);
  if (!value) throw new Error("useCase debe usarse dentro de CaseLayout");
  return value;
}

async function loadPending(caseId: number): Promise<number> {
  const [entities, relations, links] = await Promise.all([
    endpoints.listEntities(caseId, { status: ["proposed"], limit: 1 }),
    endpoints.listRelations(caseId, { status: ["proposed"], limit: 1 }),
    endpoints.listLinks(caseId, { status: "pending", limit: 1 }),
  ]);
  return entities.total + relations.total + links.total;
}

export function CaseLayout({
  caseId,
  active,
  children,
}: {
  caseId: number;
  active: CaseSection;
  children: ReactNode;
}) {
  const auth = useAuth();
  const detail = useAsync(() => endpoints.getCase(caseId), [caseId]);
  const pending = useAsync(() => loadPending(caseId), [caseId]);

  useEffect(() => {
    if (detail.data) document.title = `${detail.data.name} · Aleph`;
    return () => {
      document.title = "Aleph · P.R.O.A.";
    };
  }, [detail.data]);

  if (detail.error) {
    return (
      <div className="page">
        <ErrorBox error={detail.error} />
        <p>
          <Link to="/">Volver a mis casos</Link>
        </p>
      </div>
    );
  }
  if (!detail.data) return <Loading what="Abriendo el caso" />;

  const data = detail.data;
  const status = data.status;
  let readOnlyReason = "";
  if (!auth.canWrite) {
    readOnlyReason = "Tu rol es de auditor: podés ver y revisar el caso, pero no cambiar datos.";
  } else if (status !== "open") {
    readOnlyReason = `El caso está ${(CASE_STATUS_LABEL[status] ?? status).toLowerCase()}: solo lectura. Un administrador puede reabrirlo en Más opciones.`;
  }

  const ctx: CaseCtx = {
    caseId,
    detail: data,
    reloadCase: detail.reload,
    pending: pending.data ?? 0,
    reloadPending: pending.reload,
    canWrite: readOnlyReason === "",
    readOnlyReason,
  };

  const steps: Array<{ key: CaseSection; label: string; sub: string }> = [
    { key: "reunir", label: "1 · Reunir", sub: "Sumar datos" },
    { key: "revisar", label: "2 · Revisar", sub: "Decidir" },
    { key: "entender", label: "3 · Entender y contar", sub: "Ver y exportar" },
  ];

  return (
    <CaseContext.Provider value={ctx}>
      <div className="page case-page">
        <header className="case-head">
          <div className="case-head-main">
            <p className="eyebrow">
              <Link to="/">Mis casos</Link> / Caso {caseId}
            </p>
            <h1 className="case-name">{data.name}</h1>
            <div className="case-chips">
              <TlpChip tlp={data.tlp} />
              <Help term="tlp" />
              {status !== "open" && <StatusChip status={status} />}
            </div>
          </div>
          <nav className="case-secondary" aria-label="Otras secciones del caso">
            <Link to={`/casos/${caseId}/equipo`} className={active === "equipo" ? "is-current" : ""}>
              Equipo del caso
            </Link>
            <Link to={`/casos/${caseId}/mas`} className={active === "mas" ? "is-current" : ""}>
              Más opciones
            </Link>
          </nav>
        </header>

        <nav className="moments" aria-label="Momentos del caso">
          {steps.map((step) => {
            const current = step.key === active;
            return (
              <Link
                key={step.key}
                to={`/casos/${caseId}/${step.key}`}
                className={`moment${current ? " is-current" : ""}`}
                aria-current={current ? "page" : undefined}
              >
                <span className="moment-label">{step.label}</span>
                <span className="moment-sub">
                  {step.key === "revisar" && ctx.pending > 0
                    ? `${ctx.pending} ${ctx.pending === 1 ? "espera" : "esperan"} tu decisión`
                    : step.sub}
                </span>
              </Link>
            );
          })}
        </nav>

        {readOnlyReason && (
          <Notice tone="warn">
            <p>{readOnlyReason}</p>
          </Notice>
        )}

        <div className="case-body">{children}</div>
      </div>
    </CaseContext.Provider>
  );
}
