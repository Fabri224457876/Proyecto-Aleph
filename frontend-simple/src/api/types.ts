// Tipos de la API de Aleph, transcriptos de docs/openapi.json (y de los esquemas de backend/aleph/api).
// Donde el contrato y el backend real difieren, el comportamiento real manda y se anota aquí.

export type Role = "admin" | "analyst" | "auditor";

export interface User {
  id: number;
  username: string;
  role: Role | string;
  active: boolean;
  created_at: string;
}

export interface TokenOut {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export type Tlp = "clear" | "green" | "amber" | "amber+strict" | "red";
export type CaseStatus = "open" | "closed" | "archived";

export interface CaseOut {
  id: number;
  name: string;
  description: string;
  legal_basis: string;
  tlp: Tlp | string;
  status: CaseStatus | string;
  created_by: number;
  created_at: string;
}

export interface CaseDetail extends CaseOut {
  counts: Record<string, number>;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export type ItemStatus = "proposed" | "confirmed" | "rejected";

// La API acepta 18 tipos; openapi.json lista 16 (faltan bank_account y alias). Ver informe.
export const ENTITY_TYPES = [
  "person", "account", "email", "phone", "domain", "ip", "url", "organization",
  "location", "event", "hash", "wallet", "document", "vehicle", "malware", "vulnerability",
  "bank_account", "alias",
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export interface EntityOut {
  id: number;
  case_id: number;
  type: string;
  label: string;
  props: Record<string, unknown>;
  confidence: number;
  status: ItemStatus | string;
  source_id: number | null;
  created_at: string;
}

export interface RelationOut {
  id: number;
  case_id: number;
  src_id: number;
  dst_id: number;
  type: string;
  props: Record<string, unknown>;
  confidence: number;
  status: ItemStatus | string;
  source_id: number | null;
  created_at: string;
}

export interface GraphNode {
  id: number;
  type: string;
  label: string;
  status: ItemStatus | string;
  confidence: number;
  props: Record<string, unknown>;
  source_id: number | null;
  degree: number;
}

export interface GraphEdge {
  id: number;
  source: number;
  target: number;
  type: string;
  status: ItemStatus | string;
  confidence: number;
  props: Record<string, unknown>;
  source_id: number | null;
}

export interface GraphOut {
  nodes: GraphNode[];
  edges: GraphEdge[];
  counts: Record<string, number>;
  truncated: boolean;
}

export interface PathOut {
  found: boolean;
  length: number | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface MergeOut {
  entity: EntityOut;
  relations_repointed: number;
  relations_removed: number;
  accounts_repointed: number;
}

export type SourceKind = "connector" | "upload" | "manual" | "funes" | "menard" | "enrichment";

export interface SourceOut {
  id: number;
  case_id: number;
  kind: SourceKind | string;
  connector: string;
  reference: string;
  reliability: string;
  credibility: string;
  admiralty: string;
  sha256: string;
  has_raw: boolean;
  retrieved_at: string;
  created_by: number | null;
}

export interface AccountBrief {
  id: number;
  platform: string;
  handle: string;
  display_name: string;
  entity_id: number | null;
}

export interface AccountOut {
  id: number;
  case_id: number;
  entity_id: number | null;
  source_id: number | null;
  platform: string;
  handle: string;
  platform_uid: string;
  display_name: string;
  bio: string;
  url: string;
  created_at_platform: string | null;
  followers: number | null;
  following: number | null;
  avatar_phash: string;
  meta: Record<string, unknown>;
  post_count: number;
}

export type PostKind = "original" | "reply" | "repost" | "quote";

export interface PostOut {
  id: number;
  account_id: number;
  platform_post_id: string;
  text: string;
  created_at: string | null;
  lang: string;
  kind: PostKind | string;
  reply_to: string;
  mentions: unknown[];
  hashtags: unknown[];
  urls: unknown[];
  client: string;
  meta: Record<string, unknown>;
}

export type SignalFamily = "stylometry" | "temporal" | "behavior" | "network" | "profile" | "neural";

export interface Evidence {
  description: string;
  a: string;
  b: string;
}

export interface SignalResult {
  name: string;
  family: SignalFamily | string;
  score: number;
  weight: number;
  available: boolean;
  explanation: string;
  evidence: Evidence[];
}

export interface PairResult {
  a: string;
  b: string;
  score: number;
  confidence: "baja" | "media" | "alta" | string;
  signals: SignalResult[];
  summary: string;
}

export type ReviewStatus = "pending" | "confirmed" | "rejected";

export interface LinkOut {
  id: number;
  case_id: number;
  account_a: AccountBrief;
  account_b: AccountBrief;
  score: number;
  confidence: string;
  summary: string;
  signals: PairResult;
  review_status: ReviewStatus | string;
  reviewed_by: number | null;
  review_note: string;
  created_at: string;
}

export interface LinkReviewOut {
  link: LinkOut;
  relation: RelationOut | null;
}

export interface ClusterSummary {
  members: string[];
  account_ids: number[];
  cohesion: number;
  summary: string;
}

export interface LinkBrief {
  link_id: number;
  a: string;
  b: string;
  score: number;
  confidence: string;
  review_status: string;
}

export interface MenardRunSummary {
  case_id: number;
  job_id: number | null;
  accounts_analyzed: number;
  pairs_returned: number;
  links_created: number;
  links_updated: number;
  links_below_min_score: number;
  top: LinkBrief[];
  clusters: ClusterSummary[];
  skipped: Record<string, string>;
  params: Record<string, unknown>;
  warnings: string[];
  disclaimer: string;
}

export interface JobOut {
  id: number;
  case_id: number | null;
  kind: string;
  status: "queued" | "running" | "done" | "failed" | string;
  params: Record<string, unknown>;
  result: Record<string, unknown>;
  error: string;
  created_by: number | null;
  created_at: string;
  finished_at: string | null;
}

export interface SectionOut {
  id: number;
  name: string;
  position: number;
  findings: number;
}

export interface FindingOut {
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
}

export interface AuditEventOut {
  id: number;
  ts: string;
  user_id: number | null;
  case_id: number | null;
  action: string;
  target: string;
  detail: Record<string, unknown>;
  prev_hash: string;
  hash: string;
}

export interface AuditVerifyOut {
  ok: boolean;
  broken_event_id: number | null;
  total_events: number;
  detail: string;
}

export interface TimelineItem {
  kind: "post" | "event";
  at: string;
  title: string;
  text: string;
  post_id: number | null;
  account_id: number | null;
  entity_id: number | null;
  platform: string;
  handle: string;
  post_kind: string;
  status: string;
  source_id: number | null;
}

export interface TimelineOut extends Page<TimelineItem> {
  undated_events: number;
}

export interface FieldError {
  field: string;
  message: string;
}
