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
import { useIdentity } from "@/components/auth";
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

const SESSION_STORAGE_KEY = "voicesell:session-id";
const MESSAGES_STORAGE_KEY = "voicesell:messages";

function useSessionId() {
  const [id] = useState(() => {
    if (typeof window === "undefined") return `sess-${Math.random().toString(36).slice(2)}`;
    const existing = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (existing) return existing;
    const fresh = `sess-${Math.random().toString(36).slice(2)}`;
    window.sessionStorage.setItem(SESSION_STORAGE_KEY, fresh);
    return fresh;
  });
  return id;
}

const VOICE_LABEL: Record<VoiceState, string> = {
  idle: "Standby — ready to connect",
  connecting: "Opening voice channel…",
  listening: "Live — agent is listening",
  error: "Voice pipeline unavailable",
};

// ── WebSocket voice helpers ────────────────────────────────────────────
// The browser streams PCM16 mono @16 kHz over a WebSocket; the backend runs
// Silero VAD → Whisper → LangGraph → Orpheus and returns complete WAV files,
// one per TTS chunk, which we decode and play back strictly in order.
//
// Turn-taking is half-duplex: the moment the server announces
// "speaking_start" we stop streaming mic audio (gate closes) so speaker
// leakage can never be transcribed as user input. When our local playback
// queue drains we send "playback_done" and the gate reopens.

function floatToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function resampleTo16k(input: Float32Array, fromRate: number): Float32Array {
  const ratio = fromRate / 16000;
  if (Math.abs(ratio - 1) < 0.001) return input;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const pos = i * ratio;
    const idx = Math.floor(pos);
    const frac = pos - idx;
    const a = input[idx] ?? 0;
    const b = input[idx + 1] ?? a;
    out[i] = a + (b - a) * frac;
  }
  return out;
}

