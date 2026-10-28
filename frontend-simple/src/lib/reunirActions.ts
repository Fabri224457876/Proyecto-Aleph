// Catálogo de acciones de la pantalla "Reunir": según lo que la persona puso, qué puede hacerse con eso.
// Cada acción dice qué hace, de dónde saca el dato y qué queda en el caso, antes de ejecutarse.
// Todas las llamadas usan endpoints reales de la API (ver docs/openapi.json).
import * as ep from "../api/endpoints";
import type { ConnectorOut, Datachunk, ToolOut, PersistSummary, IngestSummary } from "../api/contract";
import type { Detection } from "./detect";

export interface ActionContext {
  caseId: number;
  detection: Detection;
  file: File | null;
  /** Texto del archivo, si es un texto legible y no muy grande. */
  fileText: string | null;
  reliability: string;
  credibility: string;
  connectors: ConnectorOut[];
  tools: ToolOut[];
}

export interface Outcome {
  title: string;
  lines: string[];
  warnings: string[];
  /** Vista previa de datos encontrados: se suman al expediente solo si la persona los elige. */
  chunks?: Datachunk[];
  /** Cuántas propuestas nuevas quedaron para revisar. */
  proposals: number;
  /** Detalle técnico, para quien lo busca. */
  details: unknown;
}

export interface ActionDef {
  id: string;
  title: string;
  what: string;
  source: string;
  effect: string;
  network: boolean;
  disabledReason?: string;
  run: () => Promise<Outcome>;
  /** Acción que pide elegir la fiabilidad (solo para subir archivos). */
  wantsAdmiralty?: boolean;
}

const ENTITY_TYPE_FOR_KIND: Record<string, string> = {
  url: "url",
  domain: "domain",
  email: "email",
  ip: "ip",
  hash: "hash",
  btc: "wallet",
  eth: "wallet",
  cve: "vulnerability",
  phone: "phone",
  handle: "account",
};

const TOOL_COPY: Record<string, { title: string; what: string; source: string }> = {
  wayback: {
    title: "Ver el historial de la página en Internet Archive",
    what: "Lista las capturas guardadas de la URL o del dominio, y cuándo cambió el contenido.",
    source: "Internet Archive (API pública CDX).",
  },
  typosquat: {
    title: "Buscar dominios parecidos",
    what: "Arma variantes del dominio (letras cambiadas, otros sufijos) y revisa cuáles existen.",
    source: "Cálculo local y consulta DNS pública.",
  },
  favicon: {
    title: "Calcular la huella del ícono del sitio",
    what: "Descarga el ícono del sitio y calcula su huella, para buscar otros sitios que usen el mismo.",
    source: "El propio sitio: se descarga su ícono.",
  },
  asn: {
    title: "Ver a qué red y empresa pertenece la IP",
    what: "Indica el sistema autónomo (ASN), el prefijo y la organización que la administra.",
    source: "RIPEstat (API pública).",
  },
  wallet: {
    title: "Validar la billetera y ver su actividad",
    what: "Comprueba que la dirección sea válida y, si es de Bitcoin, consulta saldo y movimientos recientes.",
    source: "Blockstream para Bitcoin; cálculo local para Ethereum.",
  },
  vulns: {
    title: "Ver la vulnerabilidad en NVD y en CISA",
    what: "Trae la descripción, la severidad (CVSS) y si figura en la lista de explotación activa de CISA.",
    source: "NVD (NIST) y el catálogo KEV de CISA.",
  },
  email_intel: {
    title: "Analizar el correo sin contactar a nadie",
    what: "Revisa la sintaxis, los servidores de correo del dominio, SPF y DMARC.",
    source: "Registros DNS públicos del dominio. No envía correos ni verifica la cuenta.",
  },
  phone_intel: {
    title: "Analizar el número de teléfono",
    what: "Lo normaliza y dice el país y, para Argentina, el tipo de línea y el área.",
    source: "Cálculo local. No consulta servicios de terceros.",
  },
  dorks: {
    title: "Armar consultas de búsqueda",
    what: "Genera consultas listas para Google, Bing y DuckDuckGo. No ejecuta ninguna búsqueda.",
    source: "Cálculo local.",
  },
  image_meta: {
    title: "Leer los metadatos de la imagen",
    what: "Muestra la cámara, el software, la fecha y la ubicación GPS si la imagen los trae.",
    source: "El archivo que subiste.",
  },
  doc_meta: {
    title: "Leer los metadatos del documento",
    what: "Muestra el autor, el último editor, el software y las fechas del documento.",
    source: "El archivo que subiste.",
  },
  evidence: {
    title: "Guardar el archivo como evidencia, con su huella",
    what: "Guarda el contenido con su SHA-256 y un registro de custodia encadenado.",
    source: "El archivo que subiste.",
  },
};

