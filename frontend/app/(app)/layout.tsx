"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, AnimatePresence } from "motion/react";
import {
  Waveform,
  MicrophoneStage,
  ShoppingCartSimple,
  ChartLineUp,
  Storefront,
  House,
} from "@phosphor-icons/react";

const NAV = {
  workspace: [
    { href: "/chat", label: "Voice Chat", icon: <MicrophoneStage size={18} weight="duotone" /> },
    { href: "/orders", label: "Orders", icon: <ShoppingCartSimple size={18} weight="duotone" /> },
    { href: "/catalog", label: "Catalog", icon: <Storefront size={18} weight="duotone" /> },
  ],
  system: [
    { href: "/admin", label: "Admin", icon: <ChartLineUp size={18} weight="duotone" /> },
  ],
} as const;

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  const isActive = (href: string) =>
    pathname === href || (href !== "/chat" && pathname.startsWith(href));

  const NavList = (
    <>
      {Object.entries(NAV).map(([section, items]) => (
        <div key={section}>
          <div className="sidebar-section">{section}</div>
          {items.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`sidebar-item ${isActive(item.href) ? "active" : ""}`}
              aria-current={isActive(item.href) ? "page" : undefined}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
        </div>
      ))}
    </>
  );

  return (
    <div className="shell">
      {/* ── Desktop sidebar ── */}
      <aside
        className="sidebar"
        style={{ display: "none" }}
        data-desktop-sidebar
      >
        <div className="sidebar-brand">
          <Waveform size={20} color="var(--signal)" weight="fill" />
          <span>
            CALLIOPE <span style={{ color: "var(--signal)", fontWeight: 600, letterSpacing: "0.12em" }}>AI</span>
          </span>
        </div>
        <nav className="sidebar-nav">{NavList}</nav>
        <div style={{ padding: "12px 10px", borderTop: "1px solid var(--line)" }}>
          <Link href="/" className="sidebar-item">
            <House size={17} weight="duotone" />
            Back to Home
          </Link>
        </div>
      </aside>

      {/* ── Mobile top bar ── */}
      <div style={{ display: "none" }} data-mobile-bar>
        <div className="sidebar-brand">
          <Link href="/" style={{ display: "flex", alignItems: "center", gap: 9, textDecoration: "none", color: "inherit" }}>
            <Waveform size={19} color="var(--signal)" weight="fill" />
            <span>
              CALLIOPE <span style={{ color: "var(--signal)", fontWeight: 600, letterSpacing: "0.12em" }}>AI</span>
            </span>
          </Link>
        </div>
        <nav
          style={{
            display: "flex",
            gap: 4,
            overflowX: "auto",
            padding: "8px 12px",
            borderBottom: "1px solid var(--line)",
          }}
        >
          {[...NAV.workspace, ...NAV.system].map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`sidebar-item ${isActive(item.href) ? "active" : ""}`}
              style={{ whiteSpace: "nowrap", margin: 0 }}
              aria-current={isActive(item.href) ? "page" : undefined}
            >
              {item.icon}
              {item.label}
            </Link>
          ))}
        </nav>
      </div>

      {/* ── Main content ── */}
      <main style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={pathname}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
            style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0 }}
          >
            {children}
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}
