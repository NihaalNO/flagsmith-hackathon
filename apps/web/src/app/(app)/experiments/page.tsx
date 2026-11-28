"use client";

import { AppFrame } from "@/components/seshat/app-frame";
import { Copy, FlaskConical, Plus, Sparkles, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { ProposeExperimentsDialog } from "@/components/seshat/propose-experiments";
import { useData } from "@/components/seshat/use-data";
import {
  api, post, type Experiment, type ExperimentResults, type ExperimentWithKey, type Flag, type Repository,
} from "@/lib/api";
import { timeAgo } from "@/lib/lifecycle";

const pct = (x: number | null, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
const pts = (x: number | null) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${(x * 100).toFixed(2)} pts`);
const pval = (x: number | null) => (x == null ? "—" : x < 0.001 ? "<0.001" : x.toFixed(3));

const DECISION: Record<ExperimentResults["decision"], { label: string; color: string }> = {
  keep_running: { label: "Keep running", color: "var(--stage-rollout)" },
  trending: { label: "Trending, keep running", color: "var(--stage-rollout)" },
  winner: { label: "Winner", color: "var(--stage-released)" },
  control_better: { label: "Control is better", color: "var(--stage-released)" },
  no_significant_difference: { label: "No significant difference", color: "var(--stage-retired)" },
  invalid: { label: "Results not trustworthy", color: "var(--stage-cleanup)" },
};

const MDE = [0.05, 0.1, 0.2, 0.5];

type Row = { key: string; share: string };

function NewExperimentDialog({ open, onOpenChange, flags, onCreated }: {
  open: boolean; onOpenChange: (o: boolean) => void; flags: Flag[]; onCreated: (e: ExperimentWithKey) => void;
}) {
  const [flagId, setFlagId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [mde, setMde] = useState(0.1);
  const [custom, setCustom] = useState(false);
  const [rows, setRows] = useState<Row[]>([{ key: "control", share: "50" }, { key: "variant", share: "50" }]);
  const [saving, setSaving] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!flagId) return;
    setSaving(true);
    try {
      const variants = custom
        ? rows.map((r, i) => ({ key: r.key.trim(), is_control: i === 0, expected_share: r.share ? Number(r.share) : null }))
        : undefined;
      const exp = await post<ExperimentWithKey>(`/api/flags/${flagId}/experiments`, {
        name, primary_metric: metric, hypothesis: hypothesis || null, minimum_detectable_effect: mde, variants,
      });
      onCreated(exp);
      onOpenChange(false);
      setName(""); setMetric(""); setHypothesis("");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto">
        <form onSubmit={create} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Experiment on an existing flag</DialogTitle>
            <DialogDescription>
              Set up the multivariate flag and its split in Flagsmith. Seshat only reads it; your app reports who saw
              which variant and who converted.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Flag</Label>
            <Select value={flagId} onValueChange={(v) => setFlagId(v == null ? null : String(v))}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Choose a flag">
                  {(v: string | null) => flags.find((f) => f.id === v)?.name ?? "Choose a flag"}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {flags.map((f) => <SelectItem key={f.id} value={f.id}>{f.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ex-name">Name</Label>
            <Input id="ex-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="One-page checkout" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ex-metric">Conversion event</Label>
            <Input id="ex-metric" required value={metric} onChange={(e) => setMetric(e.target.value)}
              placeholder="checkout_completed" className="font-mono" pattern="[A-Za-z0-9_.\-]{1,100}" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="ex-hyp">Hypothesis</Label>
            <Textarea id="ex-hyp" rows={2} value={hypothesis} onChange={(e) => setHypothesis(e.target.value)}
              placeholder="Fewer steps means more people finish checkout" />
          </div>
          <div className="space-y-1.5">
            <Label>Smallest lift worth detecting</Label>
            <Select value={mde} onValueChange={(v) => setMde(Number(v))}>
              <SelectTrigger className="w-full">
                <SelectValue>{(v: number) => `+${Math.round(v * 100)}% relative`}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {MDE.map((m) => <SelectItem key={m} value={m}>+{Math.round(m * 100)}% relative</SelectItem>)}
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">Smaller lifts need more users before a result counts.</p>
          </div>
          <fieldset className="space-y-2">
            <legend className="text-sm font-medium">Variants</legend>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" checked={!custom} onChange={() => setCustom(false)} /> Use the flag&apos;s variants from Flagsmith
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="radio" checked={custom} onChange={() => setCustom(true)} /> Enter them (first row is the control)
            </label>
            {custom && (
              <div className="space-y-2">
                {rows.map((r, i) => (
                  <div key={i} className="flex gap-2">
                    <Input aria-label={`Variant ${i + 1} key`} value={r.key} className="font-mono"
                      onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
                    <Input aria-label={`Variant ${i + 1} share %`} value={r.share} inputMode="decimal" className="w-24"
                      onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, share: e.target.value } : x)))} />
                    {rows.length > 2 && (
                      <Button type="button" variant="ghost" size="icon" aria-label="Remove variant"
                        onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 className="size-4" /></Button>
                    )}
                  </div>
                ))}
                {rows.length < 10 && (
                  <Button type="button" variant="secondary" size="sm" onClick={() => setRows([...rows, { key: "", share: "" }])}>
                    Add variant
                  </Button>
                )}
                <p className="text-xs text-muted-foreground">Keys are what your app reports; shares are the planned split in %.</p>
              </div>
            )}
          </fieldset>
          <DialogFooter>
            <Button type="submit" disabled={saving || !flagId || !name || !metric}>{saving ? "Creating…" : "Create experiment"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function KeyDialog({ exp, onClose }: { exp: ExperimentWithKey | null; onClose: () => void }) {
  return (
    <Dialog open={Boolean(exp)} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Event key for {exp?.name}</DialogTitle>
          <DialogDescription>
            Shown once. Send events from your server (not the browser) with this key. If it leaks, rotate it.
          </DialogDescription>
        </DialogHeader>
        {exp && (
          <div className="space-y-3">
            <div className="flex gap-2">
              <code className="min-w-0 flex-1 truncate rounded-md border border-border bg-muted/40 px-3 py-2 font-mono text-xs">
                {exp.ingest_key}
              </code>
              <Button variant="secondary" size="icon" aria-label="Copy key"
                onClick={() => navigator.clipboard.writeText(exp.ingest_key).then(() => toast.success("Key copied"))}>
                <Copy className="size-4" />
              </Button>
            </div>
            <pre className="overflow-x-auto rounded-md border border-border bg-muted/40 p-3 font-mono text-xs">{exp.ingest_example}</pre>
            <p className="text-xs text-muted-foreground">
              Report an exposure when a user is shown a variant (once is enough) and a conversion when they complete
              the goal. Only a hash of each user id is stored.
            </p>
          </div>
        )}
        <DialogFooter><Button onClick={onClose}>Done</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function ConcludeDialog({ exp, onClose, onDone }: { exp: Experiment | null; onClose: () => void; onDone: () => void }) {
  const [winner, setWinner] = useState<string>("__none__");
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  async function conclude() {
    if (!exp) return;
    setSaving(true);
    try {
      await post(`/api/experiments/${exp.id}/conclude`, { winner: winner === "__none__" ? null : winner, note: note || null });
      toast.success(`Concluded ${exp.name}`);
      onDone();
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Dialog open={Boolean(exp)} onOpenChange={(o) => { if (!o) onClose(); else setWinner(exp?.results?.winner ?? "__none__"); }}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Conclude {exp?.name}</DialogTitle>
          <DialogDescription>
            Records the decision. Rolling the winner out (and cleaning up the flag) is done in Flagsmith and your code.
          </DialogDescription>
        </DialogHeader>
        {exp?.results && <p className="text-sm">Statistics say: {exp.results.summary}</p>}
        <div className="space-y-1.5">
          <Label>Winner</Label>
          <Select value={winner} onValueChange={(v) => setWinner(String(v))}>
            <SelectTrigger className="w-full"><SelectValue>{(v: string) => (v === "__none__" ? "No winner" : v)}</SelectValue></SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__">No winner</SelectItem>
              {exp?.variants.map((v) => <SelectItem key={v.key} value={v.key}>{v.key}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
        <Textarea rows={2} placeholder="Why (saved with the experiment)" value={note} onChange={(e) => setNote(e.target.value)} />
        <DialogFooter><Button onClick={conclude} disabled={saving}>{saving ? "Saving…" : "Conclude"}</Button></DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Results({ r }: { r: ExperimentResults }) {
  const d = DECISION[r.decision];
  return (
    <div className="mt-4 space-y-3">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <span className="flex items-center gap-1.5 font-medium">
          <span className="size-2.5 rounded-full" style={{ background: d.color }} aria-hidden />{d.label}
        </span>
        <span className="text-muted-foreground">{r.summary}</span>
      </p>
      <div>
        <div className="flex justify-between text-xs text-muted-foreground">
          <span>{r.total_exposures.toLocaleString()} users, {r.total_conversions.toLocaleString()} conversions</span>
          <span>{Math.round(r.progress * 100)}% of planned sample</span>
        </div>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted" role="progressbar"
          aria-valuenow={Math.round(r.progress * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Planned sample collected">
          <div className="h-full bg-foreground/70" style={{ width: `${r.progress * 100}%` }} />
        </div>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Variant</TableHead>
            <TableHead className="text-right">Users</TableHead>
            <TableHead className="text-right">Conversion</TableHead>
            <TableHead className="text-right">Lift vs control</TableHead>
            <TableHead className="text-right">p (adj.)</TableHead>
            <TableHead className="text-right">Chance to beat control</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {r.variants.map((v) => (
            <TableRow key={v.key}>
              <TableCell className="font-mono text-xs">
                {v.key}{v.is_control && <span className="ml-2 font-sans text-muted-foreground">control</span>}
              </TableCell>
              <TableCell className="text-right tabular-nums">{v.exposures.toLocaleString()}</TableCell>
              <TableCell className="text-right tabular-nums">
                {pct(v.conversion_rate)}
                {v.rate_ci_low != null && (
                  <span className="block text-xs text-muted-foreground">{pct(v.rate_ci_low)} – {pct(v.rate_ci_high)}</span>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                {v.is_control ? "—" : pts(v.absolute_lift)}
                {!v.is_control && v.lift_ci_low != null && (
                  <span className="block text-xs text-muted-foreground">{pts(v.lift_ci_low)} to {pts(v.lift_ci_high)}</span>
                )}
              </TableCell>
              <TableCell className={`text-right tabular-nums ${v.significant ? "font-semibold" : ""}`}>
                {v.is_control ? "—" : pval(v.p_value_adjusted)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{v.is_control ? "—" : pct(v.probability_to_beat_control, 0)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      {r.sample_ratio.mismatch && <p className="text-sm text-destructive">{r.sample_ratio.detail}</p>}
      {r.warnings.length > 0 && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
          {r.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}

function ExperimentsView() {
  const experiments = useData<Experiment[]>("/api/experiments");
  const flags = useData<Flag[]>("/api/flags");
  const repositories = useData<Repository[]>("/api/repositories");
  const [creating, setCreating] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [shownKey, setShownKey] = useState<ExperimentWithKey | null>(null);
  const [concluding, setConcluding] = useState<Experiment | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function act(exp: Experiment, action: "start" | "stop" | "rotate-key" | "delete") {
    if (action === "delete" && !window.confirm(`Delete "${exp.name}" and all its recorded events? This can't be undone.`)) return;
    if (action === "rotate-key" && !window.confirm("Replace the event key? The current key stops working immediately.")) return;
    setBusy(exp.id);
    try {
      if (action === "delete") {
        await api(`/api/experiments/${exp.id}`, { method: "DELETE" });
        toast.success(`Deleted ${exp.name}`);
      } else {
        const r = await post<Experiment | ExperimentWithKey>(`/api/experiments/${exp.id}/${action}`);
        if (action === "rotate-key") setShownKey(r as ExperimentWithKey);
        else toast.success(action === "start" ? `${exp.name} is collecting events` : `${exp.name} stopped`);
      }
      experiments.reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="max-w-5xl px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Experiments</h1>
          <p className="mt-1 max-w-[65ch] text-muted-foreground">
            A/B tests on multivariate flags. Pick a repository and Seshat suggests tests from its code and creates the
            flags in Flagsmith, or run one on a flag you already have. Your app reports exposures and conversions;
            Seshat tells you when a result is real.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" className="gap-2" onClick={() => setCreating(true)}>
            <Plus className="size-4" aria-hidden /> On an existing flag
          </Button>
          <Button className="gap-2" onClick={() => setProposing(true)}>
            <Sparkles className="size-4" aria-hidden /> Experiment on a repo
          </Button>
        </div>
      </header>

      {experiments.error && <p className="mt-6 text-sm text-destructive">{experiments.error}</p>}
      {experiments.data?.length === 0 && (
        <div className="mt-10 rounded-md border border-dashed border-border px-6 py-10 text-center">
          <FlaskConical className="mx-auto size-6 text-muted-foreground" aria-hidden />
          <p className="mt-2 font-semibold">No experiments yet</p>
          <p className="mt-1 text-muted-foreground">Pick a repository and Seshat suggests what to test, then creates the flags.</p>
          <Button className="mt-4 gap-2" onClick={() => setProposing(true)}>
            <Sparkles className="size-4" aria-hidden /> Experiment on a repo
          </Button>
        </div>
      )}

      <div className="mt-6 space-y-6">
        {experiments.data?.map((x) => (
          <article key={x.id} id={x.id} className="rounded-md border border-border p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-lg font-semibold">{x.name}</h2>
                <p className="text-sm text-muted-foreground">
                  <span className="font-mono">{x.flag_name}</span> · conversion: <span className="font-mono">{x.primary_metric}</span>
                  {x.environment_name ? ` · ${x.environment_name}` : ""} · {x.status}
                  {x.started_at ? `, started ${timeAgo(x.started_at)}` : ""}
                </p>
                {x.hypothesis && <p className="mt-1 text-sm">{x.hypothesis}</p>}
              </div>
              <div className="flex flex-wrap gap-2">
                {(x.status === "draft" || x.status === "stopped") && (
                  <Button size="sm" disabled={busy === x.id} onClick={() => act(x, "start")}>
                    {x.status === "draft" ? "Start" : "Resume"}
                  </Button>
                )}
                {x.status === "running" && (
                  <Button size="sm" variant="secondary" disabled={busy === x.id} onClick={() => act(x, "stop")}>Stop</Button>
                )}
                {x.status === "stopped" && (
                  <Button size="sm" variant="secondary" onClick={() => setConcluding(x)}>Conclude</Button>
                )}
                {x.status !== "concluded" && (
                  <Button size="sm" variant="ghost" disabled={busy === x.id} onClick={() => act(x, "rotate-key")}>New key</Button>
                )}
                {x.status !== "running" && (
                  <Button size="sm" variant="ghost" aria-label={`Delete ${x.name}`} disabled={busy === x.id}
                    onClick={() => act(x, "delete")}><Trash2 className="size-4" /></Button>
                )}
              </div>
            </div>
            {x.status === "concluded" && (
              <p className="mt-3 text-sm">
                <span className="font-medium">Decision:</span> {x.winner ? `roll out “${x.winner}”` : "no winner"}
                {x.conclusion_note ? `. ${x.conclusion_note}` : ""}
              </p>
            )}
            {x.results ? <Results r={x.results} /> : x.results_error && (
              <p className="mt-3 text-sm text-muted-foreground">Results unavailable: {x.results_error}</p>
            )}
          </article>
        ))}
      </div>

      <NewExperimentDialog open={creating} onOpenChange={setCreating} flags={flags.data ?? []}
        onCreated={(e) => { setShownKey(e); experiments.reload(); }} />
      <ProposeExperimentsDialog open={proposing} onOpenChange={setProposing} repositories={repositories.data ?? []}
        onLaunched={() => { experiments.reload(); flags.reload(); }} />
      <KeyDialog exp={shownKey} onClose={() => setShownKey(null)} />
      <ConcludeDialog exp={concluding} onClose={() => setConcluding(null)} onDone={experiments.reload} />
    </div>
  );
}

export default function ExperimentsPage() {
  return <AppFrame><ExperimentsView /></AppFrame>;
}
