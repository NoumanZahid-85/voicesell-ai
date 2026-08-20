"use client";

import Link from "next/link";
import { motion } from "motion/react";
import {
  MicrophoneStage,
  Lightning,
  ShoppingCartSimple,
  ChartLineUp,
  ArrowRight,
  Waveform,
  MagnifyingGlass,
  Robot,
  ShoppingBagOpen,
  Storefront,
} from "@phosphor-icons/react";
import { LiveConsole, LiveChip } from "./components/LiveConsole";

const FEATURES = [
  {
    icon: <MicrophoneStage size={22} weight="duotone" />,
    title: "Voice-First Interface",
    description:
      "Speak naturally; the agent answers within 500ms via Deepgram STT and streaming TTS — with barge-in mid-response.",
    tag: "DEEPGRAM + CARTESIA",
  },
  {
    icon: <MagnifyingGlass size={22} weight="duotone" />,
    title: "RAG-Grounded Answers",
    description:
      "Every product answer is retrieved from a live Qdrant vector index and cited with scores — zero hallucinated stock.",
    tag: "QDRANT VECTOR INDEX",
  },
  {
    icon: <Robot size={22} weight="duotone" />,
    title: "Voice Order Management",
    description:
      "A LangGraph agent places, modifies, and cancels orders by voice, gated behind an explicit confirmation step.",
    tag: "LANGGRAPH AGENT",
  },
  {
    icon: <ShoppingBagOpen size={22} weight="duotone" />,
    title: "Intelligent Upsells",
    description:
      "After every confirmed order, one contextual suggestion from association rules and vector similarity. Never more than one.",
    tag: "1 PER CONVERSATION",
  },
];

const STATS = [
  { value: "<500ms", label: "P50 latency per turn" },
  { value: "100K+", label: "Products vectorised" },
  { value: "85%+", label: "RAG recall@5" },
  { value: "3-tier", label: "LLM failover chain" },
];

const PIPELINE = [
  { step: "01", label: "You speak", note: "WebRTC + VAD" },
  { step: "02", label: "We transcribe", note: "Deepgram STT" },
  { step: "03", label: "Agent reasons", note: "LangGraph + RAG" },
  { step: "04", label: "Agent answers", note: "Streaming TTS" },
];

const TICKER = [
  "Deepgram STT",
  "Cartesia TTS",
  "Qdrant Vectors",
  "LangGraph Agent",
  "Pipecat Pipeline",
  "Daily WebRTC",
  "Supabase Postgres",
  "Redis Cache",
  "LiteLLM Failover",
];

const MotionLink = motion.create(Link);
const CTA_SPRING = { type: "spring", stiffness: 420, damping: 28 } as const;

