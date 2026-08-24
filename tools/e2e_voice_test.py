"""
End-to-end voice pipeline test â€” simulates the browser completely.

Connects to the PRODUCTION WebSocket, receives the greeting, then streams
REAL synthesized speech (Groq TTS -> resampled to 16 kHz PCM16) exactly like
the browser mic would, and verifies:

  1. Greeting audio arrives
  2. "what products do you have"      -> transcript + spoken reply
  3. "I want to buy one bluetooth speaker" -> quote + confirm prompt
  4. "yes"                            -> order placed confirmation

Run:  python tools/e2e_voice_test.py
"""

import asyncio
import io
import json
import sys
import time
import wave

import numpy as np
import websockets
from dotenv import load_dotenv
from groq import AsyncGroq

load_dotenv(".env")
import os  # noqa: E402

WS_URL = os.environ.get(
    "E2E_WS_URL",
    "wss://voicesell-backend.onrender.com/api/v1/voice/ws?session_id=e2e-harness",
)
TARGET_RATE = 16000


def wav_to_pcm16(wav_bytes: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        rate = w.getframerate()
        frames = w.readframes(w.getnframes())
    data = np.frombuffer(frames, dtype=np.int16)
    if w.getnchannels() > 1:
        data = data.reshape(-1, w.getnchannels()).mean(axis=1).astype(np.int16)
    return data, rate


def resample(data: np.ndarray, from_rate: int, to_rate: int = TARGET_RATE) -> np.ndarray:
    if from_rate == to_rate:
        return data.astype(np.float32)
    dur = len(data) / from_rate
    out_len = int(dur * to_rate)
    x_old = np.linspace(0, dur, num=len(data), endpoint=False)
    x_new = np.linspace(0, dur, num=out_len, endpoint=False)
    return np.interp(x_new, x_old, data.astype(np.float64)).astype(np.float32)


async def recv_until_quiet(ws, quiet_s: float = 10.0, max_wait: float = 120.0):
    """Collect frames until the wire goes quiet for `quiet_s`."""
    audio_bytes = 0
    captions: list[str] = []
    transcripts: list[str] = []
    errors: list[str] = []
    last_activity = time.monotonic()
    deadline = time.monotonic() + max_wait

    while time.monotonic() - last_activity < quiet_s and time.monotonic() < deadline:
        remaining = max(0.05, quiet_s - (time.monotonic() - last_activity))
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 30))
        except asyncio.TimeoutError:
            break
        last_activity = time.monotonic()
        if isinstance(msg, bytes):
            audio_bytes += len(msg)
            continue
        d = json.loads(msg)
        t = d.get("type")
        if t == "agent_caption":
            captions.append(d["text"])
            print(f"   OMNIVOICE: {d['text']}")
        elif t == "transcript":
            transcripts.append(d["text"])
            print(f"   TRANSCRIPT: {d['text']!r}")
        elif t == "error":
            errors.append(d.get("message", ""))
            print(f"   ERROR: {d.get('message')}")
        elif t == "speaking_start":
            pass  # gate event
    return {
        "audio": audio_bytes,
        "captions": captions,
        "transcripts": transcripts,
        "errors": errors,
    }


async def speak_like_mic(ws, groq: AsyncGroq, text: str):
    """Synthesize `text`, stream as realtime mic-rate PCM, collect the reply."""
    tts = await groq.audio.speech.create(
        model="canopylabs/orpheus-v1-english",
        voice="autumn",
        input=text,
        response_format="wav",
    )
    pcm, rate = wav_to_pcm16(await tts.read())
    speech = resample(pcm, rate)

    # Pre-roll silence + trailing silence so VAD sees natural boundaries.
    utter = np.concatenate([
        np.zeros(int(TARGET_RATE * 0.5), dtype=np.float32),
        speech,
        np.zeros(int(TARGET_RATE * 2.2), dtype=np.float32),
    ])
    rms = float(np.sqrt(np.mean(speech ** 2)))
    if rms > 0:
        utter *= min(0.20 / rms, 3.0)  # normalise to realistic mic level
    int16 = (np.clip(utter, -1, 1) * 32767).astype(np.int16)

    print(f"\n>>> USER SAYS: {text!r} ({len(int16)/TARGET_RATE:.1f}s of audio)")
    chunk = TARGET_RATE // 10  # 100 ms packets, realtime pacing
    for i in range(0, len(int16), chunk):
        await ws.send(int16[i : i + chunk].tobytes())
        await asyncio.sleep(0.1)

    result = await recv_until_quiet(ws, quiet_s=24.0)
    await ws.send(json.dumps({"type": "playback_done"}))
    return result


async def main() -> int:
    groq = AsyncGroq(api_key=sys.argv[1])

    async with websockets.connect(WS_URL, max_size=20 * 1024 * 1024) as ws:
        ready = json.loads(await ws.recv())
        assert ready.get("type") == "ready", ready
        print("READY âœ“")

        print("\n=== GREETING ===")
        g = await recv_until_quiet(ws, quiet_s=6.0)
        await ws.send(json.dumps({"type": "playback_done"}))
        greeting_ok = g["audio"] > 100_000
        print(f"greeting audio: {g['audio']} bytes -> {'PASS' if greeting_ok else 'FAIL'}")

        r1 = await speak_like_mic(ws, groq, "What products do you have?")
        ok1 = bool(r1["transcripts"]) and bool(r1["captions"]) and r1["audio"] > 50_000

        r2 = await speak_like_mic(ws, groq, "I want to buy one bluetooth speaker")
        blob2 = " ".join(r2["captions"]).lower()
        ok2 = "bluetooth" in blob2 and ("shall i place" in blob2 or "confirm" in blob2)

        r3 = await speak_like_mic(ws, groq, "yes")
        blob3 = " ".join(r3["captions"]).lower()
        ok3 = ("placed" in blob3 or "confirmed" in blob3) and "order" in blob3

        print()
        print(f"  greeting            : {'PASS' if greeting_ok else 'FAIL'}")
        print(f"  catalog question    : {'PASS' if ok1 else 'FAIL'}  (stt={bool(r1['transcripts'])} tts={r1['audio']}b)")
        print(f"  order quote         : {'PASS' if ok2 else 'FAIL'}")
        print(f"  confirm + place     : {'PASS' if ok3 else 'FAIL'}")
        return 0 if (greeting_ok and ok1 and ok2 and ok3) else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))

