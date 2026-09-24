# Measured on the GPU box

Every number here was taken from a laptop on the same LAN, so it includes the
network, exactly as a call would. The method is the one the vendors were judged
by: time to first audio, and how much wall time a second of speech costs (RTF).

## Kokoro — 23 Sep 2026

`ghcr.io/remsky/kokoro-fastapi-gpu:latest`, 72 voice packs, CUDA on, **978 MiB**
of the 48 GB used. Warm-up 3.4 s at container start, once.

### One request at a time

Hindi, with English product names, a rupee figure and a time in it - which is
what these calls are actually made of:

> अमित जी, HF Deluxe का एक्स-शोरूम प्राइस ₹52,540 से शुरू होता है। क्या मैं आपको कल सुबह 10 बजे कॉल करूँ?

| Voice | First audio | Total | Audio produced | RTF |
|---|---|---|---|---|
| hf_alpha | 216 ms | 240 ms | 9.12 s | **0.026** |
| hf_beta | 242 ms | 269 ms | 8.15 s | 0.033 |
| hm_omega | 217 ms | 260 ms | 9.43 s | 0.028 |
| hm_psi | 203 ms | 234 ms | 9.55 s | 0.025 |

Nine seconds of speech in a quarter of a second. Against what the vendors give
on the same sentences:

| | First audio | RTF |
|---|---|---|
| **Kokoro, this box** | **~200 ms** | **0.03** |
| Sarvam | 220-500 ms | ~0.2 |
| Soniox, on a good stretch | 400-900 ms | varies |
| Soniox, on a bad one | 1-5 s, with holes mid-sentence | above 2 |

The RTF matters as much as the first number: at 0.03 the whole sentence exists
before a caller has heard a word of it, so there is nothing left to stall.

### Several at once

A phone system is not one request at a time. Same sentence, fired simultaneously:

| At once | First audio p50 | Worst | RTF p50 |
|---|---|---|---|
| 1 | 196 ms | 196 ms | 0.026 |
| 2 | 226 ms | 298 ms | 0.034 |
| 4 | 563 ms | 567 ms | 0.066 |
| 8 | 825 ms | 1093 ms | 0.123 |
| 16 | 1346 ms | 2132 ms | 0.206 |

A second run with a single sentence rather than a paragraph gave the same shape:
240 ms at 1, 476 at 4, 797 at 8, 1214 at 16.

**It degrades linearly - roughly 200 ms plus 75 ms per request in flight.** The
GPU is serialising; nothing is queuing badly, and nothing falls over.

What that means for ten concurrent calls is *not* the 16-row. A call asks for
speech once a turn, for a second or so, every ten or twenty seconds - so ten
calls put one or two requests in flight at a time, not ten. The 4-at-once row,
563 ms, is the honest worst case for today's traffic, and that is still better
than Soniox on a good stretch.

Worth re-measuring if calls ever get busier, and worth remembering that this box
has **8 vCPU**, which is the part most likely to give out first.

### Quality

Judged by ear, not by a number: "looking great" on the Hindi, including the
English product names and the rupee figure inside a Hindi sentence. The sample
files were `hf_alpha`, `hf_beta`, `hm_omega`, `hm_psi` - the four Hindi voices,
all graded C by Kokoro's own authors, which turned out not to matter on a phone
line.

## Kokoro through the agent's own code - 24 Sep 2026

The numbers above were taken from a laptop with a plain HTTP client. These were
taken on **.243**, through `tts-bench`, which builds the TTS with
`voice_agent._build_tts` - the same object a call speaks through, with the
StreamAdapter and the emitter in the path.

| | first audio | stalls |
|---|---|---|
| one at a time | **95-180 ms** | 0 |
| two at once | **111-201 ms** | 0 |
| the campaign's greeting, cold | 289 ms, then 106 ms | 0 |

53.5 s of audio, **0.0 s stalled**. A seven-second sentence is fully rendered in
0.4 s of wall time - RTF around 0.05 - and two at once barely moves it, which
matches the laptop's "~200 ms plus 75 ms per request in flight".

Against Soniox on the India region, measured on call 646 the day before:
**181-218 ms**. Kokoro is a little faster, on hardware we own, at no per-minute
cost.

One failure, and it belongs to the campaign rather than to Kokoro: the
recording disclosure on `default` is the single character `.`, and Kokoro
produces no audio for it - correctly, there is nothing to say. Sarvam already
refuses the same string. A real call sends the greeting and disclosure JOINED,
and that rendered in 106 ms.

