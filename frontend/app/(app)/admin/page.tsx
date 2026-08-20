"use client";

import { useState, useEffect, useCallback } from "react";
import { motion } from "motion/react";
import { animate } from "motion";
import {
  ShoppingCartSimple,
  ChartLineUp,
  Package,
  ArrowClockwise,
  Heartbeat,
  MicrophoneStage,
} from "@phosphor-icons/react";
import { api, DEMO_CUSTOMER_ID } from "@/lib/api";
import type { HealthData, Order, Product, VoiceSession } from "@/lib/types";
import { PageHeader } from "@/app/components/ui";
import { formatCurrency } from "@/lib/format";

function LedRow({ label, value, status }: { label: string; value: string; status: string }) {
  const ok = status === "ok" || status === "connected";
  const unknown = status === "unknown" || status === "";
  return (
    <div className="ledger-row" style={{ padding: "10px 0" }}>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
        <span className={`led ${unknown ? "led-dim" : ok ? "led-ok led-live" : "led-err"}`} />
        <span className="label-mono" style={{ textTransform: "none", letterSpacing: "0.06em" }}>{label}</span>
      </span>
      <span className="mono-id" style={{ color: "var(--text-hi)", fontSize: "0.78rem" }}>
        {unknown ? "…" : value}
      </span>
    </div>
  );
}

/** Count-up stat: data arrival is the event, the number narrates it. */
function StatValue({ value }: { value: string }) {
  const numeric = parseFloat(value.replace(/[^0-9.\-]/g, ""));
  if (Number.isNaN(numeric) || value === "…") return <>{value}</>;
  return <CountUp value={value} numeric={numeric} />;
}

function CountUp({ value, numeric }: { value: string; numeric: number }) {
  const [display, setDisplay] = useState("0");
  const isCurrency = value.includes("$");

  useEffect(() => {
    const controls = animate(0, numeric, {
      duration: 0.7,
      ease: [0.16, 1, 0.3, 1],
      onUpdate: (v) => setDisplay(isCurrency ? formatCurrency(v) : String(Math.round(v))),
    });
    return () => controls.stop();
  }, [numeric, isCurrency]);

  return <>{display}</>;
}

