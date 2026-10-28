import { useState } from "react";
import * as ep from "../api/endpoints";
import { downloadFile } from "../api/client";
import { ENTITY_TYPES, type EntityOut, type JobOut, type RelationOut, type SourceOut, type ItemStatus } from "../api/types";
import type { ConnectorOut, ToolOut, LookupOut } from "../api/contract";
import { useCase } from "../components/CaseLayout";
import { EntityPanel } from "../components/EntityPanel";
import { Button, Chip, ConfirmButton, ErrorBox, Field, Help, Loading, Notice, Panel, StatusChip, TlpChip } from "../components/ui";
import { useAsync } from "../lib/useAsync";
import { useAuth } from "../lib/auth";
import { useToast } from "../lib/toast";
import { errorMessage, fmtDateTime, fmtInt, plural, truncate } from "../lib/format";
import {
  ENTITY_TYPE_LABEL,
  ITEM_STATUS_LABEL,
  RELIABILITY_LABEL,
  SOURCE_KIND_LABEL,
  TLP_LABEL,
  TLP_ORDER,
  relationLabel,
} from "../lib/labels";
import { CREDIBILITY_HELP, RELIABILITY_HELP } from "../lib/glossary";
import { navigate, Link, useLocation } from "../lib/router";

type Section = "caso" | "entidades" | "relaciones" | "fuentes" | "buscar" | "cuentas" | "importar" | "trabajos" | "catalogo";

const SECTIONS: Array<{ key: Section; label: string }> = [
  { key: "caso", label: "Datos del caso" },
  { key: "entidades", label: "Entidades" },
  { key: "relaciones", label: "Relaciones" },
  { key: "fuentes", label: "Fuentes" },
  { key: "buscar", label: "Buscar en el caso" },
  { key: "cuentas", label: "Verificar cuentas" },
  { key: "importar", label: "Importar JSON" },
  { key: "trabajos", label: "Trabajos" },
  { key: "catalogo", label: "Herramientas y conectores" },
];

