"use client";

import { Copy, FileCode2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  post, type ExperimentProposal, type ExperimentWithKey, type LaunchResult, type ProposalsResponse, type Repository,
} from "@/lib/api";

type Step = "pick" | "review" | "done";

/** How the app reads the flag and reports events (JS SDK; same idea in any language). */
function wiring(x: ExperimentWithKey) {
  // Variant `name` holds the value Flagsmith serves; map it back to the key the app reports.
  const cases = x.variants
    .filter((v) => !v.is_control)
    .map((v) => `  ${JSON.stringify(v.name)}: "${v.key}",`)
    .join("\n");
  return `// where the choice is made
const value = flagsmith.getValue("${x.flag_name}");
const variant = ({
${cases}
})[value] ?? "control";

// on your server, once per user
POST /api/experiments/${x.id}/events   X-Experiment-Key: <key>
{"events": [{"type": "exposure", "subject_id": userId, "variant": variant}]}
// when they complete it
{"events": [{"type": "conversion", "subject_id": userId}]}   // ${x.primary_metric}`;
}

function ProposalCard({ p, checked, onToggle }: { p: ExperimentProposal; checked: boolean; onToggle: () => void }) {
  return (
    <label className={`block cursor-pointer rounded-md border p-4 transition-colors hover:bg-muted/40 ${checked ? "border-primary/70 bg-muted/40" : "border-border"}`}>
      <div className="flex items-start gap-3">
        <input type="checkbox" className="mt-1 size-4 accent-primary" checked={checked} onChange={onToggle} />
        <div className="min-w-0 flex-1 space-y-2">
          <div>
            <p className="font-medium">{p.title}</p>
            <p className="font-mono text-xs text-muted-foreground">{p.flag_name} · counts {p.primary_metric}</p>
          </div>
          {p.hypothesis && <p className="text-sm">{p.hypothesis}</p>}
          <ul className="space-y-1 text-sm">
            <li><span className="text-muted-foreground">Control:</span> “{p.control.value}”</li>
            {p.variants.map((v) => (
              <li key={v.key}><span className="text-muted-foreground">Variant <span className="font-mono">{v.key}</span>:</span> “{v.value}”</li>
            ))}
          </ul>
          {p.files.length > 0 && (
            <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <FileCode2 className="size-3.5" aria-hidden />
              {p.files.map((f) => <span key={f} className="font-mono">{f}</span>)}
            </p>
          )}
          {p.rationale && <p className="text-xs text-muted-foreground">{p.rationale}</p>}
        </div>
      </div>
    </label>
  );
}