export default function ChatPage() {
  const sessionId = useSessionId();
  const { identity } = useIdentity();
  const customerId = identity?.customerId ?? "";
  const [mode, setMode] = useState<Mode>("text");
  const [messages, setMessages] = useState<Message[]>(() => {
    if (typeof window !== "undefined") {
      try {
        const raw = window.sessionStorage.getItem(MESSAGES_STORAGE_KEY);
        if (raw) {
          const parsed = JSON.parse(raw) as (Omit<Message, "ts"> & { ts: string })[];
          if (Array.isArray(parsed) && parsed.length > 0) {
            return parsed.map((m) => ({ ...m, ts: new Date(m.ts) }));
          }
        }
      } catch {
        /* corrupt/old data — fall through to the default welcome message */
      }
    }
    return [
      {
        id: "welcome",
        role: "agent",
        text: "Hi! I'm CALLIOPE. Ask me anything about our product catalog, or say 'start voice' to switch to voice mode. I can also help you place and manage orders.",
        ts: new Date(),
      },
    ];
  });
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [voiceState, setVoiceState] = useState<VoiceState>("idle");
  const [voiceSessionId, setVoiceSessionId] = useState<string | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [caption, setCaption] = useState<Caption | null>(null);

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  // Audio playback queue: decoded WAV buffers awaiting sequential play.
  const playQueueRef = useRef<AudioBuffer[]>([]);
  const playingRef = useRef(false);
  // True while CALLIOPE has the floor — mic streaming is suppressed.
  const gateRef = useRef(false);
  // Mirror of voiceState usable inside stable callbacks/event handlers.
  const voiceStateRef = useRef<VoiceState>("idle");
  useEffect(() => {
    voiceStateRef.current = voiceState;
  }, [voiceState]);
  const [micPackets, setMicPackets] = useState(0);
  const [micCtxRate, setMicCtxRate] = useState(0);
  const micWatchRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Persist conversation across page navigation within this tab.
  useEffect(() => {
    try {
      window.sessionStorage.setItem(MESSAGES_STORAGE_KEY, JSON.stringify(messages));
    } catch {
      /* storage full/unavailable — conversation just won't persist, non-fatal */
    }
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
      const data = await api.chat(msg, sessionId, customerId);
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

  // ── Voice session (pure WebSocket, no Daily/WebRTC) ─────────────────
  const startVoice = useCallback(async () => {
    setVoiceState("connecting");
    setConnError(null);
    setCaption(null);

    // Fail fast with a clear message if the mic is blocked or missing.
    let micStream: MediaStream;
    try {
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    } catch {
      setVoiceState("error");
      setConnError(
        "Microphone access was blocked. Please allow microphone permission for this site and try again."
      );
      return;
    }
    micStreamRef.current = micStream;

    let audioCtx: AudioContext;
    try {
      // Default device rate (usually 48 kHz) — custom-rate contexts have
      // flaky MediaStreamSource resampling in several Chrome builds. We
      // resample to 16 kHz ourselves before sending.
      audioCtx = new AudioContext();
    } catch {
      setVoiceState("error");
      setConnError("Web Audio is not available in this browser.");
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
      return;
    }
    // Chrome starts a fresh AudioContext "suspended" under autoplay policy —
    // while suspended, onaudioprocess never fires (mic dead) and buffers
    // never play. The Connect click is the user gesture that unlocks it.
    if (audioCtx.state === "suspended") {
      try {
        await audioCtx.resume();
      } catch {
        /* older browser — proceed anyway */
      }
    }
    audioCtxRef.current = audioCtx;

    const wsSessionId = `${sessionId}-${Date.now().toString(36)}`;
    const ws = new WebSocket(api.voice.wsUrl(wsSessionId, customerId));
    ws.binaryType = "arraybuffer";
    wsRef.current = ws;
    setVoiceSessionId(wsSessionId);

    // ── Playback queue: decode WAV chunks and play strictly in order ──
    const pump = () => {
      if (playingRef.current) return;
      const next = playQueueRef.current.shift();
      if (!next) {
        // Queue drained — release the mic gate back to the user.
        gateRef.current = false;
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "playback_done" }));
        }
        return;
      }
      playingRef.current = true;
      const src = audioCtx.createBufferSource();
      src.buffer = next;
      src.onended = () => {
        playingRef.current = false;
        pump();
      };
      src.connect(audioCtx.destination);
      src.start();
    };

    ws.onmessage = async (ev: MessageEvent<string | ArrayBuffer>) => {
      if (typeof ev.data === "string") {
        try {
          const msg = JSON.parse(ev.data) as { type?: string; text?: string; message?: string };
          if (msg.type === "transcript" && msg.text) {
            setCaption({ role: "user", text: msg.text });
          } else if (msg.type === "agent_caption" && msg.text) {
            setCaption((prev) =>
              prev?.role === "agent"
                ? { role: "agent", text: `${prev.text} ${msg.text}`.trim() }
                : { role: "agent", text: msg.text ?? "" }
            );
          } else if (msg.type === "speaking_start") {
            gateRef.current = true;
            gateOpenedAt = Date.now();
            playQueueRef.current = [];
            playingRef.current = false;
          } else if (msg.type === "error" && msg.message) {
            setConnError(msg.message);
          }
        } catch {
          /* malformed control frame — ignore */
        }
        return;
      }
      // Binary frame = one complete WAV from the TTS queue.
      try {
        const buffer = await audioCtx.decodeAudioData(ev.data.slice(0));
        playQueueRef.current.push(buffer);
        pump();
      } catch {
        /* undecodable chunk — skip it rather than stall the queue */
      }
    };

    ws.onerror = () => {
      if (voiceStateRef.current !== "idle") {
        setVoiceState("error");
        setConnError("Voice connection failed. Please try again.");
      }
    };
    ws.onclose = () => {
      setVoiceState((s) => (s === "listening" || s === "connecting" ? "idle" : s));
    };

    // ── Mic capture: ScriptProcessor → resample 16k → PCM16 → WS ─────
    const sourceNode = audioCtx.createMediaStreamSource(micStream);
    const processor = audioCtx.createScriptProcessor(4096, 1, 1);
    const muteGain = audioCtx.createGain();
    muteGain.gain.value = 0; // processor must reach destination to run, silently
    let packetsSent = 0;
    let lastAudioAt = Date.now();
    processor.onaudioprocess = (e) => {
      lastAudioAt = Date.now();
      if (gateRef.current || ws.readyState !== WebSocket.OPEN) return;
      const pcm16k = resampleTo16k(e.inputBuffer.getChannelData(0), audioCtx.sampleRate);
      ws.send(floatToInt16(pcm16k).buffer as ArrayBuffer);
      packetsSent++;
      if (packetsSent % 8 === 1) setMicPackets(packetsSent); // throttle re-renders
    };
    sourceNode.connect(processor);
    processor.connect(muteGain);
    muteGain.connect(audioCtx.destination);
    setMicCtxRate(audioCtx.sampleRate);

    // Safety: if the gate ever sticks shut (missed onended, stalled queue)
    // force it open again so the mic never stays muted forever.
    let gateOpenedAt = Date.now();
    const gateWatchdog = setInterval(() => {
      if (gateRef.current && Date.now() - gateOpenedAt > 15000) {
        gateRef.current = false;
        playQueueRef.current = [];
        playingRef.current = false;
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "playback_done" }));
        }
      }
    }, 3000);
    micWatchRef.current = gateWatchdog;

    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Connection timed out. Please try again.")), 15000);
      ws.onopen = () => {
        clearTimeout(timer);
        resolve();
      };
    }).catch((err: unknown) => {
      setVoiceState("error");
      setConnError(err instanceof Error ? err.message : String(err));
      ws.close();
      audioCtx.close().catch(() => {});
      micStream.getTracks().forEach((t) => t.stop());
      wsRef.current = null;
      audioCtxRef.current = null;
      micStreamRef.current = null;
      return;
    });
    if (wsRef.current === null) return; // failed connect above already cleaned up

    setVoiceState("listening");
    setMessages((prev) => [
      ...prev,
      {
        id: Date.now().toString(),
        role: "agent",
        text: "Voice session active. Speak naturally — pause a second when you're done, and let me finish before your next question.",
        ts: new Date(),
      },
    ]);
  }, [sessionId, customerId]);

  const stopVoice = useCallback(async () => {
    try {
      wsRef.current?.close();
    } catch {
      /* best-effort */
    }
    wsRef.current = null;
    if (micWatchRef.current) {
      clearInterval(micWatchRef.current);
      micWatchRef.current = null;
    }
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    try {
      await audioCtxRef.current?.close();
    } catch {
      /* best-effort */
    }
    audioCtxRef.current = null;
    playQueueRef.current = [];
    playingRef.current = false;
    gateRef.current = false;
    // Closing the socket is the disconnect — the backend records
    // "ws_disconnected" from the receive loop's finally block.
    setVoiceState("idle");
    setVoiceSessionId(null);
    setConnError(null);
    setCaption(null);
  }, []);

  // Clean up sockets/audio if the component unmounts mid-session.
  useEffect(() => {
    return () => {
      try {
        wsRef.current?.close();
      } catch {
        /* best-effort */
      }
      if (micWatchRef.current) clearInterval(micWatchRef.current);
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
      audioCtxRef.current?.close().catch(() => {});
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
                  {voiceState === "listening" && (
                    <div className="mono-id" style={{ fontSize: "0.62rem", opacity: 0.75 }}>
                      MIC {micPackets} PKTS · CTX {micCtxRate}Hz
                      {gateRef.current ? " · BOT SPEAKING" : " · LISTENING"}
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