const TOOLS_FOR_KIND: Record<string, string[]> = {
  url: ["wayback", "favicon"],
  domain: ["wayback", "typosquat", "favicon", "dorks"],
  email: ["email_intel", "dorks"],
  ip: ["asn"],
  btc: ["wallet"],
  eth: ["wallet"],
  cve: ["vulns"],
  phone: ["phone_intel"],
  handle: ["dorks"],
};

const CONNECTOR_PARAM_FOR_KIND: Record<string, string> = {
  handle: "handle",
  domain: "domain",
};

const IMAGE_EXT = ["png", "jpg", "jpeg", "webp", "tif", "tiff", "heic"];
const DOC_EXT = ["pdf", "docx", "xlsx", "pptx"];
const TEXT_EXT = ["txt", "md", "log", "csv", "json", "jsonl", "ndjson", "html", "htm"];

export function extOf(name: string): string {
  const match = /\.([a-z0-9]+)$/i.exec(name);
  return match ? match[1].toLowerCase() : "";
}

export function isTextFile(file: File): boolean {
  return TEXT_EXT.includes(extOf(file.name)) && file.size <= 300_000;
}

/** Resumen plano de un resultado de herramienta: claves con valores sencillos, y cuántos elementos tiene cada lista. */
function plainLines(result: Record<string, unknown>, limit = 8): string[] {
  const lines: string[] = [];
  for (const [key, value] of Object.entries(result)) {
    if (lines.length >= limit) break;
    if (value === null || value === undefined || value === "") continue;
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
      lines.push(`${key.replace(/_/g, " ")}: ${String(value)}`);
    } else if (Array.isArray(value)) {
      lines.push(`${key.replace(/_/g, " ")}: ${value.length} ${value.length === 1 ? "elemento" : "elementos"}`);
    } else if (typeof value === "object") {
      lines.push(`${key.replace(/_/g, " ")}: ver detalle técnico`);
    }
  }
  return lines;
}

function persistedProposals(p: PersistSummary | undefined): number {
  if (!p) return 0;
  return (p.entities_created ?? 0) + (p.relations_created ?? 0);
}

function ingestLines(summary: IngestSummary): string[] {
  const lines: string[] = [];
  const accounts = (summary.accounts_created ?? 0) + (summary.accounts_updated ?? 0);
  if (accounts) lines.push(`Cuentas: ${accounts} (${summary.accounts_created ?? 0} nuevas).`);
  const posts = (summary.posts_created ?? 0) + (summary.posts_updated ?? 0);
  if (posts) lines.push(`Publicaciones guardadas: ${posts}.`);
  const entities = summary.entities_created ?? 0;
  if (entities) lines.push(`Entidades propuestas: ${entities}.`);
  const relations = summary.relations_created ?? 0;
  if (relations) lines.push(`Relaciones propuestas: ${relations}.`);
  if (!lines.length) lines.push("La fuente se guardó, pero no trajo cuentas ni datos nuevos.");
  return lines;
}

const LLM_TEXT: Record<string, string> = {
  usado: "Se usó el modelo de IA local.",
  no_responde: "El modelo de IA no respondió: se usaron solo reglas.",
  no_configurado: "No hay modelo de IA configurado: se usaron solo reglas.",
  omitido: "Se usaron solo reglas.",
};

export function sectionFor(kind: string): string {
  if (["domain", "ip", "url", "hash", "wallet", "malware", "vulnerability"].includes(kind)) return "Infraestructura";
  if (["email", "phone"].includes(kind)) return "Contactos";
  if (["account", "alias"].includes(kind)) return "Cuentas";
  if (["person", "organization"].includes(kind)) return "Identidad";
  if (["location"].includes(kind)) return "Ubicaciones";
  return "Actividad";
}