export function ProposeExperimentsDialog({ open, onOpenChange, repositories, onLaunched }: {
  open: boolean; onOpenChange: (o: boolean) => void; repositories: Repository[]; onLaunched: () => void;
}) {
  const [step, setStep] = useState<Step>("pick");
  const [repoId, setRepoId] = useState<string | null>(null);
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ProposalsResponse | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [launched, setLaunched] = useState<ExperimentWithKey[]>([]);
  const [start, setStart] = useState(true);

  const repo = repositories.find((r) => r.id === repoId);

  function reset() {
    setStep("pick"); setResult(null); setPicked(new Set()); setLaunched([]); setFocus("");
  }

  async function propose() {
    if (!repoId) return;
    setBusy(true);
    try {
      const r = await post<ProposalsResponse>(`/api/repositories/${repoId}/experiment-proposals`, { focus: focus || null });
      setResult(r);
      setPicked(new Set(r.proposals.slice(0, 1).map((p) => p.flag_name)));
      setStep("review");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function launch() {
    if (!repoId || !result) return;
    setBusy(true);
    try {
      const chosen = result.proposals.filter((p) => picked.has(p.flag_name));
      const r = await post<LaunchResult>(`/api/repositories/${repoId}/experiments/launch`, { proposals: chosen, start });
      r.errors.forEach((e) => toast.error(e));
      setLaunched(r.launched);
      setStep("done");
      onLaunched();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) reset(); }}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        {step === "pick" && (
          <>
            <DialogHeader>
              <DialogTitle>Experiment on a repository</DialogTitle>
              <DialogDescription>
                Seshat reads the code and suggests A/B tests. Nothing is created until you pick the ones to launch.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-1.5">
              <Label id="px-repo-label">Repository</Label>
              <Select value={repoId} onValueChange={(v) => setRepoId(v == null ? null : String(v))}>
                <SelectTrigger className="w-full" aria-labelledby="px-repo-label">
                  <SelectValue placeholder="Choose a repository">
                    {(v: string | null) => repositories.find((r) => r.id === v)?.full_name ?? "Choose a repository"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {repositories.map((r) => <SelectItem key={r.id} value={r.id}>{r.full_name}</SelectItem>)}
                </SelectContent>
              </Select>
              {repositories.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No repositories yet.{" "}
                  <Link href="/repositories" className="text-foreground underline underline-offset-4">Add one on the Repositories page</Link>.
                </p>
              )}
              {repo && repo.flagsmith_project_id == null && (
                <p className="text-xs text-muted-foreground">
                  This repository has no Flagsmith project yet. Seshat links or creates one when you launch.
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="px-focus">Focus (optional)</Label>
              <Input id="px-focus" value={focus} onChange={(e) => setFocus(e.target.value)} maxLength={300}
                placeholder="signup flow, pricing page, onboarding…" />
            </div>
            <DialogFooter>
              <Button onClick={propose} disabled={busy || !repoId}>
                {busy ? "Reading the code…" : "Suggest experiments"}
              </Button>
            </DialogFooter>
          </>
        )}

        {step === "review" && result && (
          <>
            <DialogHeader>
              <DialogTitle>Suggested for {result.repository}</DialogTitle>
              <DialogDescription>
                Launching creates each multivariate flag in Flagsmith with traffic split evenly across its values, and
                starts counting the events your app reports. From {result.llm} at {result.commit_sha.slice(0, 7)}.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              {result.proposals.map((p) => (
                <ProposalCard key={p.flag_name} p={p} checked={picked.has(p.flag_name)}
                  onToggle={() => setPicked((s) => {
                    const n = new Set(s);
                    if (n.has(p.flag_name)) n.delete(p.flag_name); else n.add(p.flag_name);
                    return n;
                  })} />
              ))}
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-primary" checked={start} onChange={() => setStart(!start)} />
              Start collecting events right away
            </label>
            <DialogFooter className="gap-2">
              <Button variant="ghost" onClick={() => setStep("pick")} disabled={busy}>Back</Button>
              <Button onClick={launch} disabled={busy || picked.size === 0}>
                {busy ? "Creating flags…" : `Launch ${picked.size} experiment${picked.size === 1 ? "" : "s"}`}
              </Button>
            </DialogFooter>
          </>
        )}

        {step === "done" && (
          <>
            <DialogHeader>
              <DialogTitle>Launched {launched.length} experiment{launched.length === 1 ? "" : "s"}</DialogTitle>
              <DialogDescription>
                The flags are live in Flagsmith. Each key below is shown once: send events from your server with it.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-5">
              {launched.map((x) => (
                <section key={x.id} className="space-y-2">
                  <p className="font-medium">{x.name} <span className="font-mono text-xs font-normal text-muted-foreground">{x.flag_name}</span></p>
                  <div className="flex gap-2">
                    <code className="min-w-0 flex-1 truncate rounded-md border border-border bg-muted/40 px-3 py-2 font-mono text-xs">
                      {x.ingest_key}
                    </code>
                    <Button variant="secondary" size="icon" aria-label={`Copy key for ${x.name}`}
                      onClick={() => navigator.clipboard.writeText(x.ingest_key).then(() => toast.success("Key copied"))}>
                      <Copy className="size-4" />
                    </Button>
                  </div>
                  <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">{wiring(x)}</pre>
                </section>
              ))}
            </div>
            <DialogFooter><Button onClick={() => { onOpenChange(false); reset(); }}>Done</Button></DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
