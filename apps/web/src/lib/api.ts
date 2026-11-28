import { supabase } from "./supabase";

const API_URL = process.env.NEXT_PUBLIC_API_URL!;
const WORKSPACE_KEY = "seshat.workspace";

export type LifecycleStatus =
  | "created" | "in_development" | "testing" | "rolling_out"
  | "fully_released" | "ready_for_cleanup" | "cleanup_in_progress" | "retired";

export type Flag = {
  id: string; name: string; description: string | null; owner: string | null;
  flagsmith_id: string | null; integration_id: string | null; flagsmith_project_id: number | null;
  project_name: string | null; environment: string | null; enabled: boolean;
  lifecycle_status: LifecycleStatus; lifecycle_changed_at: string; created_at: string; updated_at: string;
};
export type Repository = {
  id: string; name: string; full_name: string; default_branch: string; installation_id: string | null;
  last_scanned_at: string | null; last_scan_sha: string | null; flagsmith_project_id: number | null;
  created_at: string;
};
export type Integration = {
  id: string; provider: "flagsmith" | "github"; name: string; status: string; status_detail: string | null;
  config: Record<string, unknown>; has_secret: boolean; last_synced_at: string | null; created_at: string;
};
export type AuditEntry = {
  id: string; action: string; resource_type: string; resource_id: string | null; actor: string | null;
  details: Record<string, unknown>; created_at: string;
};
export type Reference = {
  id: string; repository_id: string; repository: string; path: string; line: number; snippet: string;
  commit_sha: string | null; url: string | null;
};
export type Workspace = {
  id: string; name: string; github_account_login: string; github_account_type: string; role: string;
};
export type WorkspaceList = { workspaces: Workspace[]; install_url: string | null; warnings: string[] };
export type ScanResult = {
  repository: string; commit_sha: string; files_scanned: number; references: number;
  flags: { flag: string; references: number }[];
};
export type GitHubSyncResult = {
  installations: number; repositories_added: number; repositories_updated: number; repositories: string[];
  projects_created: string[]; projects_linked: string[]; project_errors: string[];
};
export type FlagsmithSyncResult = {
  projects: string[]; created: number; linked: number; updated: number; unchanged: number;
  conflicts: string[]; missing_in_flagsmith: string[]; environment_states?: number; warnings?: string[];
};
export type FlagsmithProject = { id: number; name: string };
export type RepositoryProjectLinkResult = {
  repository: Repository; flagsmith_project_name: string;
  sync: FlagsmithSyncResult | null; sync_error: string | null;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export function getWorkspaceId(): string | null {
  return typeof window === "undefined" ? null : localStorage.getItem(WORKSPACE_KEY);
}
export function setWorkspaceId(id: string | null) {
  if (id) localStorage.setItem(WORKSPACE_KEY, id);
  else localStorage.removeItem(WORKSPACE_KEY);
}

function describeError(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown })?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return `Request failed (${status})`;
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  const headers = new Headers(init.headers);
  headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const ws = getWorkspaceId();
  if (ws && !path.startsWith("/api/workspaces")) headers.set("X-Workspace-Id", ws);

  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (res.status === 204) return null as T;
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, describeError(body, res.status));
  return body as T;
}

export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });

// --- Lifecycle analysis: flag verdict ----------------------------------------------------------
export type Activity = "active" | "inactive" | "unknown";
export type Usefulness = "useful" | "not_useful" | "uncertain";
export type VerdictReason = { text: string; points_to: string; evidence_ids: string[] };
export type LLMUsage = {
  status: "not_configured" | "used" | "unavailable" | "rejected_invalid_output";
  provider: string | null; model: string | null; validation_errors: string[]; error: string | null;
};
export type FlagVerdict = {
  flag_name: string; activity: Activity; usefulness: Usefulness; confidence: number;
  activity_reasons: VerdictReason[]; usefulness_reasons: VerdictReason[];
  recommendation: string; explanation: string; caveats: string[]; llm: LLMUsage; evaluated_at: string;
};
export type FlagAnalysis = {
  id: string; flag_id: string; stale_status: string; suggested_state: string; recommended_action: string;
  activity: Activity | null; usefulness: Usefulness | null; verdict_confidence: number | null;
  confirmed_references: number; dynamic_references: number; created_at: string; created_by: string | null;
  repositories: { full_name: string; commit_sha: string }[];
  result: { verdict?: FlagVerdict; data_notes?: string[] } & Record<string, unknown>;
};
export type FlagVerdictSummary = {
  flag_id: string; analysis_id: string; activity: Activity | null; usefulness: Usefulness | null;
  confidence: number | null; stale_status: string; analyzed_at: string;
};

// --- A/B experiments --------------------------------------------------------------------------
export type ExperimentStatus = "draft" | "running" | "stopped" | "concluded";
export type ExperimentVariant = {
  key: string; name: string | null; is_control: boolean; expected_share: number | null; flagsmith_option_id: number | null;
};
export type VariantResult = {
  key: string; is_control: boolean; exposures: number; conversions: number;
  conversion_rate: number | null; rate_ci_low: number | null; rate_ci_high: number | null;
  absolute_lift: number | null; relative_lift: number | null; lift_ci_low: number | null; lift_ci_high: number | null;
  p_value: number | null; p_value_adjusted: number | null; significant: boolean;
  probability_to_beat_control: number | null; required_exposures: number | null;
};
export type ExperimentResults = {
  decision: "keep_running" | "trending" | "winner" | "control_better" | "no_significant_difference" | "invalid";
  winner: string | null; summary: string; variants: VariantResult[];
  sample_ratio: { checked: boolean; mismatch: boolean; p_value: number | null; detail: string };
  total_exposures: number; total_conversions: number; progress: number; warnings: string[];
};
export type Experiment = {
  id: string; flag_id: string; flag_name: string | null; name: string; hypothesis: string | null;
  primary_metric: string; environment_name: string | null; variants: ExperimentVariant[];
  minimum_detectable_effect: number; alpha: number; status: ExperimentStatus;
  started_at: string | null; stopped_at: string | null; concluded_at: string | null;
  winner: string | null; conclusion_note: string | null; created_by: string | null; created_at: string;
  results: ExperimentResults | null; results_error: string | null;
};
export type ExperimentWithKey = Experiment & { ingest_key: string; ingest_example: string };
