import { useCallback, useEffect, useRef, useState } from "react";
import { api, Run, TERMINAL, TraceEvent } from "./api";

/** Loads a run and follows its trace live: Server-Sent Events first, polling as a fallback
 *  (some proxies buffer SSE), with resume from the last seen event id. */
export function useRun(runId: string) {
  const [run, setRun] = useState<Run | null>(null);
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const lastId = useRef(0);
  const seen = useRef(new Set<number>());

  const addEvents = useCallback((incoming: TraceEvent[]) => {
    const fresh = incoming.filter((e) => !seen.current.has(e.id));
    if (!fresh.length) return;
    fresh.forEach((e) => seen.current.add(e.id));
    lastId.current = Math.max(lastId.current, ...fresh.map((e) => e.id));
    setEvents((prev) => [...prev, ...fresh].sort((a, b) => a.id - b.id));
  }, []);

  const refreshRun = useCallback(async () => {
    try {
      const r = await api.getRun(runId);
      setRun(r);
      return r;
    } catch (e) {
      setError((e as Error).message);
      return null;
    }
  }, [runId]);

  useEffect(() => {
    let cancelled = false;
    let source: EventSource | null = null;
    let pollTimer: number | undefined;
    let watchdog: number | undefined;
    lastId.current = 0;
    seen.current = new Set();
    setEvents([]);
    setRun(null);
    setError(null);

    const poll = async () => {
      if (cancelled) return;
      try {
        addEvents(await api.events(runId, lastId.current));
        const r = await refreshRun();
        if (r && (TERMINAL.includes(r.status) || r.status === "awaiting_approval")) {
          addEvents(await api.events(runId, lastId.current));
          return;
        }
      } catch { /* transient: keep polling */ }
      pollTimer = window.setTimeout(poll, 1500);
    };

    const startPolling = () => {
      source?.close();
      source = null;
      window.clearTimeout(watchdog);
      if (pollTimer === undefined) poll();
    };

    const armWatchdog = () => {
      window.clearTimeout(watchdog);
      watchdog = window.setTimeout(startPolling, 12000); // no live data for 12 s -> poll instead
    };

    (async () => {
      const r = await refreshRun();
      if (cancelled || !r) return;
      addEvents(await api.events(runId, 0).catch(() => []));
      if (TERMINAL.includes(r.status) || r.status === "awaiting_approval") return;
      if (typeof EventSource === "undefined") return startPolling();
      source = new EventSource(api.streamUrl(runId, lastId.current));
      armWatchdog();
      source.addEventListener("trace", (msg) => {
        armWatchdog();
        addEvents([JSON.parse((msg as MessageEvent).data)]);
        const t = JSON.parse((msg as MessageEvent).data).type as string;
        if (["final", "awaiting_approval", "run_finished", "run_failed", "run_cancelled", "plan_created", "stopped"].includes(t)) refreshRun();
      });
      source.addEventListener("end", () => {
        window.clearTimeout(watchdog);
        source?.close();
        refreshRun();
      });
      source.onerror = () => {
        if (source && source.readyState === EventSource.CLOSED) startPolling();
      };
    })();

    return () => {
      cancelled = true;
      source?.close();
      window.clearTimeout(pollTimer);
      window.clearTimeout(watchdog);
    };
  }, [runId, addEvents, refreshRun]);

  /** Call after approving a plan: the run continues, so start following it again. */
  const follow = useCallback(() => {
    const tick = async () => {
      addEvents(await api.events(runId, lastId.current).catch(() => []));
      const r = await refreshRun();
      if (r && !TERMINAL.includes(r.status) && r.status !== "awaiting_approval") window.setTimeout(tick, 1500);
      else addEvents(await api.events(runId, lastId.current).catch(() => []));
    };
    window.setTimeout(tick, 600);
  }, [runId, addEvents, refreshRun]);

  return { run, events, error, refreshRun, follow };
}
