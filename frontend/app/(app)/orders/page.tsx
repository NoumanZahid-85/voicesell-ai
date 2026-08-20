"use client";

import { useState, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  ArrowClockwise,
  CaretDown,
  CaretUp,
  MagnifyingGlass,
  ShoppingCartSimple,
} from "@phosphor-icons/react";
import { api, DEMO_CUSTOMER_ID } from "@/lib/api";
import type { Order, OrderStatus } from "@/lib/types";
import {
  STATUS_META,
  StatusBadge,
  PageHeader,
  LoadingRows,
  ErrorPanel,
  EmptyPanel,
} from "@/app/components/ui";
import { formatCurrency, formatOrderDate } from "@/lib/format";

function OrderRow({ order }: { order: Order }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      <motion.tr
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -6 }}
        transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
        onClick={() => setExpanded(!expanded)}
        style={{ cursor: "pointer" }}
        aria-expanded={expanded}
      >
        <td>
          <code className="mono-id">{order.id.slice(0, 8)}…</code>
        </td>
        <td><StatusBadge status={order.status} /></td>
        <td style={{ fontWeight: 600, fontFamily: "var(--font-mono)", fontSize: "0.82rem" }}>
          {formatCurrency(order.total_amount)}
        </td>
        <td className="mono-id" style={{ fontSize: "0.72rem" }}>
          {formatOrderDate(order.created_at)}
        </td>
        <td style={{ width: 32, color: "var(--text-low)" }}>
          {expanded ? <CaretUp size={13} /> : <CaretDown size={13} />}
        </td>
      </motion.tr>
      <AnimatePresence>
        {expanded && (
          <tr>
            <td colSpan={5} style={{ padding: 0, border: "none" }}>
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{ duration: 0.22, ease: [0.16, 1, 0.3, 1] }}
                style={{ overflow: "hidden" }}
              >
                <div
                  style={{
                    padding: "14px 18px",
                    background: "rgba(255, 77, 122, 0.04)",
                    borderBottom: "1px solid var(--line)",
                  }}
                >
                  {order.items && order.items.length > 0 ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 9 }}>
                      {order.items.map((item) => (
                        <div
                          key={item.id}
                          style={{
                            display: "flex",
                            justifyContent: "space-between",
                            alignItems: "center",
                            gap: 12,
                          }}
                        >
                          <div style={{ minWidth: 0 }}>
                            <div style={{ fontSize: "0.86rem", fontWeight: 550 }}>
                              {item.product_name ?? `Product ${item.product_id.slice(0, 8)}`}
                            </div>
                            <div className="mono-id" style={{ fontSize: "0.68rem" }}>
                              QTY {item.quantity} × {formatCurrency(item.unit_price)}
                            </div>
                          </div>
                          <div style={{ fontWeight: 650, fontFamily: "var(--font-mono)", fontSize: "0.82rem", whiteSpace: "nowrap" }}>
                            {formatCurrency(item.subtotal)}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="mono-id" style={{ fontSize: "0.74rem" }}>
                      NO LINE ITEMS AVAILABLE FOR THIS ORDER
                    </p>
                  )}
                </div>
              </motion.div>
            </td>
          </tr>
        )}
      </AnimatePresence>
    </>
  );
}

