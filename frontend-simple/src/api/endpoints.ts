import { api } from "./client";
import type {
  AccountOut,
  AuditEventOut,
  AuditVerifyOut,
  CaseDetail,
  CaseOut,
  EntityOut,
  FindingOut,
  GraphOut,
  JobOut,
  LinkOut,
  LinkReviewOut,
  MenardRunSummary,
  MergeOut,
  Page,
  PathOut,
  PostOut,
  RelationOut,
  SectionOut,
  SourceOut,
  TimelineOut,
  TokenOut,
  User,
  ItemStatus,
} from "./types";
import type {
  AttackTechnique,
  CaptureSummary,
  CollectOut,
  ConnectorOut,
  ContradictionsOut,
  Datachunk,
  EnrichOut,
  FindingCreated,
  IocExtractOut,
  LookupOut,
  MemberOut,
  NerOut,
  ReportOut,
  SearchOut,
  StixImportOut,
  TechniqueLinkOut,
  ToolOut,
  ToolRunOut,
  IngestSummary,
} from "./contract";
import { buildQuery } from "./client";

// ---------------------------------------------------------------- sesión

export const login = (username: string, password: string) =>
  api<TokenOut>("/api/auth/login", { method: "POST", body: { username, password }, auth: false });

export const me = () => api<User>("/api/auth/me");

// ---------------------------------------------------------------- casos

export interface CaseListParams {
  status?: "open" | "closed" | "archived";
  tlp?: string;
  q?: string;
  limit?: number;
  offset?: number;
}
export const listCases = (params: CaseListParams = {}) =>
  api<Page<CaseOut>>("/api/cases", { query: { ...params } });

export interface CaseCreateBody {
  name: string;
  description: string;
  legal_basis: string;
  tlp: string;
}
export const createCase = (body: CaseCreateBody) => api<CaseOut>("/api/cases", { method: "POST", body });

export const getCase = (caseId: number) => api<CaseDetail>(`/api/cases/${caseId}`);

export const updateCase = (caseId: number, body: Partial<CaseCreateBody>) =>
  api<CaseOut>(`/api/cases/${caseId}`, { method: "PATCH", body });

export const closeCase = (caseId: number) => api<CaseOut>(`/api/cases/${caseId}/close`, { method: "POST" });
export const archiveCase = (caseId: number) => api<CaseOut>(`/api/cases/${caseId}/archive`, { method: "POST" });
export const reopenCase = (caseId: number) => api<CaseOut>(`/api/cases/${caseId}/reopen`, { method: "POST" });
export const deleteCase = (caseId: number) => api<void>(`/api/cases/${caseId}`, { method: "DELETE" });

// ---------------------------------------------------------------- grafo

export interface GraphParams {
  statuses?: ItemStatus[];
  types?: string[];
  minConfidence?: number;
  includeIsolated?: boolean;
  limit?: number;
}
export const getGraph = (caseId: number, params: GraphParams = {}) =>
  api<GraphOut>(`/api/cases/${caseId}/graph`, {
    query: {
      status: params.statuses,
      type: params.types,
      min_confidence: params.minConfidence,
      include_isolated: params.includeIsolated,
      limit: params.limit ?? 2000,
    },
  });

export const getPath = (caseId: number, source: number, target: number, statuses: ItemStatus[]) =>
  api<PathOut>(`/api/cases/${caseId}/graph/path`, { query: { source, target, status: statuses } });

export const getEntity = (caseId: number, entityId: number) =>
  api<EntityOut>(`/api/cases/${caseId}/entities/${entityId}`);

export interface EntityCreateBody {
  type: string;
  label: string;
  props?: Record<string, unknown>;
  confidence?: number;
  status?: "proposed" | "confirmed";
  source_id?: number | null;
}
export const createEntity = (caseId: number, body: EntityCreateBody) =>
  api<EntityOut>(`/api/cases/${caseId}/entities`, { method: "POST", body });

export const updateEntity = (caseId: number, entityId: number, body: Partial<EntityCreateBody>) =>
  api<EntityOut>(`/api/cases/${caseId}/entities/${entityId}`, { method: "PATCH", body });

export const deleteEntity = (caseId: number, entityId: number) =>
  api<void>(`/api/cases/${caseId}/entities/${entityId}`, { method: "DELETE" });

export const reviewEntity = (caseId: number, entityId: number, decision: "accept" | "reject", note = "") =>
  api<EntityOut>(`/api/cases/${caseId}/entities/${entityId}/review`, { method: "POST", body: { decision, note } });

