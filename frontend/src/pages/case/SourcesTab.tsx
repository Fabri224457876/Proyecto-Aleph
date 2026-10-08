import { useState, type FormEvent } from "react";
import * as endpoints from "../../api/endpoints";
import type { SourceOut } from "../../api/types";
import { Admiralty, Badge, Btn, Empty, ErrorBox, Field, InlineError, Loading, Modal } from "../../components/ui";
import { ApiError, downloadFile } from "../../api/client";
import { errorMessage, fmtDateTime, shortHash, truncate } from "../../lib/format";
import { CREDIBILITY_LABEL, RELIABILITY_LABEL, SOURCE_KIND_LABEL } from "../../lib/labels";
import { useToast } from "../../lib/toast";
import { useAsync } from "../../lib/useAsync";
import type { CaseTabProps } from "./tabProps";

const PAGE_SIZE = 50;
type KindFilter = "" | "upload" | "manual" | "connector" | "funes" | "menard" | "enrichment";

export function SourcesTab({ caseId, writable }: CaseTabProps) {
  const toast = useToast();
  const [kind, setKind] = useState<KindFilter>("");
  const [offset, setOffset] = useState(0);
  const [dialog, setDialog] = useState<"manual" | "upload" | null>(null);
  const [downloading, setDownloading] = useState<number | null>(null);

  const sources = useAsync(
    () => endpoints.listSources(caseId, { kind: kind || undefined, limit: PAGE_SIZE, offset }),
    [caseId, kind, offset],
  );
  const total = sources.data?.total ?? 0;

  async function download(source: SourceOut) {
    setDownloading(source.id);
    try {
      await downloadFile(endpoints.rawPath(caseId, source.id), `aleph-fuente-${source.id}`);
      toast.notify("Crudo descargado. El hash se verificó en el servidor.", "ok");
    } catch (err) {
      const message = err instanceof ApiError ? err.detail : errorMessage(err);
      toast.notify(message, "bad");
    } finally {
      setDownloading(null);
    }
  }

  return (
    <div className="sources">
      <div className="toolbar">
        <select className="select select-compact" aria-label="Tipo de fuente" value={kind} onChange={(e) => { setKind(e.target.value as KindFilter); setOffset(0); }}>
          <option value="">Todos los tipos</option>
          {Object.entries(SOURCE_KIND_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <span className="dim small mono">{total} fuentes</span>
        <div className="toolbar-end">
          <Btn size="sm" disabled={!writable} title={writable ? undefined : "Solo con el caso abierto y un rol de escritura"} onClick={() => setDialog("manual")}>
            Registrar fuente
          </Btn>
          <Btn size="sm" variant="primary" icon="upload" disabled={!writable} title={writable ? undefined : "Solo con el caso abierto y un rol de escritura"} onClick={() => setDialog("upload")}>
            Subir archivo
          </Btn>
        </div>
      </div>

      <p className="dim small">
        Cada dato tiene procedencia. La valoración Admiralty combina la fiabilidad de la fuente (A–F) con la credibilidad del dato (1–6). Los archivos se guardan con su hash SHA-256.
      </p>

      {sources.loading && !sources.data ? <Loading label="Cargando fuentes…" rows={4} /> : null}
      {sources.error ? <ErrorBox error={sources.error} onRetry={sources.reload} title="No se pudieron cargar las fuentes" /> : null}
      {sources.data && sources.data.items.length === 0 ? (
        <Empty title="Todavía no hay fuentes">Registrá una fuente manual o subí el archivo que aportó el operador. Cada hallazgo y cada entidad quedan ligados a su fuente.</Empty>
      ) : null}

      {sources.data && sources.data.items.length > 0 ? (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="col-id">ID</th>
                  <th>Tipo</th>
                  <th>Referencia</th>
                  <th className="col-tlp">Admiralty</th>
                  <th>SHA-256</th>
                  <th className="col-date">Recuperada</th>
                  <th className="col-action" aria-label="Acciones" />
                </tr>
              </thead>
              <tbody>
                {sources.data.items.map((source) => (
                  <tr key={source.id}>
                    <td className="mono dim">#{source.id}</td>
                    <td>
                      <Badge tone="neutral">{SOURCE_KIND_LABEL[source.kind] ?? source.kind}</Badge>
                    </td>
                    <td title={source.reference}>
                      <span className="mono small">{truncate(source.reference || "(sin referencia)", 70)}</span>
                      <div className="row-sub" title={`${RELIABILITY_LABEL[source.reliability] ?? ""} · ${CREDIBILITY_LABEL[source.credibility] ?? ""}`}>
                        {RELIABILITY_LABEL[source.reliability] ?? source.reliability} · {CREDIBILITY_LABEL[source.credibility] ?? source.credibility}
                      </div>
                    </td>
                    <td>
                      <Admiralty code={source.admiralty} />
                    </td>
                    <td className="mono dim small" title={source.sha256}>
                      {shortHash(source.sha256, 16)}
                    </td>
                    <td className="mono dim small">{fmtDateTime(source.retrieved_at)}</td>
                    <td>
                      {source.has_raw ? (
                        <Btn size="sm" icon="download" disabled={downloading === source.id} onClick={() => void download(source)}>
                          {downloading === source.id ? "Verificando…" : "Crudo"}
                        </Btn>
                      ) : (
                        <span className="dim small">sin crudo</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="pager">
            <span className="dim mono small">
              {offset + 1}–{Math.min(offset + PAGE_SIZE, total)} de {total}
            </span>
            <div className="pager-buttons">
              <Btn size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Anterior
              </Btn>
              <Btn size="sm" disabled={offset + PAGE_SIZE >= total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Siguiente
              </Btn>
            </div>
          </div>
        </>
      ) : null}

      {dialog === "manual" ? (
        <ManualSourceDialog
          caseId={caseId}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            toast.notify("Fuente registrada.", "ok");
            sources.reload();
          }}
        />
      ) : null}
      {dialog === "upload" ? (
        <UploadDialog
          caseId={caseId}
          onClose={() => setDialog(null)}
          onDone={() => {
            setDialog(null);
            toast.notify("Archivo subido como fuente. Se guardó con su hash.", "ok");
            sources.reload();
          }}
        />
      ) : null}
    </div>
  );
}

const RELIABILITY_OPTIONS = ["A", "B", "C", "D", "E", "F"];
const CREDIBILITY_OPTIONS = ["1", "2", "3", "4", "5", "6"];

function AdmiraltyFields({
  reliability,
  credibility,
  onReliability,
  onCredibility,
}: {
  reliability: string;
  credibility: string;
  onReliability: (value: string) => void;
  onCredibility: (value: string) => void;
}) {
  return (
    <div className="form-row">
      <Field label="Fiabilidad de la fuente" hint={RELIABILITY_LABEL[reliability]}>
        {(id) => (
          <select id={id} className="select" value={reliability} onChange={(e) => onReliability(e.target.value)}>
            {RELIABILITY_OPTIONS.map((code) => (
              <option key={code} value={code}>
                {code} · {RELIABILITY_LABEL[code]}
              </option>
            ))}
          </select>
        )}
      </Field>
      <Field label="Credibilidad del dato" hint={CREDIBILITY_LABEL[credibility]}>
        {(id) => (
          <select id={id} className="select" value={credibility} onChange={(e) => onCredibility(e.target.value)}>
            {CREDIBILITY_OPTIONS.map((code) => (
              <option key={code} value={code}>
                {code} · {CREDIBILITY_LABEL[code]}
              </option>
            ))}
          </select>
        )}
      </Field>
    </div>
  );
}

function ManualSourceDialog({ caseId, onClose, onDone }: { caseId: number; onClose: () => void; onDone: () => void }) {
  const [kind, setKind] = useState<"manual" | "connector">("manual");
  const [reference, setReference] = useState("");
  const [reliability, setReliability] = useState("B");
  const [credibility, setCredibility] = useState("2");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!reference.trim()) {
      setError(new Error("Indicá de dónde viene el dato: URL, documento o persona que lo aportó (sin datos personales innecesarios)."));
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await endpoints.createSource(caseId, { kind, reference: reference.trim(), reliability, credibility });
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Registrar una fuente"
      onClose={onClose}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn type="submit" form="source-form" variant="primary" disabled={busy}>
            {busy ? "Guardando…" : "Registrar"}
          </Btn>
        </>
      }
    >
      <form id="source-form" className="form-stack" onSubmit={onSubmit} noValidate>
        <Field label="Tipo">
          {(id) => (
            <select id={id} className="select" value={kind} onChange={(e) => setKind(e.target.value as "manual" | "connector")}>
              <option value="manual">Manual (dato aportado por una persona)</option>
              <option value="connector">Conector (URL o consulta)</option>
            </select>
          )}
        </Field>
        <Field label="Referencia" hint="URL, nombre del documento o descripción del origen.">
          {(id) => <textarea id={id} className="textarea" rows={2} maxLength={2000} value={reference} onChange={(e) => setReference(e.target.value)} />}
        </Field>
        <AdmiraltyFields reliability={reliability} credibility={credibility} onReliability={setReliability} onCredibility={setCredibility} />
        <InlineError error={error} />
      </form>
    </Modal>
  );
}

function UploadDialog({ caseId, onClose, onDone }: { caseId: number; onClose: () => void; onDone: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [reference, setReference] = useState("");
  const [reliability, setReliability] = useState("F");
  const [credibility, setCredibility] = useState("6");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file) {
      setError(new Error("Elegí un archivo para subir."));
      return;
    }
    const form = new FormData();
    form.append("file", file);
    form.append("reliability", reliability);
    form.append("credibility", credibility);
    if (reference.trim()) form.append("reference", reference.trim());
    setBusy(true);
    setError(null);
    try {
      await endpoints.uploadSource(caseId, form);
      onDone();
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Subir un archivo como fuente"
      onClose={onClose}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn type="submit" form="upload-form" variant="primary" disabled={busy || !file}>
            {busy ? "Subiendo…" : "Subir y registrar"}
          </Btn>
        </>
      }
    >
      <form id="upload-form" className="form-stack" onSubmit={onSubmit} noValidate>
        <p className="dim small">Subí solo material que aportó el operador o que es público. Se guarda con su SHA-256 y no se puede alterar sin que la verificación lo detecte.</p>
        <Field label="Archivo">
          {(id) => (
            <input id={id} className="input" type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          )}
        </Field>
        {file ? (
          <p className="mono small dim">
            {file.name} · {(file.size / 1024).toFixed(1)} KB
          </p>
        ) : null}
        <Field label="Descripción" hint="Opcional. Por defecto se usa el nombre del archivo.">
          {(id) => <input id={id} className="input" maxLength={500} value={reference} onChange={(e) => setReference(e.target.value)} />}
        </Field>
        <AdmiraltyFields reliability={reliability} credibility={credibility} onReliability={setReliability} onCredibility={setCredibility} />
        <InlineError error={error} />
      </form>
    </Modal>
  );
}
