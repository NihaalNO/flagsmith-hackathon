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
  conflicts: string[]; missing_in_flagsmith: string[];
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
