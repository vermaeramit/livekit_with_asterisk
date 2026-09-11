"""Where each provider's API lives, for the ones that are not their own.

Read by the agent, by the chat tester and by the widget - all three run the
campaign's language model, and all three used to assume it was OpenAI's.

In its own module because the console cannot import voice_agent: that pulls in
livekit, which is not installed there. The same reason tts_defaults.py exists,
and the same lesson underneath both - a constant only half the system can read
is a constant that will be copied, and a copy is a thing that drifts.
"""
from __future__ import annotations

# A provider that is absent here is its own API, addressed by its own SDK
# default. Only gateways need an entry.
#
# Every one of these speaks OpenAI's wire format, which is what makes them a
# base_url rather than a plugin.
LLM_BASE_URL = {"openrouter": "https://openrouter.ai/api/v1"}


def llm_base_url(provider: str | None) -> str | None:
    """-> the base URL for this provider, or None to use the SDK's own."""
    return LLM_BASE_URL.get(provider or "")
