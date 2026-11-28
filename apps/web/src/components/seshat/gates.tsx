"use client";

import { RefreshCw } from "lucide-react";
import { useEffect, useRef } from "react";
import { GithubMark } from "./github-mark";
import { Button } from "@/components/ui/button";
import { useSession } from "./session";

function Frame({ children }: { children: React.ReactNode }) {
  return (
    <main className="grid min-h-dvh place-items-center px-6">
      <div className="w-full max-w-md">{children}</div>
    </main>
  );
}

export function Wordmark({ className = "" }: { className?: string }) {
  // The same mark as the landing page: the initial on a small accent tile.
  return (
    <span className={`inline-flex items-center gap-2 font-medium tracking-tight ${className}`}>
      <span aria-hidden className="grid size-[1em] place-items-center rounded-[4px] bg-primary text-[0.62em] font-semibold text-primary-foreground">
        S
      </span>
      Seshat
    </span>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <Frame>
      <p className="text-muted-foreground" role="status">{label}</p>
    </Frame>
  );
}

/**
 * Signed out: the landing page is the static /welcome page. Its "Sign up with GitHub" buttons
 * link back here with ?signin=github, which starts the GitHub sign-in straight away.
 */
export function SignIn() {
  const { signIn } = useSession();
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (new URLSearchParams(window.location.search).get("signin") === "github") void signIn();
    else window.location.replace("/welcome");
  }, [signIn]);
  return <Loading label="Opening Seshat…" />;
}

export function InstallApp() {
  const { list, refreshWorkspaces, signOut } = useSession();
  return (
    <Frame>
      <Wordmark className="text-2xl" />
      <h1 className="mt-6 text-3xl font-medium tracking-tight">Connect your GitHub account</h1>
      <p className="mt-3 leading-relaxed text-muted-foreground">
        Install the Seshat GitHub App on your personal account or an organization. Each account gets its own
        workspace; people in the same organization share it.
      </p>
      <div className="mt-8 flex flex-wrap gap-3">
        {list?.install_url && (
          <Button size="lg" className="gap-2" nativeButton={false}
            render={<a href={list.install_url} target="_blank" rel="noreferrer" />}>
            <GithubMark /> Install GitHub App
          </Button>
        )}
        <Button size="lg" variant="secondary" className="gap-2" onClick={refreshWorkspaces}>
          <RefreshCw className="size-4" aria-hidden /> I&apos;ve installed it
        </Button>
      </div>
      {list?.warnings.map((w) => <p key={w} className="mt-4 text-sm text-[var(--stage-cleanup)]">{w}</p>)}
      <button className="mt-10 text-sm text-muted-foreground underline-offset-4 hover:underline" onClick={signOut}>
        Sign out
      </button>
    </Frame>
  );
}

export function PickWorkspace() {
  const { list, chooseWorkspace } = useSession();
  return (
    <Frame>
      <h1 className="text-3xl font-medium tracking-tight">Choose a workspace</h1>
      <ul className="mt-6 divide-y divide-border rounded-md border border-border">
        {list?.workspaces.map((w) => (
          <li key={w.id}>
            <button className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-accent"
              onClick={() => chooseWorkspace(w.id)}>
              <span className="font-medium">{w.name}</span>
              <span className="text-sm text-muted-foreground">
                {w.github_account_type === "Organization" ? "Organization" : "Personal"}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </Frame>
  );
}

export function ApiDown({ message }: { message: string }) {
  const { refreshWorkspaces } = useSession();
  return (
    <Frame>
      <h1 className="text-2xl font-medium tracking-tight">Seshat can&apos;t reach its server</h1>
      <p className="mt-3 text-muted-foreground">{message}</p>
      <Button className="mt-6" onClick={refreshWorkspaces}>Try again</Button>
    </Frame>
  );
}
