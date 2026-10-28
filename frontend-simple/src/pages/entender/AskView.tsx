import { useState, type FormEvent, type ReactNode } from "react";
import * as ep from "../../api/endpoints";
import type { AskOut, HitOut } from "../../api/contract";
import { useCase } from "../../components/CaseLayout";
import { Button, Chip, Notice, Panel, Help } from "../../components/ui";
import { Link } from "../../lib/router";
import { errorMessage } from "../../lib/format";
import { KIND_LABEL, hitLink } from "../BuscarPage";

const EXAMPLES = [
  "¿Qué dominios y billeteras aparecen en el caso?",
  "¿Qué cuentas podrían ser del mismo operador y por qué?",
  "¿Qué falta confirmar?",
];

/** Muestra la respuesta con cada cita [E12] convertida en un enlace al dato. */
function withCitations(text: string, byRef: Map<string, HitOut>): ReactNode[] {
  return text.split(/(\[[A-Z]{1,2}\d{1,7}\??\])/g).map((part, i) => {
    const match = /^\[([A-Z]{1,2}\d{1,7})(\?)?\]$/.exec(part);
    if (!match) return part;
    const hit = byRef.get(match[1]);
    if (!hit || match[2]) {
      return (
        <span key={i} className="cite cite-bad" title="La respuesta citó algo que no estaba en los datos que recibió">
          {part}
        </span>
      );
    }
    return (
      <Link key={i} to={hitLink(hit)} className="cite" title={`${KIND_LABEL[hit.kind]}: ${hit.title}`}>
        {part}
      </Link>
    );
  });
}

export function AskView() {
  const ctx = useCase();
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<AskOut | null>(null);

  async function ask(q: string) {
    if (q.trim().length < 3) return;
    setBusy(true);
    setError(null);
    try {
      setAnswer(await ep.askCase(ctx.caseId, q.trim()));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void ask(question);
  }

  const byRef = new Map((answer?.context ?? []).map((h) => [h.ref, h]));

  return (
    <div className="stack">
      <Panel
        title={
          <>
            Preguntarle al caso{" "}
            <Help text="FUNES busca en el caso los datos más relevantes (por texto y por significado) y responde solo con ellos, citando cada uno. Lo que no esté en el caso, no lo inventa: te dice que no alcanza." />
          </>
        }
      >
        <form className="stack" onSubmit={submit}>
          <textarea
            className="textarea"
            rows={2}
            maxLength={1000}
            aria-label="Tu pregunta"
            placeholder="Preguntá en lenguaje común, por ejemplo: ¿qué dominios usa la campaña?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <div className="btn-row">
            <Button variant="primary" type="submit" disabled={busy || question.trim().length < 3}>
              {busy ? "Pensando…" : "Preguntar"}
            </Button>
            {EXAMPLES.map((ex) => (
              <Button
                key={ex}
                variant="ghost"
                className="btn-small"
                disabled={busy}
                onClick={() => {
                  setQuestion(ex);
                  void ask(ex);
                }}
              >
                {ex}
              </Button>
            ))}
          </div>
        </form>
      </Panel>

      {error && (
        <Notice tone="warn" title="No pude responder">
          <p>{error}</p>
          <p className="small">Si dice que el modelo no responde, revisá que el servidor de IA local esté levantado.</p>
        </Notice>
      )}

      {answer && (
        <Panel title="Respuesta">
          <div className="stack">
            <p className="answer">{withCitations(answer.answer, byRef)}</p>
            {answer.warnings.map((w) => (
              <Notice key={w} tone="warn">
                <p>{w}</p>
              </Notice>
            ))}
            <p className="small muted">
              Es un borrador de la IA local a partir de {answer.context.length} datos del caso
              {answer.retrieval === "usado" ? ", buscados por texto y por significado" : ", buscados por texto"}. Verificá
              las citas antes de usarlo.
            </p>
            {answer.citations.length > 0 && (
              <details>
                <summary>Datos citados ({answer.citations.length})</summary>
                <ul className="list">
                  {answer.citations.map((h) => (
                    <li key={h.ref}>
                      <Link to={hitLink(h)} className="item item-link">
                        <div className="item-main">
                          <div className="item-title">
                            [{h.ref}] {h.title}
                          </div>
                          <div className="item-meta">{h.snippet}</div>
                        </div>
                        <Chip>{KIND_LABEL[h.kind]}</Chip>
                      </Link>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        </Panel>
      )}
    </div>
  );
}
