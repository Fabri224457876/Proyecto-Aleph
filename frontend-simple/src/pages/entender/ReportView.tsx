import { useState } from "react";
import * as ep from "../../api/endpoints";
import type { AttackTechnique } from "../../api/contract";
import { useCase } from "../../components/CaseLayout";
import { Button, Chip, ErrorBox, Notice, Panel, Help } from "../../components/ui";
import { useToast } from "../../lib/toast";
import { errorMessage } from "../../lib/format";
import { saveJson, saveText } from "../../lib/download";
import { downloadFile } from "../../api/client";

interface ReportState {
  markdown: string;
  generatedBy: string;
  warnings: string[];
  invalid: string[];
}

export function ReportView() {
  const ctx = useCase();
  const toast = useToast();
  const [title, setTitle] = useState("");
  const [includePending, setIncludePending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<ReportState | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<unknown>(null);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<AttackTechnique[] | null>(null);
  const [linked, setLinked] = useState<number | null>(null);

  async function draft() {
    setBusy(true);
    setReport(null);
    setReportError(null);
    try {
      const out = await ep.buildReport(ctx.caseId, { title: title.trim(), include_pending: includePending });
      setReport({
        markdown: out.markdown,
        generatedBy: out.generated_by,
        warnings: out.warnings ?? [],
        invalid: out.invalid_ids ?? [],
      });
    } catch (err) {
      // Sin modelo de IA la API responde 503 con el motivo: se muestra como "no disponible".
      setReportError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function exportFile(name: string, path: string) {
    setExportError(null);
    try {
      await downloadFile(path, name);
    } catch (err) {
      setExportError(err);
    }
  }

  async function loadLayer() {
    try {
      const layer = await ep.attackLayer(ctx.caseId, false);
      const techniques = (layer as { techniques?: unknown[] }).techniques;
      setLinked(Array.isArray(techniques) ? techniques.length : 0);
      saveJson(`aleph-caso-${ctx.caseId}-attack-navigator.json`, layer);
      toast.notify("Capa ATT&CK descargada. Se abre en ATT&CK Navigator.", "ok");
    } catch (err) {
      setExportError(err);
    }
  }

  return (
    <div className="stack">
      <Panel
        title={
          <>
            Borrador de informe <Help text="Un borrador en Markdown con lo confirmado del caso. Lo redacta la IA local; si no está disponible, te lo digo. Revisalo antes de compartirlo." />
          </>
        }
      >
        <div className="stack">
          <div className="row">
            <input
              className="input grow"
              aria-label="Título del informe (opcional)"
              placeholder="Título del informe (opcional). Por defecto, el nombre del caso."
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </div>
          <label className="check small">
            <input type="checkbox" checked={includePending} onChange={(e) => setIncludePending(e.target.checked)} />
            Incluir también lo que todavía no fue confirmado (propuestas e hipótesis pendientes)
          </label>
          <div className="btn-row">
            <Button variant="primary" disabled={busy} onClick={() => void draft()}>
              {busy ? "Redactando…" : "Redactar borrador"}
            </Button>
            {report && (
              <Button onClick={() => saveText(`aleph-caso-${ctx.caseId}-informe.md`, report.markdown, "text/markdown")}>
                Descargar .md
              </Button>
            )}
          </div>
          {reportError && (
            <Notice tone="warn" title="Informe no disponible">
              <p>{reportError}</p>
              <p className="small">No es un error del caso: el redactor de IA no está respondiendo. Podés seguir trabajando y volver a intentar más tarde.</p>
            </Notice>
          )}
          {report && (
            <div className="stack">
              <p className="small muted">
                Generado por: {report.generatedBy === "llm" ? "IA local" : "plantilla (sin IA)"}.
                {report.invalid.length > 0 && ` Hay ${report.invalid.length} referencias que no se pudieron verificar.`}
              </p>
              {report.warnings.map((w) => (
                <Notice key={w} tone="warn">
                  <p>{w}</p>
                </Notice>
              ))}
              <div className="report" tabIndex={0} aria-label="Texto del borrador">
                {report.markdown}
              </div>
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Exportar">
        <div className="stack">
          <p className="small muted">Cada exportación queda registrada en la auditoría.</p>
          {exportError ? <ErrorBox error={exportError} /> : null}
          <div className="btn-row">
            <Button onClick={() => void exportFile(`aleph-caso-${ctx.caseId}-stix.json`, ep.exportPath(ctx.caseId, "stix", false))}>
              Paquete STIX 2.1
            </Button>
            <Button onClick={() => void exportFile(`aleph-caso-${ctx.caseId}-misp.json`, ep.exportPath(ctx.caseId, "misp", false))}>
              Evento MISP
            </Button>
            <Button onClick={() => void exportFile(`aleph-caso-${ctx.caseId}.json`, ep.exportCasePath(ctx.caseId, false))}>
              Copia completa del caso (JSON)
            </Button>
            <Button onClick={() => void loadLayer()}>Capa para ATT&CK Navigator</Button>
          </div>
          <p className="small muted">
            STIX y MISP son formatos para intercambiar inteligencia con otras organizaciones. Solo salen los datos confirmados.
          </p>
        </div>
      </Panel>

      <Panel title="Técnicas ATT&CK del caso">
        <div className="stack">
          <p className="small muted">
            Vinculá una técnica como decisión tuya. Sirve para la matriz y no reemplaza el análisis.
            {linked !== null && <> Vinculadas ahora: <Chip>{linked}</Chip></>}
          </p>
          <div className="row">
            <input
              className="input grow"
              aria-label="Buscar técnica ATT&CK"
              placeholder="ID (T1566) o texto (phishing)"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Button
              disabled={!query.trim()}
              onClick={() =>
                void ep
                  .searchTechniques(query.trim(), 12)
                  .then(setHits)
                  .catch((err: unknown) => setExportError(err))
              }
            >
              Buscar
            </Button>
          </div>
          {hits && hits.length === 0 && <p className="small muted">Ninguna técnica coincide.</p>}
          {hits?.map((t) => (
            <div className="item" key={t.id}>
              <div className="item-main">
                <p className="item-title" style={{ fontSize: "1rem" }}>
                  {t.id} · {t.nombre}
                </p>
                <p className="item-meta">{t.nombres_tacticas.join(", ") || "sin táctica"}</p>
              </div>
              <Button
                variant="secondary"
                disabled={!ctx.canWrite}
                onClick={() =>
                  void ep
                    .linkTechnique(ctx.caseId, { technique_id: t.id, comment: "" })
                    .then(() => toast.notify(`Técnica ${t.id} vinculada al caso.`, "ok"))
                    .catch((err: unknown) => setExportError(err))
                }
              >
                Vincular al caso
              </Button>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