export default function OrdersPage() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [filterStatus, setFilterStatus] = useState<OrderStatus | "all">("all");

  const fetchOrders = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.orders(DEMO_CUSTOMER_ID);
      setOrders(data.orders);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const run = async () => {
      await fetchOrders();
    };
    run();
  }, [fetchOrders]);

  const filtered = orders.filter((o) => {
    const matchSearch = !search || o.id.includes(search) || o.status.includes(search);
    const matchStatus = filterStatus === "all" || o.status === filterStatus;
    return matchSearch && matchStatus;
  });

  const STATUS_OPTIONS: Array<OrderStatus | "all"> = ["all", "pending", "confirmed", "shipped", "delivered", "cancelled"];

  const counts: Record<string, number> = { all: orders.length };
  orders.forEach((o) => { counts[o.status] = (counts[o.status] ?? 0) + 1; });

  const totalValue = orders
    .filter((o) => o.status !== "cancelled")
    .reduce((sum, o) => sum + (o.total_amount ?? 0), 0);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100dvh", minWidth: 0 }}>
      {/* ── Header ── */}
      <PageHeader
        icon={<ShoppingCartSimple size={17} weight="duotone" />}
        title="Order History"
        subtitle={`CUSTOMER ${DEMO_CUSTOMER_ID.slice(0, 8).toUpperCase()} · ${orders.length} ORDERS · ${formatCurrency(totalValue)} TOTAL`}
        actions={
          <button className="btn-ghost btn-sm" onClick={fetchOrders}>
            <ArrowClockwise size={13} /> Refresh
          </button>
        }
      />

      {/* ── Summary strip ── */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(120px, 1fr))",
          borderBottom: "1px solid var(--line)",
          background: "rgba(15, 18, 24, 0.5)",
        }}
      >
        {(["pending", "confirmed", "shipped", "delivered", "cancelled"] as OrderStatus[]).map((s) => (
          <button
            key={s}
            onClick={() => setFilterStatus(filterStatus === s ? "all" : s)}
            aria-pressed={filterStatus === s}
            className="pill"
            style={{
              border: "none",
              borderRadius: 0,
              justifyContent: "flex-start",
              padding: "11px 18px",
              borderRight: "1px solid var(--line)",
            }}
          >
            <span className={`led ${STATUS_META[s].led}`} />
            <span style={{ textTransform: "capitalize", letterSpacing: "0.02em" }}>{s}</span>
            <span style={{ marginLeft: "auto", fontFamily: "var(--font-mono)", fontSize: "0.78rem", fontWeight: 650 }}>
              {counts[s] ?? 0}
            </span>
          </button>
        ))}
      </div>

      {/* ── Filters ── */}
      <div
        style={{
          padding: "11px clamp(16px, 4vw, 28px)",
          borderBottom: "1px solid var(--line)",
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          flexShrink: 0,
          background: "rgba(10, 12, 16, 0.4)",
        }}
      >
        <div style={{ position: "relative", flex: "1 1 200px", maxWidth: 300 }}>
          <MagnifyingGlass
            size={14}
            style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "var(--text-low)" }}
          />
          <input
            className="input-field"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search order ID…"
            style={{ paddingLeft: 32, paddingTop: 8, paddingBottom: 8 }}
            id="order-search"
            aria-label="Search orders"
          />
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {STATUS_OPTIONS.map((s) => (
            <button
              key={s}
              onClick={() => setFilterStatus(s)}
              aria-pressed={filterStatus === s}
              className="pill"
            >
              {s === "all" ? "All" : s.charAt(0).toUpperCase() + s.slice(1)}
              <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.68rem", opacity: 0.8 }}>{counts[s] ?? 0}</span>
            </button>
          ))}
        </div>
      </div>

      {/* ── Content ── */}
      <div className="scroll-area" style={{ flex: 1, padding: "18px clamp(16px, 4vw, 28px)" }}>
        {loading ? (
          <LoadingRows count={4} />
        ) : error ? (
          <ErrorPanel
            message={`${error} — backend should be on localhost:8000`}
            onRetry={fetchOrders}
          />
        ) : filtered.length === 0 ? (
          <EmptyPanel
            title={orders.length === 0 ? "No orders yet" : "No orders match your filters"}
            message={
              orders.length === 0
                ? 'Place your first order via the Voice Chat — just say "I want to order 2 wireless keyboards".'
                : "Try clearing the search or status filter."
            }
          />
        ) : (
          <div className="panel" style={{ overflow: "hidden" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>Order ID</th>
                  <th>Status</th>
                  <th>Total</th>
                  <th>Date</th>
                  <th style={{ width: 32 }} />
                </tr>
              </thead>
              <tbody>
                <AnimatePresence>
                  {filtered.map((order) => (
                    <OrderRow key={order.id} order={order} />
                  ))}
                </AnimatePresence>
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
