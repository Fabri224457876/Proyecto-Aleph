// Tipos de la API que la consola simple usa y que no están en types.ts (transcriptos de
// docs/openapi.json y de los routers de backend/aleph/api/routers/).
import type { AccountOut, EntityOut, PostOut, RelationOut } from "./types";

export interface MemberOut {
  user_id: number;
  username: string;
  role: string;
  added_by: number | null;
  added_at: string;
}

export interface ConnectorOut {
  name: string;
  title: string;
  mode: "live" | "import" | string;
  params: Record<string, string>;
  requires: string[];
  available: boolean;
}

export interface ToolOut {
  name: string;
  title: string;
  description: string;
  inputs: string[];
  network: boolean;
  requires_key: boolean;
  key_settings: string[];
  is_async: boolean;
}

export interface PersistSummary {
  entities_created?: number;
  entities_existing?: number;
  relations_created?: number;
  relations_existing?: number;
  entity_ids?: number[];
  relation_ids?: number[];
  warnings?: string[];
}

export interface IngestSummary {
  case_id: number;
  source_id: number;
  source_created: boolean;
  sha256: string;
  accounts_created?: number;
  accounts_updated?: number;
  posts_created?: number;
  posts_updated?: number;
  posts_unchanged?: number;
  entities_created?: number;
  entities_existing?: number;
  relations_created?: number;
  relations_existing?: number;
  account_ids?: number[];
  warnings?: string[];
}

export interface CollectOut {
  job_id: number;
  connector: string;
  ignored_params?: string[];
  ingest: IngestSummary;
}

export interface ToolRunOut {
  job_id: number;
  tool: string;
  source_id: number;
  result: Record<string, unknown>;
  persisted: PersistSummary;
  ignored_params?: string[];
}

/** Datachunk: un dato detectado en un texto. No se guarda hasta que se incorpora al expediente. */
export interface Datachunk {
  kind: string;
  value: string;
  quote?: string;
  context?: string;
  page_url: string;
  page_title?: string;
  platform?: string;
  author_handle?: string;
  post_id?: string;
  detected_by?: "rule" | "funes" | "manual" | string;
  captured_at: string;
}

export interface TechniqueMention {
  technique_id: string;
  name: string;
  tactics?: string[];
  url?: string;
  is_subtechnique?: boolean;
  occurrences?: number;
}

export interface Discarded {
  kind: string;
  value: string;
  reason: string;
}

export interface IocExtractOut {
  source_id: number;
  persisted: PersistSummary;
  techniques: TechniqueMention[];
  discarded: Discarded[];
  defanged_input?: boolean;
}

export type LlmStatus = "usado" | "no_responde" | "no_configurado" | "omitido" | string;

export interface NerOut {
  source_id: number;
  llm_status: LlmStatus;
  warnings?: string[];
  persisted: PersistSummary;
  entities: EntityOut[];
  relations: RelationOut[];
  dates?: unknown[];
  discarded?: unknown[];
}

export interface Contradiction {
  id: string;
  kind: string;
  severity: "baja" | "media" | "alta" | string;
  subject: string;
  claim_ids: string[];
  sources: string[];
  summary: string;
  details?: Record<string, unknown>;
  readings?: { kind: string; description: string }[];
  explanation?: string;
  explanation_source?: string;
}

export interface ContradictionsOut {
  llm_status: string;
  claims: unknown[];
  contradictions: Contradiction[];
  warnings?: string[];
}

export interface ReportOut {
  markdown: string;
  generated_by: "llm" | "plantilla" | string;
  invalid_ids?: string[];
  warnings?: string[];
}

export interface AttackTechnique {
  id: string;
  nombre: string;
  tacticas: string[];
  nombres_tacticas: string[];
  subtecnica: boolean;
  padre: string | null;
  url: string;
}

export interface TechniqueLinkOut {
  finding_id: number;
  technique_id: string;
  nombre: string;
  score: number | null;
  comment: string;
  estado: string;
  created: boolean;
}

export interface EnrichResult {
  provider: string;
  status: string;
  verdict: string;
  labels?: string[];
  message?: string;
  retry_after?: number | null;
}

