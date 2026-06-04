/**
 * Shared TypeScript types for the PFactory Planning Portal.
 *
 * These mirror the backend PlanSession/NormalizedPlan/EpicPlan/etc
 * contracts from apps/web-server/server/routes/plan_*.py.
 */

// ── Criteria ────────────────────────────────────────────────────────────

export interface PlanCriterion {
  id: string;
  text: string;
}

// ── Enrichment ──────────────────────────────────────────────────────────

/** A public security-group exposure surfaced by an infra probe. */
export interface InfraPolicy {
  kind: string;
  group_id: string;
  group_name: string;
  public: boolean;
  open_cidrs: string[];
  open_ports: string[];
}

/**
 * Live infrastructure snapshot captured by a probe adapter
 * (e.g. "aws", "gcp", "azure").
 */
export interface InfraSnapshot {
  adapter: string;
  target: string;
  available: boolean;
  workloads: unknown[];
  resources: Record<string, unknown>;
  policies: InfraPolicy[];
  load: Record<string, unknown>;
  findings: string[];
  error: string | null;
}

/**
 * A knowledge reference pulled from a connector
 * (e.g. "best-practices", "git-markdown", "backstage").
 */
export interface KnowledgeRef {
  connector: string;
  kind: 'policy' | 'doc' | 'adr' | 'template' | 'catalog' | 'best-practice';
  title: string;
  uri: string;
  snippet: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface PlanEnrichment {
  infra: InfraSnapshot[];
  knowledge: KnowledgeRef[];
}

// ── NormalizedPlan ──────────────────────────────────────────────────────

export interface NormalizedPlan {
  plan_id: string;
  title: string;
  description: string;
  source_format: string;
  source_channel: string | null;
  criteria: PlanCriterion[];
  target_kind: 'software' | 'non-software' | 'undetermined';
  plan_type: string;
  enrichment: PlanEnrichment;
  content_hash: string;
  ingested_at: string;
}

// ── EpicChild ───────────────────────────────────────────────────────────

export interface EpicChild {
  key: string;
  title: string;
  body: string;
  kind: string;
  labels: string[];
  depends_on: string[];
  complexity: string | number | null;
  acceptance_criteria: string[];
}

// ── EpicPlan ────────────────────────────────────────────────────────────

export interface EpicPlan {
  plan_id: string;
  epic_title: string;
  epic_body: string;
  children: EpicChild[];
  summary: string;
}

// ── Artifact ────────────────────────────────────────────────────────────

export interface PlanArtifact {
  kind: 'testing' | 'cicd';
  title: string;
  document: string; // markdown content
  child: EpicChild | null; // the dedicated child issue this artifact tracks
  filename: string;
}

// ── Finding ─────────────────────────────────────────────────────────────

export interface ReviewFinding {
  title: string;
  detail: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  source: string;
  blocking: boolean;
}

// ── Lens ────────────────────────────────────────────────────────────────

export interface ReviewLens {
  lens: string;
  score: number;
  max: number;
  findings: ReviewFinding[];
  blocking: boolean;
}

// ── HumanApproval ───────────────────────────────────────────────────────

export interface HumanApproval {
  approved: boolean;
  approved_by: string | null;
  approved_at: string | null;
  plan_hash: string | null;
  valid: boolean;
  review_count: number;
  feedback: string[];
}

// ── PlanReview ──────────────────────────────────────────────────────────

export interface PlanReview {
  plan_id: string;
  lenses: ReviewLens[];
  threshold: number;
  aggregate_score: number;
  gates_passed: boolean;
  code_gates_applied: boolean;
  human_approval: HumanApproval;
}

// ── EmitResult ──────────────────────────────────────────────────────────

export interface EmitResult {
  dry_run: boolean;
  repo: string | null;
  planned: {
    epic: string | null;
    children: string[];
  };
  errors: string[];
}

// ── PlanSession ─────────────────────────────────────────────────────────

export type PlanSessionStatus =
  | 'ingested'
  | 'processed'
  | 'approved'
  | 'rejected'
  | 'emitted';

export interface PlanSession {
  session_id: string;
  status: PlanSessionStatus;
  plan: NormalizedPlan;
  epic: EpicPlan | null;
  artifacts: PlanArtifact[];
  review: PlanReview | null;
  emit_result: EmitResult | null;
  created_at: string;
}

// ── SessionSummary ──────────────────────────────────────────────────────

export interface SessionSummary {
  session_id: string;
  title: string;
  status: PlanSessionStatus;
  target_kind: 'software' | 'non-software' | 'undetermined';
  plan_type: string;
  children: number;
  gates_passed: boolean | null;
  created_at: string;
}

// ── Meta types ──────────────────────────────────────────────────────────

export interface RegistryEntry {
  id: string;
  kind: string;
  title: string;
  version: string;
  capabilities: string[];
  enabled: boolean;
}

export interface TemplateEntry {
  name: string;
  title: string;
  category?: string;
  description?: string;
  tags: string[];
  policy: Record<string, unknown>;
}

// Intake categories (#1) — each groups its plan-types + matching templates.
export interface CategoryPlanType {
  name: string;
  title: string;
  stages: Record<string, boolean>;
}

export interface CategoryEntry {
  category: string;
  plan_types: CategoryPlanType[];
  templates: { name: string; title: string }[];
}