export function MasPage() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const requested = params.get("seccion") as Section | null;
  const section: Section = SECTIONS.some((s) => s.key === requested) ? (requested as Section) : "caso";
  const ctx = useCase();

  return (
    <div className="page">
      <div className="page-title">
        <h1>Más opciones</h1>
        <p className="lead">Herramientas avanzadas para quien las necesita. Lo habitual se hace desde Reunir, Revisar y Entender.</p>
      </div>
      <div className="split" style={{ gridTemplateColumns: "240px minmax(0, 1fr)" }}>
        <nav className="stack" aria-label="Secciones de Más opciones">
          {SECTIONS.map((s) => (
            <Link
              key={s.key}
              to={`/casos/${ctx.caseId}/mas?seccion=${s.key}`}
              className={`btn ${s.key === section ? "btn-primary" : "btn-secondary"}`}
              style={{ justifyContent: "flex-start" }}
              aria-current={s.key === section ? "page" : undefined}
            >
              {s.label}
            </Link>
          ))}
        </nav>
        <div className="stack">
          {section === "caso" && <CasoSection />}
          {section === "entidades" && <EntidadesSection />}
          {section === "relaciones" && <RelacionesSection />}
          {section === "fuentes" && <FuentesSection />}
          {section === "buscar" && <BuscarSection />}
          {section === "cuentas" && <CuentasSection />}
          {section === "importar" && <ImportarSection />}
          {section === "trabajos" && <TrabajosSection />}
          {section === "catalogo" && <CatalogoSection />}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- datos del caso

function CasoSection() {
  const ctx = useCase();
  const auth = useAuth();
  const toast = useToast();
  const d = ctx.detail;
  const [name, setName] = useState(d.name);
  const [description, setDescription] = useState(d.description);
  const [legalBasis, setLegalBasis] = useState(d.legal_basis);
  const [tlp, setTlp] = useState(d.tlp);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function act(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      toast.notify(message, "ok");
      ctx.reloadCase();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Panel title="Datos del caso">
        <div className="stack">
          <Field label="Nombre">
            {(id) => <input id={id} className="input" maxLength={200} value={name} disabled={!ctx.canWrite} onChange={(e) => setName(e.target.value)} />}
          </Field>
          <Field label="Para qué se investiga">
            {(id) => <textarea id={id} className="textarea" rows={2} maxLength={2000} value={description} disabled={!ctx.canWrite} onChange={(e) => setDescription(e.target.value)} />}
          </Field>
          <Field label={<>Base legal <Help term="baselegal" /></>}>
            {(id) => <textarea id={id} className="textarea" rows={3} maxLength={4000} value={legalBasis} disabled={!ctx.canWrite} onChange={(e) => setLegalBasis(e.target.value)} />}
          </Field>
          <Field label={<>Nivel de reserva <Help term="tlp" /></>}>
            {(id) => (
              <select id={id} className="select" value={tlp} disabled={!ctx.canWrite} onChange={(e) => setTlp(e.target.value)}>
                {TLP_ORDER.map((t) => (
                  <option key={t} value={t}>
                    {TLP_LABEL[t]}
                  </option>
                ))}
              </select>
            )}
          </Field>
          {error ? <ErrorBox error={error} /> : null}
          {ctx.canWrite && (
            <div className="btn-row">
              <Button
                variant="primary"
                disabled={busy || !name.trim()}
                onClick={() =>
                  void act(() => ep.updateCase(ctx.caseId, { name: name.trim(), description, legal_basis: legalBasis, tlp }), "Datos del caso guardados.")
                }
              >
                Guardar datos
              </Button>
            </div>
          )}
          <p className="small muted">
            Estado: <StatusChip status={d.status} /> · <TlpChip tlp={d.tlp} /> · Abierto por usuario {d.created_by}.
          </p>
        </div>
      </Panel>

      {ctx.canWrite || auth.isAdmin ? (
        <Panel title="Estado del caso">
          <div className="stack">
            {d.status === "open" && ctx.canWrite && (
              <div className="btn-row">
                <ConfirmButton
                  label="Cerrar caso"
                  confirmLabel="Confirmar: queda de solo lectura"
                  variant="secondary"
                  onConfirm={() => void act(() => ep.closeCase(ctx.caseId), "Caso cerrado: queda de solo lectura.")}
                />
                <ConfirmButton
                  label="Archivar caso"
                  confirmLabel="Confirmar archivado"
                  variant="secondary"
                  onConfirm={() => void act(() => ep.archiveCase(ctx.caseId), "Caso archivado.")}
                />
              </div>
            )}
            {d.status !== "open" && auth.isAdmin && (
              <div className="btn-row">
                <Button variant="primary" disabled={busy} onClick={() => void act(() => ep.reopenCase(ctx.caseId), "Caso reabierto.")}>
                  Reabrir caso
                </Button>
              </div>
            )}
            {auth.isAdmin && (
              <div className="stack">
                <p className="small muted">Solo un administrador puede borrar un caso, y solo si está vacío.</p>
                <div className="btn-row">
                  <ConfirmButton
                    label="Borrar caso vacío"
                    confirmLabel="Confirmar borrado"
                    onConfirm={() =>
                      void ep
                        .deleteCase(ctx.caseId)
                        .then(() => navigate("/"))
                        .catch((err: unknown) => setError(err))
                    }
                  />
                </div>
              </div>
            )}
          </div>
        </Panel>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------- entidades

function EntidadesSection() {
  const ctx = useCase();
  const toast = useToast();
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const [status, setStatus] = useState<"" | ItemStatus>("");
  const [applied, setApplied] = useState({ q: "", type: "", status: "" as "" | ItemStatus });
  const [selected, setSelected] = useState<number | null>(null);
  const list = useAsync(
    () =>
      ep.listEntities(ctx.caseId, {
        q: applied.q || undefined,
        type: applied.type ? [applied.type] : undefined,
        status: applied.status ? [applied.status] : undefined,
        limit: 200,
      }),
    [ctx.caseId, applied.q, applied.type, applied.status],
  );
  const [newType, setNewType] = useState<string>("domain");
  const [newLabel, setNewLabel] = useState("");
  const [newStatus, setNewStatus] = useState<"confirmed" | "proposed">("confirmed");
  const [error, setError] = useState<unknown>(null);

  async function create() {
    setError(null);
    try {
      const created = await ep.createEntity(ctx.caseId, { type: newType, label: newLabel.trim(), status: newStatus });
      setNewLabel("");
      toast.notify("Entidad creada.", "ok");
      setSelected(created.id);
      list.reload();
      ctx.reloadPending();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="stack">
      <Panel title="Buscar entidades">
        <div className="row">
          <input className="input grow" aria-label="Texto a buscar" placeholder="Texto" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="select" aria-label="Tipo" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Todos los tipos</option>
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {ENTITY_TYPE_LABEL[t]}
              </option>
            ))}
          </select>
          <select className="select" aria-label="Estado" value={status} onChange={(e) => setStatus(e.target.value as "" | ItemStatus)}>
            <option value="">Cualquier estado</option>
            <option value="confirmed">Confirmadas</option>
            <option value="proposed">Propuestas</option>
            <option value="rejected">Descartadas</option>
          </select>
          <Button variant="primary" onClick={() => setApplied({ q: q.trim(), type, status })}>
            Buscar
          </Button>
        </div>
      </Panel>

      <div className="split">
        <Panel title={`Entidades (${list.data ? fmtInt(list.data.total) : "…"})`}>
          {list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : null}
          {!list.data && !list.error && <Loading />}
          <div className="stack" style={{ gap: 6 }}>
            {list.data?.items.map((e: EntityOut) => (
              <button
                key={e.id}
                type="button"
                className="person-card"
                aria-pressed={selected === e.id}
                onClick={() => setSelected(e.id)}
              >
                <span style={{ wordBreak: "break-all" }}>{e.label}</span>
                <span className="small muted">
                  {ENTITY_TYPE_LABEL[e.type as keyof typeof ENTITY_TYPE_LABEL] ?? e.type} · {ITEM_STATUS_LABEL[e.status] ?? e.status}
                </span>
              </button>
            ))}
          </div>
          {ctx.canWrite && (
            <div className="stack" style={{ marginTop: 16 }}>
              <h3>Nueva entidad a mano</h3>
              <div className="row">
                <select className="select" aria-label="Tipo de la nueva entidad" value={newType} onChange={(e) => setNewType(e.target.value)}>
                  {ENTITY_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {ENTITY_TYPE_LABEL[t]}
                    </option>
                  ))}
                </select>
                <input className="input grow" aria-label="Valor de la nueva entidad" placeholder="Valor" maxLength={500} value={newLabel} onChange={(e) => setNewLabel(e.target.value)} />
              </div>
              <div className="row">
                <select className="select" aria-label="Estado de la nueva entidad" value={newStatus} onChange={(e) => setNewStatus(e.target.value as "confirmed" | "proposed")}>
                  <option value="confirmed">Confirmada</option>
                  <option value="proposed">Propuesta (para revisar)</option>
                </select>
                <Button variant="primary" disabled={!newLabel.trim()} onClick={() => void create()}>
                  Crear entidad
                </Button>
              </div>
              {error ? <p className="field-error">{errorMessage(error)}</p> : null}
            </div>
          )}
        </Panel>
        <Panel title="Detalle">
          {selected === null ? (
            <p className="muted">Elegí una entidad para verla, editarla, fusionarla o conectarla.</p>
          ) : (
            <EntityPanel key={selected} entityId={selected} onChanged={list.reload} onDeleted={() => { setSelected(null); list.reload(); }} />
          )}
        </Panel>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- relaciones

const RELATION_TYPE_OPTIONS = ["related_to", "contacto", "aloja", "usa_alias", "pertenece_a", "miembro", "ubicado_en", "distribuido_desde", "muestra_de", "pago_a"];

function RelacionesSection() {
  const ctx = useCase();
  const toast = useToast();
  const [detail, setDetail] = useState<RelationOut | null>(null);
  const [status, setStatus] = useState<"" | ItemStatus>("");
  const relations = useAsync(
    () => ep.listRelations(ctx.caseId, { status: status ? [status] : undefined, limit: 200 }),
    [ctx.caseId, status],
  );
  const entities = useAsync(() => ep.listEntities(ctx.caseId, { limit: 200 }), [ctx.caseId]);
  const labels = new Map((entities.data?.items ?? []).map((e) => [e.id, e.label]));
  const [src, setSrc] = useState<number | "">("");
  const [dst, setDst] = useState<number | "">("");
  const [type, setType] = useState("related_to");
  const [error, setError] = useState<unknown>(null);

  async function act(fn: () => Promise<unknown>, message: string) {
    setError(null);
    try {
      await fn();
      toast.notify(message, "ok");
      relations.reload();
      ctx.reloadPending();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="stack">
      <Panel
        title="Relaciones"
        actions={
          <select className="select" aria-label="Estado" value={status} onChange={(e) => setStatus(e.target.value as "" | ItemStatus)}>
            <option value="">Cualquier estado</option>
            <option value="confirmed">Confirmadas</option>
            <option value="proposed">Propuestas</option>
            <option value="rejected">Descartadas</option>
          </select>
        }
      >
        {error ? <ErrorBox error={error} /> : null}
        {relations.error ? <ErrorBox error={relations.error} onRetry={relations.reload} /> : null}
        {!relations.data && !relations.error && <Loading />}
        <ul className="list">
          {relations.data?.items.map((r: RelationOut) => (
            <li key={r.id} className="item">
              <div className="item-main">
                <p className="item-title" style={{ fontSize: "1rem" }}>
                  {labels.get(r.src_id) ?? `entidad ${r.src_id}`} → {relationLabel(r.type)} → {labels.get(r.dst_id) ?? `entidad ${r.dst_id}`}
                </p>
                <p className="item-meta">
                  {ITEM_STATUS_LABEL[r.status] ?? r.status} · confianza {Math.round(r.confidence * 100)}%
                </p>
              </div>
              {ctx.canWrite && (
                <div className="item-actions">
                  <select
                    className="select"
                    aria-label={`Cambiar el tipo de la relación ${r.id}`}
                    value={r.type}
                    onChange={(e) => void act(() => ep.updateRelation(ctx.caseId, r.id, { type: e.target.value }), "Tipo de relación cambiado.")}
                  >
                    {[...new Set([r.type, ...RELATION_TYPE_OPTIONS])].map((t) => (
                      <option key={t} value={t}>
                        {relationLabel(t)}
                      </option>
                    ))}
                  </select>
                  <Button
                    variant="ghost"
                    onClick={() =>
                      void ep
                        .getRelation(ctx.caseId, r.id)
                        .then(setDetail)
                        .catch((err: unknown) => setError(err))
                    }
                  >
                    Ver
                  </Button>
                  {r.status === "proposed" && (
                    <>
                      <Button variant="primary" onClick={() => void act(() => ep.reviewRelation(ctx.caseId, r.id, "accept"), "Relación confirmada.")}>
                        Confirmar
                      </Button>
                      <Button variant="danger" onClick={() => void act(() => ep.reviewRelation(ctx.caseId, r.id, "reject"), "Relación descartada.")}>
                        Descartar
                      </Button>
                    </>
                  )}
                  <ConfirmButton
                    label="Borrar"
                    confirmLabel="Confirmar"
                    onConfirm={() => void act(() => ep.deleteRelation(ctx.caseId, r.id), "Relación borrada.")}
                  />
                </div>
              )}
            </li>
          ))}
        </ul>
        {relations.data && relations.data.items.length === 0 && <p className="muted">No hay relaciones con ese estado.</p>}
        {detail && (
          <Notice tone="info" title={`Relación ${detail.id}`}>
            <p>
              Estado: {ITEM_STATUS_LABEL[detail.status] ?? detail.status} · fuente {detail.source_id ?? "sin fuente"} · confianza {Math.round(detail.confidence * 100)}%
            </p>
            <pre style={{ marginTop: 8 }}>{JSON.stringify(detail.props, null, 2)}</pre>
            <Button variant="ghost" onClick={() => setDetail(null)}>
              Cerrar
            </Button>
          </Notice>
        )}
      </Panel>

      {ctx.canWrite && (
        <Panel title="Crear una relación">
          <div className="stack">
            <div className="row">
              <select className="select grow" aria-label="Origen" value={src} onChange={(e) => setSrc(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Origen…</option>
                {entities.data?.items.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.label}
                  </option>
                ))}
              </select>
              <select className="select" aria-label="Tipo de relación" value={type} onChange={(e) => setType(e.target.value)}>
                {["related_to", "contacto", "aloja", "usa_alias", "pertenece_a", "miembro", "ubicado_en", "distribuido_desde", "muestra_de", "pago_a"].map((t) => (
                  <option key={t} value={t}>
                    {relationLabel(t)}
                  </option>
                ))}
              </select>
              <select className="select grow" aria-label="Destino" value={dst} onChange={(e) => setDst(e.target.value ? Number(e.target.value) : "")}>
                <option value="">Destino…</option>
                {entities.data?.items.map((e) => (
                  <option key={e.id} value={e.id}>
                    {e.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="btn-row">
              <Button
                variant="primary"
                disabled={src === "" || dst === "" || src === dst}
                onClick={() => {
                  if (src === "" || dst === "") return;
                  void act(async () => {
                    await ep.createRelation(ctx.caseId, { src_id: src, dst_id: dst, type, status: "confirmed" });
                    setSrc("");
                    setDst("");
                  }, "Relación creada.");
                }}
              >
                Crear relación
              </Button>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- fuentes

function FuentesSection() {
  const ctx = useCase();
  const toast = useToast();
  const sources = useAsync(() => ep.listSources(ctx.caseId, { limit: 200 }), [ctx.caseId]);
  const [detail, setDetail] = useState<SourceOut | null>(null);
  const [reference, setReference] = useState("");
  const [reliability, setReliability] = useState("F");
  const [credibility, setCredibility] = useState("6");
  const [error, setError] = useState<unknown>(null);

  async function open(id: number) {
    setError(null);
    try {
      setDetail(await ep.getSource(ctx.caseId, id));
    } catch (err) {
      setError(err);
    }
  }

  async function create() {
    setError(null);
    try {
      const created = await ep.createSource(ctx.caseId, {
        kind: "manual",
        connector: "manual",
        reference: reference.trim(),
        reliability,
        credibility,
      });
      setReference("");
      toast.notify("Fuente registrada.", "ok");
      sources.reload();
      setDetail(created);
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="stack">
      <Panel title="Fuentes del caso" actions={<span className="small muted">{sources.data ? plural(sources.data.total, "fuente", "fuentes") : ""}</span>}>
        {sources.error ? <ErrorBox error={sources.error} onRetry={sources.reload} /> : null}
        {!sources.data && !sources.error && <Loading />}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Fuente</th>
                <th scope="col">Tipo</th>
                <th scope="col">Fiabilidad</th>
                <th scope="col">Fecha</th>
                <th scope="col">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {sources.data?.items.map((s) => (
                <tr key={s.id}>
                  <td>
                    <button type="button" className="link-button" onClick={() => void open(s.id)}>
                      {truncate(s.reference || `fuente ${s.id}`, 70)}
                    </button>
                  </td>
                  <td>{SOURCE_KIND_LABEL[s.kind] ?? s.kind}</td>
                  <td>
                    {s.reliability} / {s.credibility}
                  </td>
                  <td className="small">{fmtDateTime(s.retrieved_at)}</td>
                  <td>
                    {s.has_raw && (
                      <Button variant="ghost" onClick={() => void downloadFile(`/api/cases/${ctx.caseId}/sources/${s.id}/raw`, `fuente-${s.id}`).catch((err: unknown) => setError(err))}>
                        Descargar original
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {error ? <p className="field-error" style={{ marginTop: 10 }}>{errorMessage(error)}</p> : null}
      </Panel>

      {detail && (
        <Panel title={`Fuente ${detail.id}`}>
          <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "6px 12px", margin: 0 }}>
            <dt className="muted">Referencia</dt>
            <dd style={{ margin: 0, wordBreak: "break-all" }}>{detail.reference || "sin referencia"}</dd>
            <dt className="muted">Tipo</dt>
            <dd style={{ margin: 0 }}>{SOURCE_KIND_LABEL[detail.kind] ?? detail.kind}</dd>
            <dt className="muted">Fiabilidad</dt>
            <dd style={{ margin: 0 }}>{RELIABILITY_LABEL[detail.reliability] ?? detail.reliability}</dd>
            <dt className="muted">Credibilidad</dt>
            <dd style={{ margin: 0 }}>{CREDIBILITY_HELP[detail.credibility] ?? detail.credibility}</dd>
            <dt className="muted">Huella SHA-256</dt>
            <dd className="mono" style={{ margin: 0, wordBreak: "break-all", fontSize: "0.85rem" }}>{detail.sha256 || "sin huella"}</dd>
            <dt className="muted">Original guardado</dt>
            <dd style={{ margin: 0 }}>{detail.has_raw ? "Sí" : "No"}</dd>
          </dl>
        </Panel>
      )}

      {ctx.canWrite && (
        <Panel title="Registrar una fuente a mano" actions={<Help text="Para algo que consultaste fuera de Aleph y querés dejar asentado. Queda con la fiabilidad que le pongas." />}>
          <div className="stack">
            <Field label="Referencia">
              {(id) => <input id={id} className="input" maxLength={500} value={reference} onChange={(e) => setReference(e.target.value)} placeholder="Qué es y dónde está" />}
            </Field>
            <div className="grid-2">
              <Field label="Fiabilidad de la fuente" hint={RELIABILITY_HELP[reliability]}>
                {(id) => (
                  <select id={id} className="select" value={reliability} onChange={(e) => setReliability(e.target.value)}>
                    {Object.entries(RELIABILITY_HELP).map(([k, v]) => (
                      <option key={k} value={k}>
                        {k}: {v}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <Field label="Credibilidad del dato" hint={CREDIBILITY_HELP[credibility]}>
                {(id) => (
                  <select id={id} className="select" value={credibility} onChange={(e) => setCredibility(e.target.value)}>
                    {Object.entries(CREDIBILITY_HELP).map(([k, v]) => (
                      <option key={k} value={k}>
                        {k}: {v}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
            </div>
            <div className="btn-row">
              <Button variant="primary" disabled={!reference.trim()} onClick={() => void create()}>
                Registrar fuente
              </Button>
            </div>
          </div>
        </Panel>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- buscar

function BuscarSection() {
  const ctx = useCase();
  const [q, setQ] = useState("");
  const [applied, setApplied] = useState("");
  const result = useAsync(
    () => (applied ? ep.searchCase(ctx.caseId, applied, 25) : Promise.resolve(null)),
    [ctx.caseId, applied],
  );
  return (
    <Panel title="Buscar en el caso" actions={<Help text="Busca en entidades, cuentas y publicaciones del caso." />}>
      <div className="stack">
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            setApplied(q.trim());
          }}
        >
          <input className="input grow" aria-label="Qué buscar" placeholder="Texto a buscar" maxLength={200} value={q} onChange={(e) => setQ(e.target.value)} />
          <Button variant="primary" type="submit" disabled={!q.trim()}>
            Buscar
          </Button>
        </form>
        {result.error ? <ErrorBox error={result.error} /> : null}
        {applied && result.loading && <Loading />}
        {result.data && (
          <div className="stack">
            <h3>Entidades ({result.data.entities.length})</h3>
            {result.data.entities.map((e) => (
              <p key={e.id} className="small">
                {e.label} <span className="muted">· {ENTITY_TYPE_LABEL[e.type as keyof typeof ENTITY_TYPE_LABEL] ?? e.type}</span>
              </p>
            ))}
            <h3>Cuentas ({result.data.accounts.length})</h3>
            {result.data.accounts.map((a) => (
              <p key={a.id} className="small">
                @{a.handle} <span className="muted">· {a.platform}</span>
              </p>
            ))}
            <h3>Publicaciones ({result.data.posts.length})</h3>
            {result.data.posts.map((p) => (
              <p key={p.id} className="small">
                <span className="muted">@{p.handle}:</span> {truncate(p.text, 200)}
              </p>
            ))}
            {result.data.entities.length + result.data.accounts.length + result.data.posts.length === 0 && (
              <p className="muted">No hay coincidencias.</p>
            )}
          </div>
        )}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- verificar cuentas

const PLATFORM_SUGGESTIONS = ["bluesky", "mastodon", "github", "reddit", "telegram", "x", "twitter", "instagram", "discord"];

function CuentasSection() {
  const ctx = useCase();
  const [platform, setPlatform] = useState("bluesky");
  const [text, setText] = useState("");
  const [result, setResult] = useState<LookupOut | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function check() {
    const handles = text
      .split(/\s+/)
      .map((h) => h.replace(/^@/, "").trim())
      .filter(Boolean)
      .slice(0, 500);
    if (!handles.length) return;
    setBusy(true);
    setError(null);
    try {
      setResult(await ep.lookupHandles(ctx.caseId, platform.trim().toLowerCase(), handles));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel title="Verificar cuentas" actions={<Help text="Pregunta qué sabe el caso de una lista de usuarios de una plataforma. No consulta internet." />}>
      <div className="stack">
        <div className="grid-2">
          <Field label="Plataforma">
            {(id) => (
              <>
                <input id={id} className="input" list="plataformas" value={platform} onChange={(e) => setPlatform(e.target.value)} />
                <datalist id="plataformas">
                  {PLATFORM_SUGGESTIONS.map((p) => (
                    <option key={p} value={p} />
                  ))}
                </datalist>
              </>
            )}
          </Field>
        </div>
        <Field label="Usuarios" hint="Uno por línea, o separados por espacios. Hasta 500.">
          {(id) => <textarea id={id} className="textarea" rows={4} value={text} onChange={(e) => setText(e.target.value)} />}
        </Field>
        <div className="btn-row">
          <Button variant="primary" disabled={busy || !text.trim()} onClick={() => void check()}>
            {busy ? "Revisando…" : "Verificar"}
          </Button>
        </div>
        {error ? <ErrorBox error={error} /> : null}
        {result?.handles && (
          <ul className="list">
            {Object.entries(result.handles).map(([handle, info]) => (
              <li key={handle} className="item">
                <div className="item-main">
                  <p className="item-title" style={{ fontSize: "1rem" }}>
                    @{handle}
                  </p>
                  <p className="item-meta">
                    {info.known
                      ? `Ya está en el caso${info.posts_captured ? ` · ${plural(info.posts_captured, "publicación", "publicaciones")}` : ""}`
                      : "El caso no la conoce todavía."}
                  </p>
                </div>
                {info.known ? <Chip tone="ok">Conocida</Chip> : <Chip>Nueva</Chip>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- importar JSON

const SAMPLE_COLLECTION = (now: string) =>
  JSON.stringify(
    {
      connector: "muestra_sintetica",
      reference: "Muestra sintética para probar la importación",
      retrieved_at: now,
      profiles: [
        {
          account: { platform: "demo", handle: "cuenta_sintetica_01", display_name: "Cuenta sintética 1" },
          posts: [{ platform_post_id: "p1", text: "Publicación sintética de prueba.", created_at: now, kind: "original" }],
        },
      ],
      entities: [{ type: "domain", label: "ejemplo-sintetico.test", confidence: 0.5 }],
      relations: [],
    },
    null,
    2,
  );

function ImportarSection() {
  const ctx = useCase();
  const toast = useToast();
  const [mode, setMode] = useState<"collection" | "capture">("collection");
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setError(null);
    setResult(null);
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      setError("Ese texto no es JSON válido. Revisá las comillas y las llaves.");
      return;
    }
    setBusy(true);
    try {
      const out = mode === "collection" ? await ep.importCollection(ctx.caseId, body) : await ep.importCapture(ctx.caseId, body);
      setResult(out);
      toast.notify("Importación terminada. Lo nuevo quedó para revisar.", "ok");
      ctx.reloadPending();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Panel
      title="Importar JSON normalizado"
      actions={<Help text="Para datos que ya vienen en el formato de Aleph. Para archivos de redes, usá Reunir: lo reconoce solo." />}
    >
      <div className="stack">
        <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
          <legend>Qué estás importando</legend>
          <label className="check">
            <input type="radio" name="modo" checked={mode === "collection"} onChange={() => setMode("collection")} />
            Una recolección ya normalizada
          </label>
          <label className="check">
            <input type="radio" name="modo" checked={mode === "capture"} onChange={() => setMode("capture")} />
            Una captura de la extensión del navegador
          </label>
        </fieldset>
        <div className="btn-row">
          <Button
            variant="ghost"
            onClick={() => {
              setMode("collection");
              setText(SAMPLE_COLLECTION(new Date().toISOString()));
            }}
          >
            Cargar un ejemplo sintético
          </Button>
        </div>
        <Field label="JSON">
          {(id) => <textarea id={id} className="textarea mono" rows={12} value={text} onChange={(e) => setText(e.target.value)} />}
        </Field>
        {error && <Notice tone="bad"><p>{error}</p></Notice>}
        {result !== null && (
          <Notice tone="ok" title="Importado">
            <pre style={{ marginTop: 8 }}>{JSON.stringify(result, null, 2)}</pre>
          </Notice>
        )}
        <div className="btn-row">
          <Button variant="primary" disabled={!ctx.canWrite || busy || !text.trim()} onClick={() => void submit()}>
            {busy ? "Importando…" : "Importar"}
          </Button>
        </div>
      </div>
    </Panel>
  );
}

// ---------------------------------------------------------------- trabajos

function TrabajosSection() {
  const ctx = useCase();
  const jobs = useAsync(() => ep.listJobs({ case_id: ctx.caseId, limit: 50 }), [ctx.caseId]);
  const [open, setOpen] = useState<JobOut | null>(null);
  const [error, setError] = useState<unknown>(null);

  async function show(id: number) {
    setError(null);
    try {
      setOpen(await ep.getJob(id));
    } catch (err) {
      setError(err);
    }
  }

  return (
    <Panel title="Trabajos del caso" actions={<span className="small muted">Recolecciones, herramientas, MENARD y FUNES que corrieron.</span>}>
      {jobs.error ? <ErrorBox error={jobs.error} onRetry={jobs.reload} /> : null}
      {!jobs.data && !jobs.error && <Loading />}
      <ul className="list">
        {jobs.data?.items.map((j) => (
          <li key={j.id} className="item">
            <div className="item-main">
              <p className="item-title" style={{ fontSize: "1rem" }}>
                {j.kind} · trabajo {j.id}
              </p>
              <p className="item-meta">
                {fmtDateTime(j.created_at)} · {j.status}
                {j.error ? ` · ${truncate(j.error, 120)}` : ""}
              </p>
            </div>
            <div className="item-actions">
              <Chip tone={j.status === "failed" ? "bad" : j.status === "done" ? "ok" : "neutral"}>{j.status}</Chip>
              <Button variant="ghost" onClick={() => void show(j.id)}>
                Ver
              </Button>
            </div>
          </li>
        ))}
      </ul>
      {jobs.data && jobs.data.items.length === 0 && <p className="muted">Todavía no corrió nada en este caso.</p>}
      {error ? <p className="field-error">{errorMessage(error)}</p> : null}
      {open && (
        <div className="stack" style={{ marginTop: 14 }}>
          <h3>Trabajo {open.id}</h3>
          <pre>{JSON.stringify({ params: open.params, result: open.result, error: open.error }, null, 2)}</pre>
        </div>
      )}
    </Panel>
  );
}

// ---------------------------------------------------------------- catálogo

function CatalogoSection() {
  const connectors = useAsync<ConnectorOut[]>(() => ep.listConnectors().catch(() => []), []);
  const tools = useAsync<ToolOut[]>(() => ep.listTools().catch(() => []), []);
  return (
    <div className="stack">
      <Panel title="Conectores">
        {!connectors.data ? (
          <Loading />
        ) : (
          <ul className="list">
            {connectors.data.map((c) => (
              <li key={c.name} className="item">
                <div className="item-main">
                  <p className="item-title" style={{ fontSize: "1rem" }}>{c.title}</p>
                  <p className="item-meta">
                    {c.mode === "import" ? "Importación de un archivo" : "En vivo, desde una API pública"} · parámetros: {Object.keys(c.params).join(", ") || "ninguno"}
                  </p>
                </div>
                {c.available ? <Chip tone="ok">Disponible</Chip> : <Chip tone="warn">Falta una clave</Chip>}
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <Panel title="Herramientas OSINT">
        {!tools.data ? (
          <Loading />
        ) : (
          <ul className="list">
            {tools.data.map((t) => (
              <li key={t.name} className="item">
                <div className="item-main">
                  <p className="item-title" style={{ fontSize: "1rem" }}>{t.title}</p>
                  <p className="item-meta">{t.description}</p>
                </div>
                <Chip tone={t.network ? "warn" : "neutral"}>{t.network ? "Consulta internet" : "Sin red"}</Chip>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <p className="small muted">
        Para usarlos, andá a Reunir y elegí qué dato querés investigar. Las herramientas no cambian el caso hasta que decidas.
      </p>
    </div>
  );
}
