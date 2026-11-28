"use client";

import type { Activity, Usefulness } from "@/lib/api";

const ACTIVITY: Record<Activity, { label: string; dot: string }> = {
  active: { label: "Active", dot: "#3f9a4a" },
  inactive: { label: "Inactive", dot: "#9aa39a" },
  unknown: { label: "Activity unknown", dot: "#c9a23f" },
};
const USEFULNESS: Record<Usefulness, { label: string; dot: string }> = {
  useful: { label: "Useful", dot: "#3f9a4a" },
  not_useful: { label: "Not useful", dot: "#c4574a" },
  uncertain: { label: "Usefulness unclear", dot: "#c9a23f" },
};

/** Compact verdict: a dot and a word for activity and for usefulness. */
export function VerdictTags({ activity, usefulness, className = "" }: {
  activity: Activity | null; usefulness: Usefulness | null; className?: string;
}) {
  if (!activity || !usefulness) return null;
  const a = ACTIVITY[activity], u = USEFULNESS[usefulness];
  return (
    <span className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${className}`}>
      {[a, u].map((x) => (
        <span key={x.label} className="flex items-center gap-1.5">
          <span className="size-2 rounded-full" style={{ background: x.dot }} aria-hidden />
          {x.label}
        </span>
      ))}
    </span>
  );
}

export const activityLabel = (a: Activity) => ACTIVITY[a].label;
export const usefulnessLabel = (u: Usefulness) => USEFULNESS[u].label;
