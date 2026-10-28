import { useState } from "react";
import * as ep from "../api/endpoints";
import { Button, Help, Notice, PageTitle, Panel, Field } from "../components/ui";
import { useToast } from "../lib/toast";
import { errorMessage } from "../lib/format";
import { TLP_HINT, TLP_LABEL, TLP_ORDER } from "../lib/labels";
import { navigate, Link } from "../lib/router";
import { useAuth } from "../lib/auth";

const STEPS = ["Nombre y propósito", "Base legal", "Nivel de reserva"];

export function NewCasePage() {
  const auth = useAuth();
  const toast = useToast();
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [legalBasis, setLegalBasis] = useState("");
  const [tlp, setTlp] = useState<string>("amber");
  const [stepError, setStepError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!auth.canWrite) {
    return (
      <div className="page">
        <PageTitle>Abrir un caso</PageTitle>
        <Notice tone="warn">Tu rol no puede abrir casos. Pedile a un analista o a un administrador que lo haga.</Notice>
        <Link to="/">Volver a mis casos</Link>
      </div>
    );
  }

  function next() {
    if (step === 0 && !name.trim()) return setStepError("Ponele un nombre al caso para reconocerlo después.");
    if (step === 1 && legalBasis.trim().length < 5)
      return setStepError("Escribí la base legal: la autorización concreta con la que investigás.");
    setStepError(null);
    setStep((s) => Math.min(s + 1, STEPS.length - 1));
  }

  async function submit() {
    setBusy(true);
    setSubmitError(null);
    try {
      const created = await ep.createCase({
        name: name.trim(),
        description: purpose.trim(),
        legal_basis: legalBasis.trim(),
        tlp,
      });
      toast.notify("Caso abierto. Empezá por reunir la primera fuente.", "ok");
      navigate(`/casos/${created.id}/reunir`);
    } catch (err) {
      setSubmitError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page">
      <PageTitle lead="Tres pasos. Después, el caso queda listo para reunir datos.">Abrir un caso nuevo</PageTitle>

      <ol className="wizard-progress" aria-label="Pasos">
        {STEPS.map((label, i) => (
          <li key={label} className={i === step ? "is-current" : i < step ? "is-done" : ""} aria-current={i === step ? "step" : undefined}>
            {i + 1}. {label}
          </li>
        ))}
      </ol>

      <Panel>
        {step === 0 && (
          <div className="stack">
            <Field label="Nombre del caso" hint="Algo que lo identifique en la lista. Podés cambiarlo después.">
              {(id) => <input id={id} className="input" maxLength={200} value={name} onChange={(e) => setName(e.target.value)} autoFocus />}
            </Field>
            <Field
              label={
                <>
                  ¿Para qué lo investigás? <span className="muted">(opcional)</span>
                </>
              }
              hint="Una o dos frases. Ayuda a quien revise el caso a entender el objetivo."
            >
              {(id) => (
                <textarea id={id} className="textarea" rows={3} maxLength={2000} value={purpose} onChange={(e) => setPurpose(e.target.value)} />
              )}
            </Field>
          </div>
        )}

        {step === 1 && (
          <div className="stack">
            <Field
              label={
                <>
                  Base legal <Help text="Es obligatoria. Es la autorización con la que investigás: una orden, una denuncia o un encargo. Sin ella el caso no se abre, y queda en la auditoría." />
                </>
              }
              hint="Escribí la autorización concreta, por ejemplo: número de orden o expediente, la denuncia, o el encargo que recibiste."
            >
              {(id) => (
                <textarea
                  id={id}
                  className="textarea"
                  rows={4}
                  maxLength={4000}
                  value={legalBasis}
                  onChange={(e) => setLegalBasis(e.target.value)}
                  autoFocus
                />
              )}
            </Field>
            <p className="small muted">Solo se usan fuentes públicas, APIs oficiales o exportaciones que aportás vos.</p>
          </div>
        )}

        {step === 2 && (
          <fieldset className="stack" style={{ border: 0, padding: 0, margin: 0 }}>
            <legend>
              Nivel de reserva (TLP) <Help term="tlp" />
            </legend>
            <div className="tlp-options">
              {TLP_ORDER.map((level) => (
                <label key={level} className="tlp-option">
                  <input type="radio" name="tlp" value={level} checked={tlp === level} onChange={() => setTlp(level)} />
                  <span>
                    <strong>{TLP_LABEL[level]}</strong>
                    <span className="desc">{TLP_HINT[level]}</span>
                  </span>
                </label>
              ))}
            </div>
            <p className="small muted">Podés cambiarlo después en Más opciones. Por defecto queda AMBER: lo más prudente para casos nuevos.</p>
            <div className="notice notice-info">
              <p className="notice-title">Resumen</p>
              <p>
                <strong>{name}</strong> · TLP {TLP_LABEL[tlp]}
              </p>
            </div>
          </fieldset>
        )}

        {stepError && (
          <div style={{ marginTop: 14 }}>
            <Notice tone="warn">
              <p>{stepError}</p>
            </Notice>
          </div>
        )}
        {submitError && (
          <div style={{ marginTop: 14 }}>
            <Notice tone="bad">
              <p>{submitError}</p>
            </Notice>
          </div>
        )}

        <div className="btn-row" style={{ marginTop: 20, justifyContent: "space-between" }}>
          <div className="btn-row">
            {step > 0 ? (
              <Button onClick={() => setStep((s) => s - 1)}>← Atrás</Button>
            ) : (
              <Link to="/" className="btn btn-ghost">
                Cancelar
              </Link>
            )}
          </div>
          {step < STEPS.length - 1 ? (
            <Button variant="primary" onClick={next}>
              Siguiente →
            </Button>
          ) : (
            <Button variant="primary" big disabled={busy} onClick={() => void submit()}>
              {busy ? "Abriendo…" : "Abrir el caso"}
            </Button>
          )}
        </div>
      </Panel>
    </div>
  );
}
