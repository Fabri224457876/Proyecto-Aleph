import { useState, type FormEvent } from "react";
import * as endpoints from "../../api/endpoints";
import { ENTITY_TYPES, type EntityType, type GraphNode } from "../../api/types";
import { Btn, Field, Modal } from "../../components/ui";
import { errorMessage } from "../../lib/format";
import { COMMON_RELATION_TYPES, ENTITY_TYPE_LABEL } from "../../lib/labels";

function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="inline-error" role="alert">
      {errorMessage(error)}
    </p>
  );
}

function pctString(value: number): string {
  return String(Math.round(value * 100));
}

// ---------------------------------------------------------------- entidad (alta y edición)

export function EntityDialog({
  caseId,
  entity,
  onClose,
  onDone,
}: {
  caseId: number;
  entity?: GraphNode;
  onClose: () => void;
  onDone: (saved: { id: number; label: string }) => void;
}) {
  const editing = Boolean(entity);
  const [type, setType] = useState<string>(entity?.type ?? "person");
  const [label, setLabel] = useState(entity?.label ?? "");
  const [confidence, setConfidence] = useState(entity ? pctString(entity.confidence) : "100");
  const [status, setStatus] = useState<"confirmed" | "proposed">("confirmed");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);

  const labelMissing = !label.trim();

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched(true);
    if (labelMissing) return;
    const pct = Number(confidence);
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      setError(new Error("La confianza tiene que estar entre 0 y 100%."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (editing && entity) {
        const saved = await endpoints.updateEntity(caseId, entity.id, {
          type,
          label: label.trim(),
          confidence: pct / 100,
        });
        onDone({ id: saved.id, label: saved.label });
      } else {
        const saved = await endpoints.createEntity(caseId, {
          type,
          label: label.trim(),
          confidence: pct / 100,
          status,
        });
        onDone({ id: saved.id, label: saved.label });
      }
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title={editing ? "Editar entidad" : "Nueva entidad"}
      onClose={onClose}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn type="submit" form="entity-form" variant="primary" disabled={busy}>
            {busy ? "Guardando…" : editing ? "Guardar" : "Crear entidad"}
          </Btn>
        </>
      }
    >
      <form id="entity-form" className="form-stack" onSubmit={onSubmit} noValidate>
        <Field label="Tipo">
          {(id) => (
            <select id={id} className="select" value={type} onChange={(event) => setType(event.target.value)}>
              {ENTITY_TYPES.map((item) => (
                <option key={item} value={item}>
                  {ENTITY_TYPE_LABEL[item as EntityType]}
                </option>
              ))}
            </select>
          )}
        </Field>
        <Field label="Etiqueta" error={touched && labelMissing ? "La etiqueta no puede quedar vacía." : null}>
          {(id) => (
            <input id={id} className="input" maxLength={500} value={label} onChange={(event) => setLabel(event.target.value)} autoFocus />
          )}
        </Field>
        <Field label="Confianza (%)" hint="Qué tan seguro estás de este dato. Lo confirmado por una persona suele ir en 100%.">
          {(id) => (
            <input
              id={id}
              className="input input-narrow mono"
              type="number"
              min={0}
              max={100}
              step={5}
              value={confidence}
              onChange={(event) => setConfidence(event.target.value)}
            />
          )}
        </Field>
        {editing ? null : (
          <Field label="Estado" hint={status === "proposed" ? "Queda como propuesta hasta que un analista la confirme." : "Entra al grafo como confirmada."}>
            {(id) => (
              <select id={id} className="select" value={status} onChange={(event) => setStatus(event.target.value as "confirmed" | "proposed")}>
                <option value="confirmed">Confirmada</option>
                <option value="proposed">Propuesta (para revisar)</option>
              </select>
            )}
          </Field>
        )}
        <FormError error={error} />
      </form>
    </Modal>
  );
}

// ---------------------------------------------------------------- fusión