export function buildActions(ctx: ActionContext): ActionDef[] {
  const { caseId, detection, file } = ctx;
  const value = detection.value;
  const actions: ActionDef[] = [];

  // ---- archivo
  if (file) {
    const ext = extOf(file.name);
    actions.push({
      id: "upload",
      title: "Guardar el archivo como fuente del caso",
      what: "Guarda el archivo original, con su huella SHA-256, para que siempre se pueda verificar que no cambió.",
      source: `El archivo "${file.name}" que subiste.`,
      effect: "Queda como fuente del caso. No crea entidades por sí solo.",
      network: false,
      wantsAdmiralty: true,
      run: async () => {
        const form = new FormData();
        form.append("file", file);
        form.append("reliability", ctx.reliability);
        form.append("credibility", ctx.credibility);
        form.append("reference", file.name);
        const source = await ep.uploadSource(caseId, form);
        return {
          title: "Archivo guardado como fuente",
          lines: [
            `Fuente ${source.id} creada. Huella SHA-256: ${source.sha256.slice(0, 16)}…`,
            "Podés verla en Más opciones, en Fuentes.",
          ],
          warnings: [],
          proposals: 0,
          details: source,
        };
      },
    });
    if (ext === "json") {
      actions.push({
        id: "stix",
        title: "Importar como inteligencia STIX 2.1",
        what: "Si el archivo es un paquete STIX (un bundle), convierte sus objetos en entidades y relaciones del caso.",
        source: `El archivo "${file.name}". No consulta internet.`,
        effect: "Las entidades y relaciones entran como propuestas, para revisar en Revisar.",
        network: false,
        run: async () => {
          const bundle: unknown = JSON.parse(ctx.fileText ?? "null");
          const out = await ep.importStix(caseId, bundle);
          return {
            title: "Paquete STIX importado",
            lines: [
              `Entidades nuevas: ${out.entidades_creadas}. Ya existían: ${out.entidades_existentes}.`,
              `Relaciones nuevas: ${out.relaciones_creadas}.`,
              `Técnicas ATT&CK propuestas: ${out.tecnicas_propuestas}.`,
            ],
            warnings: out.avisos ?? [],
            proposals: out.entidades_creadas + out.relaciones_creadas,
            details: out,
          };
        },
      });
    }
    if (["csv", "jsonl", "ndjson", "txt"].includes(ext)) {
      actions.push({
        id: "csv",
        title: "Importar como lista de datos (CSV o JSONL)",
        what: "Lee cada fila como una publicación o un registro y arma las cuentas y propuestas que se puedan deducir.",
        source: `El archivo "${file.name}". No consulta internet.`,
        effect: "Crea la fuente, las cuentas y las propuestas. Las propuestas quedan para revisar.",
        network: false,
        run: async () => {
          const out = await ep.runConnector(caseId, "generic_csv", {}, file);
          return {
            title: "Lista importada",
            lines: ingestLines(out.ingest),
            warnings: out.ingest.warnings ?? [],
            proposals: (out.ingest.entities_created ?? 0) + (out.ingest.relations_created ?? 0),
            details: out,
          };
        },
      });
    }
    const tools = ctx.tools.filter((t) => {
      if (t.name === "evidence") return true;
      if (t.name === "image_meta") return IMAGE_EXT.includes(ext);
      if (t.name === "doc_meta") return DOC_EXT.includes(ext);
      return false;
    });
    for (const tool of tools) {
      const copy = TOOL_COPY[tool.name];
      actions.push({
        id: `tool:${tool.name}`,
        title: copy?.title ?? tool.title,
        what: copy?.what ?? tool.description,
        source: copy?.source ?? "El archivo que subiste.",
        effect: "El resultado queda como propuesta de la herramienta, para revisar.",
        network: tool.network,
        run: () => runToolAction(caseId, tool.name, "", file, copy?.title ?? tool.title),
      });
    }
    if (ctx.fileText) actions.push(...textActions(ctx, ctx.fileText, "el archivo"));
    return actions;
  }

  // ---- texto largo
  if (detection.kind === "text") {
    return textActions(ctx, value, "el texto");
  }

  // ---- un solo dato
  if (detection.kind === "empty") return actions;

  const entityType = ENTITY_TYPE_FOR_KIND[detection.kind];
  if (entityType) {
    actions.push({
      id: "indicator",
      title: `Guardar como ${entityLabel(entityType)} del caso (propuesta)`,
      what: "Lo agrega al grafo como una entidad pendiente, para que la confirmes después.",
      source: "Lo que escribiste. No consulta internet.",
      effect: "Queda como propuesta en Revisar. Nada se confirma solo.",
      network: false,
      run: async () => {
        const entity = await ep.createEntity(caseId, {
          type: entityType,
          label: value,
          status: "proposed",
          props: { origen: "Reunir: escrito a mano" },
        });
        return {
          title: "Dato guardado como propuesta",
          lines: [`${entity.label} quedó como ${entityLabel(entity.type)} pendiente de revisión.`],
          warnings: [],
          proposals: 1,
          details: entity,
        };
      },
    });
  }

  if (detection.kind === "url" || detection.kind === "domain" || detection.kind === "email" || detection.kind === "ip") {
    actions.push(...textActions(ctx, value, "el dato"));
  }

  // Herramientas OSINT que aplican a este tipo de dato
  const toolNames = TOOLS_FOR_KIND[detection.kind] ?? [];
  for (const name of toolNames) {
    const tool = ctx.tools.find((t) => t.name === name);
    if (!tool) continue;
    const copy = TOOL_COPY[name];
    actions.push({
      id: `tool:${name}`,
      title: copy?.title ?? tool.title,
      what: copy?.what ?? tool.description,
      source: copy?.source ?? "Herramienta OSINT del catálogo.",
      effect: "El resultado queda como propuesta de la herramienta, para revisar.",
      network: tool.network,
      run: () => runToolAction(caseId, name, value, null, copy?.title ?? tool.title),
    });
  }

  // Conectores: buscan una cuenta o un dominio en una fuente pública
  const paramName = CONNECTOR_PARAM_FOR_KIND[detection.kind];
  if (paramName) {
    for (const connector of ctx.connectors) {
      if (connector.mode !== "live" || !(paramName in connector.params)) continue;
      actions.push({
        id: `connector:${connector.name}`,
        title: `Recolectar desde ${connector.title}`,
        what:
          detection.kind === "handle"
            ? `Busca la cuenta "${value}" en ${connector.title} y guarda sus publicaciones visibles.`
            : `Consulta ${value} en ${connector.title} y guarda lo que devuelve.`,
        source: `Fuente pública de ${connector.title}. Usa solo la API oficial o pública.`,
        effect: "Crea cuentas, publicaciones y propuestas en el caso. Quedan para revisar.",
        network: true,
        disabledReason: connector.available
          ? undefined
          : `Falta configurar una clave en el servidor: ${connector.requires.join(", ") || "clave del conector"}.`,
        run: async () => {
          const out = await ep.runConnector(caseId, connector.name, { [paramName]: value });
          return {
            title: `Recolección desde ${connector.title}`,
            lines: ingestLines(out.ingest),
            warnings: out.ingest.warnings ?? [],
            proposals: (out.ingest.entities_created ?? 0) + (out.ingest.relations_created ?? 0),
            details: out,
          };
        },
      });
    }
  }
  if (detection.kind === "email") {
    const mastodon = ctx.connectors.find((c) => c.name === "mastodon");
    if (mastodon) {
      actions.push({
        id: "connector:mastodon-email",
        title: "Buscar como cuenta de Mastodon",
        what: "Solo corresponde si el dato es usuario@instancia de Mastodon. Consulta esa cuenta pública.",
        source: "API pública de la instancia de Mastodon indicada.",
        effect: "Crea cuentas y publicaciones que quedan para revisar.",
        network: true,
        disabledReason: mastodon.available ? undefined : "El conector de Mastodon no está disponible.",
        run: async () => {
          const out = await ep.runConnector(caseId, "mastodon", { handle: value });
          return {
            title: "Recolección desde Mastodon",
            lines: ingestLines(out.ingest),
            warnings: out.ingest.warnings ?? [],
            proposals: (out.ingest.entities_created ?? 0) + (out.ingest.relations_created ?? 0),
            details: out,
          };
        },
      });
    }
  }

  return actions;
}

