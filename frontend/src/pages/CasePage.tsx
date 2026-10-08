import { lazy, Suspense, useEffect, useState, type FormEvent } from "react";
import * as endpoints from "../api/endpoints";
import type { CaseDetail } from "../api/types";
import { Badge, Btn, Empty, ErrorBox, Field, Loading, Modal, TlpBadge } from "../components/ui";
import { ConfirmDialog } from "../components/Dialogs";
import { useAuth } from "../lib/auth";
import { setCaseChip } from "../lib/caseChip";
import { CASE_STATUS_LABEL, TLP_HINT, TLP_LABEL, TLP_ORDER } from "../lib/labels";
import { errorMessage, fmtDate, fmtInt, isNotFound } from "../lib/format";
import { Link, navigate, useLocation } from "../lib/router";
import { useToast } from "../lib/toast";
import { useAsync } from "../lib/useAsync";
import { Icon } from "../components/Icon";
import { ExpedienteTab } from "./case/ExpedienteTab";
import { MenardTab } from "./case/MenardTab";
import { AccountsTab } from "./case/AccountsTab";
import { TimelineTab } from "./case/TimelineTab";
import { SourcesTab } from "./case/SourcesTab";
import type { CaseTabProps } from "./case/tabProps";

// Cytoscape pesa: el grafo se descarga solo cuando el analista entra a esta pestaña.
const GraphTab = lazy(() => import("./case/GraphTab").then((module) => ({ default: module.GraphTab })));

export const CASE_TABS = ["grafo", "expediente", "menard", "cuentas", "linea-de-tiempo", "fuentes"] as const;
export type CaseTab = (typeof CASE_TABS)[number];

const TAB_LABEL: Record<CaseTab, string> = {
  grafo: "Grafo",
  expediente: "Expediente",
  menard: "MENARD",
  cuentas: "Cuentas",
  "linea-de-tiempo": "Línea de tiempo",
  fuentes: "Fuentes",
};

type Dialog = "edit" | "close" | "archive" | "reopen" | "delete" | null;

