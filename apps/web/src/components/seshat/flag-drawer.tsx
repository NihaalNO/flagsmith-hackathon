"use client";

import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { post, type AuditEntry, type Flag, type LifecycleStatus, type Reference } from "@/lib/api";
import { STAGES, stage, timeAgo } from "@/lib/lifecycle";
import { FlagAnalysisSection } from "./flag-analysis";
import { useData } from "./use-data";

type Transitions = Record<LifecycleStatus, LifecycleStatus[]>;

function describe(e: AuditEntry): string {
  const d = e.details as Record<string, unknown>;
  switch (e.action) {
    case "flag.created": return "Created";
    case "flag.imported": return `Imported from Flagsmith${d.project ? ` (${d.project})` : ""}`;
    case "flag.linked_to_flagsmith": return "Linked to Flagsmith";
    case "flag.lifecycle_changed":
      return `Moved from ${stage(d.from as LifecycleStatus).label} to ${stage(d.to as LifecycleStatus).label}` +
        (d.reason ? `: “${d.reason}”` : "");
    case "flag.synced": return `Updated from Flagsmith (${Object.keys((d.changes as object) ?? {}).join(", ")})`;
    case "flag.updated": return `Edited ${Object.keys((d.changes as object) ?? {}).join(", ")}`;
    default: return e.action;
  }
}

export function FlagDrawer({ flag, transitions, onClose, onChanged, onAnalyzed }: {
  flag: Flag | null; transitions: Transitions | null; onClose: () => void; onChanged: (f: Flag) => void;
  onAnalyzed?: () => void;
}) {
  const refs = useData<Reference[]>(flag ? `/api/flags/${flag.id}/references` : null);
  const history = useData<AuditEntry[]>(flag ? `/api/audit-logs?resource_type=flag&resource_id=${flag.id}&limit=50` : null);
  const [reason, setReason] = useState("");
  const [moving, setMoving] = useState<LifecycleStatus | null>(null);

  async function move(to: LifecycleStatus) {
    if (!flag) return;
    setMoving(to);
    try {
      const updated = await post<Flag>(`/api/flags/${flag.id}/lifecycle`, { status: to, reason: reason || null });
      toast.success(`${flag.name} moved to ${stage(to).label}`);
      setReason("");
      onChanged(updated);
      history.reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setMoving(null);
    }
  }

  const next = flag && transitions ? transitions[flag.lifecycle_status] ?? [] : [];
  const order = (s: LifecycleStatus) => STAGES.findIndex((x) => x.id === s);
  const forward = next.filter((s) => flag && order(s) > order(flag.lifecycle_status));
  const back = next.filter((s) => flag && order(s) < order(flag.lifecycle_status));

  const byFile = new Map<string, Reference[]>();
  refs.data?.forEach((r) => {
    const k = `${r.repository}/${r.path}`;
    byFile.set(k, [...(byFile.get(k) ?? []), r]);
  });

  return (
    <Sheet open={Boolean(flag)} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-xl">
        {flag && (
          <>
            <SheetHeader className="border-b border-border pb-5">
              <span className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="h-3 w-3 rounded-sm" style={{ background: stage(flag.lifecycle_status).band }} />
                {stage(flag.lifecycle_status).label} for {timeAgo(flag.lifecycle_changed_at).replace(" ago", "")}
              </span>
              <SheetTitle className="font-mono text-xl break-all">{flag.name}</SheetTitle>
              <SheetDescription>{flag.description ?? "No description in Flagsmith."}</SheetDescription>
              <dl className="mt-3 grid grid-cols-3 gap-px overflow-hidden rounded-md border border-border bg-border text-sm">
                {[
                  ["Owner", flag.owner ?? "—"],
                  ["Project", flag.project_name ?? "Local only"],
                  [flag.environment ?? "Environment", flag.enabled ? "On" : "Off"],
                ].map(([k, v]) => (
                  <div key={k} className="bg-card px-3 py-2">
                    <dt className="text-xs text-muted-foreground">{k}</dt>
                    <dd className="truncate font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
            </SheetHeader>

            <FlagAnalysisSection key={flag.id} flag={flag} onAnalyzed={onAnalyzed} />

            <section className="border-b border-border px-4 py-5" aria-labelledby="move-h">
              <h3 id="move-h" className="font-medium">Move this flag</h3>
              {next.length === 0 ? (
                <p className="mt-2 text-sm text-muted-foreground">Retired flags don&apos;t move any further.</p>
              ) : (
                <>
                  <Textarea className="mt-3" placeholder="Reason (saved in the history)" value={reason}
                    onChange={(e) => setReason(e.target.value)} rows={2} />
                  <div className="mt-3 flex flex-wrap gap-2">
                    {forward.map((s) => (
                      <Button key={s} disabled={moving !== null} onClick={() => move(s)}>
                        {moving === s ? "Moving…" : stage(s).label}
                      </Button>
                    ))}
                    {back.map((s) => (
                      <Button key={s} variant="secondary" disabled={moving !== null} onClick={() => move(s)}>
                        Back to {stage(s).label.toLowerCase()}
                      </Button>
                    ))}
                  </div>
                </>
              )}
            </section>

            <section className="border-b border-border px-4 py-5" aria-labelledby="code-h">
              <h3 id="code-h" className="font-medium">
                In code{refs.data && refs.data.length > 0 ? `: ${refs.data.length} place${refs.data.length === 1 ? "" : "s"}` : ""}
              </h3>
              {refs.loading && <p className="mt-2 text-sm text-muted-foreground">Loading…</p>}
              {refs.data?.length === 0 && (
                <p className="mt-2 text-sm text-muted-foreground">
                  Not found in any scanned repository. Scan a repository on the Repositories page to check.
                </p>
              )}
              <div className="mt-3 space-y-4">
                {[...byFile.entries()].map(([file, rows]) => (
                  <div key={file}>
                    <p className="truncate font-mono text-xs text-muted-foreground">{file}</p>
                    <ul className="mt-1 overflow-hidden rounded-md border border-border">
                      {rows.map((r) => (
                        <li key={r.id} className="flex items-start gap-3 border-b border-border px-3 py-2 last:border-b-0">
                          <span className="w-8 shrink-0 text-right font-mono text-xs text-muted-foreground">{r.line}</span>
                          <code className="min-w-0 flex-1 truncate font-mono text-xs">{r.snippet}</code>
                          {r.url && (
                            <a href={r.url} target="_blank" rel="noreferrer" aria-label="Open on GitHub"
                              className="text-muted-foreground hover:text-foreground">
                              <ExternalLink className="size-3.5" />
                            </a>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>

            <section className="px-4 py-5" aria-labelledby="hist-h">
              <h3 id="hist-h" className="font-medium">History</h3>
              <ol className="mt-3 space-y-3 border-l border-border pl-4">
                {history.data?.map((e) => (
                  <li key={e.id} className="text-sm">
                    <p>{describe(e)}</p>
                    <p className="text-xs text-muted-foreground">{timeAgo(e.created_at)}{e.actor ? ` by ${e.actor}` : ""}</p>
                  </li>
                ))}
              </ol>
            </section>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