export default function LandingPage() {
  return (
    <div style={{ position: "relative", minHeight: "100dvh", zIndex: 1 }}>
      {/* ── Nav ── */}
      <nav
        style={{
          position: "fixed",
          top: 0,
          left: 0,
          right: 0,
          zIndex: 50,
          height: 62,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "0 clamp(18px, 5vw, 52px)",
          background: "var(--chrome)",
          backdropFilter: "blur(16px)",
          borderBottom: "1px solid var(--line)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
          <Waveform size={22} color="var(--signal)" weight="fill" />
          <span style={{ fontWeight: 750, fontSize: "1rem", letterSpacing: "-0.01em" }}>
            CALLIOPE <span style={{ color: "var(--signal)" }}>AI</span>
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <Link href="/admin" className="btn-ghost btn-sm">
            Admin
          </Link>
          <Link href="/chat" className="btn-primary btn-sm">
            Open Demo <ArrowRight size={13} weight="bold" />
          </Link>
        </div>
      </nav>

      {/* ── Hero: asymmetric split ── */}
      <section
        style={{
          minHeight: "100dvh",
          display: "flex",
          alignItems: "center",
          padding: "110px clamp(18px, 5vw, 52px) 60px",
        }}
      >
        <div className="container-page" style={{ maxWidth: 1180 }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "minmax(0, 1.05fr) minmax(320px, 0.95fr)",
              gap: "clamp(32px, 6vw, 88px)",
              alignItems: "center",
            }}
          >
            {/* Copy */}
            <div>
              <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }}>
                <LiveChip />
              </motion.div>

              <motion.h1
                className="display-xl"
                initial={{ opacity: 0, y: 22 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.55, delay: 0.08 }}
                style={{ marginTop: 26, marginBottom: 22, maxWidth: 560 }}
              >
                Talk to <span className="emphasis">your store.</span>
              </motion.h1>

              <motion.p
                initial={{ opacity: 0, y: 22 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.55, delay: 0.16 }}
                style={{
                  color: "var(--text-mid)",
                  fontSize: "1.02rem",
                  lineHeight: 1.65,
                  maxWidth: 520,
                  marginBottom: 34,
                }}
              >
                CALLIOPE answers product questions, takes orders, and suggests upsells
                over a live voice call — grounded in your catalog, with answers starting
                in under 500ms.
              </motion.p>

              <motion.div
                initial={{ opacity: 0, y: 22 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.55, delay: 0.24 }}
                style={{ display: "flex", gap: 12, flexWrap: "wrap" }}
              >
                <MotionLink
                  href="/chat"
                  className="btn-primary"
                  whileHover={{ y: -2, scale: 1.02 }}
                  whileTap={{ scale: 0.97 }}
                  transition={CTA_SPRING}
                  style={{ fontSize: "0.92rem", padding: "11px 26px" }}
                >
                  <MicrophoneStage size={17} weight="fill" />
                  Open Voice Chat
                </MotionLink>
                <MotionLink
                  href="/catalog"
                  className="btn-ghost"
                  whileHover={{ y: -2, scale: 1.02 }}
                  whileTap={{ scale: 0.97 }}
                  transition={CTA_SPRING}
                  style={{ fontSize: "0.92rem", padding: "11px 26px" }}
                >
                  <Storefront size={17} />
                  Browse Catalog
                </MotionLink>
                <MotionLink
                  href="/orders"
                  className="btn-ghost"
                  whileHover={{ y: -2, scale: 1.02 }}
                  whileTap={{ scale: 0.97 }}
                  transition={CTA_SPRING}
                  style={{ fontSize: "0.92rem", padding: "11px 26px" }}
                >
                  <ShoppingCartSimple size={17} />
                  View Orders
                </MotionLink>
              </motion.div>
            </div>

            {/* Live console — real backend data */}
            <motion.div
              initial={{ opacity: 0, y: 28, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.6, delay: 0.2 }}
            >
              <LiveConsole />
              <p
                className="label-mono"
                style={{
                  textAlign: "center",
                  marginTop: 14,
                  letterSpacing: "0.18em",
                }}
              >
                LIVE FROM THE RUNNING BACKEND
              </p>
            </motion.div>
          </div>
        </div>
      </section>

      {/* ── Broadcast ticker: the stack, live ── */}
      <section className="ticker" aria-hidden="true">
        <div className="ticker-track">
          {[...TICKER, ...TICKER].map((item, i) => (
            <span key={i} className="ticker-item">
              {item}
            </span>
          ))}
        </div>
      </section>

      {/* ── Stats: hairline ledger, not cards ── */}
      <section style={{ padding: "8px clamp(18px, 5vw, 52px) 72px" }}>
        <div className="container-page" style={{ maxWidth: 1180 }}>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              border: "1px solid var(--line)",
              borderRadius: "var(--r-lg)",
              background: "var(--ink-1)",
              overflow: "hidden",
            }}
          >
            {STATS.map((s, i) => (
              <motion.div
                key={s.label}
                initial={{ opacity: 0 }}
                whileInView={{ opacity: 1 }}
                viewport={{ once: true }}
                transition={{ duration: 0.4, delay: i * 0.08 }}
                style={{
                  padding: "22px 26px",
                  borderRight: i < STATS.length - 1 ? "1px solid var(--line)" : "none",
                  borderBottom: "1px solid var(--line)",
                }}
              >
                <div className="stat-number" style={{ color: "var(--signal)" }}>
                  {s.value}
                </div>
                <div className="label-mono" style={{ marginTop: 8, textTransform: "none", letterSpacing: "0.04em" }}>
                  {s.label}
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features: divided editorial list ── */}
      <section style={{ padding: "0 clamp(18px, 5vw, 52px) 88px" }}>
        <div className="container-page" style={{ maxWidth: 1180 }}>
          <motion.div
            initial={{ opacity: 0, y: 14 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.4 }}
            style={{ marginBottom: 30 }}
          >
            <span className="label-mono">What it does</span>
            <h2 className="display-lg" style={{ marginTop: 8 }}>
              A full sales flow, <span className="emphasis">voice-native</span>
            </h2>
          </motion.div>

          <div style={{ borderTop: "1px solid var(--line)" }}>
            {FEATURES.map((f, i) => (
              <motion.div
                key={f.title}
                className="ledger-row"
                initial={{ opacity: 0, y: 14 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.4, delay: i * 0.06 }}
                style={{ display: "grid", gridTemplateColumns: "44px minmax(0,1fr) auto", gap: 20, alignItems: "start" }}
              >
                <div
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: "var(--r-md)",
                    background: "var(--signal-soft)",
                    border: "1px solid rgba(69, 80, 229, 0.18)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    color: "var(--signal)",
                    marginTop: 2,
                  }}
                >
                  {f.icon}
                </div>
                <div>
                  <h3 style={{ fontSize: "1.02rem", fontWeight: 700, marginBottom: 5 }}>{f.title}</h3>
                  <p style={{ color: "var(--text-mid)", fontSize: "0.875rem", lineHeight: 1.6, maxWidth: 620 }}>
                    {f.description}
                  </p>
                </div>
                <span
                  className="chip"
                  style={{ marginTop: 6, whiteSpace: "nowrap" }}
                >
                  {f.tag}
                </span>
              </motion.div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Pipeline band ── */}
      <section style={{ padding: "0 clamp(18px, 5vw, 52px) 96px" }}>
        <div className="container-page" style={{ maxWidth: 1180 }}>
          <div
            className="panel"
            style={{ padding: "34px clamp(24px, 4vw, 56px)", display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 20 }}
          >
            {PIPELINE.map((p, i) => (
              <div key={p.step} style={{ position: "relative", padding: i < PIPELINE.length - 1 ? "0 24px 0 0" : 0 }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                  <span className="label-mono" style={{ color: "var(--signal)" }}>
                    {p.step}
                  </span>
                  <h3 style={{ fontSize: "0.95rem", fontWeight: 650 }}>{p.label}</h3>
                </div>
                <p className="mono-id" style={{ marginTop: 5, fontSize: "0.7rem" }}>
                  {p.note}
                </p>
                {i < PIPELINE.length - 1 && (
                  <ArrowRight
                    size={16}
                    aria-hidden="true"
                    style={{
                      position: "absolute",
                      right: -6,
                      top: 6,
                      color: "var(--text-low)",
                    }}
                  />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── CTA ── */}
      <section style={{ padding: "0 clamp(18px, 5vw, 52px) 72px" }}>
        <div className="container-page" style={{ maxWidth: 1180 }}>
          <motion.div
            className="panel"
            initial={{ opacity: 0, y: 18 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.45 }}
            style={{
              maxWidth: 760,
              margin: "0 auto",
              padding: "46px 40px",
              textAlign: "center",
              borderColor: "rgba(69, 80, 229, 0.14)",
              background: "linear-gradient(180deg, rgba(69,80,229,0.05), var(--ink-2) 45%), var(--ink-2)",
            }}
          >
            <h2 className="display-md" style={{ marginBottom: 12 }}>
              The pipeline is running — <span className="emphasis">talk to it</span>
            </h2>
            <p style={{ color: "var(--text-mid)", marginBottom: 28, fontSize: "0.95rem" }}>
              Backend verified live: Postgres, Qdrant, and the LangGraph agent are all
              up. Jump into the demo.
            </p>
            <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
              <Link href="/chat" className="btn-primary">
                <Lightning size={16} weight="fill" /> Try the Demo
              </Link>
              <Link href="/admin" className="btn-ghost">
                <ChartLineUp size={16} /> System Console
              </Link>
            </div>
            <div
              style={{
                display: "flex",
                gap: 20,
                justifyContent: "center",
                marginTop: 26,
                flexWrap: "wrap",
              }}
            >
              {["Voice + text modes", "RAG-grounded answers", "Order management", "Upsell engine"].map((f) => (
                <span key={f} className="chip" style={{ background: "transparent", borderColor: "var(--line-strong)" }}>
                  {f}
                </span>
              ))}
            </div>
          </motion.div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer
        style={{
          borderTop: "1px solid var(--line)",
          padding: "22px clamp(18px, 5vw, 52px)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          flexWrap: "wrap",
        }}
      >
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <Waveform size={16} color="var(--signal)" weight="fill" />
          <span style={{ fontSize: "0.85rem", fontWeight: 650 }}>CALLIOPE</span>
        </span>
        <span className="mono-id" style={{ fontSize: "0.66rem" }}>
          PHASE 6 FRONTEND — NEXT.JS · PIPECAT · LANGGRAPH · QDRANT
        </span>
      </footer>
    </div>
  );
}
