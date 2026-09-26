"""Where each provider's API lives, for the ones that are not their own.

Read by the agent, by the chat tester and by the widget - all three run the
campaign's language model, and all three used to assume it was OpenAI's.

In its own module because the console cannot import voice_agent: that pulls in
livekit, which is not installed there. The same reason tts_defaults.py exists,
and the same lesson underneath both - a constant only half the system can read
is a constant that will be copied, and a copy is a thing that drifts.
"""
from __future__ import annotations

import os

# A provider that is absent here is its own API, addressed by its own SDK
# default. Only gateways need an entry.
#
# Every one of these speaks OpenAI's wire format, which is what makes them a
# base_url rather than a plugin.
LLM_BASE_URL = {"openrouter": "https://openrouter.ai/api/v1"}

# Language models on our own hardware. Not in the dict above because their
# address is per-server and lives in the environment: a LAN address differs
# between machines, and a hardcoded one already sent production's calls to the
# development box for two days (docs/REPLICA.md).
#
# Named qwen-llm and not qwen, which is already taken by Qwen3-ASR on the same
# box. The two are different services with different ports, and costing looks
# a rate up by this name.
LOCAL_LLM_ENV = {"qwen-llm": "QWEN_LLM_URL"}

# Providers with no account behind them, so no key to demand of a campaign.
# The agent and the console both read this; see KEYLESS in voice_agent.py for
# the speech layers.
KEYLESS_LLM = tuple(LOCAL_LLM_ENV)


def llm_base_url(provider: str | None) -> str | None:
    """-> the base URL for this provider, or None to use the SDK's own."""
    env = LOCAL_LLM_ENV.get(provider or "")
    if env:
        return os.getenv(env, "").strip() or None
    return LLM_BASE_URL.get(provider or "")


def llm_extra_body(provider: str | None) -> dict | None:
    """-> request fields this provider needs that the SDK has no argument for.

    Qwen3 reasons before it answers, and the reasoning is tokens. Asked a
    one-line Hindi question with thinking on, it spent all 120 tokens arguing
    with itself in English and never reached the reply. In a chat window that
    is a feature; on a phone call it is the caller listening to silence.

    `chat_template_kwargs` is not an OpenAI parameter, so it travels in
    extra_body - which livekit's OpenAI LLM plugin takes, and which the chat
    tester passes through as well. Both paths therefore turn it off the same
    way, which is the point of this module.
    """
    if provider in LOCAL_LLM_ENV:
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return None
