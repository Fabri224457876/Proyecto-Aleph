import { useSyncExternalStore } from "react";

// Caso activo que muestra la barra superior. Lo publica la página de caso.
export interface CaseChip {
  id: number;
  name: string;
  tlp: string;
  status: string;
}

let current: CaseChip | null = null;
const listeners = new Set<() => void>();

export function setCaseChip(chip: CaseChip | null): void {
  current = chip;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useCaseChip(): CaseChip | null {
  return useSyncExternalStore(subscribe, () => current, () => null);
}
