"use client";

import { LayoutGroup, motion, useReducedMotion } from "framer-motion";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import type { Flag, LifecycleStatus } from "@/lib/api";
import { stage } from "@/lib/lifecycle";
import { Wordmark } from "./gates";
import { GithubMark } from "./github-mark";
import { Strip } from "./strip";

const ex = (name: string, description: string, owner: string, lifecycle_status: LifecycleStatus,
  enabled: boolean, project = "acme/web"): Flag => ({
  id: name, name, description, owner, lifecycle_status, enabled, project_name: project, environment: "Production",
  flagsmith_id: null, integration_id: null, flagsmith_project_id: null,
  lifecycle_changed_at: "", created_at: "", updated_at: "",
});

const COLUMNS: LifecycleStatus[] = ["rolling_out", "fully_released", "ready_for_cleanup", "cleanup_in_progress"];

const START: Flag[] = [
  ex("new_checkout", "One-page checkout for signed-in users", "payments", "rolling_out", true),
  ex("smart_search", "Typo-tolerant product search", "search", "rolling_out", true, "acme/api"),
  ex("dark_mode", "Dark theme across the dashboard", "design", "fully_released", true),
  ex("bulk_export", "CSV export for order history", "ops", "fully_released", true, "acme/api"),
  ex("legacy_search", "Old results page, replaced in March", "search", "ready_for_cleanup", false),
  ex("old_onboarding", "Three-step signup, removal PR open", "growth", "cleanup_in_progress", false),
];

/** The one orchestrated moment: dark_mode moves from released to ready for cleanup. */
function MiniBoard() {
  const reduce = useReducedMotion();
  const [flags, setFlags] = useState(START);
  useEffect(() => {
    if (reduce) return;
    const t = setTimeout(() => setFlags((fs) => fs.map((f) =>
      f.name === "dark_mode" ? { ...f, lifecycle_status: "ready_for_cleanup" } : f)), 1800);
    return () => clearTimeout(t);
  }, [reduce]);

  return (
    <LayoutGroup>
      <div className="flex gap-3">
        {COLUMNS.map((c) => {
          const s = stage(c);
          const items = flags.filter((f) => f.lifecycle_status === c);
          return (
            <div key={c} className="w-64 shrink-0 rounded-md bg-[var(--board-raised)]/90 p-2 shadow-[0_20px_50px_-20px_rgba(0,0,0,.6)]">
              <div className="mb-2 border-t-4 px-1.5 pt-2" style={{ borderColor: s.band }}>
                <div className="flex items-baseline justify-between">
                  <span className="font-semibold">{s.label}</span>
                  <motion.span key={items.length} initial={reduce ? false : { y: -6, opacity: 0 }}
                    animate={{ y: 0, opacity: 1 }} className="tabular-nums text-muted-foreground">
                    {items.length}
                  </motion.span>
                </div>
              </div>
              <div className="min-h-[13.5rem] space-y-2">
                {items.map((f) => (
                  <motion.div key={f.id} layoutId={f.id} transition={{ type: "spring", stiffness: 140, damping: 20 }}>
                    <Strip flag={f} onOpen={() => {}} />
                  </motion.div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </LayoutGroup>
  );
}

export function Landing({ onSignIn }: { onSignIn: () => void }) {
  return (
    <div className="relative min-h-dvh overflow-hidden">
      {/* strip-board rails + vignette */}
      <div aria-hidden className="pointer-events-none absolute inset-0"
        style={{
          backgroundImage:
            "radial-gradient(120% 80% at 70% 40%, transparent 0%, rgba(14,19,25,.55) 100%)," +
            "repeating-linear-gradient(to bottom, transparent 0 71px, rgba(255,255,255,.035) 71px 72px)",
        }} />

      <header className="relative flex items-center justify-between px-6 py-5 md:px-12">
        <Wordmark className="text-2xl" />
        <span className="hidden text-sm text-muted-foreground sm:block">For teams on Flagsmith and GitHub</span>
      </header>

      <main className="relative grid items-center gap-14 px-6 pt-10 pb-16 md:px-12 lg:min-h-[calc(100dvh-80px)]
                       lg:grid-cols-[minmax(0,34rem)_1fr] lg:pt-0">
        <div>
          <h1 className="text-5xl leading-[1.02] font-semibold tracking-[-0.02em] text-balance sm:text-6xl lg:text-7xl">
            Know when a feature flag is done.
          </h1>
          <p className="mt-6 max-w-[46ch] text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Seshat follows every flag from first commit to final cleanup. It syncs with Flagsmith, finds each
            flag in your code, and shows what&apos;s ready to remove.
          </p>
          <Button size="lg" onClick={onSignIn}
            className="mt-9 h-12 gap-2.5 px-6 text-base shadow-[0_10px_30px_-10px_rgba(237,241,234,.45)]">
            <GithubMark className="size-5" /> Sign in with GitHub
          </Button>
          <p className="mt-4 text-sm text-muted-foreground">
            Seshat reads your code to find flags. It doesn&apos;t push changes.
          </p>
        </div>

        <div aria-hidden className="pointer-events-none -mr-6 overflow-hidden md:-mr-12 lg:overflow-visible">
          <div className="origin-left [transform:perspective(1600px)_rotateY(-11deg)_rotateX(4deg)] lg:translate-x-4">
            <MiniBoard />
          </div>
        </div>
      </main>
    </div>
  );
}
