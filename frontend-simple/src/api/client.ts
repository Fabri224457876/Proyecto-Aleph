import type { FieldError } from "./types";

// Error de la API. `detail` viene en español desde el backend: se muestra tal cual.
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly errors: FieldError[];

  constructor(status: number, detail: string, errors: FieldError[] = []) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.errors = errors;
  }
}

export type QueryValue = string | number | boolean | null | undefined | ReadonlyArray<string | number>;

interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  form?: FormData;
  query?: Record<string, QueryValue>;
  /** false para el login: no envía token y un 401 no cierra la sesión. */
  auth?: boolean;
  signal?: AbortSignal;
}

const STORAGE_KEY = "aleph.token";
let currentToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

export function readStoredToken(): string | null {
  try {
    return window.sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null; // sin sessionStorage (modo privado estricto): solo queda la memoria
  }
}

export function setToken(token: string | null): void {
  currentToken = token;
  try {
    if (token) window.sessionStorage.setItem(STORAGE_KEY, token);
    else window.sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    /* sin almacenamiento: el token sigue en memoria */
  }
}

export function getToken(): string | null {
  return currentToken;
}

export function onUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

export function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else {
      params.append(key, String(value));
    }
  }
  const text = params.toString();
  return text ? `?${text}` : "";
}

async function readError(res: Response): Promise<{ detail: string; errors: FieldError[] }> {
  let detail = `Error ${res.status} al consultar la API.`;
  let errors: FieldError[] = [];
  try {
    const data: unknown = await res.json();
    if (data && typeof data === "object") {
      const record = data as Record<string, unknown>;
      if (typeof record.detail === "string") {
        detail = record.detail;
      } else if (Array.isArray(record.detail)) {
        detail = record.detail
          .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
          .join("; ");
      }
      if (Array.isArray(record.errors)) errors = record.errors as FieldError[];
    }
  } catch {
    /* cuerpo que no es JSON: queda el mensaje genérico */
  }
  return { detail, errors };
}

export async function api<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;
  if (options.form) {
    body = options.form; // el navegador pone el Content-Type multipart con su boundary
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const useAuth = options.auth !== false;
  const tokenUsed = useAuth ? currentToken : null;
  if (tokenUsed) headers.Authorization = `Bearer ${tokenUsed}`;

  let res: Response;
  try {
    res = await fetch(`${path}${buildQuery(options.query)}`, {
      method,
      headers,
      body,
      signal: options.signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") throw err;
    throw new ApiError(0, "No hay conexión con la API. Verificá que esté levantada (127.0.0.1:8100).");
  }

  if (res.status === 204) return undefined as T;
  if (!res.ok) {
    const { detail, errors } = await readError(res);
    if (res.status === 401 && tokenUsed && tokenUsed === currentToken) {
      unauthorizedHandler?.();
    }
    throw new ApiError(res.status, detail, errors);
  }
  const contentType = res.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

/** Descarga un archivo protegido por token (el enlace directo no envía el Bearer). */
export async function downloadFile(path: string, fallbackName: string): Promise<void> {
  const headers: Record<string, string> = {};
  if (currentToken) headers.Authorization = `Bearer ${currentToken}`;
  const res = await fetch(path, { headers });
  if (!res.ok) {
    const { detail, errors } = await readError(res);
    if (res.status === 401 && currentToken) unauthorizedHandler?.();
    throw new ApiError(res.status, detail, errors);
  }
  const blob = await res.blob();
  const disposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^";]+)"?/.exec(disposition);
  const name = match?.[1] ?? fallbackName;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}
