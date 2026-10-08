import { useState, type ReactNode } from "react";
import { Btn, InlineError, Modal } from "./ui";
import { errorMessage } from "../lib/format";

/** Confirmación para acciones irreversibles o que cambian el estado del caso. */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  danger = false,
  onConfirm,
  onClose,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => Promise<void> | void;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(new Error(errorMessage(err)));
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={busy ? () => undefined : onClose}
      danger={danger}
      footer={
        <>
          <Btn onClick={onClose} disabled={busy}>
            Cancelar
          </Btn>
          <Btn variant={danger ? "danger" : "primary"} onClick={run} disabled={busy}>
            {busy ? "Procesando…" : confirmLabel}
          </Btn>
        </>
      }
    >
      <div className="stack-sm">{children}</div>
      <InlineError error={error} />
    </Modal>
  );
}
