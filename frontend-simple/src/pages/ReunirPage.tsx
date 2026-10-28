import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import * as ep from "../api/endpoints";
import type { ConnectorOut, ToolOut } from "../api/contract";
import { useCase } from "../components/CaseLayout";
import { ResultView, type ResultItem } from "../components/ResultView";
import { Button, Chip, Field, Help, Notice, PageTitle, Panel, Loading } from "../components/ui";
import { detect, type Detection } from "../lib/detect";
import { buildActions, isTextFile, extOf, type ActionDef } from "../lib/reunirActions";
import { errorMessage, fmtInt } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { useToast } from "../lib/toast";
import { CREDIBILITY_HELP, RELIABILITY_HELP } from "../lib/glossary";
import { Link } from "../lib/router";

const EXAMPLES = ["ejemplo.com", "persona@ejemplo.com", "203.0.113.7", "CVE-2021-44228", "usuario_ejemplo"];

// Plataformas en las que se busca si el caso ya conoce un nombre de usuario.
const LOOKUP_PLATFORMS = ["bluesky", "mastodon", "github", "reddit", "telegram", "x", "twitter", "instagram", "discord"];

function sizeText(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function ReunirPage() {
  const ctx = useCase();
  const toast = useToast();
  const connectorsState = useAsync<ConnectorOut[]>(() => ep.listConnectors().catch(() => []), []);
  const toolsState = useAsync<ToolOut[]>(() => ep.listTools().catch(() => []), []);

  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileText, setFileText] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const [reliability, setReliability] = useState("F");
  const [credibility, setCredibility] = useState("6");
  const [results, setResults] = useState<ResultItem[]>([]);
  const [running, setRunning] = useState<string | null>(null);
  const [known, setKnown] = useState<Array<{ platform: string; posts: number; entityId: number | null }>>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const textBox = useRef<HTMLTextAreaElement>(null);

  const detection: Detection = useMemo(() => detect(text), [text]);

  // Si lo escrito es un nombre de usuario, mira si el caso ya lo conoce en alguna plataforma.
  const handle = !file && detection.kind === "handle" ? detection.value.replace(/^@/, "") : "";
  useEffect(() => {
    setKnown([]);
    if (!handle) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      Promise.all(
        LOOKUP_PLATFORMS.map((platform) =>
          ep
            .lookupHandles(ctx.caseId, platform, [handle])
            .then((out) => {
              const info = out.handles?.[handle];
              return info?.known ? { platform, posts: info.posts_captured ?? 0, entityId: info.entity_id ?? null } : null;
            })
            .catch(() => null),
        ),
      ).then((rows) => {
        if (alive) setKnown(rows.filter((r): r is { platform: string; posts: number; entityId: number | null } => r !== null));
      });
    }, 600);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [handle, ctx.caseId]);

  async function takeFile(next: File) {
    setFile(next);
    setFileText(null);
    if (isTextFile(next)) {
      try {
        setFileText(await next.text());
      } catch {
        setFileText(null);
      }
    }
  }

  function onDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setOver(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) void takeFile(dropped);
  }

  const actions: ActionDef[] | null =
    connectorsState.data && toolsState.data
      ? buildActions({
          caseId: ctx.caseId,
          detection,
          file,
          fileText,
          reliability,
          credibility,
          connectors: connectorsState.data,
          tools: toolsState.data,
        })
      : null;

  async function execute(action: ActionDef) {
    if (running) return;
    setRunning(action.id);
    const id = Date.now() + Math.random();
    try {
      const outcome = await action.run();
      setResults((prev) => [{ id, actionTitle: action.title, outcome }, ...prev]);
      ctx.reloadPending();
    } catch (error) {
      const message = errorMessage(error);
      setResults((prev) => [{ id, actionTitle: action.title, error: message }, ...prev]);
      toast.notify(message, "bad");
    } finally {
      setRunning(null);
    }
  }

  const label = file ? `Archivo: ${file.name}` : detection.label;
  const hint = file ? `${extOf(file.name) ? extOf(file.name).toUpperCase() : "Archivo"}, ${sizeText(file.size)}.` : detection.why;

  return (
    <div className="page">
      <PageTitle lead="Escribí o pegá un dato, o soltá un archivo. Te digo qué podés hacer con eso y de dónde sale cada dato, antes de hacer nada.">
        Reunir
      </PageTitle>

      <div className="stack">
        <div
          className={`capture${over ? " is-over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
        >
          {file ? (
            <div className="stack">
              <p className="eyebrow">Archivo cargado</p>
              <p className="mono" style={{ fontSize: "1.1rem" }}>
                {file.name} <span className="muted">({sizeText(file.size)})</span>
              </p>
              {fileText !== null && <p className="small muted">Es un texto: también puedo buscar datos dentro.</p>}
              <div className="btn-row">
                <Button variant="ghost" onClick={() => setFile(null)}>
                  Quitar archivo
                </Button>
              </div>
            </div>
          ) : (
            <>
              <label htmlFor="reunir-input" className="capture-question">
                ¿Qué querés investigar?
              </label>
              <textarea
                id="reunir-input"
                ref={textBox}
                className="capture-input"
                rows={3}
                value={text}
                placeholder="Un usuario, un dominio, un correo, una IP, una URL, un hash, o un texto largo para buscar datos adentro."
                onChange={(e) => setText(e.target.value)}
                aria-describedby="reunir-detect"
              />
              <div className="capture-foot">
                <p id="reunir-detect" className="detected" aria-live="polite">
                  {text.trim() ? (
                    <>
                      <span className="small muted">Detectado</span>
                      <strong>{label}</strong>
                      <span className="small muted">{hint}</span>
                    </>
                  ) : (
                    <span className="small muted">Arrastrá un archivo acá o usá el botón. Nada se guarda hasta que elijas una acción.</span>
                  )}
                </p>
                <div className="btn-row">
                  <input
                    ref={fileInput}
                    type="file"
                    className="sr-only"
                    id="reunir-file"
                    onChange={(e) => {
                      const chosen = e.target.files?.[0];
                      if (chosen) void takeFile(chosen);
                      e.target.value = "";
                    }}
                  />
                  <Button onClick={() => fileInput.current?.click()}>Elegir archivo</Button>
                  {text && (
                    <Button variant="ghost" onClick={() => setText("")}>
                      Borrar
                    </Button>
                  )}
                </div>
              </div>
            </>
          )}
        </div>

        {!text.trim() && !file && (
          <div className="row small" style={{ marginTop: 14 }}>
            <span className="muted">Probá con:</span>
            {EXAMPLES.map((example) => (
              <button
                key={example}
                type="button"
                className="chip"
                style={{ cursor: "pointer" }}
                onClick={() => {
                  setText(example);
                  textBox.current?.focus();
                }}
              >
                {example}
              </button>
            ))}
          </div>
        )}
      </div>

      {!ctx.canWrite && <Notice tone="info">Podés ver las acciones, pero no ejecutarlas: {ctx.readOnlyReason}</Notice>}

      {handle && known.length > 0 && (
        <Notice tone="info" title={`El caso ya conoce "${handle}"`}>
          <p>
            Aparece en {known.length === 1 ? "una plataforma" : `${known.length} plataformas`}:{" "}
            {known.map((k) => `${k.platform} (${fmtInt(k.posts)} ${k.posts === 1 ? "publicación" : "publicaciones"})`).join(", ")}.
          </p>
          <p className="small">
            <Link to={`/casos/${ctx.caseId}/entender?vista=personas`}>Ver personas y cuentas del caso</Link>
          </p>
        </Notice>
      )}

      <section aria-labelledby="acciones-titulo" className="stack">
        <h2 id="acciones-titulo">Qué podés hacer con esto</h2>
        {!text.trim() && !file && <p className="muted">Escribí o soltá algo arriba y acá aparecen las acciones posibles.</p>}
        {(text.trim() || file) && actions === null && <Loading what="Preparando las acciones" />}
        {actions && actions.length === 0 && (
          <Notice tone="info">Para este dato no hay acciones automáticas. Podés guardarlo a mano en Más opciones, Entidades.</Notice>
        )}
        {actions && actions.length > 0 && (
          <div className="action-grid">
            {actions.map((action) => (
              <article className="action" key={action.id} aria-labelledby={`a-${action.id}`}>
                <h3 id={`a-${action.id}`}>{action.title}</h3>
                <dl>
                  <dt>Qué hace</dt>
                  <dd>{action.what}</dd>
                  <dt>De dónde saca el dato</dt>
                  <dd>{action.source}</dd>
                  <dt>Qué queda en el caso</dt>
                  <dd>{action.effect}</dd>
                </dl>
                {action.network && <Chip tone="warn">Consulta un servicio externo</Chip>}
                {action.wantsAdmiralty && (
                  <div className="row">
                    <Field label={<>Fiabilidad de la fuente <Help term="admiralty" /></>} hint={RELIABILITY_HELP[reliability]}>
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
                )}
                {action.disabledReason && <Notice tone="warn">{action.disabledReason}</Notice>}
                <div className="action-foot">
                  <span className="small muted">
                    {running === action.id ? "Ejecutando…" : ""}
                  </span>
                  <Button
                    variant="primary"
                    disabled={!ctx.canWrite || Boolean(action.disabledReason) || running !== null}
                    onClick={() => void execute(action)}
                  >
                    {running === action.id ? "Ejecutando…" : "Ejecutar"}
                  </Button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      {results.length > 0 && (
        <section aria-label="Resultados de esta sesión" className="stack">
          <h2>Resultados</h2>
          {results.map((item) => (
            <ResultView key={item.id} item={item} />
          ))}
        </section>
      )}

      <Panel title="Cómo funciona">
        <ul className="stack" style={{ margin: 0, paddingLeft: "1.2em" }}>
          <li>Nada entra confirmado solo: lo que el sistema propone va a <strong>Revisar</strong>, y lo decidís vos.</li>
          <li>Cada acción dice de dónde saca el dato antes de ejecutarse. Las que consultan internet lo indican.</li>
          <li>Los datos que encuentra en un texto son una vista previa: solo se suman al expediente los que elegís.</li>
        </ul>
      </Panel>
    </div>
  );
}
