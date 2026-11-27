"use client";

import { AppFrame } from "@/components/seshat/app-frame";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useSession } from "@/components/seshat/session";
import { useData } from "@/components/seshat/use-data";
import { patch, post, type Integration } from "@/lib/api";
import { timeAgo } from "@/lib/lifecycle";

const STATUS: Record<string, { label: string; color: string }> = {
  connected: { label: "Connected", color: "var(--stage-released)" },
  pending: { label: "Not tested yet", color: "var(--stage-rollout)" },
  error: { label: "Needs attention", color: "var(--stage-cleanup)" },
};

function KeyForm({ existing, onSaved }: { existing?: Integration; onSaved: () => void }) {
  const [key, setKey] = useState("");
  const [saving, setSaving] = useState(false);
  async function save(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const integ = existing
        ? await patch<Integration>(`/api/integrations/${existing.id}`, { api_token: key })
        : await post<Integration>("/api/integrations", { provider: "flagsmith", name: "Flagsmith", api_token: key });
      const test = await post<{ ok: boolean; detail: string }>(`/api/integrations/${integ.id}/test`);
      if (test.ok) toast.success(existing ? "Key replaced and working" : "Flagsmith connected");
      else toast.error(test.detail);
      setKey("");
      onSaved();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <form onSubmit={save} className="mt-4 max-w-md space-y-2">
      <Label htmlFor="fs-key">{existing ? "Replace API key" : "Organisation API key"}</Label>
      <div className="flex gap-2">
        <Input id="fs-key" type="password" required value={key} onChange={(e) => setKey(e.target.value)}
          autoComplete="off" placeholder="Paste key" />
        <Button type="submit" disabled={saving || !key}>{saving ? "Checking…" : existing ? "Replace" : "Connect"}</Button>
      </div>
      <p className="text-xs text-muted-foreground">
        In Flagsmith: Organisation Settings, API Access, Create API Key, with Admin turned on so Seshat can
        create a project per repository. Stored encrypted; never shown again.
      </p>
    </form>
  );
}

function ConnectionsView() {
  const { list, workspace } = useSession();
  const integrations = useData<Integration[]>("/api/integrations");
  const flagsmith = integrations.data?.find((i) => i.provider === "flagsmith");
  const [testing, setTesting] = useState(false);

  async function test() {
    if (!flagsmith) return;
    setTesting(true);
    try {
      const r = await post<{ ok: boolean; detail: string; organisations: string[] }>(`/api/integrations/${flagsmith.id}/test`);
      if (r.ok) toast.success(`Connected to ${r.organisations.join(", ")}`);
      else toast.error(r.detail);
      integrations.reload();
    } finally {
      setTesting(false);
    }
  }

  const st = flagsmith ? STATUS[flagsmith.status] ?? STATUS.pending : null;

  return (
    <div className="max-w-3xl px-6 py-6">
      <h1 className="text-3xl font-semibold tracking-tight">Connections</h1>
      <p className="mt-1 text-muted-foreground">Services this workspace uses. Each workspace connects its own.</p>

      <section className="mt-8 border-t border-border pt-6" aria-labelledby="fs-h">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="fs-h" className="text-xl font-semibold">Flagsmith</h2>
            <p className="text-muted-foreground">Source of truth for flag names, descriptions and on/off state.</p>
          </div>
          {st && (
            <span className="flex items-center gap-2 text-sm">
              <span className="size-2.5 rounded-full" style={{ background: st.color }} aria-hidden />
              {st.label}
            </span>
          )}
        </div>
        {flagsmith ? (
          <>
            <dl className="mt-4 grid max-w-md grid-cols-2 gap-y-1 text-sm">
              <dt className="text-muted-foreground">Last sync</dt><dd>{timeAgo(flagsmith.last_synced_at)}</dd>
              {flagsmith.status_detail && (<><dt className="text-muted-foreground">Problem</dt>
                <dd className="text-[var(--stage-cleanup)]">{flagsmith.status_detail}</dd></>)}
            </dl>
            <Button variant="secondary" className="mt-4" onClick={test} disabled={testing}>
              {testing ? "Testing…" : "Test connection"}
            </Button>
            <KeyForm existing={flagsmith} onSaved={integrations.reload} />
          </>
        ) : integrations.data ? (
          <KeyForm onSaved={integrations.reload} />
        ) : null}
      </section>

      <section className="mt-10 border-t border-border pt-6" aria-labelledby="gh-h">
        <h2 id="gh-h" className="text-xl font-semibold">GitHub</h2>
        <p className="text-muted-foreground">
          The Seshat GitHub App is installed on <strong className="text-foreground">{workspace?.github_account_login}</strong>
          {workspace?.github_account_type === "Organization" ? ", shared with its members." : "."}
        </p>
        {list?.install_url && (
          <Button variant="secondary" className="mt-4" nativeButton={false}
            render={<a href={list.install_url} target="_blank" rel="noreferrer" />}>
            Change repository access on GitHub
          </Button>
        )}
      </section>
    </div>
  );
}

export default function ConnectionsPage() {
  return <AppFrame><ConnectionsView /></AppFrame>;
}
