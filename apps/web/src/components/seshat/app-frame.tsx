"use client";

import { ApiDown, InstallApp, Loading, PickWorkspace, SignIn } from "./gates";
import { useSession } from "./session";
import { Shell } from "./shell";

/**
 * Sign-in / workspace gate, rendered by each page (not the layout), so the layout always renders
 * its page segment and Next.js can validate it. `children` only mounts once a workspace is chosen,
 * so no data is fetched before sign-in.
 */
export function AppFrame({ children }: { children: React.ReactNode }) {
  const { authReady, session, list, listError, workspace } = useSession();
  if (!authReady) return <Loading label="Loading…" />;
  if (!session) return <SignIn />;
  if (listError && !list) return <ApiDown message={listError} />;
  if (!list) return <Loading label="Checking your GitHub access…" />;
  if (list.workspaces.length === 0) return <InstallApp />;
  if (!workspace) return <PickWorkspace />;
  // key: remount when the workspace changes so no data carries over between workspaces.
  return <Shell><div key={workspace.id}>{children}</div></Shell>;
}