export const mergeEntities = (caseId: number, keepId: number, duplicateId: number) =>
  api<MergeOut>(`/api/cases/${caseId}/entities/merge`, {
    method: "POST",
    body: { keep_id: keepId, duplicate_id: duplicateId },
  });

export interface RelationCreateBody {
  src_id: number;
  dst_id: number;
  type: string;
  confidence?: number;
  status?: "proposed" | "confirmed";
  source_id?: number | null;
}
export const createRelation = (caseId: number, body: RelationCreateBody) =>
  api<RelationOut>(`/api/cases/${caseId}/relations`, { method: "POST", body });

export const deleteRelation = (caseId: number, relationId: number) =>
  api<void>(`/api/cases/${caseId}/relations/${relationId}`, { method: "DELETE" });

export const reviewRelation = (caseId: number, relationId: number, decision: "accept" | "reject", note = "") =>
  api<RelationOut>(`/api/cases/${caseId}/relations/${relationId}/review`, {
    method: "POST",
    body: { decision, note },
  });

// ---------------------------------------------------------------- expediente (incisos y hallazgos)

export const listSections = (caseId: number) => api<SectionOut[]>(`/api/cases/${caseId}/sections`);

export const createSection = (caseId: number, name: string) =>
  api<SectionOut>(`/api/cases/${caseId}/sections`, { method: "POST", body: { name } });

export const updateSection = (caseId: number, sectionId: number, body: { name?: string; position?: number }) =>
  api<SectionOut>(`/api/cases/${caseId}/sections/${sectionId}`, { method: "PATCH", body });

export const deleteSection = (caseId: number, sectionId: number) =>
  api<void>(`/api/cases/${caseId}/sections/${sectionId}`, { method: "DELETE" });

export interface FindingListParams {
  section_id?: number;
  unclassified?: boolean;
  entity_id?: number;
  kind?: string;
  limit?: number;
  offset?: number;
}
export const listFindings = (caseId: number, params: FindingListParams = {}) =>
  api<Page<FindingOut>>(`/api/cases/${caseId}/findings`, { query: { ...params } });

/** Trae todos los hallazgos del caso (la API limita cada página a 500). */
export async function listAllFindings(caseId: number): Promise<FindingOut[]> {
  const all: FindingOut[] = [];
  let offset = 0;
  for (;;) {
    const page = await listFindings(caseId, { limit: 500, offset });
    all.push(...page.items);
    offset += page.items.length;
    if (page.items.length === 0 || offset >= page.total) break;
  }
  return all;
}

export const updateFinding = (
  caseId: number,
  findingId: number,
  body: { section_id?: number | null; note?: string },
) => api<FindingOut>(`/api/cases/${caseId}/findings/${findingId}`, { method: "PATCH", body });

export const deleteFinding = (caseId: number, findingId: number) =>
  api<void>(`/api/cases/${caseId}/findings/${findingId}`, { method: "DELETE" });

// ---------------------------------------------------------------- fuentes

export interface SourceListParams {
  kind?: string;
  limit?: number;
  offset?: number;
}
export const listSources = (caseId: number, params: SourceListParams = {}) =>
  api<Page<SourceOut>>(`/api/cases/${caseId}/sources`, { query: { ...params } });

export const getSource = (caseId: number, sourceId: number) =>
  api<SourceOut>(`/api/cases/${caseId}/sources/${sourceId}`);

export interface SourceCreateBody {
  kind: string;
  connector?: string;
  reference: string;
  reliability: string;
  credibility: string;
  sha256?: string;
  retrieved_at?: string | null;
}
export const createSource = (caseId: number, body: SourceCreateBody) =>
  api<SourceOut>(`/api/cases/${caseId}/sources`, { method: "POST", body });

export const uploadSource = (caseId: number, form: FormData) =>
  api<SourceOut>(`/api/cases/${caseId}/sources/upload`, { method: "POST", form });

export const rawPath = (caseId: number, sourceId: number) => `/api/cases/${caseId}/sources/${sourceId}/raw`;

// ---------------------------------------------------------------- cuentas y publicaciones

export interface AccountListParams {
  platform?: string;
  q?: string;
  limit?: number;
  offset?: number;
}
export const listAccounts = (caseId: number, params: AccountListParams = {}) =>
  api<Page<AccountOut>>(`/api/cases/${caseId}/accounts`, { query: { ...params } });

export const getAccount = (caseId: number, accountId: number) =>
  api<AccountOut>(`/api/cases/${caseId}/accounts/${accountId}`);

