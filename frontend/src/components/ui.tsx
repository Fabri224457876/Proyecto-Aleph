import {
  useEffect,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { ITEM_STATUS_LABEL, REVIEW_LABEL, TLP_HINT, TLP_LABEL } from "../lib/labels";
import { classNames, errorMessage } from "../lib/format";
import { Icon, type IconName } from "./Icon";

// ---------------------------------------------------------------- botones

type ButtonVariant = "default" | "primary" | "ok" | "danger" | "ghost";

interface BtnProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  icon?: IconName;
}

export function Btn({ variant = "default", size = "md", icon, className, children, type = "button", ...rest }: BtnProps) {
  return (
    <button
      type={type}
      className={classNames("btn", `btn-${variant}`, size === "sm" && "btn-sm", className)}
      {...rest}
    >
      {icon ? <Icon name={icon} /> : null}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- insignias

export type Tone = "neutral" | "amber" | "ok" | "bad" | "muted" | "info";

export function Badge({
  tone = "neutral",
  children,
  title,
  className,
  dashed = false,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
  className?: string;
  dashed?: boolean;
}) {
  return (
    <span className={classNames("badge", `badge-${tone}`, dashed && "badge-dashed", className)} title={title}>
      {children}
    </span>
  );
}

export function TlpBadge({ tlp }: { tlp: string }) {
  const label = TLP_LABEL[tlp] ?? tlp.toUpperCase();
  return (
    <span className={`tlp tlp-${tlp.replace("+", "-")}`} title={`TLP:${label}. ${TLP_HINT[tlp] ?? ""}`}>
      TLP:{label}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone: Tone = status === "confirmed" ? "ok" : status === "rejected" ? "bad" : "amber";
  return (
    <Badge tone={tone} dashed={status === "proposed"} title={status === "proposed" ? "Pendiente de revisión humana" : undefined}>
      {ITEM_STATUS_LABEL[status] ?? status}
    </Badge>
  );
}

export function ReviewBadge({ status }: { status: string }) {
  const tone: Tone = status === "confirmed" ? "ok" : status === "rejected" ? "bad" : "amber";
  return (
    <Badge tone={tone} dashed={status === "pending"}>
      {REVIEW_LABEL[status] ?? status}
    </Badge>
  );
}

export function Admiralty({ code }: { code: string }) {
  if (!code) return <span className="dim">—</span>;
  return (
    <span className="admiralty" title={`Código Admiralty ${code}: fiabilidad de la fuente y credibilidad del dato`}>
      {code}
    </span>
  );
}

// ---------------------------------------------------------------- superficies

export function Panel({
  title,
  hint,
  actions,
  children,
  className,
  bodyClassName,
  id,
}: {
  title?: ReactNode;
  hint?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section className={classNames("panel", className)} id={id}>
      {title || actions ? (
        <header className="panel-head">
          <h2 className="panel-title">
            {title}
            {hint ? <span className="panel-hint">{hint}</span> : null}
          </h2>
          {actions ? <div className="panel-actions">{actions}</div> : null}
        </header>
      ) : null}
      <div className={classNames("panel-body", bodyClassName)}>{children}</div>
    </section>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: string;
  hint?: ReactNode;
  error?: string | null;
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={classNames("field", className)}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      {children(id)}
      {hint ? <p className="field-hint">{hint}</p> : null}
      {error ? <p className="field-error">{error}</p> : null}
    </div>
  );
}

export function Bar({ value, tone = "amber", label }: { value: number; tone?: Tone; label?: string }) {
  const width = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0)) * 100;
  return (
    <span className={classNames("bar", `bar-${tone}`)} role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value} aria-label={label}>
      <span className="bar-fill" style={{ width: `${width}%` }} />
    </span>
  );
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Array<{ value: T; label: string; count?: number }>;
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={classNames("segmented-item", option.value === value && "on")}
          onClick={() => onChange(option.value)}
        >
          {option.label}
          {option.count !== undefined ? <span className="segmented-count">{option.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- estados

export function Loading({ label = "Cargando…", rows = 3 }: { label?: string; rows?: number }) {
  return (
    <div className="loading" role="status" aria-live="polite">
      <span className="loading-label">{label}</span>
      <div className="skeleton-stack" aria-hidden="true">
        {Array.from({ length: rows }, (_, i) => (
          <span key={i} className="skeleton" style={{ width: `${92 - i * 14}%` }} />
        ))}
      </div>
    </div>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children ? <p className="empty-text">{children}</p> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function ErrorBox({ error, onRetry, title = "No se pudo cargar" }: { error: unknown; onRetry?: () => void; title?: string }) {
  return (
    <div className="error-box" role="alert">
      <p className="error-title">{title}</p>
      <p className="error-text">{errorMessage(error)}</p>
      {onRetry ? (
        <Btn size="sm" icon="refresh" onClick={onRetry}>
          Reintentar
        </Btn>
      ) : null}
    </div>
  );
}

export function InlineError({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p className="inline-error" role="alert">
      {errorMessage(error)}
    </p>
  );
}

// ---------------------------------------------------------------- modal

export function Modal({
  title,
  onClose,
  children,
  footer,
  width = 520,
  danger = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  danger?: boolean;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleId = useId();

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const focusable = dialogRef.current?.querySelector<HTMLElement>("input, select, textarea, button:not(.modal-close)");
    (focusable ?? dialogRef.current)?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={dialogRef}
        className={classNames("modal", danger && "modal-danger")}
        style={{ width: `min(${width}px, calc(100vw - 32px))` }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <header className="modal-head">
          <h2 id={titleId} className="modal-title">
            {title}
          </h2>
          <button type="button" className="icon-btn modal-close" onClick={onClose} aria-label="Cerrar">
            <Icon name="x" />
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer ? <footer className="modal-foot">{footer}</footer> : null}
      </div>
    </div>
  );
}

export function IconButton({
  icon,
  label,
  onClick,
  disabled,
  className,
  title,
}: {
  icon: IconName;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  className?: string;
  title?: string;
}) {
  return (
    <button type="button" className={classNames("icon-btn", className)} onClick={onClick} disabled={disabled} aria-label={label} title={title ?? label}>
      <Icon name={icon} />
    </button>
  );
}
