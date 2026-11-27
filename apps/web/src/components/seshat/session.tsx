"use client";

import type { Session } from "@supabase/supabase-js";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, getWorkspaceId, setWorkspaceId, type Workspace, type WorkspaceList } from "@/lib/api";
import { supabase } from "@/lib/supabase";

type Ctx = {
  session: Session | null;
  authReady: boolean;
  list: WorkspaceList | null;
  listError: string | null;
  workspace: Workspace | null;
  chooseWorkspace: (id: string) => void;
  refreshWorkspaces: () => Promise<void>;
  signIn: () => Promise<void>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<Ctx | null>(null);

export function useSession() {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession outside SessionProvider");
  return ctx;
}

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [list, setList] = useState<WorkspaceList | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [workspaceId, setWsId] = useState<string | null>(null);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthReady(true);
    });
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  const applyList = useCallback((l: WorkspaceList) => {
    setListError(null);
    setList(l);
    const saved = getWorkspaceId();
    const pick = l.workspaces.find((w) => w.id === saved) ?? (l.workspaces.length === 1 ? l.workspaces[0] : null);
    setWorkspaceId(pick?.id ?? null);
    setWsId(pick?.id ?? null);
  }, []);

  const refreshWorkspaces = useCallback(async () => {
    try {
      applyList(await api<WorkspaceList>("/api/workspaces"));
    } catch (e) {
      setListError((e as Error).message);
    }
  }, [applyList]);

  const userId = session?.user.id;
  useEffect(() => {
    if (!userId) return;
    let cancelled = false;
    api<WorkspaceList>("/api/workspaces")
      .then((l) => { if (!cancelled) applyList(l); })
      .catch((e: Error) => { if (!cancelled) setListError(e.message); });
    return () => { cancelled = true; };
  }, [userId, applyList]);

  const chooseWorkspace = (id: string) => {
    setWorkspaceId(id);
    setWsId(id);
  };

  const signIn = async () => {
    await supabase.auth.signInWithOAuth({ provider: "github", options: { redirectTo: window.location.origin } });
  };
  const signOut = async () => {
    setWorkspaceId(null);
    setWsId(null);
    setList(null);
    await supabase.auth.signOut();
  };

  // Signed out -> no workspace data, whatever is still in state.
  const visibleList = userId ? list : null;
  const workspace = visibleList?.workspaces.find((w) => w.id === workspaceId) ?? null;

  return (
    <SessionContext.Provider value={{ session, authReady, list: visibleList, listError, workspace, chooseWorkspace,
      refreshWorkspaces, signIn, signOut }}>
      {children}
    </SessionContext.Provider>
  );
}
