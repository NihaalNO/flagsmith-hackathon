"use client";

import { AppFrame } from "@/components/seshat/app-frame";
import { ScanSearch } from "lucide-react";
import { GithubMark } from "@/components/seshat/github-mark";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useSession } from "@/components/seshat/session";
import { useData } from "@/components/seshat/use-data";
import {
  post, put, type FlagsmithProject, type GitHubSyncResult, type Integration, type Repository,
  type RepositoryProjectLinkResult, type ScanResult,
} from "@/lib/api";
import { timeAgo } from "@/lib/lifecycle";

function RepositoriesView() {
  const { list, workspace } = useSession();
  const repos = useData<Repository[]>("/api/repositories");
  const [importing, setImporting] = useState(false);
  const [scanning, setScanning] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, ScanResult>>({});
  const [linking, setLinking] = useState<string | null>(null);
  const integrations = useData<Integration[]>("/api/integrations");
  const flagsmith = integrations.data?.find((i) => i.provider === "flagsmith" && i.has_secret);
  const projects = useData<FlagsmithProject[]>(flagsmith ? `/api/integrations/${flagsmith.id}/flagsmith/projects` : null);
  const projectName = (id: number | null) =>
    id == null ? null : projects.data?.find((p) => p.id === id)?.name ?? `#${id}`;

  async function linkProject(repo: Repository, projectId: number) {
    if (projectId === repo.flagsmith_project_id) return;
    setLinking(repo.id);
    try {
      const r = await put<RepositoryProjectLinkResult>(`/api/repositories/${repo.id}/flagsmith-project`,
        { flagsmith_project_id: projectId });
      if (r.sync) {
        const s = r.sync;
        toast.success(`${repo.full_name} now uses “${r.flagsmith_project_name}”: ` +
          `${s.created} flag${s.created === 1 ? "" : "s"} imported, ${s.updated} updated. Scan to find them in code.`);
      }
      if (r.sync_error) toast.error(r.sync_error);
      repos.reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setLinking(null);
    }
  }

  async function importRepos() {
    setImporting(true);
    try {
      const r = await post<GitHubSyncResult>("/api/github/sync");
      toast.success(`${r.repositories.length} repositor${r.repositories.length === 1 ? "y" : "ies"} from GitHub` +
        (r.repositories_added ? `, ${r.repositories_added} new` : ""));
      if (r.projects_created.length) toast.success(`Created Flagsmith projects: ${r.projects_created.join(", ")}`);
      r.project_errors.forEach((e) => toast.error(e));
      repos.reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setImporting(false);
    }
  }

  async function scan(repo: Repository) {
    setScanning(repo.id);
    try {
      const r = await post<ScanResult>(`/api/repositories/${repo.id}/scan`);
      setResults((m) => ({ ...m, [repo.id]: r }));
      toast.success(`Scanned ${r.files_scanned} files in ${repo.full_name}: ${r.references} flag reference${r.references === 1 ? "" : "s"}`);
      repos.reload();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setScanning(null);
    }
  }

  return (
    <div className="max-w-5xl px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Repositories</h1>
          <p className="mt-1 max-w-[65ch] text-muted-foreground">
            Repositories the GitHub App can read in {workspace?.name}. Each one is linked to a Flagsmith project:
            an existing one with a matching name, otherwise a new one. If a repository&apos;s flags live in a
            different project, choose it below. Scanning finds every place a flag&apos;s key appears in the
            default branch.
          </p>
        </div>
        <div className="flex gap-2">
          {list?.install_url && (
            <Button variant="secondary" className="gap-2" nativeButton={false}
              render={<a href={list.install_url} target="_blank" rel="noreferrer" />}>
              <GithubMark /> Choose repositories
            </Button>
          )}
          <Button onClick={importRepos} disabled={importing}>{importing ? "Importing…" : "Import from GitHub"}</Button>
        </div>
      </header>

      {repos.error && <p className="mt-6 text-sm text-destructive">{repos.error}</p>}
      {repos.data?.length === 0 && (
        <div className="mt-10 rounded-md border border-dashed border-border px-6 py-10 text-center">
          <p className="font-semibold">No repositories yet</p>
          <p className="mt-1 text-muted-foreground">
            Import the repositories you gave the GitHub App access to.
          </p>
          <Button className="mt-4" onClick={importRepos} disabled={importing}>Import from GitHub</Button>
        </div>
      )}

      {repos.data && repos.data.length > 0 && (
        <Table className="mt-6">
          <TableHeader>
            <TableRow>
              <TableHead>Repository</TableHead>
              <TableHead>Flagsmith project</TableHead>
              <TableHead>Last scan</TableHead>
              <TableHead className="text-right">Scan</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {repos.data.map((r) => {
              const res = results[r.id];
              return (
                <TableRow key={r.id}>
                  <TableCell>
                    <a href={`https://github.com/${r.full_name}`} target="_blank" rel="noreferrer"
                      className="font-medium hover:underline">{r.full_name}</a>
                    <span className="block text-xs text-muted-foreground">{r.default_branch}</span>
                  </TableCell>
                  <TableCell>
                    {projects.data ? (
                      <Select value={r.flagsmith_project_id} disabled={linking !== null}
                        onValueChange={(v) => { if (v != null) linkProject(r, Number(v)); }}>
                        <SelectTrigger className="w-56" aria-label={`Flagsmith project for ${r.full_name}`}>
                          <SelectValue placeholder="Choose a project">
                            {(v: number | null) => (linking === r.id ? "Linking…" : projectName(v) ?? "Choose a project")}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {[...projects.data].sort((a, b) => a.name.localeCompare(b.name)).map((p) => (
                            <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : r.flagsmith_project_id
                      ? <span>{projectName(r.flagsmith_project_id)}</span>
                      : <span className="text-muted-foreground">
                          {flagsmith ? "Not linked yet" : "Connect Flagsmith on Connections to link a project"}
                        </span>}
                  </TableCell>
                  <TableCell>
                    {timeAgo(r.last_scanned_at)}
                    {res && (
                      <span className="block text-xs text-muted-foreground">
                        {res.files_scanned} files, {res.flags.map((f) => `${f.flag} ×${f.references}`).join(", ") || "no flags found"}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-right">
                    <Button size="sm" variant="secondary" className="gap-2" disabled={scanning !== null || !r.installation_id}
                      onClick={() => scan(r)}>
                      <ScanSearch className="size-4" aria-hidden />
                      {scanning === r.id ? "Scanning…" : "Scan"}
                    </Button>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}

export default function RepositoriesPage() {
  return <AppFrame><RepositoriesView /></AppFrame>;
}
