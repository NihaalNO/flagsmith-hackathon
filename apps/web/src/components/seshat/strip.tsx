"use client";

import type { Flag, FlagVerdictSummary } from "@/lib/api";
import { stage } from "@/lib/lifecycle";
import { activityLabel, usefulnessLabel, VerdictTags } from "./verdict";

/** A flag as a flight progress strip: stage band, key, and boxed cells. */
export function Strip({ flag, onOpen, verdict }: { flag: Flag; onOpen: () => void; verdict?: FlagVerdictSummary }) {
  const s = stage(flag.lifecycle_status);
  const v = verdict?.activity && verdict.usefulness
    ? `, ${activityLabel(verdict.activity)}, ${usefulnessLabel(verdict.usefulness)}` : "";
  return (
    <button
      onClick={onOpen}
      className="group grid w-full grid-cols-[10px_1fr] overflow-hidden rounded-[3px] bg-[var(--paper)] text-left
                 text-[var(--ink)] shadow-[0_1px_0_rgba(0,0,0,.35)] transition-transform
                 hover:-translate-y-px focus-visible:-translate-y-px"
      aria-label={`${flag.name}, ${s.label}${flag.enabled ? ", on" : ", off"}${v}`}
    >
      <span style={{ background: s.band }} aria-hidden />
      <span className="min-w-0">
        <span className="block px-3 pt-2 pb-1.5">
          <span className="block truncate font-mono text-[13px] font-semibold">{flag.name}</span>
          {flag.description && (
            <span className="mt-0.5 line-clamp-2 block text-[13px] leading-snug text-[var(--ink-soft)]">
              {flag.description}
            </span>
          )}
          {verdict && (
            <VerdictTags activity={verdict.activity} usefulness={verdict.usefulness}
              className="mt-1 text-[12px] text-[var(--ink-soft)]" />
          )}
        </span>
        <span className="grid grid-cols-[1fr_1fr_auto] border-t border-[var(--paper-line)] text-[12px]">
          <span className="truncate border-r border-[var(--paper-line)] px-3 py-1" title="Owner">
            {flag.owner ?? <span className="text-[var(--ink-soft)]">No owner</span>}
          </span>
          <span className="truncate border-r border-[var(--paper-line)] px-3 py-1" title="Flagsmith project">
            {flag.project_name ?? <span className="text-[var(--ink-soft)]">Local only</span>}
          </span>
          <span className="flex items-center gap-1.5 px-3 py-1" title={flag.environment ?? undefined}>
            <span
              className="size-2 rounded-full"
              style={{ background: flag.enabled ? "#3f9a4a" : "#b9c1b6",
                       boxShadow: flag.enabled ? "0 0 0 2px rgba(63,154,74,.18)" : "none" }}
              aria-hidden
            />
            {flag.enabled ? "On" : "Off"}
          </span>
        </span>
      </span>
    </button>
  );
}
