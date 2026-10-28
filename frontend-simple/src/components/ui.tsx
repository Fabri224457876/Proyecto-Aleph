import { useId, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { errorMessage } from "../lib/format";
import { TLP_HINT, TLP_LABEL, CASE_STATUS_LABEL } from "../lib/labels";
import { GLOSSARY } from "../lib/glossary";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";

export function Button({
  variant = "secondary",
  big = false,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant; big?: boolean }) {
  return (
    <button
      type="button"
      {...rest}
      className={`btn btn-${variant}${big ? " btn-big" : ""} ${className}`.trim()}
    />
  );
}

export function Panel({
  title,
  children,
  actions,
  id,
  className = "",
}: {
  title?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  id?: string;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`.trim()} id={id}>
      {(title || actions) && (
        <header className="panel-head">
          {title && <h2 className="panel-title">{title}</h2>}
          {actions && <div className="panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export type Tone = "neutral" | "accent" | "ok" | "warn" | "bad";

export function Chip({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`chip chip-${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "ok" | "warn" | "bad";
  title?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className={`notice notice-${tone}`} role={tone === "bad" ? "alert" : "status"}>
      {title && <p className="notice-title">{title}</p>}
      {children && <div className="notice-body">{children}</div>}
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
      {action && <div className="empty-action">{action}</div>}
    </div>
  );
}

export function Loading({ what = "Cargando" }: { what?: string }) {
  return (
    <p className="loading" role="status" aria-live="polite">
      {what}…
    </p>
  );
}

export function ErrorBox({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <Notice tone="bad" title="No se pudo completar">
      <p>{errorMessage(error)}</p>
      {onRetry && (
        <p>
          <Button onClick={onRetry}>Reintentar</Button>
        </p>
      )}
    </Notice>
  );
}

export function Field({
  label,
  hint,
  children,
  error,
}: {
  label: ReactNode;
  hint?: ReactNode;
  error?: string;
  children: (id: string) => ReactNode;
}) {
  const id = useId();
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {hint && <p className="field-hint" id={`${id}-hint`}>{hint}</p>}
      {children(id)}
      {error && <p className="field-error">{error}</p>}
    </div>
  );
}

/** Explicación desplegable de un término, con botón accesible. */
export function Help({ term, text }: { term?: string; text?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const body = text ?? (term ? GLOSSARY[term] : undefined);
  if (!body) return null;
  return (
    <span className="help">
      <button
        type="button"
        className="help-btn"
        aria-expanded={open}
        aria-controls={id}
        aria-label="Qué significa"
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {open && (
        <span className="help-body" id={id} role="note">
          {body}
        </span>
      )}
    </span>
  );
}

/** Botón que pide un segundo clic para acciones que borran o quitan algo. */
export function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
  disabled,
  variant = "danger",
}: {
  label: string;
  confirmLabel: string;
  onConfirm: () => void;
  disabled?: boolean;
  variant?: ButtonVariant;
}) {
  const [armed, setArmed] = useState(false);
  if (!armed) {
    return (
      <Button variant={variant} disabled={disabled} onClick={() => setArmed(true)}>
        {label}
      </Button>
    );
  }
  return (
    <span className="confirm-pair">
      <Button
        variant="danger"
        onClick={() => {
          setArmed(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </Button>
      <Button variant="ghost" onClick={() => setArmed(false)}>
        Cancelar
      </Button>
    </span>
  );
}

export function TlpChip({ tlp }: { tlp: string }) {
  return (
    <Chip tone="accent" title={TLP_HINT[tlp] ?? ""}>
      TLP: {TLP_LABEL[tlp] ?? tlp}
    </Chip>
  );
}

export function StatusChip({ status }: { status: string }) {
  const tone: Tone = status === "open" ? "ok" : status === "archived" ? "neutral" : "warn";
  return <Chip tone={tone}>{CASE_STATUS_LABEL[status] ?? status}</Chip>;
}

export function Stat({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="stat" title={hint}>
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}

export function PageTitle({ children, lead }: { children: ReactNode; lead?: ReactNode }) {
  return (
    <div className="page-title">
      <h1>{children}</h1>
      {lead && <p className="lead">{lead}</p>}
    </div>
  );
}
