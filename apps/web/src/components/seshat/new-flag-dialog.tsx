"use client";

import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { post, type Flag, type Repository } from "@/lib/api";

const LOCAL = "__local__";

export function NewFlagDialog({ open, onOpenChange, repos, onCreated }: {
  open: boolean; onOpenChange: (o: boolean) => void; repos: Repository[]; onCreated: (f: Flag) => void;
}) {
  const withProject = repos.filter((r) => r.flagsmith_project_id !== null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [target, setTarget] = useState<string>(withProject[0]?.id ?? LOCAL);
  const [saving, setSaving] = useState(false);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    try {
      const flag = await post<Flag>("/api/flags", {
        name, description: description || null, owner: owner || null,
        repository_id: target === LOCAL ? null : target,
      });
      toast.success(target === LOCAL ? `Created ${flag.name}` : `Created ${flag.name} in Flagsmith`);
      onCreated(flag);
      onOpenChange(false);
      setName(""); setDescription(""); setOwner("");
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <form onSubmit={create} className="space-y-4">
          <DialogHeader>
            <DialogTitle>New flag</DialogTitle>
            <DialogDescription>
              Created in the repository&apos;s Flagsmith project first, then tracked here.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="nf-name">Key</Label>
            <Input id="nf-name" required value={name} onChange={(e) => setName(e.target.value)}
              placeholder="new_checkout" className="font-mono" autoComplete="off" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="nf-desc">What it controls</Label>
            <Input id="nf-desc" value={description} onChange={(e) => setDescription(e.target.value)}
              placeholder="New checkout flow for logged-in users" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="nf-owner">Owner</Label>
            <Input id="nf-owner" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder="payments-team" />
          </div>
          <div className="space-y-1.5">
            <Label>Where</Label>
            <Select value={target} onValueChange={(v) => setTarget(String(v))}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {withProject.map((r) => <SelectItem key={r.id} value={r.id}>{r.full_name}</SelectItem>)}
                <SelectItem value={LOCAL}>Seshat only (not in Flagsmith)</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <DialogFooter>
            <Button type="submit" disabled={saving || !name}>{saving ? "Creating…" : "Create flag"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