function entityLabel(type: string): string {
  const map: Record<string, string> = {
    url: "URL",
    domain: "dominio",
    email: "correo",
    ip: "dirección IP",
    hash: "hash",
    wallet: "billetera",
    vulnerability: "vulnerabilidad",
    phone: "teléfono",
    account: "cuenta",
  };
  return map[type] ?? type;
}

/** Acciones que sirven para cualquier texto: buscar datos (vista previa), extraer indicadores, pedir a la IA local. */
function textActions(ctx: ActionContext, text: string, what: string): ActionDef[] {
  const { caseId } = ctx;
  return [
    {
      id: "chunks",
      title: "Buscar datos dentro del texto",
      what: `Busca dominios, correos, IP, hashes, URL y fechas en ${what}, y te los muestra para que elijas cuáles sumar al expediente.`,
      source: `${what[0].toUpperCase()}${what.slice(1)} que escribiste o subiste. No consulta internet.`,
      effect: "No guarda nada: solo sumás al expediente lo que elijas.",
      network: false,
      run: async () => {
        // La API exige un origen para cada hallazgo: si el dato no trae URL, se usa un rótulo legible.
        const origin = ctx.file ? `Archivo ${ctx.file.name}` : ctx.detection.kind === "url" ? ctx.detection.value : "Texto pegado en Reunir";
        const found = await ep.previewChunks(caseId, { text, platform: "generic" });
        const chunks = found.map((c) => ({ ...c, page_url: c.page_url || origin, page_title: c.page_title || origin }));
        return {
          title: "Datos encontrados en el texto",
          lines: [
            chunks.length
              ? `Encontré ${chunks.length} ${chunks.length === 1 ? "dato" : "datos"}. Elegí cuáles sumar al expediente.`
              : "No encontré datos técnicos en el texto.",
          ],
          warnings: [],
          chunks,
          proposals: 0,
          details: chunks,
        };
      },
    },
    {
      id: "ioc",
      title: "Extraer indicadores y técnicas ATT&CK",
      what: `Extrae los indicadores técnicos de ${what}, marca las técnicas ATT&CK que se mencionan y explica qué descartó y por qué.`,
      source: `${what[0].toUpperCase()}${what.slice(1)} que escribiste o subiste. No consulta internet.`,
      effect: "Los indicadores quedan como propuestas en Revisar. Las técnicas son sugerencias.",
      network: false,
      run: async () => {
        const out = await ep.extractIocs(caseId, text);
        const p = out.persisted;
        const lines = [
          `Entidades nuevas propuestas: ${p.entities_created ?? 0}. Ya estaban en el caso: ${p.entities_existing ?? 0}.`,
          `Relaciones nuevas propuestas: ${p.relations_created ?? 0}.`,
          `Técnicas ATT&CK mencionadas: ${out.techniques.length}.`,
        ];
        if (out.discarded.length) lines.push(`Descartados: ${out.discarded.length} (ver detalle técnico).`);
        return {
          title: "Indicadores extraídos",
          lines,
          warnings: p.warnings ?? [],
          proposals: persistedProposals(p),
          details: out,
        };
      },
    },
    {
      id: "ner",
      title: "Extraer personas, organizaciones y relaciones con la IA local",
      what: `FUNES lee ${what} y propone entidades y relaciones. Si el modelo no responde, usa solo reglas.`,
      source: `${what[0].toUpperCase()}${what.slice(1)}. El procesamiento corre en la red propia, no en la nube.`,
      effect: "Todo lo que propone queda en Revisar, sin confirmar.",
      network: false,
      run: async () => {
        const out = await ep.runNer(caseId, { text, use_llm: true });
        const p = out.persisted;
        return {
          title: "Entidades propuestas por FUNES",
          lines: [
            LLM_TEXT[out.llm_status] ?? `Estado del modelo: ${out.llm_status}.`,
            `Entidades nuevas propuestas: ${p.entities_created ?? 0}. Relaciones nuevas: ${p.relations_created ?? 0}.`,
          ],
          warnings: out.warnings ?? [],
          proposals: persistedProposals(p),
          details: out,
        };
      },
    },
  ];
}

async function runToolAction(
  caseId: number,
  name: string,
  target: string,
  file: File | null,
  title: string,
): Promise<Outcome> {
  const out = await ep.runTool(caseId, name, target, file ?? undefined);
  const p = out.persisted;
  return {
    title,
    lines: [...plainLines(out.result), `Propuestas nuevas: ${persistedProposals(p)}.`],
    warnings: p.warnings ?? [],
    proposals: persistedProposals(p),
    details: out,
  };
}

