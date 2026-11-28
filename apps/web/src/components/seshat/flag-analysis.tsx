"use client";

import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { post, type Experiment, type Flag, type FlagAnalysis, type LLMUsage, type VerdictReason } from "@/lib/api";
import { timeAgo } from "@/lib/lifecycle";
import { useData } from "./use-data";
import { VerdictTags } from "./verdict";

function aiLine(llm: LLMUsage): string {
  const who = [llm.provider, llm.model].filter(Boolean).join(" · ");
  switch (llm.status) {
    case "used": return `Explanation written by ${who || "the configured AI model"} from the evidence below.`;
    case "rejected_invalid_output":
      return `The AI's explanation was rejected (it cited something not in the evidence), so a plain summary is shown.`;
    case "unavailable": return `AI model unavailable${llm.error ? ` (${llm.error})` : ""}; showing a plain summary.`;
    default: return "No AI model configured; showing a plain summary. Labels come from the same evidence either way.";
  }
}

function Reasons({ title, reasons }: { title: string; reasons: VerdictReason[] }) {
  if (!reasons.length) return null;
  return (
    <div>
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <ul className="mt-1 space-y-1 text-sm">
        {reasons.map((r, i) => (
          <li key={i} className="flex gap-2">
            <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-muted-foreground/60" aria-hidden />
            <span>{r.text}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function FlagAnalysisSection({ flag, onAnalyzed }: { flag: Flag; onAnalyzed?: () => void }) {
  // null = never analyzed (a normal state, not an error)
  const latest = useData<FlagAnalysis | null>(`/api/flags/${flag.id}/analysis`);
  const experiments = useData<Experiment[]>(`/api/flags/${flag.id}/experiments`);
  const [running, setRunning] = useState(false);

  async function analyze() {
    setRunning(true);
    try {
      const r = await post<FlagAnalysis>(`/api/flags/${flag.id}/analysis`, {});
      latest.setData(r);
      onAnalyzed?.();
      toast.success(`Analyzed ${flag.name}`);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  const v = latest.data?.result.verdict;
  const notes = latest.data?.result.data_notes ?? [];

  return (
    <section className="border-b border-border px-4 py-5" aria-labelledby="ai-h">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 id="ai-h" className="font-medium">
            AI analysis
          </h3>
          <p className="text-sm text-muted-foreground">
            {latest.data ? `Analyzed ${timeAgo(latest.data.created_at)}` : "Is this flag active, and is it still useful?"}
          </p>
        </div>
        <Button size="sm" variant={latest.data ? "secondary" : "default"} onClick={analyze} disabled={running}>
          {running ? "Analyzing…" : latest.data ? "Analyze again" : "Analyze"}
        </Button>
      </div>
      {running && (
        <p className="mt-2 text-xs text-muted-foreground">Reading the repository code and Flagsmith state; this takes a few seconds.</p>
      )}
      {latest.error && <p className="mt-3 text-sm text-destructive">{latest.error}</p>}

      {v && (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <VerdictTags activity={v.activity} usefulness={v.usefulness} className="text-base font-medium" />
            <span className="text-xs text-muted-foreground">{Math.round(v.confidence * 100)}% confidence</span>
          </div>
          <p className="text-sm leading-relaxed">{v.explanation}</p>
          <p className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm">
            <span className="font-medium">Recommended: </span>{v.recommendation}
          </p>
          <Reasons title="Why active or inactive" reasons={v.activity_reasons} />
          <Reasons title="Why useful or not" reasons={v.usefulness_reasons} />
          {(v.caveats.length > 0 || notes.length > 0) && (
            <div>
              <p className="text-xs font-medium text-muted-foreground">Limits of this analysis</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
                {[...notes, ...v.caveats].map((c) => <li key={c}>{c}</li>)}
              </ul>
            </div>
          )}
          <p className="text-xs text-muted-foreground">{aiLine(v.llm)}</p>
        </div>
      )}
      {latest.data && !v && (
        <p className="mt-3 text-sm text-muted-foreground">This analysis predates verdicts. Analyze again to get one.</p>
      )}

      {experiments.data && experiments.data.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-muted-foreground">Experiments on this flag</p>
          <ul className="mt-1 space-y-1 text-sm">
            {experiments.data.map((x) => (
              <li key={x.id} className="flex justify-between gap-2">
                <Link href={`/experiments#${x.id}`} className="truncate hover:underline">{x.name}</Link>
                <span className="text-muted-foreground">{x.status}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
