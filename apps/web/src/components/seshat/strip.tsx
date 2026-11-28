"use client";

import type { Flag, FlagVerdictSummary } from "@/lib/api";
import { stage } from "@/lib/lifecycle";
import { activityLabel, usefulnessLabel, VerdictTags } from "./verdict";

/**
 * A flag on the board. One step off its column, a hairline, no shadow at rest; it lifts 2px on
 * hover. The column header carries the stage colour, so the card doesn't repeat it. The one
 * accent is the "On" dot.
 */
export function Strip({ flag, onOpen, verdict }: { flag: Flag; onOpen: () => void; verdict?: FlagVerdictSummary }) {
  const s = stage(flag.lifecycle_status);
  const v = verdict?.activity && verdict.usefulness
    ? `, ${activityLabel(verdict.activity)}, ${usefulnessLabel(verdict.usefulness)}` : "";
  const meta = [flag.owner ?? "No owner", flag.project_name ?? "Local only"].join(" · ");
  return (
    <button
      onClick={onOpen}
      className="block w-full rounded-md border border-border bg-[var(--strip)] px-3 py-2.5 text-left
                 transition-[translate,background-color,border-color] duration-150 ease-out
                 hover:-translate-y-0.5 hover:border-[var(--strip-line-hover)] hover:bg-[var(--strip-hover)]
                 active:translate-y-0 motion-reduce:hover:translate-y-0"
      aria-label={`${flag.name}, ${s.label}${flag.enabled ? ", on" : ", off"}${v}`}
    >
      <span className="flex items-baseline justify-between gap-3">
        <span className="min-w-0 truncate font-mono text-[13px] font-medium">{flag.name}</span>
        <span className="flex shrink-0 items-center gap-1.5 text-[12px] text-muted-foreground"
          title={flag.environment ?? undefined}>
          <span aria-hidden className={`size-1.5 rounded-full ${flag.enabled
            ? "bg-primary" : "border border-muted-foreground/70"}`} />
          {flag.enabled ? "On" : "Off"}
        </span>
      </span>
      {flag.description && (
        <span className="mt-1 line-clamp-2 text-[13px] leading-[1.45] text-muted-foreground">
          {flag.description}
        </span>
      )}
      {verdict && (
        <VerdictTags activity={verdict.activity} usefulness={verdict.usefulness}
          className="mt-2 text-[12px] text-muted-foreground" />
      )}
      <span className="mt-2 block truncate text-[12px] text-muted-foreground" title="Owner · Flagsmith project">
        {meta}
      </span>
    </button>
  );
}
