"use client";

import { AppFrame } from "@/components/seshat/app-frame";
import Link from "next/link";
import { Plus, RefreshCw } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { FlagDrawer } from "@/components/seshat/flag-drawer";
import { NewFlagDialog } from "@/components/seshat/new-flag-dialog";
import { Strip } from "@/components/seshat/strip";
import { useData } from "@/components/seshat/use-data";
import {
  post, type Flag, type FlagsmithSyncResult, type FlagVerdictSummary, type Integration, type LifecycleStatus,
  type Repository,
} from "@/lib/api";
import { STAGES, timeAgo } from "@/lib/lifecycle";

function BoardView() {
  const flags = useData<Flag[]>("/api/flags");
  const integrations = useData<Integration[]>("/api/integrations");
  const repos = useData<Repository[]>("/api/repositories");
  const transitions = useData<Record<LifecycleStatus, LifecycleStatus[]>>("/api/flags/lifecycle/transitions");
  const verdicts = useData<FlagVerdictSummary[]>("/api/flag-verdicts");
  const verdictOf = useMemo(() => new Map(verdicts.data?.map((v) => [v.flag_id, v]) ?? []), [verdicts.data]);
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [syncing, setSyncing] = useState(false);

  const flagsmith = integrations.data?.find((i) => i.provider === "flagsmith" && i.has_secret);
  const byStage = useMemo(() => {
    const m = new Map<LifecycleStatus, Flag[]>(STAGES.map((s) => [s.id, []]));
    flags.data?.forEach((f) => m.get(f.lifecycle_status)?.push(f));
    return m;
  }, [flags.data]);
  const open = flags.data?.find((f) => f.id === openId) ?? null;

  async function sync() {
    if (!flagsmith) return;
    setSyncing(true);
    try {
      const r = await post<FlagsmithSyncResult>(`/api/integrations/${flagsmith.id}/sync`);
      const parts = [r.created && `${r.created} new`, r.updated && `${r.updated} updated`].filter(Boolean);
      toast.success(`Synced ${r.projects.join(", ")}${parts.length ? `: ${parts.join(", ")}` : ": no changes"}`);
      if (r.missing_in_flagsmith.length) toast.warning(`Deleted in Flagsmith: ${r.missing_in_flagsmith.join(", ")}`);
      (r.warnings ?? []).forEach((w) => toast.warning(w, { duration: 10000 }));
      flags.reload(); integrations.reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSyncing(false);
    }
  }

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex flex-wrap items-end justify-between gap-4 px-6 pt-6 pb-5">
        <div>
          <h1 className="text-3xl font-medium tracking-tight">Board</h1>
          <p className="text-muted-foreground">
            {flags.data ? `${flags.data.length} flag${flags.data.length === 1 ? "" : "s"}` : "Loading flags"}
            {flagsmith && `, synced with Flagsmith ${timeAgo(flagsmith.last_synced_at)}`}
          </p>
        </div>
        <div className="flex gap-2">
          {flagsmith && (
            <Button variant="secondary" className="gap-2" onClick={sync} disabled={syncing}>
              <RefreshCw className={`size-4 ${syncing ? "animate-spin" : ""}`} aria-hidden />
              {syncing ? "Syncing…" : "Sync from Flagsmith"}
            </Button>
          )}
          <Button className="gap-2" onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden /> New flag
          </Button>
        </div>
      </header>

      {integrations.data && !flagsmith && (
        <p className="mx-6 mb-4 rounded-md border border-[var(--stage-rollout)]/50 px-4 py-3 text-sm">
          Flagsmith isn&apos;t connected for this workspace yet, so flags won&apos;t sync.{" "}
          <Link href="/connections" className="font-medium underline underline-offset-4">Connect Flagsmith</Link>
        </p>
      )}
      {flags.error && <p className="mx-6 mb-4 text-sm text-destructive">{flags.error}</p>}

      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto px-6 pb-6" role="list" aria-label="Lifecycle stages">
        {STAGES.map((s) => {
          const items = byStage.get(s.id) ?? [];
          return (
            <section key={s.id} role="listitem" aria-label={`${s.label}, ${items.length} flags`}
              className="flex w-72 shrink-0 flex-col rounded-md border border-border bg-card">
              <header className="border-t-2 px-3 pt-2.5 pb-2" style={{ borderColor: s.band }}>
                <div className="flex items-baseline justify-between">
                  <h2 className="font-medium">{s.label}</h2>
                  <span className="tabular-nums text-muted-foreground">{items.length}</span>
                </div>
                <p className="text-xs text-muted-foreground">{s.hint}</p>
              </header>
              <div className="min-h-0 flex-1">
                {/* Gutter reserved on both edges: strips keep one width whether or not a column scrolls */}
                <div className="h-full space-y-2 overflow-y-auto px-1 pt-1 pb-3 [scrollbar-gutter:stable_both-edges]">
                  {flags.loading && !flags.data && <Skeleton className="h-16 w-full" />}
                  {items.map((f) => <Strip key={f.id} flag={f} verdict={verdictOf.get(f.id)} onOpen={() => setOpenId(f.id)} />)}
                </div>
              </div>
            </section>
          );
        })}
      </div>

      <FlagDrawer
        flag={open}
        transitions={transitions.data}
        onClose={() => setOpenId(null)}
        onChanged={(f) => flags.setData((all) => all?.map((x) => (x.id === f.id ? f : x)) ?? null)}
        onAnalyzed={verdicts.reload}
      />
      <NewFlagDialog open={creating} onOpenChange={setCreating} repos={repos.data ?? []}
        onCreated={(f) => flags.setData((all) => [f, ...(all ?? [])])} />
    </div>
  );
}

export default function BoardPage() {
  return <AppFrame><BoardView /></AppFrame>;
}
