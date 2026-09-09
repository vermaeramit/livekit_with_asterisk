"""What a campaign gets when it has not chosen a model or a voice.

Read by the agent when it builds a TTS, and by the console when it warns that a
campaign is leaning on one of these without knowing it.

In one place because two copies is how the console ends up confidently naming a
voice the agent stopped using. It is also the reason this module imports
nothing: the console cannot import voice_agent - that pulls in livekit, which
is not installed there - and a constant that only half the system can read is
the same problem wearing a different hat.

WHY IT MATTERS THAT THESE HAVE NAMES AT ALL

A campaign with no voice stored is not "using the provider's default". It is
using a name written here, and Soniox has withdrawn voices before - Meera,
Maya, Noah, Jack, Claire, Sofia and Elise all went with tts-rt-v2. A voice the
model does not have fails at construction, so the campaign goes silent mid-call
on a date nothing in this repo would have warned about.

Sarvam is the exception and deliberately not listed for voice: when nothing is
stored the agent omits `speaker` entirely and Sarvam picks, server-side. There
is no name here to rot.
"""
from __future__ import annotations

SONIOX_MODEL = "tts-rt-v2"
SONIOX_VOICE = "Priya"
SARVAM_MODEL = "bulbul:v3"
OPENAI_MODEL = "gpt-4o-mini-tts"

# OpenAI is not given a voice at all - see _build_tts. Whatever the livekit
# plugin defaults to is what speaks, and the console's voice field does nothing
# on an OpenAI campaign. Recorded here so the warning can say so rather than
# somebody discovering it by choosing a voice and hearing another one.
OPENAI_IGNORES_VOICE = True
