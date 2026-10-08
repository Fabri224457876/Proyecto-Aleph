import { useMemo, useState, type DragEvent, type FormEvent } from "react";
import * as endpoints from "../../api/endpoints";
import type { FindingOut, SectionOut } from "../../api/types";
import { Badge, Btn, Empty, ErrorBox, IconButton, InlineError, Loading, Modal } from "../../components/ui";
import { ConfirmDialog } from "../../components/Dialogs";
import { classNames, fmtDate, fmtDateTime, truncate } from "../../lib/format";
import { entityTypeLabel } from "../../lib/labels";
import { Link } from "../../lib/router";
import { useToast } from "../../lib/toast";
import { useAsync } from "../../lib/useAsync";
import type { CaseTabProps } from "./tabProps";

const UNCLASSIFIED_NAME = "sin clasificar";

function chunkText(finding: FindingOut, key: string): string {
  const value = finding.chunk?.[key];
  return typeof value === "string" ? value : "";
}

export function ExpedienteTab({ caseId, writable, refreshCase }: CaseTabProps) {
  const toast = useToast();
  const sections = useAsync(() => endpoints.listSections(caseId), [caseId]);
  const findings = useAsync(() => endpoints.listAllFindings(caseId), [caseId]);

  const [draggingId, setDraggingId] = useState<number | null>(null);
  const [dropTarget, setDropTarget] = useState<number | "none" | null>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const [renaming, setRenaming] = useState<{ id: number; name: string } | null>(null);
  const [noteEdit, setNoteEdit] = useState<{ id: number; text: string } | null>(null);
  const [confirm, setConfirm] = useState<{ kind: "section"; section: SectionOut } | { kind: "finding"; finding: FindingOut } | null>(null);

  const orderedSections = useMemo(
    () => [...(sections.data ?? [])].sort((a, b) => a.position - b.position || a.id - b.id),
    [sections.data],
  );
  const unclassifiedSection = orderedSections.find((s) => s.name.trim().toLowerCase() === UNCLASSIFIED_NAME);

  const byColumn = useMemo(() => {
    const map = new Map<number | "none", FindingOut[]>();
    for (const finding of findings.data ?? []) {
      const key = finding.section_id ?? "none";
      if (!map.has(key)) map.set(key, []);
      map.get(key)?.push(finding);
    }
    return map;
  }, [findings.data]);

  const totalFindings = findings.data?.length ?? 0;
  const sectionsLabel = (id: number) => orderedSections.find((s) => s.id === id)?.name ?? "";

  function columnItems(section: SectionOut): FindingOut[] {
    const own = byColumn.get(section.id) ?? [];
    if (unclassifiedSection && unclassifiedSection.id === section.id) {
      return [...own, ...(byColumn.get("none") ?? [])];
    }
    return own;
  }

  async function moveFinding(finding: FindingOut, target: number | null) {
    if ((finding.section_id ?? null) === target) return;
    const previous = findings.data;
    findings.setData((list) => list?.map((item) => (item.id === finding.id ? { ...item, section_id: target } : item)));
    setActionError(null);
    try {
      await endpoints.updateFinding(caseId, finding.id, { section_id: target });
      sections.reload();
      refreshCase();
      toast.notify(
        target === null ? "Hallazgo sin clasificar." : `Hallazgo movido a «${sectionsLabel(target)}».`,
        "ok",
      );
    } catch (err) {
      findings.setData(previous);
      setActionError(err);
    } finally {
      setDraggingId(null);
      setDropTarget(null);
    }
  }

  function onDragStart(event: DragEvent<HTMLElement>, finding: FindingOut) {
    if (!writable) return;
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", String(finding.id));
    setDraggingId(finding.id);
  }

  function onDropOn(event: DragEvent<HTMLElement>, target: number | null) {
    event.preventDefault();
    const finding = (findings.data ?? []).find((item) => item.id === draggingId);
    if (finding) void moveFinding(finding, target);
    setDropTarget(null);
  }

  async function createSection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newName.trim();
    if (!name) return;
    setActionError(null);
    try {
      await endpoints.createSection(caseId, name);
      setNewName("");
      setCreating(false);
      sections.reload();
      toast.notify(`Inciso «${name}» creado.`, "ok");
    } catch (err) {
      setActionError(err);
    }
  }

  async function saveRename() {
    if (!renaming) return;
    const name = renaming.name.trim();
    if (!name) {
      setActionError(new Error("El nombre del inciso no puede quedar vacío."));
      return;
    }
    setActionError(null);
    try {
      await endpoints.updateSection(caseId, renaming.id, { name });
      setRenaming(null);
      sections.reload();
      toast.notify("Inciso renombrado.", "ok");
    } catch (err) {
      setActionError(err);
    }
  }

  async function saveNote() {
    if (!noteEdit) return;
    setActionError(null);
    try {
      const saved = await endpoints.updateFinding(caseId, noteEdit.id, { note: noteEdit.text });
      findings.setData((list) => list?.map((item) => (item.id === saved.id ? { ...item, note: saved.note } : item)));
      setNoteEdit(null);
      toast.notify("Nota guardada.", "ok");
    } catch (err) {
      setActionError(err);
    }
  }

  if (sections.loading && !sections.data) return <Loading label="Abriendo el expediente…" rows={3} />;
  if (sections.error) return <ErrorBox error={sections.error} onRetry={sections.reload} title="No se pudo abrir el expediente" />;

  return (
    <div className="expediente">
      <div className="exp-toolbar">
        <p className="dim small exp-intro">
          Los hallazgos son fragmentos con su cita, su contexto y la URL de origen. Arrastrá un hallazgo a otro inciso o usá su selector.
          {totalFindings ? ` ${totalFindings} hallazgos en el caso.` : ""}
        </p>
        <div className="exp-tools">
          {creating ? (
            <form className="inline-form" onSubmit={createSection}>
              <label className="sr-only" htmlFor="new-section">
                Nombre del inciso
              </label>
              <input
                id="new-section"
                className="input input-sm"
                maxLength={100}
                placeholder="Nombre del inciso"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                autoFocus
              />
              <Btn size="sm" variant="primary" type="submit" disabled={!newName.trim() || !writable}>
                Crear inciso
              </Btn>
              <Btn size="sm" variant="ghost" onClick={() => setCreating(false)}>
                Cancelar
              </Btn>
            </form>
          ) : (
            <Btn size="sm" icon="plus" variant="primary" disabled={!writable} title={writable ? undefined : "Solo con el caso abierto y un rol de escritura"} onClick={() => setCreating(true)}>
              Nuevo inciso
            </Btn>
          )}
        </div>
      </div>
      <InlineError error={actionError} />

      {findings.loading && !findings.data ? <Loading label="Cargando hallazgos…" rows={2} /> : null}
      {findings.error ? <ErrorBox error={findings.error} onRetry={findings.reload} title="No se pudieron cargar los hallazgos" /> : null}

      {orderedSections.length === 0 ? (
        <Empty title="Todavía no hay incisos">Los incisos ordenan el expediente (identidad, cuentas, contactos…). Creá el primero.</Empty>
      ) : (
        <div className="board" role="list" aria-label="Incisos del expediente">
          {orderedSections.map((section) => {
            const items = columnItems(section);
            const isUnclassified = unclassifiedSection?.id === section.id;
            return (
              <section
                key={section.id}
                role="listitem"
                className={classNames("col", dropTarget === section.id && "over")}
                onDragOver={(event) => {
                  if (draggingId === null) return;
                  event.preventDefault();
                  setDropTarget(section.id);
                }}
                onDragLeave={() => setDropTarget((current) => (current === section.id ? null : current))}
                onDrop={(event) => onDropOn(event, section.id)}
                aria-label={`Inciso ${section.name}`}
              >
                <header className="col-head">
                  {renaming?.id === section.id ? (
                    <form
                      className="rename-form"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void saveRename();
                      }}
                    >
                      <input
                        className="input input-sm"
                        maxLength={100}
                        value={renaming.name}
                        onChange={(event) => setRenaming({ id: section.id, name: event.target.value })}
                        onKeyDown={(event) => {
                          if (event.key === "Escape") setRenaming(null);
                        }}
                        aria-label="Nuevo nombre del inciso"
                        autoFocus
                      />
                      <IconButton icon="check" label="Guardar nombre" onClick={() => void saveRename()} />
                      <IconButton icon="x" label="Cancelar" onClick={() => setRenaming(null)} />
                    </form>
                  ) : (
                    <>
                      <h2 className="col-title" onDoubleClick={() => writable && !isUnclassified && setRenaming({ id: section.id, name: section.name })}>
                        {section.name}
                      </h2>
                      <span className="col-count mono">{items.length}</span>
                      {writable && !isUnclassified ? (
                        <span className="col-tools">
                          <IconButton icon="edit" label={`Renombrar «${section.name}»`} onClick={() => setRenaming({ id: section.id, name: section.name })} />
                          <IconButton icon="trash" label={`Borrar «${section.name}»`} onClick={() => setConfirm({ kind: "section", section })} />
                        </span>
                      ) : null}
                    </>
                  )}
                </header>

                <div className="col-body">
                  {items.length === 0 ? (
                    <p className="col-empty dim small">{draggingId !== null ? "Soltá acá para mover el hallazgo." : "Sin hallazgos en este inciso."}</p>
                  ) : null}
                  {items.map((finding) => (
                    <FindingCard
                      key={finding.id}
                      finding={finding}
                      caseId={caseId}
                      sections={orderedSections}
                      writable={writable}
                      dragging={draggingId === finding.id}
                      onDragStart={(event) => onDragStart(event, finding)}
                      onDragEnd={() => {
                        setDraggingId(null);
                        setDropTarget(null);
                      }}
                      onMove={(target) => void moveFinding(finding, target)}
                      onEditNote={() => setNoteEdit({ id: finding.id, text: finding.note ?? "" })}
                      onDelete={() => setConfirm({ kind: "finding", finding })}
                    />
                  ))}
                </div>
              </section>
            );
          })}

          <section
            className={classNames("col col-ghost", dropTarget === "none" && "over")}
            onDragOver={(event) => {
              if (draggingId === null || unclassifiedSection) return;
              event.preventDefault();
              setDropTarget("none");
            }}
            onDragLeave={() => setDropTarget((current) => (current === "none" ? null : current))}
            onDrop={(event) => {
              if (unclassifiedSection) return;
              onDropOn(event, null);
            }}
            hidden={unclassifiedSection !== undefined || (byColumn.get("none")?.length ?? 0) === 0}
            aria-label="Hallazgos sin inciso"
          >
            <header className="col-head">
              <h2 className="col-title">Sin inciso</h2>
              <span className="col-count mono">{byColumn.get("none")?.length ?? 0}</span>
            </header>
            <div className="col-body">
              {(byColumn.get("none") ?? []).map((finding) => (
                <FindingCard
                  key={finding.id}
                  finding={finding}
                  caseId={caseId}
                  sections={orderedSections}
                  writable={writable}
                  dragging={draggingId === finding.id}
                  onDragStart={(event) => onDragStart(event, finding)}
                  onDragEnd={() => {
                    setDraggingId(null);
                    setDropTarget(null);
                  }}
                  onMove={(target) => void moveFinding(finding, target)}
                  onEditNote={() => setNoteEdit({ id: finding.id, text: finding.note ?? "" })}
                  onDelete={() => setConfirm({ kind: "finding", finding })}
                />
              ))}
            </div>
          </section>
        </div>
      )}

      {noteEdit ? (
        <Modal
          title="Nota del hallazgo"
          onClose={() => setNoteEdit(null)}
          footer={
            <>
              <Btn onClick={() => setNoteEdit(null)}>Cancelar</Btn>
              <Btn variant="primary" onClick={() => void saveNote()} disabled={!writable}>
                Guardar nota
              </Btn>
            </>
          }
        >
          <textarea
            className="textarea"
            rows={5}
            maxLength={10000}
            value={noteEdit.text}
            onChange={(event) => setNoteEdit({ id: noteEdit.id, text: event.target.value })}
            aria-label="Nota"
            autoFocus
          />
          <InlineError error={actionError} />
        </Modal>
      ) : null}

      {confirm?.kind === "section" ? (
        <ConfirmDialog
          title="Borrar el inciso"
          confirmLabel="Borrar inciso"
          danger
          onClose={() => setConfirm(null)}
          onConfirm={async () => {
            const { section } = confirm;
            await endpoints.deleteSection(caseId, section.id);
            setConfirm(null);
            toast.notify(`Inciso «${section.name}» borrado. Sus hallazgos pasaron a sin clasificar.`, "info");
            sections.reload();
            findings.reload();
            refreshCase();
          }}
        >
          <p>
            Se borra «{confirm.section.name}». Sus {confirm.section.findings} hallazgos no se pierden: pasan a «sin clasificar».
          </p>
        </ConfirmDialog>
      ) : null}

      {confirm?.kind === "finding" ? (
        <ConfirmDialog
          title="Quitar el hallazgo del expediente"
          confirmLabel="Quitar"
          danger
          onClose={() => setConfirm(null)}
          onConfirm={async () => {
            const { finding } = confirm;
            await endpoints.deleteFinding(caseId, finding.id);
            setConfirm(null);
            findings.setData((list) => list?.filter((item) => item.id !== finding.id));
            sections.reload();
            refreshCase();
            toast.notify("Hallazgo quitado del expediente. Su fuente y su entidad se conservan.", "info");
          }}
        >
          <p>Se quita «{truncate(confirm.finding.value, 80)}» del expediente. La fuente y la entidad asociadas no se borran.</p>
        </ConfirmDialog>
      ) : null}

    </div>
  );
}

