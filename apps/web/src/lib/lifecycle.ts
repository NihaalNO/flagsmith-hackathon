import type { LifecycleStatus } from "./api";

export const STAGES: { id: LifecycleStatus; label: string; band: string; hint: string }[] = [
  { id: "created", label: "Created", band: "var(--stage-dev)", hint: "Exists, no work started" },
  { id: "in_development", label: "In development", band: "var(--stage-dev)", hint: "Code being written behind the flag" },
  { id: "testing", label: "Testing", band: "var(--stage-dev)", hint: "Verified in test environments" },
  { id: "rolling_out", label: "Rolling out", band: "var(--stage-rollout)", hint: "Partially enabled in production" },
  { id: "fully_released", label: "Fully released", band: "var(--stage-released)", hint: "On for everyone" },
  { id: "ready_for_cleanup", label: "Ready for cleanup", band: "var(--stage-cleanup)", hint: "Flag code can be removed" },
  { id: "cleanup_in_progress", label: "Cleanup in progress", band: "var(--stage-cleanup)", hint: "Removal under way" },
  { id: "retired", label: "Retired", band: "var(--stage-retired)", hint: "Gone from code and Flagsmith" },
];

export const stage = (id: LifecycleStatus) => STAGES.find((s) => s.id === id) ?? STAGES[0];

export function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d < 30 ? `${d} d ago` : new Date(iso).toLocaleDateString();
}