export function CasePage({ caseId, tab }: { caseId: number; tab: CaseTab }) {
  const auth = useAuth();
  const toast = useToast();
  const { search } = useLocation();
  const detail = useAsync(() => endpoints.getCase(caseId), [caseId]);
  const [dialog, setDialog] = useState<Dialog>(null);

  const caseData: CaseDetail | undefined = detail.data;
  const isOpen = caseData?.status === "open";
  const writable = auth.canWrite && isOpen;

  useEffect(() => {
    if (caseData) {
      setCaseChip({ id: caseData.id, name: caseData.name, tlp: caseData.tlp, status: caseData.status });
    }
  }, [caseData]);

  useEffect(() => () => setCaseChip(null), []);

  if (detail.loading && !caseData) {
    return (
      <div className="page">
        <Loading label="Abriendo el caso…" rows={2} />
      </div>
    );
  }

  if (detail.error) {
    if (isNotFound(detail.error)) {
      return (
        <div className="page">
          <Empty title="Ese caso no existe" action={<Link className="btn btn-default" to="/casos">Volver a casos</Link>}>
            Puede que el enlace sea viejo o que el caso haya sido borrado.
          </Empty>
        </div>
      );
    }
    return (
      <div className="page">
        <ErrorBox error={detail.error} onRetry={detail.reload} title="No se pudo abrir el caso" />
      </div>
    );
  }

  if (!caseData) return null;

  const counts = caseData.counts ?? {};
  const ctx: CaseTabProps = { caseId, open: isOpen, writable, refreshCase: detail.reload };

  return (
    <div className="case-page">
      <header className="case-head">
        <div className="case-head-main">
          <div className="case-title-row">
            <span className="mono dim">#{caseData.id}</span>
            <h1 className="case-title">{caseData.name}</h1>
            <TlpBadge tlp={caseData.tlp} />
            <Badge tone={isOpen ? "ok" : "muted"}>{CASE_STATUS_LABEL[caseData.status] ?? caseData.status}</Badge>
          </div>
          <details className="case-legal">
            <summary>
              <Icon name="file" /> Propósito y base legal
            </summary>
            <p>{caseData.legal_basis}</p>
            <p className="dim">
              Abierto el {fmtDate(caseData.created_at)}. TLP:{TLP_LABEL[caseData.tlp] ?? caseData.tlp}: {TLP_HINT[caseData.tlp] ?? ""}
            </p>
          </details>
        </div>

        <div className="case-actions">
          {auth.canWrite && caseData.status === "open" ? (
            <Btn size="sm" icon="edit" onClick={() => setDialog("edit")}>
              Editar
            </Btn>
          ) : null}
          {auth.canWrite && caseData.status === "open" ? (
            <Btn size="sm" onClick={() => setDialog("close")}>
              Cerrar caso
            </Btn>
          ) : null}
          {auth.canWrite && (caseData.status === "open" || caseData.status === "closed") ? (
            <Btn size="sm" onClick={() => setDialog("archive")}>
              Archivar
            </Btn>
          ) : null}
          {auth.isAdmin && (caseData.status === "closed" || caseData.status === "archived") ? (
            <Btn size="sm" variant="ok" onClick={() => setDialog("reopen")}>
              Reabrir
            </Btn>
          ) : null}
          {auth.isAdmin && isEmpty(counts) ? (
            <Btn size="sm" variant="danger" icon="trash" onClick={() => setDialog("delete")}>
              Borrar
            </Btn>
          ) : null}
        </div>
      </header>

      {!isOpen ? (
        <p className="notice notice-muted" role="status">
          <Icon name="ban" />{" "}
          {caseData.status === "closed"
            ? "El caso está cerrado: solo lectura."
            : "El caso está archivado: solo lectura."}{" "}
          {auth.isAdmin ? "Como administrador podés reabrirlo." : "Un administrador puede reabrirlo."}
        </p>
      ) : null}
      {isOpen && !auth.canWrite ? (
        <p className="notice notice-muted" role="status">
          <Icon name="eye" /> Tu rol puede consultar este caso pero no modificarlo.
        </p>
      ) : null}

      <div className="case-counts" aria-label="Resumen del caso">
        <Count label="Entidades" value={counts.entities} extra={counts.entities_proposed ? `${counts.entities_proposed} propuestas` : undefined} tone="amber" />
        <Count label="Relaciones" value={counts.relations} />
        <Count label="Hallazgos" value={counts.findings} />
        <Count label="Cuentas" value={counts.accounts} />
        <Count label="Publicaciones" value={counts.posts} />
        <Count label="Vínculos MENARD" value={counts.links} extra={counts.links_pending ? `${counts.links_pending} pendientes` : undefined} tone="amber" />
        <Count label="Fuentes" value={counts.sources} />
      </div>

      <nav className="tabs" aria-label="Secciones del caso">
        {CASE_TABS.map((item) => (
          <Link
            key={item}
            to={`/casos/${caseId}/${item}${search}`}
            className={`tab ${item === tab ? "on" : ""}`}
            aria-current={item === tab ? "page" : undefined}
          >
            {TAB_LABEL[item]}
          </Link>
        ))}
      </nav>

      <div className="case-body">
        {tab === "grafo" ? (
          <Suspense fallback={<Loading label="Cargando el visor del grafo…" rows={2} />}>
            <GraphTab {...ctx} />
          </Suspense>
        ) : null}
        {tab === "expediente" ? <ExpedienteTab {...ctx} /> : null}
        {tab === "menard" ? <MenardTab {...ctx} /> : null}
        {tab === "cuentas" ? <AccountsTab {...ctx} /> : null}
        {tab === "linea-de-tiempo" ? <TimelineTab {...ctx} /> : null}
        {tab === "fuentes" ? <SourcesTab {...ctx} /> : null}
      </div>

      {dialog === "edit" ? (
        <EditCaseDialog
          caseData={caseData}
          onClose={() => setDialog(null)}
          onSaved={() => {
            setDialog(null);
            toast.notify("Caso actualizado.", "ok");
            detail.reload();
          }}
        />
      ) : null}

      {dialog === "close" ? (
        <ConfirmDialog
          title="Cerrar el caso"
          confirmLabel="Cerrar caso"
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await endpoints.closeCase(caseId);
            setDialog(null);
            toast.notify("Caso cerrado. Queda en solo lectura.", "ok");
            detail.reload();
          }}
        >
          <p>El caso pasa a solo lectura: no se podrán agregar entidades, hallazgos ni revisiones hasta reabrirlo.</p>
        </ConfirmDialog>
      ) : null}

      {dialog === "archive" ? (
        <ConfirmDialog
          title="Archivar el caso"
          confirmLabel="Archivar"
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await endpoints.archiveCase(caseId);
            setDialog(null);
            toast.notify("Caso archivado.", "ok");
            detail.reload();
          }}
        >
          <p>El caso queda archivado y de solo lectura. Sale de la lista de abiertos, pero su trazabilidad se conserva.</p>
        </ConfirmDialog>
      ) : null}

      {dialog === "reopen" ? (
        <ConfirmDialog
          title="Reabrir el caso"
          confirmLabel="Reabrir"
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await endpoints.reopenCase(caseId);
            setDialog(null);
            toast.notify("Caso reabierto.", "ok");
            detail.reload();
          }}
        >
          <p>Vuelve a estado abierto y admite cambios. Queda registrado en la auditoría.</p>
        </ConfirmDialog>
      ) : null}

      {dialog === "delete" ? (
        <ConfirmDialog
          title="Borrar el caso"
          confirmLabel="Borrar caso"
          danger
          onClose={() => setDialog(null)}
          onConfirm={async () => {
            await endpoints.deleteCase(caseId);
            setCaseChip(null);
            toast.notify("Caso borrado.", "info");
            navigate("/casos", { replace: true });
          }}
        >
          <p>Solo se puede borrar un caso vacío. Si tiene datos, la API lo rechaza para conservar la trazabilidad.</p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

function isEmpty(counts: Record<string, number>): boolean {
  return Object.values(counts).every((value) => !value);
}

function Count({ label, value, extra, tone }: { label: string; value: number | undefined; extra?: string; tone?: "amber" }) {
  return (
    <div className="count">
      <span className="count-label">{label}</span>
      <span className={`count-value${tone && extra ? " count-value-hot" : ""}`}>{fmtInt(value ?? 0)}</span>
      {extra ? <span className="count-extra">{extra}</span> : null}
    </div>
  );
}

function EditCaseDialog({
  caseData,
  onClose,
  onSaved,
}: {
  caseData: CaseDetail;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(caseData.name);
  const [description, setDescription] = useState(caseData.description);
  const [legalBasis, setLegalBasis] = useState(caseData.legal_basis);
  const [tlp, setTlp] = useState(caseData.tlp);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim() || !legalBasis.trim()) {
      setError(new Error("El nombre y el propósito con base legal no pueden quedar vacíos."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await endpoints.updateCase(caseData.id, {
        name: name.trim(),
        description,
        legal_basis: legalBasis.trim(),
        tlp,
      });
      onSaved();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Editar el caso"
      onClose={onClose}
      width={600}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn type="submit" form="edit-case-form" variant="primary" disabled={busy}>
            {busy ? "Guardando…" : "Guardar cambios"}
          </Btn>
        </>
      }
    >
      <form id="edit-case-form" className="form-stack" onSubmit={onSubmit} noValidate>
        <Field label="Nombre del caso">
          {(id) => <input id={id} className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />}
        </Field>
        <Field label="Propósito y base legal" hint="No puede quedar vacío.">
          {(id) => <textarea id={id} className="textarea" rows={4} value={legalBasis} onChange={(e) => setLegalBasis(e.target.value)} />}
        </Field>
        <Field label="Descripción">
          {(id) => <textarea id={id} className="textarea" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />}
        </Field>
        <Field label="Marca TLP" hint={TLP_HINT[tlp]}>
          {(id) => (
            <select id={id} className="select" value={tlp} onChange={(e) => setTlp(e.target.value)}>
              {TLP_ORDER.map((level) => (
                <option key={level} value={level}>
                  TLP:{TLP_LABEL[level]}
                </option>
              ))}
            </select>
          )}
        </Field>
        {error ? (
          <p className="inline-error" role="alert">
            {errorMessage(error)}
          </p>
        ) : null}
      </form>
    </Modal>
  );
}
