// Lo que cada pestaña del caso recibe de la página de caso.
export interface CaseTabProps {
  caseId: number;
  /** El caso está abierto (no cerrado ni archivado). */
  open: boolean;
  /** Rol con permiso de escritura y caso abierto: si es false, las acciones de escritura quedan deshabilitadas. */
  writable: boolean;
  /** Vuelve a leer el caso para actualizar los contadores. */
  refreshCase: () => void;
}