export interface PostListParams {
  q?: string;
  kind?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
/** Queda auditado en la API: pedirlo solo cuando el analista lo abre. */
export const listPosts = (caseId: number, accountId: number, params: PostListParams = {}) =>
  api<Page<PostOut>>(`/api/cases/${caseId}/accounts/${accountId}/posts`, { query: { ...params } });

// ---------------------------------------------------------------- línea de tiempo

export interface TimelineParams {
  kind?: string[];
  account_id?: number;
  since?: string;
  until?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
export const getTimeline = (caseId: number, params: TimelineParams = {}) =>
  api<TimelineOut>(`/api/cases/${caseId}/timeline`, { query: { ...params } });

// ---------------------------------------------------------------- MENARD

export const runMenard = (caseId: number, body: { account_ids?: number[] | null; min_score?: number } = {}) =>
  api<MenardRunSummary>(`/api/cases/${caseId}/menard/run`, { method: "POST", body });

export interface LinkListParams {
  status?: "pending" | "confirmed" | "rejected";
  min_score?: number;
  account_id?: number;
  limit?: number;
  offset?: number;
}
export const listLinks = (caseId: number, params: LinkListParams = {}) =>
  api<Page<LinkOut>>(`/api/cases/${caseId}/menard/links`, { query: { ...params } });

export const getLink = (caseId: number, linkId: number) =>
  api<LinkOut>(`/api/cases/${caseId}/menard/links/${linkId}`);

export const reviewLink = (caseId: number, linkId: number, decision: "confirm" | "reject", note: string) =>
  api<LinkReviewOut>(`/api/cases/${caseId}/menard/links/${linkId}/review`, {
    method: "POST",
    body: { decision, note },
  });

export const listJobs = (params: { case_id?: number; kind?: string; limit?: number }) =>
  api<Page<JobOut>>("/api/jobs", { query: { ...params } });

// ---------------------------------------------------------------- auditoría

export interface AuditListParams {
  case_id?: number;
  user_id?: number;
  action?: string;
  since?: string;
  until?: string;
  order?: "asc" | "desc";
  limit?: number;
  offset?: number;
}
export const listAuditEvents = (params: AuditListParams = {}) =>
  api<Page<AuditEventOut>>("/api/audit/events", { query: { ...params } });

export const verifyAudit = () => api<AuditVerifyOut>("/api/audit/verify");

export const getJob = (jobId: number) => api<JobOut>(`/api/jobs/${jobId}`);

// ---------------------------------------------------------------- sistema y cuenta

export const health = () => api<{ status: string }>("/health", { auth: false });

export const changePassword = (currentPassword: string, newPassword: string) =>
  api<User>("/api/auth/password", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });

export const listUsers = () => api<Page<User>>("/api/users", { query: { limit: 200 } });

export const createUser = (body: { username: string; password: string; role: string }) =>
  api<User>("/api/users", { method: "POST", body });

export const updateUser = (userId: number, body: { role?: string; active?: boolean; password?: string }) =>
  api<User>(`/api/users/${userId}`, { method: "PATCH", body });

// ---------------------------------------------------------------- miembros del caso

export const listMembers = (caseId: number) =>
  api<Page<MemberOut>>(`/api/cases/${caseId}/members`, { query: { limit: 200 } });

export const addMember = (caseId: number, body: { user_id?: number; username?: string }) =>
  api<MemberOut>(`/api/cases/${caseId}/members`, { method: "POST", body });

export const removeMember = (caseId: number, userId: number) =>
  api<void>(`/api/cases/${caseId}/members/${userId}`, { method: "DELETE" });

// ---------------------------------------------------------------- entidades y relaciones (listados y vecindario)

export interface EntityListParams {
  type?: string[];
  status?: ItemStatus[];
  q?: string;
  limit?: number;
  offset?: number;
}
export const listEntities = (caseId: number, params: EntityListParams = {}) =>
  api<Page<EntityOut>>(`/api/cases/${caseId}/entities`, { query: { ...params } });

export const neighbors = (caseId: number, entityId: number, depth = 1, statuses?: ItemStatus[]) =>
  api<GraphOut>(`/api/cases/${caseId}/entities/${entityId}/neighbors`, {
    query: { depth, status: statuses },
  });

export interface RelationListParams {
  status?: ItemStatus[];
  type?: string;
  entity_id?: number;
  limit?: number;
  offset?: number;
}
export const listRelations = (caseId: number, params: RelationListParams = {}) =>
  api<Page<RelationOut>>(`/api/cases/${caseId}/relations`, { query: { ...params } });

export const getRelation = (caseId: number, relationId: number) =>
  api<RelationOut>(`/api/cases/${caseId}/relations/${relationId}`);

export const updateRelation = (caseId: number, relationId: number, body: { type?: string; confidence?: number }) =>
  api<RelationOut>(`/api/cases/${caseId}/relations/${relationId}`, { method: "PATCH", body });

export const searchCase = (caseId: number, q: string, limit = 25) =>
  api<SearchOut>(`/api/cases/${caseId}/search`, { query: { q, limit } });

// ---------------------------------------------------------------- hallazgos e incisos (creación)

export const createFinding = (
  caseId: number,
  body: { section_id?: number | null; chunk: Datachunk; attach_to_entity_id?: number | null; note?: string },
) => api<FindingCreated>(`/api/cases/${caseId}/findings`, { method: "POST", body });

export const getFinding = (caseId: number, findingId: number) =>
  api<FindingOut>(`/api/cases/${caseId}/findings/${findingId}`);

// ---------------------------------------------------------------- recolección y herramientas

export const listConnectors = () => api<ConnectorOut[]>("/api/connectors");

/** Conector de tipo "live" (sin archivo) o de importación (con `file`). `params` viaja como JSON en el formulario. */
export const runConnector = (caseId: number, connector: string, params: Record<string, unknown>, file?: File) => {
  const form = new FormData();
  form.append("connector", connector);
  form.append("params", JSON.stringify(params));
  if (file) form.append("file", file);
  return api<CollectOut>(`/api/cases/${caseId}/collect`, { method: "POST", form });
};

export const listTools = () => api<ToolOut[]>("/api/tools");

export const runTool = (caseId: number, name: string, target: string, file?: File) => {
  const form = new FormData();
  form.append("target", target);
  if (file) form.append("file", file);
  return api<ToolRunOut>(`/api/cases/${caseId}/tools/${encodeURIComponent(name)}/run`, { method: "POST", form });
};

// ---------------------------------------------------------------- CTI y FUNES

export const extractIocs = (caseId: number, text: string) =>
  api<IocExtractOut>(`/api/cases/${caseId}/ioc/extract`, { method: "POST", body: { text } });

/** Vista previa de datachunks: no guarda nada en la base. */
export const previewChunks = (
  caseId: number,
  body: { text: string; page_url?: string; page_title?: string; platform?: string },
) => api<Datachunk[]>(`/api/cases/${caseId}/captures/chunks`, { method: "POST", body });

export const enrichEntity = (caseId: number, entityId: number) =>
  api<EnrichOut>(`/api/cases/${caseId}/entities/${entityId}/enrich`, { method: "POST" });

export const runNer = (caseId: number, body: { text?: string; source_id?: number; use_llm?: boolean }) =>
  api<NerOut>(`/api/cases/${caseId}/funes/ner`, { method: "POST", body });

export const findContradictions = (caseId: number, sourceIds: number[]) =>
  api<ContradictionsOut>(`/api/cases/${caseId}/funes/contradictions`, {
    method: "POST",
    body: { source_ids: sourceIds },
  });

export const buildReport = (caseId: number, body: { title?: string; include_pending?: boolean }) =>
  api<ReportOut>(`/api/cases/${caseId}/funes/report`, { method: "POST", body });

export const searchTechniques = (q: string, limit = 20) =>
  api<AttackTechnique[]>("/api/attack/techniques", { query: { q, limit } });

export const linkTechnique = (caseId: number, body: { technique_id: string; score?: number | null; comment?: string }) =>
  api<TechniqueLinkOut>(`/api/cases/${caseId}/attack/techniques`, { method: "POST", body });

export const attackLayer = (caseId: number, includePending = false) =>
  api<Record<string, unknown>>(`/api/cases/${caseId}/attack/layer`, { query: { include_pending: includePending } });

export const exportPath = (caseId: number, format: "stix" | "misp", includePending: boolean, maxTlp?: string) =>
  `/api/cases/${caseId}/export/${format}${buildQuery({ include_pending: includePending, max_tlp: maxTlp })}`;

export const exportCasePath = (caseId: number, includePosts: boolean) =>
  `/api/cases/${caseId}/export${buildQuery({ include_posts: includePosts })}`;

export const importStix = (caseId: number, bundle: unknown) =>
  api<StixImportOut>(`/api/cases/${caseId}/import/stix`, { method: "POST", body: bundle });

// ---------------------------------------------------------------- importación de JSON normalizado (avanzado)

export const importCollection = (caseId: number, body: unknown) =>
  api<IngestSummary>(`/api/cases/${caseId}/collections`, { method: "POST", body });

export const importCapture = (caseId: number, body: unknown) =>
  api<CaptureSummary>(`/api/cases/${caseId}/captures`, { method: "POST", body });

export const lookupHandles = (caseId: number, platform: string, handles: string[]) =>
  api<LookupOut>(`/api/cases/${caseId}/captures/lookup`, { method: "POST", body: { platform, handles } });
