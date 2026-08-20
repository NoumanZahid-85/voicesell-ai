"""
LiteLLM gateway — unified LLM access with automatic failover.

Fallback chain: Groq (primary) → OpenAI → Gemini. Managed by LiteLLM's Router,
which retries on 429/5xx and cooldowns failing providers via circuit breakers.

Config-driven: adding/removing providers requires zero code changes.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, cast

from app.core.config import get_settings

logger = logging.getLogger(__name__)

PRIMARY_MODEL = "agent"


@lru_cache
def get_llm_router():
    """Build the LiteLLM Router singleton with the configured provider chain."""
    from litellm.router import Router

    settings = get_settings()
    model_list: list[dict[str, Any]] = []

    if settings.groq_api_key:
        model_list.append(
            {
                "model_name": PRIMARY_MODEL,
                "litellm_params": {
                    # llama-3.3-70b-versatile was decommissioned by Groq on
                    # 2026-08-16 (announced 2026-06-17). openai/gpt-oss-120b
                    # is Groq's recommended replacement.
                    "model": "groq/openai/gpt-oss-120b",
                    "api_key": settings.groq_api_key,
                    "timeout": 5,
                },
            }
        )

    if settings.openai_api_key and settings.openai_base_url:
        model_list.append(
            {
                "model_name": "fallback-openai",
                "litellm_params": {
                    "model": f"openai/{settings.openai_model_name or 'gpt-4o-mini'}",
                    "api_key": settings.openai_api_key,
                    "api_base": settings.openai_base_url,
                    "timeout": 10,
                },
            }
        )

    if settings.gemini_api_key:
        model_list.append(
            {
                "model_name": "fallback-gemini",
                "litellm_params": {
                    "model": "gemini/gemini-2.5-flash",
                    "api_key": settings.gemini_api_key,
                    "timeout": 15,
                },
            }
        )

    if not model_list:
        raise RuntimeError(
            "No LLM provider configured — set GROQ_API_KEY, OPENAI_API_KEY+OPENAI_BASE_URL, or GEMINI_API_KEY"
        )

    fallback_names = [m["model_name"] for m in model_list if m["model_name"] != PRIMARY_MODEL]

    # LiteLLM's Router only consults the fallbacks entry keyed by the model
    # group it is *currently* trying. If "agent" fails over to
    # "fallback-openai" and that ALSO fails, the Router looks for an entry
    # keyed "fallback-openai" — not the original "agent" entry — to decide
    # whether to keep going. Without that entry it aborts with "No fallback
    # model group found for original model_group=fallback-openai" instead of
    # continuing on to "fallback-gemini". So we chain every step explicitly:
    # {agent: [openai, gemini]}, {openai: [gemini]}, ...
    fallbacks = []
    if fallback_names:
        fallbacks.append({PRIMARY_MODEL: fallback_names})
        for i in range(len(fallback_names) - 1):
            fallbacks.append({fallback_names[i]: fallback_names[i + 1 :]})

    router = Router(
        model_list=model_list,
        fallbacks=fallbacks,
        allowed_fails=3,
        cooldown_time=60,
        num_retries=1,
    )
    logger.info(
        "LiteLLM router ready: primary=%s, fallbacks=%s",
        PRIMARY_MODEL,
        fallback_names or "none",
    )
    return router


async def llm_generate(
    messages: list[dict[str, str]],
    temperature: float = 0.3,
    max_tokens: int = 512,
) -> str:
    """Generate a text completion through the LiteLLM fallback chain."""
    router = get_llm_router()
    response = await router.acompletion(
        model=PRIMARY_MODEL,
        messages=cast(Any, messages),
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (response.choices[0].message.content or "").strip()