### What it took to get there

The obvious route - livekit's OpenAI TTS plugin pointed at Kokoro with a
`base_url` - does not work, and the symptom blames the wrong thing. It reports
"no audio frames were pushed" while curl gets 87,916 bytes, the OpenAI SDK
alone gets 108,566 in three chunks, and Kokoro logs every attempt as 200 OK.
Instrumenting the emitter settled it: `initialize x4, flush x4, push x0`. The
plugin receives the audio and hands the emitter nothing.

`agent/kokoro_tts.py` speaks to the box directly instead - the Sarvam plugin's
shape with the Sarvam parts removed. One POST, raw PCM, push the chunks.

## How many calls it carries - 24 Sep 2026

Measured from .243 with `kokoro-load.py`, which models CALLS rather than
requests: each simulated call thinks for about ten seconds, then renders an
answer of two or three sentences one at a time, as a real answer is rendered.
Ninety seconds per level.

| Calls | first p50 | p95 | worst | over 400 ms | **slower than real time** |
|---|---|---|---|---|---|
| 5 | 109 ms | 165 | 278 | 0 | **0** |
| 10 | 120 ms | 308 | 350 | 0 | **0** |
| 20 | 122 ms | 357 | 540 | 22 of 446 | **0** |
| 40 | 234-239 ms | 693-809 | 1236-1267 | ~25% | **0** |

The 40 row was run twice and agreed to within 6%.

**Nothing was ever slower than real time, at any level.** The box's throughput
was never the limit in this range; what grew was the time a request spent
waiting behind another.

### It is queueing, not working

At 40 calls the box produced roughly 35-40x real time in aggregate - which is
what a SINGLE stream produces on its own (RTF 0.026, about 38x). Requests are
being served one at a time, very fast.

The CPU says the same thing: **101% at peak, of 800% available**. One core of
eight. And the GPU holds about 1 GB of 48.

So the lever, if traffic ever needs more than this, is **more copies of Kokoro**
- not more CPU, and not a bigger card. Four copies would be 4 GB of VRAM on a
48 GB card. Neither is needed today: the call system's own capacity is 10
concurrent calls, where this box is at 120 ms p50 with nothing over 400 ms.

Not yet confirmed: GPU utilisation during the 40-call run. The aggregate rate
and the idle CPU both point at serialisation inside the server, but `nvidia-smi
dmon` was not running alongside, so that is inference rather than measurement.

## Which languages it actually speaks - 24 Sep 2026

Kokoro serves **72 voices in 9 languages**, read from the box itself:

| | | | |
|---|---|---|---|
| American English | 33 | Spanish | 3 |
| British English | 13 | Portuguese (BR) | 3 |
| Mandarin | 8 | Italian | 2 |
| Japanese | 5 | French | 1 |
| **Hindi** | **4** - `hf_alpha`, `hf_beta`, `hm_omega`, `hm_psi` | | |

No other Indian language is listed. Kokoro grades its own Hindi voices **C**,
against **A** for `af_heart` - which matters less than it reads on an 8 kHz
phone line, where six live calls were accepted by ear.

### Other Indian scripts: eight work, two do not

Tried after other Indian languages were tested by ear and sounded right.
`tools/probe-kokoro-languages.py`, one sentence in nine scripts through
`hf_alpha`, seconds of audio per character against Hindi:

| | | | |
|---|---|---|---|
| Marathi | 109% | Punjabi | 94% |
| Kannada | 112% | Tamil | 76% |
| Bengali | 105% | **Telugu** | **720%** |
| Gujarati | 102% | **Odia** | **1308%** |
| Malayalam | 96% | | |

So the phonemiser reads more than Devanagari. But **Telugu and Odia are not
speech**: 22 s and 40 s of audio for a one-line sentence. Every one of the ten
returned **HTTP 200** - nothing in the response says which two are broken.

Two limits on what this proves. Plausible length is not correct pronunciation,
and the voice is Hindi either way, so another language gets a Hindi accent.
Both are for a speaker of the language to judge, which is why the probe keeps
its wav files.

## Still to measure

- **Chatterbox Multilingual v3** on the same sentences, for a quality comparison
  at the same latency bar.
- CPU and GPU utilisation during the 8-and-16 runs - the client side cannot see
  which of the two is the limit.
- A long soak: this has only been sampled for minutes, and the fault we are
  running from was one that only showed up over hours.
