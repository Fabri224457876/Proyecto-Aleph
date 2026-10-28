import { useMemo, useState } from "react";
import * as ep from "../../api/endpoints";
import type { FindingOut, SectionOut } from "../../api/types";
import type { Datachunk } from "../../api/contract";
import { useCase } from "../../components/CaseLayout";
import { Button, ConfirmButton, Empty, ErrorBox, Field, Help, Loading, Notice, Panel } from "../../components/ui";
import { useAsync } from "../../lib/useAsync";
import { useToast } from "../../lib/toast";
import { errorMessage, fmtDateTime, plural, truncate } from "../../lib/format";
import { ENTITY_TYPE_LABEL } from "../../lib/labels";
import { Link } from "../../lib/router";

const UNCLASSIFIED = "sin clasificar";

interface Data {
  sections: SectionOut[];
  findings: FindingOut[];
}

async function loadExpediente(caseId: number): Promise<Data> {
  const [sections, findings] = await Promise.all([ep.listSections(caseId), ep.listAllFindings(caseId)]);
  return { sections, findings };
}

function kindLabel(kind: string): string {
  return (ENTITY_TYPE_LABEL as Record<string, string>)[kind] ?? (kind === "text" ? "Texto" : kind);
}

function FindingCard({
  finding,
  sections,
  onChanged,
}: {
  finding: FindingOut;
  sections: SectionOut[];
  onChanged: () => void;
}) {
  const ctx = useCase();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [note, setNote] = useState(finding.note);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [detail, setDetail] = useState<FindingOut | null>(null);
  const chunk = finding.chunk as Partial<Datachunk>;

  async function act(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      toast.notify(message, "ok");
      onChanged();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="finding" aria-label={`Hallazgo ${finding.value}`}>
      <div className="row small">
        <span className="chip">{kindLabel(finding.kind)}</span>
        <span className="muted">{fmtDateTime(finding.created_at)}</span>
      </div>
      <p className="finding-value">{finding.value}</p>
      {chunk.quote ? <blockquote style={{ margin: 0, color: "var(--muted)", fontSize: "0.97rem" }}>“{truncate(chunk.quote, 220)}”</blockquote> : null}
      {finding.note && !editing && <p className="small">Nota: {finding.note}</p>}
      {error ? <p className="field-error">{errorMessage(error)}</p> : null}

      {editing && (
        <Field label="Nota">
          {(id) => (
            <textarea id={id} className="textarea" rows={2} value={note} maxLength={10000} onChange={(e) => setNote(e.target.value)} />
          )}
        </Field>
      )}

      <details
        onToggle={(e) => {
          if ((e.currentTarget as HTMLDetailsElement).open && !detail) {
            ep.getFinding(ctx.caseId, finding.id).then(setDetail).catch((err: unknown) => setError(err));
          }
        }}
      >
        <summary>Ver origen</summary>
        <div className="finding-detail">
          {detail ? (
            <>
              {chunk.page_url ? <p>Página: {chunk.page_url}</p> : null}
              {chunk.context ? <p>Contexto: {chunk.context}</p> : null}
              {chunk.captured_at ? <p>Capturado: {fmtDateTime(String(chunk.captured_at))}</p> : null}
              {finding.source_id !== null && <p>Fuente {finding.source_id}.</p>}
              {finding.entity_id !== null && <p>Entidad {finding.entity_id}.</p>}
            </>
          ) : (
            <Loading what="Cargando el origen" />
          )}
        </div>
      </details>

      {ctx.canWrite && (
        <div className="row small" style={{ justifyContent: "space-between" }}>
          <label className="row" style={{ gap: 6 }}>
            <span className="muted">Mover a</span>
            <select
              className="select"
              aria-label="Mover a otro inciso"
              value={finding.section_id ?? ""}
              disabled={busy}
              onChange={(e) =>
                void act(
                  () => ep.updateFinding(ctx.caseId, finding.id, { section_id: e.target.value ? Number(e.target.value) : null }),
                  "Hallazgo movido.",
                )
              }
            >
              <option value="">Sin inciso</option>
              {sections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <div className="btn-row">
            {editing ? (
              <>
                <Button variant="primary" disabled={busy} onClick={() => void act(async () => {
                  await ep.updateFinding(ctx.caseId, finding.id, { note });
                  setEditing(false);
                }, "Nota guardada.")}>
                  Guardar nota
                </Button>
                <Button variant="ghost" onClick={() => setEditing(false)}>
                  Cancelar
                </Button>
              </>
            ) : (
              <Button variant="ghost" disabled={busy} onClick={() => setEditing(true)}>
                Editar nota
              </Button>
            )}
            <ConfirmButton
              label="Quitar"
              confirmLabel="Confirmar"
              disabled={busy}
              onConfirm={() => void act(() => ep.deleteFinding(ctx.caseId, finding.id), "Hallazgo quitado del expediente. Su fuente se conserva.")}
            />
          </div>
        </div>
      )}
      {!ctx.canWrite && <p className="small muted">Solo lectura.</p>}
    </article>
  );
}

function SectionManager({ sections, onChanged }: { sections: SectionOut[]; onChanged: () => void }) {
  const ctx = useCase();
  const toast = useToast();
  const [newName, setNewName] = useState("");
  const [names, setNames] = useState<Record<number, string>>({});
  const [error, setError] = useState<unknown>(null);

  async function act(fn: () => Promise<unknown>, message: string) {
    setError(null);
    try {
      await fn();
      toast.notify(message, "ok");
      onChanged();
    } catch (err) {
      setError(err);
    }
  }

  return (
    <details>
      <summary className="small" style={{ cursor: "pointer", color: "var(--accent)" }}>
        Gestionar incisos
      </summary>
      <div className="stack" style={{ marginTop: 12 }}>
        {error ? <p className="field-error">{errorMessage(error)}</p> : null}
        {ctx.canWrite && (
          <div className="row">
            <input
              className="input grow"
              aria-label="Nombre del nuevo inciso"
              placeholder="Nombre del nuevo inciso"
              maxLength={100}
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <Button
              disabled={!newName.trim()}
              onClick={() =>
                void act(async () => {
                  await ep.createSection(ctx.caseId, newName.trim());
                  setNewName("");
                }, "Inciso creado.")
              }
            >
              Crear inciso
            </Button>
          </div>
        )}
        {sections.map((s) => (
          <div className="row" key={s.id}>
            <input
              className="input grow"
              aria-label={`Nombre del inciso ${s.name}`}
              value={names[s.id] ?? s.name}
              disabled={!ctx.canWrite || s.name.toLowerCase() === UNCLASSIFIED}
              onChange={(e) => setNames((prev) => ({ ...prev, [s.id]: e.target.value }))}
            />
            {ctx.canWrite && s.name.toLowerCase() !== UNCLASSIFIED && (
              <>
                <Button
                  disabled={(names[s.id] ?? s.name).trim() === s.name}
                  onClick={() =>
                    void act(async () => {
                      await ep.updateSection(ctx.caseId, s.id, { name: (names[s.id] ?? s.name).trim() });
                      setNames((prev) => {
                        const next = { ...prev };
                        delete next[s.id];
                        return next;
                      });
                    }, "Inciso renombrado.")
                  }
                >
                  Guardar nombre
                </Button>
                <ConfirmButton
                  label="Borrar"
                  confirmLabel="Confirmar"
                  onConfirm={() => void act(() => ep.deleteSection(ctx.caseId, s.id), "Inciso borrado. Sus hallazgos pasaron a sin clasificar.")}
                />
              </>
            )}
          </div>
        ))}
        <p className="small muted">
          Borrar un inciso no borra sus hallazgos: pasan a «{UNCLASSIFIED}».
        </p>
      </div>
    </details>
  );
}

export function ExpedienteView() {
  const ctx = useCase();
  const data = useAsync(() => loadExpediente(ctx.caseId), [ctx.caseId]);
  const [filter, setFilter] = useState("");

  const grouped = useMemo(() => {
    if (!data.data) return null;
    const unclassified = data.data.sections.find((s) => s.name.toLowerCase() === UNCLASSIFIED);
    const bySection = new Map<number | null, FindingOut[]>();
    const q = filter.trim().toLowerCase();
    for (const f of data.data.findings) {
      if (q && !`${f.value} ${f.note} ${String((f.chunk as Partial<Datachunk>).quote ?? "")}`.toLowerCase().includes(q)) continue;
      const key = f.section_id === null && unclassified ? unclassified.id : f.section_id;
      const list = bySection.get(key) ?? [];
      list.push(f);
      bySection.set(key, list);
    }
    return bySection;
  }, [data.data, filter]);

  if (data.error) return <ErrorBox error={data.error} onRetry={data.reload} />;
  if (!data.data || !grouped) return <Loading what="Armando el expediente" />;

  const sections = data.data.sections;
  const total = data.data.findings.length;

  return (
    <div className="stack">
      <Panel
        title={
          <>
            Expediente <Help term="expediente" />
          </>
        }
        actions={
          <input
            className="input"
            style={{ width: 260 }}
            aria-label="Buscar en el expediente"
            placeholder="Buscar en el expediente"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        }
      >
        <p className="small muted">
          {plural(total, "hallazgo", "hallazgos")} en {plural(sections.length, "inciso", "incisos")}. Cada hallazgo guarda de dónde salió.
        </p>
      </Panel>

      {total === 0 && (
        <Empty title="El expediente todavía está vacío." action={<Link to={`/casos/${ctx.caseId}/reunir`} className="btn btn-primary">Ir a Reunir</Link>}>
          <p>Reuní un texto o un dato, y elegí lo que querés sumar al expediente.</p>
        </Empty>
      )}

      {sections.map((section) => {
        const list = grouped.get(section.id) ?? [];
        if (total > 0 && list.length === 0 && filter) return null;
        return (
          <section key={section.id} className="section-card" aria-labelledby={`sec-${section.id}`}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <h3 id={`sec-${section.id}`}>{section.name}</h3>
              <span className="chip">{plural(list.length, "hallazgo", "hallazgos")}</span>
            </div>
            {list.length === 0 ? (
              <p className="small muted">Nada en este inciso todavía.</p>
            ) : (
              list.map((finding) => (
                <FindingCard key={finding.id} finding={finding} sections={sections} onChanged={data.reload} />
              ))
            )}
          </section>
        );
      })}

      {data.data.findings.some((f) => f.section_id === null && !sections.some((s) => s.name.toLowerCase() === UNCLASSIFIED)) && (
        <Notice tone="warn">Hay hallazgos sin inciso. Podés moverlos a uno desde cada tarjeta.</Notice>
      )}

      <SectionManager sections={sections} onChanged={data.reload} />

    </div>
  );
}
