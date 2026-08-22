"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "motion/react";
import {
  MicrophoneStage,
  Stop,
  PaperPlaneTilt,
  Spinner,
  Microphone,
  ChatText,
  ArrowClockwise,
  WarningCircle,
} from "@phosphor-icons/react";
import { api } from "@/lib/api";
import type { ChatSource } from "@/lib/types";
import { PageHeader } from "@/app/components/ui";
import { formatCurrency, formatTime } from "@/lib/format";

type Mode = "text" | "voice";
type VoiceState = "idle" | "connecting" | "listening" | "error";

interface Message {
  id: string;
  role: "user" | "agent";
  text: string;
  sources?: ChatSource[];
  ts: Date;
}

interface Caption {
  role: "user" | "agent";
  text: string;
}

function useSessionId() {
  const [id] = useState(() => `sess-${Math.random().toString(36).slice(2)}`);
  return id;
}

const VOICE_LABEL: Record<VoiceState, string> = {
  idle: "Standby — ready to connect",
  connecting: "Provisioning WebRTC room…",
  listening: "Live — agent is listening",
  error: "Voice pipeline unavailable",
};

export default function ChatPage() {
  const sessionId = useSessionId();
  const [mode, setMode] = useState<Mode>("text");
  const [messages, setMessages] = useState<Message[]>([
    {
      id: "welcome",
      role: "agent",
      text: "Hi! I'm CALLIOPE. Ask me anything about our product catalog, or say 'start voice' to switch to voice mode. I can also help you place and manage orders.",
      ts: new Date(),
    },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [roomUrl, setRoomUrl] = useState<string | null>(null);
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [caption, setCaption] = useState<Caption | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const callRef = useRef<any>(null);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // ── Text chat ──────────────────────────────────────────────────────
  const sendText = useCallback(async () => {
    const msg = input.trim();
    if (!msg || loading) return;
    setInput("");
    setMessages((prev) => [
      ...prev,
      { id: Date.now().toString(), role: "user", text: msg, ts: new Date() },
    ]);
    setLoading(true);
    try {
      const data = await api.chat(msg, sessionId);
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: "agent",
          text: data.reply,
          sources: data.sources,
          ts: new Date(),
        },
      ]);
    } catch (err: unknown) {
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: "agent",
          text: `Connection error: ${err instanceof Error ? err.message : String(err)}. Make sure the backend is running.`,
          ts: new Date(),
        },
      ]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendText();
    }
  };

  // ── Voice session ──────────────────────────────────────────────────
  const startVoice = useCallback(async () => {
    setVoiceState("connecting");
    setConnError(null);
    setCaption(null);
    try {
      const data = await api.voice.connect();
      setRoomUrl(data.room_url);
      setVoiceSessionId(data.session_id);

      // Headless call object (no visible Daily UI) — this is what actually
      // requests mic permission and publishes audio. The earlier hidden
      // 1x1 iframe pointed at Daily's *prebuilt* room, which requires a
      // visible "join" click to ever start streaming mic audio; since the
      // iframe was invisible, that click could never happen and the
      // agent never received any audio.
      const DailyMod = await import("@daily-co/daily-js");
      const Daily = DailyMod.default;
      const call = Daily.createCallObject({
        audioSource: true,
        videoSource: false,
      });
      callRef.current = call;

      call.on("app-message", (ev: { data?: Caption }) => {
        if (ev?.data?.role && typeof ev.data.text === "string") {
          if (ev.data.role === "user") {
            setCaption({ role: "user", text: ev.data.text });
          } else {
            // Agent replies stream in sentence-by-sentence — append so the
            // caption grows the way subtitles do, rather than replacing.
            setCaption((prev) =>
              prev?.role === "agent"
                ? { role: "agent", text: `${prev.text} ${ev.data!.text}`.trim() }
                : { role: "agent", text: ev.data!.text }
            );
          }
        }
      });

      call.on("left-meeting", () => {
        setVoiceState((s) => (s === "listening" ? "idle" : s));
      });

      call.on("error", (ev: { errorMsg?: string }) => {
        setVoiceState("error");
        setConnError(ev?.errorMsg || "Voice call encountered an error.");
      });

      await call.join({ url: data.room_url });

      setVoiceState("listening");
      setMessages((prev) => [
        ...prev,
        {
          id: Date.now().toString(),
          role: "agent",
          text: "Voice session active. Speak naturally — I'm listening.",
          ts: new Date(),
        },
      ]);
    } catch (err: unknown) {
      setVoiceState("error");
      setConnError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const stopVoice = useCallback(async () => {
    if (callRef.current) {
      try {
        await callRef.current.leave();
        callRef.current.destroy();
      } catch {
        /* best-effort */
      }
      callRef.current = null;
    }
    if (voiceSessionId) {
      try {
        await api.voice.disconnect(voiceSessionId);
      } catch {
        /* best-effort */
      }
    }
    setVoiceState("idle");
    setRoomUrl(null);
    setVoiceSessionId(null);
    setConnError(null);
    setCaption(null);
  }, [voiceSessionId]);

  // Clean up the call object if the component unmounts mid-session.
  useEffect(() => {
    return () => {
      callRef.current?.destroy?.();
    };
  }, []);

  const voiceLed =
    voiceState === "listening" ? "led-signal led-live" :
    voiceState === "connecting" ? "led-warn blink" :
    voiceState === "error" ? "led-err" : "led-dim";

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100dvh", minWidth: 0 }}>
      {/* ── Header ── */}
      <PageHeader
        icon={<MicrophoneStage size={17} weight="duotone" />}
        title="Voice Chat"
        subtitle={`SESSION ${sessionId.toUpperCase()}`}
        actions={
          /* Mode toggle */
          <div className="seg" role="tablist" aria-label="Chat mode">
            {(["text", "voice"] as const).map((m) => {
              const active = mode === m;
              return (
                <button
                  key={m}
                  role="tab"
                  aria-selected={active}
                  onClick={() => setMode(m)}
                  style={{ position: "relative" }}
                >
                  {active && (
                    <motion.span
                      layoutId="mode-pill"
                      style={{
                        position: "absolute",
                        inset: 0,
                        borderRadius: "var(--r-sm)",
                        background: "var(--ink-4)",
                        boxShadow:
                          "inset 0 0 0 1px var(--line-strong), 0 1px 6px rgba(0,0,0,0.35)",
                      }}
                      transition={{ type: "spring", stiffness: 420, damping: 32 }}
                    />
                  )}
                  <span style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: 6 }}>
                    {m === "text" ? <ChatText size={14} weight="fill" /> : <Microphone size={14} weight="fill" />}
                    {m === "text" ? "Text" : "Voice"}
                  </span>
                </button>
              );
            })}
          </div>
        }
      />

      {/* ── Messages ── */}
      <div
        className="scroll-area"
        aria-live="polite"
        style={{
          flex: 1,
          padding: "22px clamp(16px, 4vw, 40px)",
          display: "flex",
          flexDirection: "column",
          gap: 14,
          maxWidth: 900,
          width: "100%",
          margin: "0 auto",
        }}
      >
        <AnimatePresence initial={false}>
          {messages.map((m) => (
            <motion.div
              key={m.id}
              initial={{ opacity: 0, y: 10, scale: 0.97 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
              style={{ display: "flex", flexDirection: "column", alignItems: m.role === "user" ? "flex-end" : "flex-start" }}
            >
              <div className={m.role === "user" ? "bubble-user" : "bubble-agent"} style={{ whiteSpace: "pre-wrap" }}>
                {m.text}
              </div>
              {m.sources && m.sources.length > 0 && (
                <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
                  {m.sources.slice(0, 3).map((s, i) => (
                    <span key={i} className="chip" title={`Similarity ${(s.score * 100).toFixed(0)}%`}>
                      {s.name} · {formatCurrency(s.price)}
                    </span>
                  ))}
                </div>
              )}
              <span className="mono-id" style={{ marginTop: 4, fontSize: "0.6rem" }}>
                {formatTime(m.ts)}
              </span>
            </motion.div>
          ))}
        </AnimatePresence>

        {loading && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--text-low)", fontSize: "0.82rem" }}
          >
            <span className="typing" role="status" aria-label="Agent is typing">
              <span />
              <span />
              <span />
            </span>
            <span className="label-mono" style={{ letterSpacing: "0.1em" }}>Agent is thinking…</span>
          </motion.div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* ── Voice console ── */}
      <AnimatePresence>
        {mode === "voice" && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
            style={{ overflow: "hidden" }}
          >
            <div
              style={{
                padding: "13px 20px",
                borderTop: "1px solid var(--line)",
                background: "var(--chrome-deep)",
                backdropFilter: "blur(12px)",
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 16,
                flexWrap: "wrap",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 220 }}>
                <span className={`led ${voiceLed}`} />
                <div>
                  <div
                    className="label-mono"
                    style={{
                      textTransform: "none",
                      letterSpacing: "0.08em",
                      color: voiceState === "error" ? "var(--danger)" : voiceState === "listening" ? "var(--signal)" : "var(--text-mid)",
                      fontSize: "0.72rem",
                    }}
                  >
                    {VOICE_LABEL[voiceState]}
                  </div>
                  {roomUrl && (
                    <div className="mono-id" style={{ fontSize: "0.64rem", maxWidth: 340, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {roomUrl.replace("https://", "")}
                    </div>
                  )}
                  {connError && (
                    <div style={{ fontSize: "0.74rem", color: "var(--danger)", maxWidth: 420, marginTop: 3, lineHeight: 1.5 }}>
                      {connError}
                    </div>
                  )}
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                {voiceState !== "error" && (
                  <div className="vu idle" aria-hidden="true">
                    {Array.from({ length: 14 }).map((_, i) => (
                      <span key={i} />
                    ))}
                  </div>
                )}
                <div style={{ display: "flex", gap: 8 }}>
                  {voiceState === "error" && (
                    <button className="btn-ghost btn-sm" onClick={startVoice}>
                      <ArrowClockwise size={13} /> Retry
                    </button>
                  )}
                  {voiceState === "listening" ? (
                    <button className="btn-danger btn-sm" onClick={stopVoice} style={{ padding: "8px 18px" }}>
                      <Stop size={15} weight="fill" />
                      Disconnect
                    </button>
                  ) : (
                    <button
                      className="btn-primary btn-sm"
                      onClick={startVoice}
                      disabled={voiceState === "connecting"}
                      style={{ padding: "8px 18px" }}
                    >
                      {voiceState === "connecting" ? <Spinner size={15} className="blink" /> : <MicrophoneStage size={15} weight="fill" />}
                      {voiceState === "connecting" ? "Connecting" : "Connect"}
                    </button>
                  )}
                </div>
              </div>
            </div>

            {voiceState === "error" && (
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
                <span>
                  Voice session couldn&apos;t connect. Text mode below is fully functional — try voice again in a moment.
                </span>
              </div>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Live captions — mirrors what the mic/agent are producing in real time */}
      {mode === "voice" && voiceState === "listening" && caption && (
        <div
          style={{
            padding: "8px 20px",
            borderTop: "1px solid var(--line)",
            background: "var(--chrome-deep)",
            fontSize: "0.86rem",
            lineHeight: 1.5,
            color: caption.role === "user" ? "var(--text-mid)" : "var(--signal)",
          }}
        >
          <span className="label-mono" style={{ marginRight: 8, fontSize: "0.62rem", opacity: 0.7 }}>
            {caption.role === "user" ? "YOU" : "CALLIOPE"}
          </span>
          {caption.text}
          <span className="blink" style={{ marginLeft: 2 }}>▍</span>
        </div>
      )}

      {/* ── Text input ── */}
      {mode === "text" && (
        <div
          style={{
            padding: "12px clamp(16px, 4vw, 40px) 14px",
            borderTop: "1px solid var(--line)",
            display: "flex",
            gap: 10,
            alignItems: "center",
            background: "var(--chrome-deep)",
            backdropFilter: "blur(12px)",
            maxWidth: 900,
            width: "100%",
            margin: "0 auto",
          }}
        >
          <input
            ref={inputRef}
            className="input-field"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about products, place an order…"
            disabled={loading}
            id="chat-input"
            aria-label="Message"
          />
          <button
            className="btn-primary"
            onClick={sendText}
            disabled={loading || !input.trim()}
            style={{ flexShrink: 0, padding: "10px 16px" }}
            aria-label="Send message"
          >
            {loading ? <Spinner size={16} className="blink" /> : <PaperPlaneTilt size={16} weight="fill" />}
          </button>
        </div>
      )}
    </div>
  );
}
