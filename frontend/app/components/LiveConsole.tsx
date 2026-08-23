"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { HealthData } from "@/lib/types";

const ok = (v: string | undefined) => v === "ok" || v === "connected";

/**
 * LiveConsole — the landing hero's instrument panel. Polls the real
 * backend: health (db/qdrant), active voice sessions, and measured
 * request latency. Every number on this panel is live data.
 */
export function LiveConsole() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [sessions, setSessions] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;

    const poll = async () => {
      const t0 = performance.now();
      try {
        const [h, s] = await Promise.all([
          api.health(),
          api.voice.sessions().catch(() => []),
        ]);
        if (!alive) return;
        setHealth(h);
        setLatency(Math.round(performance.now() - t0));
        setSessions(s.length);
        setFailed(false);
      } catch {
        if (!alive) return;
        setHealth(null);
        setLatency(null);
        setFailed(true);
      }
      if (alive) timer = setTimeout(poll, 4000);
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  return (
    <div className="panel" role="status" aria-label="Backend live status">
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "12px 18px",
          borderBottom: "1px solid var(--line)",
        }}
      >
        <span className="label-mono">System Console</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span className={`led ${failed ? "led-err" : "led-signal led-live"}`} />
          <span className="label-mono" style={{ color: failed ? "var(--danger)" : "var(--signal)" }}>
            {failed ? "Offline" : "Live"}
          </span>
        </span>
      </div>

      <div style={{ padding: "6px 18px" }}>
        {[
          { k: "Backend", v: failed ? "—" : latency !== null ? `${latency} ms` : "…", led: failed ? "err" : latency !== null ? "ok" : "dim", live: !failed },
          { k: "Postgres", v: health ? health.db : "…", led: health ? (ok(health.db) ? "ok" : "err") : "dim", live: false },
          { k: "Qdrant", v: health ? health.qdrant : "…", led: health ? (ok(health.qdrant) ? "ok" : "err") : "dim", live: false },
          { k: "Active sessions", v: sessions !== null ? String(sessions) : "…", led: "dim", live: false },
        ].map((row) => (
          <div key={row.k} className="ledger-row" style={{ padding: "11px 0" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 9 }}>
              <span className={`led led-${row.led} ${row.live ? "led-live" : ""}`} />
              <span className="label-mono" style={{ textTransform: "none", letterSpacing: "0.06em" }}>
                {row.k}
              </span>
            </span>
            <span className="mono-id" style={{ color: "var(--text-hi)", fontSize: "0.78rem" }}>
              {row.v}
            </span>
          </div>
        ))}
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          padding: "10px 18px 14px",
          borderTop: "1px solid var(--line)",
        }}
      >
        <div className={`vu ${failed ? "" : "live"}`} aria-hidden="true">
          {Array.from({ length: 16 }).map((_, i) => (
            <span key={i} />
          ))}
        </div>
        <span className="mono-id" style={{ fontSize: "0.62rem" }}>
          POLL /api/v1/health · 4S
        </span>
      </div>
    </div>
  );
}

/** LiveChip — the hero's LIVE badge, backed by a real health check. */
export function LiveChip() {
  const [state, setState] = useState<"checking" | "live" | "down">("checking");

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        await api.health();
        // Any HTTP response means the backend itself is reachable — the
        // System Console below already shows Postgres/Qdrant health per-row,
        // so a "degraded" dependency must not flip this badge to Offline.
        if (alive) setState("live");
      } catch {
        if (alive) setState("down");
      }
    };
    check();
    const timer = setInterval(check, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        padding: "5px 14px",
        borderRadius: "var(--r-pill)",
        border: "1px solid var(--line-strong)",
        background: "var(--signal-soft)",
        fontFamily: "var(--font-mono)",
        fontSize: "0.68rem",
        fontWeight: 600,
        letterSpacing: "0.14em",
        color: "var(--signal)",
        textTransform: "uppercase",
      }}
    >
      <span
        className={`led ${
          state === "live" ? "led-signal led-live" : state === "down" ? "led-err" : "led-dim"
        }`}
      />
      {state === "checking" ? "Checking…" : state === "live" ? "Backend Live" : "Backend Offline"}
    </span>
  );
}
