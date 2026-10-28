import { ENTITY_TYPES, type EntityType } from "../api/types";

export const CASE_STATUS_LABEL: Record<string, string> = {
  open: "Abierto",
  closed: "Cerrado",
  archived: "Archivado",
};

export const ROLE_LABEL: Record<string, string> = {
  admin: "Administrador",
  analyst: "Analista",
  auditor: "Auditor",
};

export const ITEM_STATUS_LABEL: Record<string, string> = {
  proposed: "Propuesta",
  confirmed: "Confirmada",
  rejected: "Rechazada",
};

export const REVIEW_LABEL: Record<string, string> = {
  pending: "Pendiente",
  confirmed: "Confirmada",
  rejected: "Rechazada",
};

// TLP 2.0: colores oficiales del estándar del FIRST.
export const TLP_LABEL: Record<string, string> = {
  clear: "CLEAR",
  green: "GREEN",
  amber: "AMBER",
  "amber+strict": "AMBER+STRICT",
  red: "RED",
};

export const TLP_HINT: Record<string, string> = {
  clear: "Divulgación sin restricción.",
  green: "Compartible dentro de la comunidad, sin difusión pública.",
  amber: "Compartible dentro de la organización, solo con quien necesita conocerlo.",
  "amber+strict": "Solo dentro de la organización. No se reenvía.",
  red: "Solo para la persona que lo recibe. No se comparte.",
};

export const TLP_ORDER = ["clear", "green", "amber", "amber+strict", "red"] as const;

export const RELIABILITY_LABEL: Record<string, string> = {
  A: "Completamente fiable",
  B: "Normalmente fiable",
  C: "Bastante fiable",
  D: "Normalmente no fiable",
  E: "No fiable",
  F: "No se puede juzgar",
};

export const CREDIBILITY_LABEL: Record<string, string> = {
  "1": "Confirmado por otras fuentes",
  "2": "Probablemente cierto",
  "3": "Posiblemente cierto",
  "4": "Dudoso",
  "5": "Improbable",
  "6": "No se puede juzgar",
};

export const POST_KIND_LABEL: Record<string, string> = {
  original: "original",
  reply: "respuesta",
  repost: "repost",
  quote: "cita",
};

export const SOURCE_KIND_LABEL: Record<string, string> = {
  connector: "Conector",
  upload: "Archivo",
  manual: "Manual",
  funes: "FUNES",
  menard: "MENARD",
  enrichment: "Enriquecimiento",
};

export const ENTITY_TYPE_LABEL: Record<EntityType, string> = {
  person: "Persona",
  account: "Cuenta",
  email: "Correo",
  phone: "Teléfono",
  domain: "Dominio",
  ip: "Dirección IP",
  url: "URL",
  organization: "Organización",
  location: "Lugar",
  event: "Evento",
  hash: "Hash",
  wallet: "Billetera",
  document: "Documento",
  vehicle: "Vehículo",
  malware: "Malware",
  vulnerability: "Vulnerabilidad",
  bank_account: "Cuenta bancaria",
  alias: "Alias",
};

export function entityTypeLabel(type: string): string {
  return (ENTITY_TYPES as readonly string[]).includes(type) ? ENTITY_TYPE_LABEL[type as EntityType] : type;
}

// Familias de señales de MENARD (SignalResult.family), en el orden en que se muestran.
export const SIGNAL_FAMILY_ORDER = ["stylometry", "temporal", "behavior", "network", "profile", "neural"] as const;

export const SIGNAL_FAMILY_LABEL: Record<string, string> = {
  stylometry: "Estilometría",
  temporal: "Temporal",
  behavior: "Conducta",
  network: "Red",
  profile: "Perfil",
  neural: "Neuronal (GPU)",
};

const SIGNAL_NAME_LABEL: Record<string, string> = {
  "stylometry.char_ngrams": "N-gramas de caracteres",
  "stylometry.function_words": "Palabras función",
  "stylometry.punctuation": "Puntuación",
  "stylometry.capitalization": "Mayúsculas",
  "stylometry.elongation": "Alargamientos y risas",
  "stylometry.emoji": "Emojis",
  "stylometry.orthography": "Ortografía y abreviaturas",
  "stylometry.rioplatense": "Marcas rioplatenses",
  "stylometry.length": "Longitud de mensajes",
  "temporal.hourly": "Horario de publicación",
  "temporal.weekday": "Día de la semana",
  "temporal.sleep_window": "Ventana de sueño",
  "temporal.sync_bursts": "Ráfagas sincronizadas",
  "temporal.alternation": "Alternancia",
  "behavior.client": "Cliente o dispositivo",
  "behavior.hashtags": "Hashtags",
  "behavior.domains": "Dominios compartidos",
  "behavior.targets": "Destinatarios de respuestas",
  "behavior.post_mix": "Tipos de publicación",
  "network.following": "Seguidos en común",
  "network.followers": "Seguidores en común",
  "network.mutual_mentions": "Menciones mutuas",
  "profile.handle": "Nombre de usuario",
  "profile.display_name": "Nombre visible",
  "profile.bio": "Biografía",
  "profile.created_at": "Fecha de creación",
  "profile.avatar": "Avatar (hash perceptual)",
};

export function signalLabel(name: string): string {
  if (SIGNAL_NAME_LABEL[name]) return SIGNAL_NAME_LABEL[name];
  const tail = name.split(".").pop() ?? name;
  return tail.replace(/_/g, " ");
}

// Tipos de relación conocidos. Los demás se muestran tal como vienen, sin guiones bajos.
const RELATION_LABEL: Record<string, string> = {
  same_operator: "mismo operador",
  related_to: "relacionado con",
  contacto: "contacto",
  aloja: "aloja",
  usa_alias: "usa alias",
  dominio_de_contacto: "dominio de contacto",
  pertenece_a: "pertenece a",
  distribuido_desde: "distribuido desde",
  muestra_de: "muestra de",
  miembro: "miembro de",
  ubicado_en: "ubicado en",
  pago_a: "pagó a",
};

export function relationLabel(type: string): string {
  return RELATION_LABEL[type] ?? type.replace(/_/g, " ");
}

export const COMMON_RELATION_TYPES = [
  "related_to",
  "contacto",
  "aloja",
  "usa_alias",
  "pertenece_a",
  "miembro",
  "ubicado_en",
  "distribuido_desde",
  "muestra_de",
  "pago_a",
] as const;
