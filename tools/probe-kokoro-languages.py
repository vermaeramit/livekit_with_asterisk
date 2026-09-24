"""Which Indian languages does Kokoro's Hindi voice actually pronounce?

    python tools/probe-kokoro-languages.py      # KOKORO_URL, or the box

Run this before pointing a campaign in a NEW language at our own server. Asked
on 24 Sep 2026 after other Indian languages were tried by ear and sounded
right - eight of them are, and two are not, and nothing in the response says
so: every one of them returns HTTP 200.

Measured 24 Sep 2026, hf_alpha, seconds of audio per character against Hindi:

    marathi 109%   bengali 105%   kannada 112%   gujarati 102%
    malayalam 96%  punjabi 94%    tamil 76%
    TELUGU 720%    ODIA 1308%   <- 22 s and 40 s for a one-line sentence

The last two are not speech. The rest produced plausible audio - which is not
the same as CORRECT audio, and the voice is a Hindi one either way, so another
language gets a Hindi accent. Only a speaker of the language can judge that,
which is why the wav files are kept.

The same sentence - "What is your name? Please tell me." - in nine scripts,
through hf_alpha, a HINDI voice. Kokoro lists no other Indian language and has
four Hindi voices out of seventy-two; what the phonemiser makes of another
script was the open question, and the answer turned out not to be "nothing".

Judged by seconds of audio per character, against Hindi as the baseline. A
script it cannot read produces almost nothing, or minutes of noise; one it can
produces roughly Hindi's rate. This says whether SOUND came out, not whether it
was CORRECT - only a speaker of the language can say that.

The wav files are left behind for exactly that reason.
"""
import os
import time

import requests

# KOKORO_URL where it is set - on the servers it always is. The fallback is the
# development box, and it is here only because this is a probe somebody runs
# from a laptop; nothing in the call path has a default address, for the reason
# migration 059 gives.
HOST = os.getenv("KOKORO_URL", "http://10.130.9.248:8880/v1").rstrip("/")
if not HOST.endswith("/v1"):
    HOST += "/v1"
OUT = os.path.dirname(os.path.abspath(__file__)) + "/kokoro-langs"
os.makedirs(OUT, exist_ok=True)

TEXTS = {
    "hindi (baseline)": "आपका नाम क्या है? कृपया मुझे बताइए।",
    "marathi":          "तुमचे नाव काय आहे? कृपया मला सांगा.",
    "gujarati":         "તમારું નામ શું છે? કૃપા કરીને મને કહો.",
    "bengali":          "আপনার নাম কি? দয়া করে আমাকে বলুন.",
    "punjabi":          "ਤੁਹਾਡਾ ਨਾਮ ਕੀ ਹੈ? ਕਿਰਪਾ ਕਰਕੇ ਮੈਨੂੰ ਦੱਸੋ.",
    "tamil":            "உங்கள் பெயர் என்ன? தயவுசெய்து எனக்குச் சொல்லுங்கள்.",
    "telugu":           "మీ పేరు ఏమిటి? దయచేసి నాకు చెప్పండి.",
    "kannada":          "ನಿಮ್ಮ ಹೆಸರು ಏನು? ದಯವಿಟ್ಟು ನನಗೆ ಹೇಳಿ.",
    "malayalam":        "നിങ്ങളുടെ പേര് എന്താണ്? ദയവായി എന്നോട് പറയുക.",
    "odia":             "ଆପଣଙ୍କ ନାମ କଣ? ଦୟାକରି ମୋତେ କୁହନ୍ତୁ.",
}


def say(label: str, text: str) -> tuple[float, float]:
    t0 = time.monotonic()
    r = requests.post(f"{HOST}/audio/speech", json={
        "model": "kokoro", "voice": "hf_alpha", "input": text,
        "response_format": "wav",
    }, timeout=120)
    wall = time.monotonic() - t0
    if r.status_code != 200:
        print(f"{label:<18} HTTP {r.status_code}: {r.text[:90]}")
        return 0.0, wall
    with open(f"{OUT}/{label.split()[0]}.wav", "wb") as f:
        f.write(r.content)
    # NOT the WAV header: Kokoro streams the body and writes a placeholder
    # length, which read back as 89,478 seconds for every language including
    # the one-line ones. The bytes are the only honest measure.
    secs = max(len(r.content) - 44, 0) / 2 / 24000
    return secs, wall


base = None
print(f"{'language':<18} {'chars':>5} {'audio':>7} {'s/char':>7}  vs hindi")
for label, text in TEXTS.items():
    secs, _ = say(label, text)
    rate = secs / len(text) if text else 0
    if base is None:
        base = rate or 1
    print(f"{label:<18} {len(text):>5} {secs:>6.1f}s {rate:>7.3f}  "
          f"{rate / base * 100:>5.0f}%")
print("\nwav files:", OUT)
