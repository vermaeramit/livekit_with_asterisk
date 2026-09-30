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

# Kokoro, on our own GPU box. Four Hindi voices exist - hf_alpha, hf_beta,
# hm_omega, hm_psi - and hf_alpha is the one that measured fastest to first
# audio (216 ms) and was accepted by ear on Hindi with English product names
# and a rupee figure in it. All four are graded C by Kokoro's own authors,
# which turned out not to matter on a phone line.
#
# The model name is "kokoro" because that is what its server calls it; unlike
# the vendors there is no second model to drift onto, and no removal date.
KOKORO_MODEL = "kokoro"
KOKORO_VOICE = "hf_alpha"

# Speech recognition on the same box, served by vLLM. The name is whatever
# --served-model-name says in /srv/gpu-stack/qwen-asr/docker-compose.yml; the
# two have to agree or vLLM answers 404 for a model it is not serving.
#
# This file is named for TTS and now holds an STT default. Left here rather
# than split because the console imports it and the reason for the file - one
# place, so the console and the agent cannot disagree - applies just the same.
QWEN_STT_MODEL = "qwen3-asr"

# And the language model on the same box, also served by vLLM on its own port.
# Measured on campaign `default` - 26,000 characters of prompt, a knowledge
# base index and six tools - against gpt-4.1-mini on the same prompt:
#
#     qwen3-32b      cold  456 ms   warm 106 ms   spread   6 ms
#     gpt-4.1-mini   cold 3298 ms   warm 700 ms   spread 322 ms
#
# The spread is the number that matters: gpt-4.1-mini was chosen for variance
# in the first place, after one 6286 ms turn ended a call.
QWEN_LLM_MODEL = "qwen3-32b"

# Gemini's own TTS models, reached over the Gemini API - NOT Google Cloud TTS,
# which is a different product with different voices and different credentials.
# See gemini_tts.py.
#
# Read off the API on 30 Sep 2026 rather than remembered - the first value here
# was gemini-2.5-flash-preview-tts, chosen from memory, and the key can reach
# five TTS models of which that is the oldest:
#
#     gemini-2.5-flash-preview-tts     gemini-3.1-flash-tts-preview
#     gemini-2.5-pro-preview-tts       gemini-3.8-flash-lite-tts
#                                      gemini-3.8-flash-tts
#
#   docker exec -i admin-api python - < server-configs/provider-catalog.py gemini tts-models
#
# Flash rather than pro: this sits in a phone call, where a better reply that
# arrives later is a worse reply. The 3.8 pair carry no "preview" in the name,
# which is the one thing that separates them from the other three - a preview
# model can be withdrawn on a date nothing here would warn about, exactly as
# Soniox withdrew seven voices with tts-rt-v2.
#
# lite is the latency candidate and is not the default until it has been
# measured. 2.5-flash-preview-tts was 3,832 ms to first audio against kokoro's
# 114 - see the bench.
GEMINI_MODEL = "gemini-3.8-flash-tts"
GEMINI_VOICE = "Kore"

# There is no rate parameter on these models. Delivery is steered by prompting,
# and an instruction glued onto the caller's sentence is one the model may read
# out loud. So the campaign's tts_speed is ignored on Gemini, and the console
# says so rather than leaving somebody to move a slider that does nothing -
# which is the bug that was just fixed for every other provider.
GEMINI_IGNORES_SPEED = True

# OpenAI is not given a voice at all - see _build_tts. Whatever the livekit
# plugin defaults to is what speaks, and the console's voice field does nothing
# on an OpenAI campaign. Recorded here so the warning can say so rather than
# somebody discovering it by choosing a voice and hearing another one.
OPENAI_IGNORES_VOICE = True