export function MergeDialog({
  caseId,
  current,
  candidates,
  onClose,
  onDone,
}: {
  caseId: number;
  current: GraphNode;
  candidates: GraphNode[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [otherId, setOtherId] = useState<number | null>(candidates[0]?.id ?? null);
  const [keepCurrent, setKeepCurrent] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const other = candidates.find((node) => node.id === otherId) ?? null;
  const keepId = keepCurrent ? current.id : other?.id ?? null;
  const dupId = keepCurrent ? other?.id ?? null : current.id;
  const keepLabel = keepCurrent ? current.label : other?.label ?? "";
  const dupLabel = keepCurrent ? other?.label ?? "" : current.label;

  async function run() {
    if (keepId === null || dupId === null) return;
    setBusy(true);
    setError(null);
    try {
      await endpoints.mergeEntities(caseId, keepId, dupId);
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Fusionar entidades duplicadas"
      onClose={onClose}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn variant="danger" onClick={run} disabled={busy || !other}>
            {busy ? "Fusionando…" : "Fusionar"}
          </Btn>
        </>
      }
    >
      <div className="form-stack">
        {candidates.length === 0 ? (
          <p className="dim">No hay otra entidad del mismo tipo en el grafo para fusionar.</p>
        ) : (
          <>
            <Field label="Duplicado de" hint="Solo se fusionan entidades del mismo tipo.">
              {(id) => (
                <select id={id} className="select" value={otherId ?? ""} onChange={(event) => setOtherId(Number(event.target.value))}>
                  {candidates.map((node) => (
                    <option key={node.id} value={node.id}>
                      #{node.id} · {node.label}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <fieldset className="fieldset">
              <legend>¿Cuál se conserva?</legend>
              <label className="radio-row">
                <input type="radio" name="keep" checked={keepCurrent} onChange={() => setKeepCurrent(true)} />
                <span>
                  Conservar <strong>#{current.id}</strong> · {current.label}
                </span>
              </label>
              <label className="radio-row">
                <input type="radio" name="keep" checked={!keepCurrent} onChange={() => setKeepCurrent(false)} />
                <span>
                  Conservar <strong>#{other?.id ?? "—"}</strong> · {other?.label ?? "—"}
                </span>
              </label>
            </fieldset>
            <p className="notice notice-muted">
              Se conserva «{keepLabel}». «{dupLabel}» se borra: sus relaciones pasan a la entidad conservada y las que
              quedan duplicadas se unifican. Queda registrado en la auditoría.
            </p>
          </>
        )}
        <FormError error={error} />
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- relación nueva

export function RelationDialog({
  caseId,
  source,
  targets,
  onClose,
  onDone,
}: {
  caseId: number;
  source: GraphNode;
  targets: GraphNode[];
  onClose: () => void;
  onDone: () => void;
}) {
  const [targetId, setTargetId] = useState<number | null>(targets[0]?.id ?? null);
  const [type, setType] = useState<string>("related_to");
  const [confidence, setConfidence] = useState("100");
  const [status, setStatus] = useState<"confirmed" | "proposed">("confirmed");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (targetId === null) {
      setError(new Error("Elegí la entidad de destino."));
      return;
    }
    if (!type.trim()) {
      setError(new Error("El tipo de relación no puede quedar vacío."));
      return;
    }
    const pct = Number(confidence);
    if (!Number.isFinite(pct) || pct < 0 || pct > 100) {
      setError(new Error("La confianza tiene que estar entre 0 y 100%."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await endpoints.createRelation(caseId, {
        src_id: source.id,
        dst_id: targetId,
        type: type.trim(),
        confidence: pct / 100,
        status,
      });
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Nueva relación"
      onClose={onClose}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn type="submit" form="relation-form" variant="primary" disabled={busy || targets.length === 0}>
            {busy ? "Creando…" : "Crear relación"}
          </Btn>
        </>
      }
    >
      <form id="relation-form" className="form-stack" onSubmit={onSubmit} noValidate>
        <p className="dim">
          Desde <strong className="text">#{source.id} · {source.label}</strong>
        </p>
        {targets.length === 0 ? (
          <p className="dim">No hay otra entidad visible en el grafo para unir.</p>
        ) : (
          <>
            <Field label="Hasta">
              {(id) => (
                <select id={id} className="select" value={targetId ?? ""} onChange={(event) => setTargetId(Number(event.target.value))}>
                  {targets.map((node) => (
                    <option key={node.id} value={node.id}>
                      #{node.id} · {node.label}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <Field label="Tipo de relación" hint="Texto libre en minúsculas y con guion bajo. Abajo hay tipos frecuentes.">
              {(id) => (
                <>
                  <input id={id} className="input mono" list="relation-types" maxLength={64} value={type} onChange={(event) => setType(event.target.value)} />
                  <datalist id="relation-types">
                    {COMMON_RELATION_TYPES.map((item) => (
                      <option key={item} value={item} />
                    ))}
                  </datalist>
                </>
              )}
            </Field>
            <div className="form-row">
              <Field label="Confianza (%)">
                {(id) => (
                  <input id={id} className="input input-narrow mono" type="number" min={0} max={100} step={5} value={confidence} onChange={(event) => setConfidence(event.target.value)} />
                )}
              </Field>
              <Field label="Estado">
                {(id) => (
                  <select id={id} className="select" value={status} onChange={(event) => setStatus(event.target.value as "confirmed" | "proposed")}>
                    <option value="confirmed">Confirmada</option>
                    <option value="proposed">Propuesta (para revisar)</option>
                  </select>
                )}
              </Field>
            </div>
          </>
        )}
        <FormError error={error} />
      </form>
    </Modal>
  );
}