function FindingCard({
  finding,
  caseId,
  sections,
  writable,
  dragging,
  onDragStart,
  onDragEnd,
  onMove,
  onEditNote,
  onDelete,
}: {
  finding: FindingOut;
  caseId: number;
  sections: SectionOut[];
  writable: boolean;
  dragging: boolean;
  onDragStart: (event: DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onMove: (target: number | null) => void;
  onEditNote: () => void;
  onDelete: () => void;
}) {
  const quote = chunkText(finding, "quote");
  const context = chunkText(finding, "context");
  const pageUrl = chunkText(finding, "page_url");
  const pageTitle = chunkText(finding, "page_title");
  const author = chunkText(finding, "author_handle");
  const platform = chunkText(finding, "platform");
  const detectedBy = chunkText(finding, "detected_by");
  const when = chunkText(finding, "captured_at") || finding.created_at;

  return (
    <article
      className={classNames("finding", dragging && "dragging")}
      draggable={writable}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      aria-label={`Hallazgo ${finding.value}`}
    >
      <header className="finding-head">
        <Badge tone="neutral">{finding.kind === "text" ? "Cita" : entityTypeLabel(finding.kind)}</Badge>
        {detectedBy ? <span className="dim small">{detectedBy === "manual" ? "manual" : detectedBy === "funes" ? "FUNES" : "regla"}</span> : null}
        <time className="finding-date mono" dateTime={when} title={fmtDateTime(when)}>
          {fmtDate(when)}
        </time>
      </header>
      <div className="finding-value mono">{truncate(finding.value, 140)}</div>
      {quote ? <blockquote className="finding-quote">{truncate(quote, 320)}</blockquote> : null}
      {context ? <p className="finding-context">{truncate(context, 220)}</p> : null}
      {pageUrl ? (
        <p className="finding-src">
          <a className="mono" href={pageUrl} target="_blank" rel="noopener noreferrer" title={pageUrl}>
            {truncate(pageUrl, 58)}
          </a>
          {pageTitle ? <span className="dim small">{truncate(pageTitle, 70)}</span> : null}
        </p>
      ) : null}
      {author || platform ? (
        <p className="dim small mono">
          {platform ? `${platform}` : ""}
          {author ? ` · @${author}` : ""}
        </p>
      ) : null}
      {finding.note ? <p className="finding-note">Nota: {truncate(finding.note, 200)}</p> : null}

      <footer className="finding-foot">
        <label className="sr-only" htmlFor={`move-${finding.id}`}>
          Mover a inciso
        </label>
        <select
          id={`move-${finding.id}`}
          className="select select-xs"
          value={finding.section_id ?? ""}
          disabled={!writable}
          onChange={(event) => onMove(event.target.value === "" ? null : Number(event.target.value))}
        >
          <option value="">Sin inciso</option>
          {sections.map((section) => (
            <option key={section.id} value={section.id}>
              {section.name}
            </option>
          ))}
        </select>
        {finding.entity_id ? (
          <Link className="icon-btn" to={`/casos/${caseId}/grafo?nodo=${finding.entity_id}`} title="Ver la entidad en el grafo" aria-label="Ver la entidad en el grafo">
            <span className="mono small">↗</span>
          </Link>
        ) : null}
        <IconButton icon="edit" label="Editar nota" disabled={!writable} onClick={onEditNote} />
        <IconButton icon="trash" label="Quitar del expediente" disabled={!writable} onClick={onDelete} />
      </footer>
    </article>
  );
}
