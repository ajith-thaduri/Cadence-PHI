// Request / response types. Workbench types come from the generated OpenAPI schema (npm run gen:api);
// endpoints that return free-form documents are typed here.
import type { components } from './schema';

type S = components['schemas'];
export type AnalyzeIn = S['AnalyzeIn'];
export type AnalyzeOut = S['AnalyzeOut'];
export type DetectionOut = S['DetectionOut'];
export type EvidenceOut = S['EvidenceOut'];
export type GuardOut = S['GuardOut'];
export type KnownDetails = S['KnownDetails'];
export type WhatIf = S['WhatIf'];
export type RestoreOut = S['RestoreOut'];
export type Modality = AnalyzeIn['modality'];

export type Role = 'viewer' | 'operator' | 'admin';

export interface SessionInfo {
  auth_mode: 'none' | 'token' | 'users';
  user: { name: string; role: Role } | null;
}

export interface Status {
  console_version: string;
  engine: {
    ready: boolean;
    error: string | null;
    environment: string;
    config_sha256: string;
    catalog_version: string;
    device: string | null;
    models: string[];
    weights_verified: Record<string, string | null>;
    self_test_required: boolean;
    consistency_scope: string;
    vault_backend: string;
    outbound_guard: boolean;
    code_commit: string;
    code_fingerprint: string;
  };
  user: { name: string; role: Role };
  auth_mode: string;
  permissions: Record<
    'analyze' | 'batch' | 'run_jobs' | 'run_test_data_jobs' | 'restore' | 'view_audit',
    boolean
  >;
  features: { what_if: boolean; restore: boolean };
  limits: { max_text_chars: number; max_upload_mb: number; max_batch_files: number };
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted';

export interface Job {
  id: string;
  type: string;
  title: string;
  status: JobStatus;
  params: Record<string, unknown>;
  commands: string[];
  created: string;
  started: string | null;
  finished: string | null;
  exit_code: number | null;
  user: string;
  output: string | null;
  run_ref: { kind: string; name: string } | null;
  progress: { done: number; total: number } | null;
  error: string | null;
}

export interface JobType {
  type: string;
  title: string;
  description: string;
  estimate: string;
  allowed: boolean;
  params_schema: unknown;
}

export interface Dataset {
  id: string;
  role: 'dev' | 'test';
  phi_class: string;
  modality: string;
  allowed: boolean;
}

export type RunKind = 'phi' | 'suite' | 'checklist' | 'perf' | 'gates';

export interface RunListItem {
  kind: RunKind;
  label: string;
  name: string;
  modified: string;
  config_sha256: string | null;
  environment: string | null;
  headline: Record<string, unknown>;
}

export interface RunDetail {
  kind: RunKind;
  name: string;
  label: string;
  summary: Record<string, unknown>;
  headline: Record<string, unknown>;
  entity_metrics?: Record<string, string>[];
  report_md?: string;
}

export type Verdict = 'PASS' | 'FAIL' | 'INSUFFICIENT_EVIDENCE';

export interface GateResult {
  verdict: Verdict;
  reasons: string[];
  evidence: unknown;
}

export interface GatesLatest extends RunDetail {
  summary: { created: string; decision: 'GO' | 'NO-GO'; worst: Verdict; gates: Record<string, GateResult> };
  definitions: Record<string, { name: string; metric: string; evidence: string[] }>;
  gates_status: string;
}

export interface EntityInfo {
  id: string;
  description: string;
  category: string;
  risk_tier: string;
  enabled: boolean;
  safe_harbor: string[];
  default_action: string | null;
  outbound_guard: string;
  subtypes: string[];
  high_risk: boolean;
}

export interface ConfigView {
  describe: {
    config_sha256: string;
    environment: string;
    catalog_version: string;
    sources: (string | { path: string; sha256: string })[];
    threshold_overlays: string[];
  };
  entities: EntityInfo[];
  recognizers: {
    id: string;
    kind: string;
    enabled: boolean;
    mode: string;
    version: string;
    developed_on: string[];
    entities: string[];
  }[];
  models: {
    id: string;
    enabled: boolean;
    mode: string;
    candidate_floor: number;
    weights_pinned: boolean;
    model_id: string;
    revision: string;
  }[];
  normalization_profiles: Record<string, string[]>;
  policy_profiles: string[];
  thresholds: Record<string, unknown>;
  redaction: Record<string, unknown>;
  failure: Record<string, unknown>;
  outbound_guard: Record<string, unknown>;
  type_refinements: Record<string, string[]>;
  operators: string[];
}

export interface ValidationResult {
  environment: string;
  ok: boolean;
  config_sha256: string | null;
  message: string;
}

export interface CanaryReport {
  cases: number;
  passed: number;
  failed: string[];
  ok: boolean;
}

export interface MonitoringView {
  baseline: {
    created: string;
    config_sha256: string;
    datasets: string[];
    documents: Record<string, number>;
    chars: Record<string, number>;
    rates_per_1k_chars: Record<string, Record<string, number>>;
  } | null;
  counters: Record<string, number>;
  alerts: {
    modality: string;
    entity: string;
    observed: number;
    expected: number;
    z: number;
    direction: string;
  }[];
  canaries: { id: string; entities: string[]; negative: boolean }[];
  settings: Record<string, unknown>;
}

export interface DocIndexItem {
  slug: string;
  path: string;
  title: string;
}

export type LedgerRow = Record<string, unknown> & { run_id: string; timestamp: string; command: string };
export type AuditRow = Record<string, unknown> & {
  ts: string;
  user: string;
  role: string;
  action: string;
  ok: boolean;
};
