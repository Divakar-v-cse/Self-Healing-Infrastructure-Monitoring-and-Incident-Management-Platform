import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Fetch on mount and then on an interval, keeping the previous payload visible
 * while refreshing so the dashboard does not flicker.
 */
export function usePolling<T>(fetcher: () => Promise<T>, intervalMs = 15000) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await fetcherRef.current();
      setData(result);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Request failed');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tick = async (silent: boolean) => {
      if (cancelled) return;
      await load(silent);
    };
    void tick(false);
    if (intervalMs <= 0) return () => { cancelled = true; };
    const timer = window.setInterval(() => void tick(true), intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [load, intervalMs]);

  return { data, error, loading, refresh: () => load(true), reload: () => load(false) };
}

export function useAsyncAction() {
  const [pending, setPending] = useState<string | null>(null);

  const run = useCallback(async (key: string, action: () => Promise<void>) => {
    setPending(key);
    try {
      await action();
    } finally {
      setPending(null);
    }
  }, []);

  return { pending, run, isPending: (key: string) => pending === key };
}
