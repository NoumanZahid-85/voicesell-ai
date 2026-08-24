"""One-shot: read the full Groq TTS rate-limit message to learn the window."""
import asyncio
import os
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")
from groq import AsyncGroq


async def main() -> None:
    g = AsyncGroq(api_key=os.environ["GROQ_API_KEY"])
    try:
        resp = await g.audio.speech.create(
            model="canopylabs/orpheus-v1-english",
            voice="autumn",
            input="hi",
            response_format="wav",
        )
        data = await resp.read()
        print(f"TTS OK — {len(data)} bytes (quota has reset!)")
    except Exception as exc:
        print(str(exc)[:600])


asyncio.run(main())
