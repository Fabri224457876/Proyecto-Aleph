import { ApiError } from "../api/client";

const dateTimeFormat = new Intl.DateTimeFormat("es-AR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const dateFormat = new Intl.DateTimeFormat("es-AR", { day: "2-digit", month: "short", year: "numeric" });

const dayFormat = new Intl.DateTimeFormat("es-AR", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});

const timeFormat = new Intl.DateTimeFormat("es-AR", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Fecha y hora en la zona del navegador: "08/10/2026 09:29". */
export function fmtDateTime(value: string | null | undefined): string {
  const date = parse(value);
  return date ? dateTimeFormat.format(date) : "sin fecha";
}

export function fmtDate(value: string | null | undefined): string {
  const date = parse(value);
  return date ? dateFormat.format(date) : "sin fecha";
}

export function fmtDay(value: string | null | undefined): string {
  const date = parse(value);
  return date ? dayFormat.format(date) : "sin fecha";
}

export function fmtTime(value: string | null | undefined): string {
  const date = parse(value);
  return date ? timeFormat.format(date) : "";
}

/** Fecha ISO completa para tooltips (UTC). */
export function isoTitle(value: string | null | undefined): string | undefined {
  const date = parse(value);
  return date ? date.toISOString() : undefined;
}

export function pct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function score2(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(2);
}

export function shortHash(hash: string | null | undefined, length = 12): string {
  if (!hash) return "—";
  return hash.length > length ? `${hash.slice(0, length)}…` : hash;
}

export function truncate(text: string | null | undefined, max: number): string {
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat("es-AR").format(value);
}

/** Mensaje legible para mostrar en la interfaz. Los de la API ya vienen en español. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Ocurrió un error inesperado.";
}

export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404;
}

/** Convierte un valor de props en texto corto y legible para tablas. */
export function propText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function classNames(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