export interface EnrichOut {
  source_id: number;
  indicator: Record<string, string>;
  verdict: string;
  results: EnrichResult[];
  persisted: PersistSummary;
}

export interface HandleInfo {
  known?: boolean;
  entity_id?: number | null;
  posts_captured?: number;
  links?: unknown[];
  relations?: unknown[];
  note?: string;
}

export interface LookupOut {
  handles?: Record<string, HandleInfo>;
}

export interface CaptureSummary {
  case_id: number;
  source_id: number;
  source_created: boolean;
  accounts_new?: number;
  accounts_known?: number;
  posts_new?: number;
  posts_known?: number;
  relations_new?: number;
  relations_known?: number;
  warnings?: string[];
}

export interface StixImportOut {
  source_id: number;
  entidades_creadas: number;
  entidades_existentes: number;
  relaciones_creadas: number;
  relaciones_existentes: number;
  tecnicas_propuestas: number;
  tecnicas_ya_conocidas: number;
  caso_del_informe?: Record<string, unknown> | null;
  omitidos?: string[];
  omitidos_total?: number;
  avisos?: string[];
}

export interface PostHit extends PostOut {
  platform: string;
  handle: string;
}

export interface SearchOut {
  query: string;
  entities: EntityOut[];
  accounts: AccountOut[];
  posts: PostHit[];
  totals: Record<string, number>;
}

export interface FindingCreated {
  id: number;
  case_id: number;
  section_id: number | null;
  entity_id: number | null;
  source_id: number | null;
  kind: string;
  value: string;
  chunk: Record<string, unknown>;
  note: string;
  created_by: number | null;
  created_at: string;
  entity_created?: boolean;
  relation_id?: number | null;
}

// ---------------------------------------------------------------- búsqueda, preguntas y TLÖN

export type HitKind = "case" | "entity" | "account" | "post" | "source" | "finding" | "link";

export interface HitOut {
  ref: string;
  kind: HitKind;
  id: number;
  case_id: number;
  title: string;
  snippet: string;
  score: number;
  lexical: number;
  semantic: number | null;
  status: string;
  extra: Record<string, unknown>;
}

export interface GlobalSearchOut {
  query: string;
  semantic_status: "usado" | "no_configurado" | "no_responde" | "omitido";
  warnings: string[];
  items: HitOut[];
}

export interface AskOut {
  answer: string;
  llm_status: string;
  retrieval: string;
  citations: HitOut[];
  context: HitOut[];
  invalid_citations: string[];
  warnings: string[];
}

export interface LookupAction {
  id: string;
  title: string;
  description: string;
  kind: "connector" | "tool";
  name: string;
  method: string;
  path: string;
  form: Record<string, string>;
  network: boolean;
  available: boolean;
  unavailable_reason: string;
}

export interface LookupPlan {
  query: string;
  kind: string;
  kind_label: string;
  value: string;
  actions: LookupAction[];
  note: string;
}

export interface TlonReview {
  id: string;
  veredicto: "plausible" | "dudosa" | "descartable";
  razon: string;
  verificar: string;
}

export interface TlonHypothesis {
  ref: string;
  src: number;
  dst: number;
  src_label: string;
  dst_label: string;
  type: string;
  score: number;
  percentile: number;
  relation_id: number | null;
  review: TlonReview | null;
  evidence: {
    similar_neighbors: string[];
    common_neighbors: string[];
    shared_attributes: string[];
    explanation: string;
  };
}

export interface TlonRunOut {
  status: "ok" | "datos_insuficientes";
  message: string;
  nodes: number;
  edges: number;
  quality: {
    holdout_edges: number;
    mrr: number | null;
    hits_at_5: number | null;
    baseline_hits_at_5: number | null;
    final_loss: number;
    feature_source: string;
  } | null;
  feature_source: string;
  llm_status: string;
  embeddings_status: string;
  hypotheses: TlonHypothesis[];
  lectura: Array<{ texto: string; ids: string[] }>;
  created_relations: number;
  source_id: number | null;
  warnings: string[];
}
