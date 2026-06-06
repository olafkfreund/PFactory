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

// ── Feasibility (cost · time · access) ──────────────────────────────────

export interface CostLine {
  resource: string;
  quantity: number;
  monthly_usd: number;
  detail: string;
}

export interface CostEstimate {
  monthly_usd: number;
  currency: string;
  lines: CostLine[];
  confidence: 'low' | 'medium' | 'high';
  source: string;
}

export interface EffortEstimate {
  story_points: number;
  duration_days_low: number;
  duration_days_high: number;
  confidence: 'low' | 'medium' | 'high';
  assumptions: string[];
}

export interface AccessRequirement {
  provider: string;
  action: string;
  resource: string;
  region: string;
  granted: boolean | null;
  detail: string;
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
  effort_estimate?: EffortEstimate | null;
  access_requirements?: AccessRequirement[];
}

// ── EpicPlan ────────────────────────────────────────────────────────────

export interface EpicPlan {
  plan_id: string;
  epic_title: string;
  epic_body: string;
  children: EpicChild[];
  summary: string;
  cost_estimate?: CostEstimate | null;
  effort_estimate?: EffortEstimate | null;
  access_requirements?: AccessRequirement[];
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

export interface FindingCitation {
  why: string;
  uri: string;
  title: string;
  source: string;
}

export interface ReviewFinding {
  title: string;
  detail: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  source: string;
  blocking: boolean;
  citations?: FindingCitation[];
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

// ── ReadinessGate ────────────────────────────────────────────────────────

export type ReadinessStatus = 'pass' | 'fail' | 'not_applicable' | 'skipped';

export interface ReadinessCheck {
  check_id: string;
  title: string;
  status: ReadinessStatus;
  severity: string;
  hard: boolean;
  waivable: boolean;
  detail: string;
  remediation: string;
  evidence: string;
}

export interface ReadinessWaiver {
  check_ids: string[];
  reason: string;
  waived_by: string;
  waived_at: string;
  valid: boolean;
}

export interface ReadinessGate {
  plan_id: string;
  plan_hash: string;
  generated_at: string;
  results: ReadinessCheck[];
  waivers: ReadinessWaiver[];
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
  readiness: ReadinessGate | null;
}

// ── EmitResult ──────────────────────────────────────────────────────────

// One planned GitHub issue payload (epic first, then children). Matches the
// backend EmitResult.planned shape (a flat list of payload dicts).
export interface EmitPlannedItem {
  kind: string;            // "epic" | child kind
  title: string;
  body: string;            // includes the pfactory:meta block
  labels: string[];        // the taxonomy labels
  key?: string;            // child key (absent on the epic)
  depends_on?: string[];
}

export interface EmitResult {
  dry_run: boolean;
  epic_number?: number | null;
  child_numbers?: Record<string, number>;
  planned: EmitPlannedItem[];
  errors: string[];
}

// ── PlanSession ─────────────────────────────────────────────────────────

export type PlanSessionStatus =
  | 'ingested'
  | 'processing'
  | 'reviewing'
  | 'processed'
  | 'approved'
  | 'rejected'
  | 'emitted';

// ── Annotation (honoured document, Phase D) ─────────────────────────────

export interface SuggestedEdit {
  anchor: string;
  anchor_line: number;
  original_excerpt: string;
  suggestion: string;
  why: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info';
  source: string;
  citation: FindingCitation | null;
}

export interface PlanAnnotation {
  suggestions: SuggestedEdit[];
  improved_markdown: string;
  change_log: Record<string, unknown>[];
  original_preserved: boolean;
}

export type BoardColumn =
  | 'backlog'
  | 'in_progress'
  | 'ai_review'
  | 'human_review'
  | 'done';

export interface PlanSession {
  session_id: string;
  status: PlanSessionStatus;
  plan: NormalizedPlan;
  epic: EpicPlan | null;
  artifacts: PlanArtifact[];
  review: PlanReview | null;
  annotation?: PlanAnnotation | null;
  emit_result: EmitResult | null;
  created_at: string;
  board_state?: BoardColumn;
  original_filename?: string;
  selected_category?: string;
  selected_template?: string;
  suggested_template?: string;
}

// ── SessionSummary ──────────────────────────────────────────────────────

export interface SessionSummary {
  session_id: string;
  title: string;
  status: PlanSessionStatus;
  board_state?: BoardColumn;
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
