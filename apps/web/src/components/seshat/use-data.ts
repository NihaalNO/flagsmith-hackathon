"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Fetch on mount (and whenever `path` changes), with a manual reload. */
export function useData<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedPath, setLoadedPath] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    api<T>(path)
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e: Error) => { if (!cancelled) setError(e.message); })
      .finally(() => { if (!cancelled) setLoadedPath(`${path}#${nonce}`); });
    return () => { cancelled = true; };
  }, [path, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const loading = Boolean(path) && loadedPath !== `${path}#${nonce}`;
  return { data: path ? data : null, error, loading, reload, setData };
}
