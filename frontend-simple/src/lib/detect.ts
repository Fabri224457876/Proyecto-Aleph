// Reconoce qué es lo que la persona escribió o pegó. Todo es local: no se envía nada a la API para esto.

export type InputKind =
  | "empty"
  | "url"
  | "domain"
  | "email"
  | "ip"
  | "hash"
  | "btc"
  | "eth"
  | "cve"
  | "phone"
  | "handle"
  | "text";

export interface Detection {
  kind: InputKind;
  value: string;
  /** Nombre en lenguaje llano para mostrar como "Detectado: ...". */
  label: string;
  /** Una línea que explica por qué se lo tomó así. */
  why: string;
}

const LABELS: Record<InputKind, string> = {
  empty: "Nada todavía",
  url: "Una URL",
  domain: "Un dominio",
  email: "Un correo electrónico",
  ip: "Una dirección IP",
  hash: "Un hash (huella de un archivo)",
  btc: "Una billetera de Bitcoin",
  eth: "Una billetera de Ethereum",
  cve: "Una vulnerabilidad (CVE)",
  phone: "Un número de teléfono",
  handle: "Un nombre de usuario",
  text: "Un texto",
};

const WHY: Record<InputKind, string> = {
  empty: "Escribí o pegá algo para empezar.",
  url: "Empieza con http o https.",
  domain: "Tiene la forma de un nombre de sitio, por ejemplo ejemplo.com.",
  email: "Tiene una arroba seguida de un dominio.",
  ip: "Son cuatro números separados por puntos.",
  hash: "Es una cadena hexadecimal de 32, 40 o 64 caracteres.",
  btc: "Tiene el formato de una dirección de Bitcoin.",
  eth: "Empieza con 0x y tiene 40 caracteres hexadecimales.",
  cve: "Tiene la forma CVE-año-número.",
  phone: "Son solo números, con signos + y separadores.",
  handle: "Es una sola palabra: puede ser un usuario de una red social.",
  text: "Es una frase o un texto largo: lo voy a revisar en busca de datos.",
};

const RE_URL = /^https?:\/\/\S+$/i;
const RE_EMAIL = /^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i;
const RE_IP = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;
const RE_HASH = /^([a-f0-9]{32}|[a-f0-9]{40}|[a-f0-9]{64})$/i;
const RE_BTC = /^(bc1[a-z0-9]{25,87}|[13][a-km-zA-HJ-NP-Z1-9]{25,34})$/;
const RE_ETH = /^0x[a-fA-F0-9]{40}$/;
const RE_CVE = /^CVE-\d{4}-\d{4,}$/i;
const RE_DOMAIN = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/i;
const RE_PHONE = /^\+?[\d\s()-]+$/;
const RE_HANDLE = /^@?[A-Za-z0-9_.-]{2,40}$/;

function isIp(value: string): boolean {
  const match = RE_IP.exec(value);
  if (!match) return false;
  return match.slice(1).every((part) => Number(part) <= 255);
}

export function detect(raw: string): Detection {
  const value = raw.trim();
  const make = (kind: InputKind): Detection => ({ kind, value, label: LABELS[kind], why: WHY[kind] });
  if (!value) return make("empty");
  if (value.includes("\n") || value.length > 120 || /\s/.test(value)) {
    // Una línea con espacios puede ser una URL o un correo mal pegado; lo tratamos como texto salvo que lo sea sin duda.
    if (!/\s/.test(value) && RE_URL.test(value)) return make("url");
    return make("text");
  }
  if (RE_URL.test(value)) return make("url");
  if (RE_EMAIL.test(value)) return make("email");
  if (isIp(value)) return make("ip");
  if (RE_CVE.test(value)) return make("cve");
  if (RE_HASH.test(value)) return make("hash");
  if (RE_ETH.test(value)) return make("eth");
  if (RE_BTC.test(value)) return make("btc");
  if (RE_PHONE.test(value) && value.replace(/\D/g, "").length >= 7) return make("phone");
  if (RE_DOMAIN.test(value)) return make("domain");
  if (RE_HANDLE.test(value)) return make("handle");
  return make("text");
}

/** Texto con varios datos o una frase: se busca dentro. */
export function isLongText(value: string): boolean {
  return detect(value).kind === "text";
}
