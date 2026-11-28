"use client";

import { AppFrame } from "@/components/seshat/app-frame";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useData } from "@/components/seshat/use-data";
import type { AuditEntry } from "@/lib/api";
import { timeAgo } from "@/lib/lifecycle";

const LABELS: Record<string, string> = {
  "flag.created": "Flag created", "flag.updated": "Flag edited", "flag.deleted": "Flag deleted",
  "flag.lifecycle_changed": "Stage changed", "flag.imported": "Imported from Flagsmith",
  "flag.synced": "Updated from Flagsmith", "flag.linked_to_flagsmith": "Linked to Flagsmith",
  "integration.created": "Connection added", "integration.updated": "Connection changed",
  "integration.connected": "Connection verified", "integration.synced": "Flagsmith sync",
  "integration.error": "Connection error", "github.synced": "GitHub import",
  "repository.created": "Repository added", "repository.scanned": "Repository scanned",
  "flagsmith.project_created": "Flagsmith project created",
};

function summary(e: AuditEntry): string {
  const d = e.details as Record<string, unknown>;
  if (e.action === "flag.lifecycle_changed") return `${d.from} → ${d.to}${d.reason ? `: ${d.reason}` : ""}`;
  if (e.action === "repository.scanned") return `${d.files_scanned} files at ${String(d.commit_sha).slice(0, 7)}`;
  if (typeof d.name === "string") return d.name;
  if (typeof d.full_name === "string") return d.full_name;
  if (typeof d.error === "string") return d.error;
  return "";
}

function ActivityView() {
  const log = useData<AuditEntry[]>("/api/audit-logs?limit=200");
  return (
    <div className="max-w-5xl px-6 py-6">
      <h1 className="text-3xl font-medium tracking-tight">Activity</h1>
      <p className="mt-1 text-muted-foreground">Everything that changed in this workspace, and who changed it.</p>
      {log.error && <p className="mt-6 text-sm text-destructive">{log.error}</p>}
      <Table className="mt-6">
        <TableHeader>
          <TableRow>
            <TableHead className="w-32">When</TableHead>
            <TableHead>What</TableHead>
            <TableHead>Details</TableHead>
            <TableHead>Who</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {log.data?.map((e) => (
            <TableRow key={e.id}>
              <TableCell className="text-muted-foreground" title={new Date(e.created_at).toLocaleString()}>
                {timeAgo(e.created_at)}
              </TableCell>
              <TableCell>{LABELS[e.action] ?? e.action}</TableCell>
              <TableCell className="max-w-[40ch] truncate text-muted-foreground">{summary(e)}</TableCell>
              <TableCell className="text-muted-foreground">{e.actor ?? "System"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

export default function ActivityPage() {
  return <AppFrame><ActivityView /></AppFrame>;
}