export default function AdminPage() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [sessions, setSessions] = useState<VoiceSession[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [h, s, o, p] = await Promise.allSettled([
        api.health(),
        api.voice.sessions(),
        api.orders(DEMO_CUSTOMER_ID),
        api.products({ limit: 500 }),
      ]);
      if (h.status === "fulfilled") setHealth(h.value);
      if (s.status === "fulfilled") setSessions(s.value);
      if (o.status === "fulfilled") setOrders(o.value.orders);
      if (p.status === "fulfilled") setProducts(p.value.products);
    } catch {
      /* per-endpoint failures are surfaced by the allSettled branches above */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const run = async () => { if (!cancelled) await refresh(); };
    run();
    const iv = setInterval(run, 15_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, [refresh]);

  const activeOrders = orders.filter((o) => o.status !== "cancelled");
  const revenue = activeOrders.reduce((sum, o) => sum + (o.total_amount ?? 0), 0);

  const STATS = [
    {
      title: "Orders",
      value: loading ? "…" : String(orders.length),
      sub: `${activeOrders.length} active`,
      icon: <ShoppingCartSimple size={18} weight="duotone" />,
      live: true,
    },
    {
      title: "Revenue",
      value: loading ? "…" : formatCurrency(revenue),
      sub: "excl. cancelled",
      icon: <ChartLineUp size={18} weight="duotone" />,
      live: true,
    },
    {
      title: "Products",
      value: loading ? "…" : String(products.length),
      sub: `${products.filter((p) => p.stock_quantity < 10).length} low stock`,
      icon: <Package size={18} weight="duotone" />,
      live: true,
    },
    {
      title: "Voice Sessions",
      value: loading ? "…" : String(sessions.length),
      sub: "currently active",
      icon: <MicrophoneStage size={18} weight="duotone" />,
      live: true,
    },
  ];

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100dvh", minWidth: 0 }}>
      {/* ── Header ── */}
      <PageHeader
        icon={<Heartbeat size={17} weight="duotone" />}
        title="Admin Console"
        subtitle="SYSTEM HEALTH · LIVE DATA · 15S POLL"
        actions={
          <button className="btn-ghost btn-sm" onClick={refresh}>
            <ArrowClockwise size={13} /> Refresh
          </button>
        }
      />

      <div className="scroll-area" style={{ flex: 1, padding: "20px clamp(16px, 4vw, 28px)" }}>
        {/* ── Service health ── */}
        <motion.div
          className="panel"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          style={{ padding: "6px 22px 10px", marginBottom: 18 }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "10px 0",
              borderBottom: "1px solid var(--line)",
            }}
          >
            <span className="label-mono">Service Health</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
              <span className={`led ${health?.status === "ok" ? "led-ok led-live" : health ? "led-err" : "led-dim"}`} />
              <span className="label-mono" style={{ color: health?.status === "ok" ? "var(--ok)" : "var(--text-low)" }}>
                {loading ? "POLLING…" : health?.status === "ok" ? "ALL SYSTEMS NOMINAL" : "CHECKING…"}
              </span>
            </span>
          </div>
          <LedRow label="API" value={health?.status ?? "…"} status={health?.status ?? "unknown"} />
          <LedRow label="Postgres" value={health?.db ?? "…"} status={health?.db ?? "unknown"} />
          <LedRow label="Qdrant" value={health?.qdrant ?? "…"} status={health?.qdrant ?? "unknown"} />
        </motion.div>

        {/* ── Stats grid ── */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
            gap: 12,
            marginBottom: 18,
          }}
        >
          {STATS.map((s, i) => (
            <motion.div
              key={s.title}
              className="panel panel-hover"
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.35, delay: i * 0.05 }}
              style={{ padding: "18px 20px" }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 12,
                }}
              >
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: "var(--r-md)",
                    background: s.live ? "var(--signal-soft)" : "var(--ink-3)",
                    border: `1px solid ${s.live ? "rgba(201,241,105,0.18)" : "var(--line)"}`,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: s.live ? "var(--signal)" : "var(--text-low)",
                  }}
                >
                  {s.icon}
                </div>
                {s.live && <span className={`led ${s.value === "…" ? "led-dim" : "led-signal led-live"}`} />}
              </div>
              <div className="stat-number" style={{ fontSize: "1.55rem", marginBottom: 3 }}>
                <StatValue value={s.value} />
              </div>
              <div style={{ fontSize: "0.82rem", color: "var(--text-mid)", fontWeight: 550 }}>{s.title}</div>
              {s.sub && <div className="mono-id" style={{ fontSize: "0.62rem", marginTop: 3 }}>{s.sub.toUpperCase()}</div>}
            </motion.div>
          ))}
        </div>

        {/* ── Active sessions ── */}
        <motion.div
          className="panel"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          style={{ marginBottom: 18, overflow: "hidden" }}
        >
          <div
            style={{
              padding: "13px 20px",
              borderBottom: "1px solid var(--line)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <span className="label-mono">Active Voice Sessions</span>
            <span className="chip">{sessions.length}</span>
          </div>
          {sessions.length === 0 ? (
            <div style={{ padding: "34px 20px", textAlign: "center" }}>
              <MicrophoneStage size={34} color="var(--text-low)" weight="duotone" style={{ marginBottom: 10 }} />
              <div style={{ fontSize: "0.9rem", color: "var(--text-mid)", fontWeight: 550 }}>
                No active voice sessions
              </div>
              <p className="mono-id" style={{ fontSize: "0.68rem", marginTop: 6 }}>
                START ONE FROM THE VOICE CHAT TAB
              </p>
            </div>
          ) : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Session ID</th>
                  <th>Room</th>
                  <th>Age</th>
                  <th>Turns</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr key={s.session_id}>
                    <td><code className="mono-id">{s.session_id.slice(0, 8)}…</code></td>
                    <td className="mono-id" style={{ fontSize: "0.72rem" }}>{s.room_name}</td>
                    <td className="mono-id" style={{ fontSize: "0.72rem" }}>{Math.round(s.age_seconds)}s</td>
                    <td><span className="badge badge-confirmed">{s.turn_count} turns</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </motion.div>
      </div>
    </div>
  );
}
