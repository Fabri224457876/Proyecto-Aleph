import { useState } from "react";
import * as ep from "../api/endpoints";
import { ENTITY_TYPES, type EntityOut, type GraphOut } from "../api/types";
import type { EnrichOut } from "../api/contract";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { errorMessage, pct, propText, plural } from "../lib/format";
import { ENTITY_TYPE_LABEL, ITEM_STATUS_LABEL, relationLabel } from "../lib/labels";
import { Button, ConfirmButton, ErrorBox, Field, Help, Loading, Notice } from "./ui";
import { useCase } from "./CaseLayout";

const RELATION_TYPES = [
  "related_to",
  "contacto",
  "aloja",
  "usa_alias",
  "pertenece_a",
  "miembro",
  "ubicado_en",
  "distribuido_desde",
  "muestra_de",
  "pago_a",
];

interface Props {
  entityId: number;
  /** Modo de camino más corto: permite usar esta entidad como origen o destino. */
  onPickPath?: (entityId: number, label: string) => void;
  onChanged?: () => void;
  onDeleted?: () => void;
  onOpenEntity?: (entityId: number) => void;
}

export function EntityPanel({ entityId, onPickPath, onChanged, onDeleted, onOpenEntity }: Props) {
  const ctx = useCase();
  const toast = useToast();
  const entity = useAsync(() => ep.getEntity(ctx.caseId, entityId), [ctx.caseId, entityId]);
  const near = useAsync<GraphOut>(() => ep.neighbors(ctx.caseId, entityId, 1), [ctx.caseId, entityId]);
  const targets = useAsync(() => ep.listEntities(ctx.caseId, { limit: 200 }), [ctx.caseId]);
  const [editing, setEditing] = useState(false);
  const [draftLabel, setDraftLabel] = useState("");
  const [draftType, setDraftType] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [mergeQuery, setMergeQuery] = useState("");
  const [mergeHits, setMergeHits] = useState<EntityOut[] | null>(null);
  const [enrich, setEnrich] = useState<EnrichOut | null>(null);
  const [relTarget, setRelTarget] = useState<number | "">("");
  const [relType, setRelType] = useState("related_to");

  if (entity.error) return <ErrorBox error={entity.error} onRetry={entity.reload} />;
  if (!entity.data) return <Loading what="Abriendo la entidad" />;
  const e = entity.data;

  function changed(message: string) {
    toast.notify(message, "ok");
    entity.reload();
    near.reload();
    onChanged?.();
  }

  async function run(action: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
      changed(message);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  const nodes = new Map((near.data?.nodes ?? []).map((n) => [n.id, n]));
  const neighbours = (near.data?.edges ?? []).map((edge) => {
    const otherId = edge.source === entityId ? edge.target : edge.source;
    return { edge, other: nodes.get(otherId), otherId };
  });
  const typeLabel = (type: string) => (ENTITY_TYPE_LABEL as Record<string, string>)[type] ?? type;

  return (
    <div className="stack">
      <div>
        <p className="eyebrow">
          {typeLabel(e.type)} · {ITEM_STATUS_LABEL[e.status] ?? e.status}
        </p>
        <h3 style={{ wordBreak: "break-word" }}>{e.label}</h3>
      </div>

      <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "6px 12px", margin: 0 }}>
        <dt className="muted">Confianza</dt>
        <dd style={{ margin: 0 }}>{pct(e.confidence)}</dd>
        {Object.entries(e.props ?? {})
          .slice(0, 12)
          .map(([key, value]) => (
            <div key={key} style={{ display: "contents" }}>
              <dt className="muted">{key.replace(/_/g, " ")}</dt>
              <dd style={{ margin: 0, wordBreak: "break-word" }}>{propText(value)}</dd>
            </div>
          ))}
      </dl>

      {onPickPath && (
        <div className="btn-row">
          <Button variant="secondary" onClick={() => onPickPath(e.id, e.label)}>
            Usar como origen o destino del camino
          </Button>
        </div>
      )}

      <section className="stack" aria-labelledby={`vec-${entityId}`}>
        <h4 id={`vec-${entityId}`}>Conexiones directas</h4>
        {near.loading && <Loading what="Buscando conexiones" />}
        {near.error ? <ErrorBox error={near.error} /> : null}
        {!near.loading && neighbours.length === 0 && <p className="small muted">No tiene conexiones directas en el grafo.</p>}
        <ul className="stack" style={{ margin: 0, paddingLeft: "1.1em" }}>
          {neighbours.map(({ edge, other, otherId }) => (
            <li key={edge.id} className="small">
              {relationLabel(edge.type)} →{" "}
              {onOpenEntity ? (
                <button type="button" className="link-button" onClick={() => onOpenEntity(otherId)}>
                  {other?.label ?? `entidad ${otherId}`}
                </button>
              ) : (
                other?.label ?? `entidad ${otherId}`
              )}{" "}
              <span className="muted">({ITEM_STATUS_LABEL[edge.status] ?? edge.status})</span>
            </li>
          ))}
        </ul>
      </section>

      {!ctx.canWrite && <p className="small muted">Solo lectura: {ctx.readOnlyReason || "tu rol no permite cambiar datos."}</p>}

      {ctx.canWrite && (
        <section className="stack" aria-labelledby={`acc-${entityId}`}>
          <h4 id={`acc-${entityId}`}>Acciones</h4>
          {error ? (
            <Notice tone="bad">
              <p>{errorMessage(error)}</p>
            </Notice>
          ) : null}

          {e.status === "proposed" && (
            <div className="btn-row">
              <Button variant="primary" disabled={busy} onClick={() => void run(() => ep.reviewEntity(ctx.caseId, e.id, "accept", ""), "Entidad confirmada.")}>
                Confirmar entidad
              </Button>
              <Button variant="danger" disabled={busy} onClick={() => void run(() => ep.reviewEntity(ctx.caseId, e.id, "reject", ""), "Entidad descartada.")}>
                Descartar
              </Button>
            </div>
          )}

          {!editing ? (
            <div className="btn-row">
              <Button
                disabled={busy}
                onClick={() => {
                  setDraftLabel(e.label);
                  setDraftType(e.type);
                  setEditing(true);
                }}
              >
                Editar
              </Button>
              <ConfirmButton
                label="Borrar entidad"
                confirmLabel="Confirmar borrado"
                disabled={busy}
                onConfirm={() => {
                  setBusy(true);
                  ep.deleteEntity(ctx.caseId, e.id)
                    .then(() => {
                      toast.notify("Entidad borrada, con sus relaciones.", "ok");
                      onDeleted?.();
                    })
                    .catch((err: unknown) => setError(err))
                    .finally(() => setBusy(false));
                }}
              />
            </div>
          ) : (
            <div className="stack">
              <Field label="Nombre o valor">
                {(id) => <input id={id} className="input" value={draftLabel} onChange={(x) => setDraftLabel(x.target.value)} />}
              </Field>
              <Field label="Tipo">
                {(id) => (
                  <select id={id} className="select" value={draftType} onChange={(x) => setDraftType(x.target.value)}>
                    {ENTITY_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {typeLabel(t)}
                      </option>
                    ))}
                  </select>
                )}
              </Field>
              <div className="btn-row">
                <Button
                  variant="primary"
                  disabled={busy || !draftLabel.trim()}
                  onClick={() => {
                    setEditing(false);
                    void run(
                      () => ep.updateEntity(ctx.caseId, e.id, { label: draftLabel.trim(), type: draftType }),
                      "Entidad actualizada.",
                    );
                  }}
                >
                  Guardar cambios
                </Button>
                <Button variant="ghost" onClick={() => setEditing(false)}>
                  Cancelar
                </Button>
              </div>
            </div>
          )}

          <div className="stack">
            <p className="small muted">
              Fusionar: si la otra entidad es el mismo dato que esta, se une a esta y se borra.{" "}
              <Help text="Es una acción de mantenimiento. Las relaciones de la entidad duplicada pasan a esta." />
            </p>
            <div className="row">
              <input
                className="input grow"
                aria-label="Buscar la entidad duplicada"
                placeholder="Buscar la entidad duplicada"
                value={mergeQuery}
                onChange={(x) => setMergeQuery(x.target.value)}
              />
              <Button
                disabled={!mergeQuery.trim() || busy}
                onClick={() =>
                  void ep
                    .listEntities(ctx.caseId, { q: mergeQuery.trim(), limit: 8 })
                    .then((page) => setMergeHits(page.items.filter((x) => x.id !== e.id)))
                    .catch((err: unknown) => setError(err))
                }
              >
                Buscar
              </Button>
            </div>
            {mergeHits && mergeHits.length === 0 && <p className="small muted">No hay otra entidad con ese texto.</p>}
            {mergeHits?.map((hit) => (
              <div className="item" key={hit.id}>
                <div className="item-main">
                  <p className="item-title" style={{ fontSize: "1rem" }}>
                    {hit.label}
                  </p>
                  <p className="item-meta">{typeLabel(hit.type)}</p>
                </div>
                <ConfirmButton
                  label="Fusionar aquí"
                  confirmLabel="Confirmar fusión"
                  disabled={busy}
                  variant="secondary"
                  onConfirm={() => {
                    setMergeHits(null);
                    setMergeQuery("");
                    void run(() => ep.mergeEntities(ctx.caseId, e.id, hit.id), "Entidades fusionadas.");
                  }}
                />
              </div>
            ))}
          </div>

          <div className="stack">
            <p className="small muted">Crear una relación desde esta entidad hacia otra del caso.</p>
            <div className="row">
              <select
                className="select"
                aria-label="Entidad destino"
                value={relTarget}
                onChange={(x) => setRelTarget(x.target.value ? Number(x.target.value) : "")}
              >
                <option value="">Elegir destino…</option>
                {(targets.data?.items ?? [])
                  .filter((t) => t.id !== e.id)
                  .map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
              </select>
              <select className="select" aria-label="Tipo de relación" value={relType} onChange={(x) => setRelType(x.target.value)}>
                {RELATION_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {relationLabel(t)}
                  </option>
                ))}
              </select>
              <Button
                disabled={relTarget === "" || busy}
                onClick={() => {
                  if (relTarget === "") return;
                  void run(
                    () => ep.createRelation(ctx.caseId, { src_id: e.id, dst_id: relTarget, type: relType, status: "confirmed" }),
                    "Relación creada.",
                  );
                }}
              >
                Crear relación
              </Button>
            </div>
          </div>

          <div className="stack">
            <p className="small muted">Consultar proveedores de inteligencia sobre este indicador. Lo nuevo entra como propuesta.</p>
            <div className="btn-row">
              <Button
                disabled={busy}
                onClick={() => {
                  setBusy(true);
                  ep.enrichEntity(ctx.caseId, e.id)
                    .then((out) => {
                      setEnrich(out);
                      changed("Consulta terminada. Lo nuevo quedó para revisar.");
                    })
                    .catch((err: unknown) => setError(err))
                    .finally(() => setBusy(false));
                }}
              >
                Consultar proveedores CTI
              </Button>
            </div>
            {enrich && (
              <ul className="stack small" style={{ margin: 0, paddingLeft: "1.1em" }}>
                {enrich.results.map((r) => (
                  <li key={r.provider}>
                    <strong>{r.provider}</strong>: {r.verdict} ({r.status}){r.message ? ` · ${r.message}` : ""}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      )}

      <p className="small muted">
        {plural(neighbours.length, "conexión directa", "conexiones directas")}
        {e.source_id !== null && <> · fuente {e.source_id}</>}
      </p>
    </div>
  );
}
