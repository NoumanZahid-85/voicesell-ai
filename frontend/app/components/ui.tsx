// UI kit — the shared primitives of the broadcast-console design.
// One implementation for the page chrome, status badges, and the
// loading / error / empty state trio, so pages compose instead of copy.

"use client";

import type { ReactNode } from "react";
import { motion } from "motion/react";
import {
  CheckCircle,
  Clock,
  Truck,
  XCircle,
  ArrowClockwise,
  WarningCircle,
  Package,
} from "@phosphor-icons/react";
import type { OrderStatus } from "@/lib/types";

// ── Page chrome ──────────────────────────────────────────────────────

interface PageHeaderProps {
  icon: ReactNode;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}

/** The blurred console header: signal icon box + mono subtitle + actions. */
export function PageHeader({ icon, title, subtitle, actions }: PageHeaderProps) {
  return (
    <div
      style={{
        padding: "13px clamp(16px, 4vw, 28px)",
        borderBottom: "1px solid var(--line)",
        background: "var(--chrome)",
        backdropFilter: "blur(12px)",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <div className="icon-box">
          {icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <h1 style={{ fontSize: "0.98rem", fontWeight: 700, letterSpacing: "-0.01em", whiteSpace: "nowrap" }}>
            {title}
          </h1>
          {subtitle && (
            <p className="mono-id" style={{ fontSize: "0.64rem", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {actions}
    </div>
  );
}

// ── Status badges ────────────────────────────────────────────────────

export const STATUS_META: Record<OrderStatus, { label: string; led: string; icon: ReactNode }> = {
  pending:    { label: "Pending",    led: "led-warn",   icon: <Clock size={11} weight="fill" /> },
  confirmed:  { label: "Confirmed",  led: "led-info",   icon: <CheckCircle size={11} weight="fill" /> },
  shipped:    { label: "Shipped",    led: "led-violet", icon: <Truck size={11} weight="fill" /> },
  delivered:  { label: "Delivered",  led: "led-ok",     icon: <CheckCircle size={11} weight="fill" /> },
  cancelled:  { label: "Cancelled",  led: "led-err",    icon: <XCircle size={11} weight="fill" /> },
};

export function StatusBadge({ status }: { status: OrderStatus }) {
  const meta = STATUS_META[status];
  return (
    <span className={`badge badge-${status}`}>
      {meta.icon}
      {meta.label}
    </span>
  );
}

export function StockBadge({ stock }: { stock: number }) {
  const out = stock <= 0;
  const low = stock < 10;
  return (
    <span className={`badge ${out ? "badge-cancelled" : low ? "badge-pending" : "badge-delivered"}`}>
      <span className={`led ${out ? "led-err" : low ? "led-warn" : "led-ok"}`} />
      {out ? "Out of stock" : low ? "Low stock" : "In stock"}
    </span>
  );
}

// ── Loading / error / empty states ───────────────────────────────────

export function LoadingRows({ count = 4 }: { count?: number }) {
  return (
    <div style={{ display: "grid", gap: 10 }}>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="shimmer" style={{ height: 52, borderRadius: "var(--r-md)" }} />
      ))}
    </div>
  );
}

interface StatePanelProps {
  icon?: ReactNode;
  title: string;
  message?: ReactNode;
  action?: ReactNode;
}

export function StatePanel({ icon, title, message, action }: StatePanelProps) {
  return (
    <motion.div
      className="panel"
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
      style={{ padding: 40, textAlign: "center", maxWidth: 560, margin: "40px auto" }}
    >
      {icon && <div style={{ marginBottom: 12 }}>{icon}</div>}
      <div style={{ fontWeight: 650, marginBottom: 8 }}>{title}</div>
      {message && <div style={{ color: "var(--text-mid)", fontSize: "0.875rem" }}>{message}</div>}
      {action && <div style={{ marginTop: 18 }}>{action}</div>}
    </motion.div>
  );
}

export function ErrorPanel({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <StatePanel
      icon={<XCircle size={36} color="var(--danger)" weight="duotone" />}
      title="Could not load data"
      message={<p className="mono-id" style={{ fontSize: "0.74rem", marginTop: 8 }}>{message}</p>}
      action={
        onRetry && (
          <button className="btn-primary" onClick={onRetry}>
            <ArrowClockwise size={14} /> Retry
          </button>
        )
      }
    />
  );
}

export function EmptyPanel({ title, message }: { title: string; message: ReactNode }) {
  return (
    <StatePanel
      icon={<Package size={44} color="var(--text-low)" weight="duotone" />}
      title={title}
      message={message}
    />
  );
}

export function SectionHeader({ label, right }: { label: string; right?: ReactNode }) {
  return (
    <div
      style={{
        padding: "13px 20px",
        borderBottom: "1px solid var(--line)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <span className="label-mono">{label}</span>
      {right}
    </div>
  );
}

export function WarningStrip({ children }: { children: ReactNode }) {
  return (
    <div
      style={{
        padding: "10px 20px",
        borderTop: "1px solid var(--line)",
        display: "flex",
        alignItems: "center",
        gap: 8,
        background: "rgba(255,112,112,0.05)",
        color: "var(--danger)",
        fontSize: "0.78rem",
      }}
    >
      <WarningCircle size={15} weight="fill" />
      <span>{children}</span>
    </div>
  );
}