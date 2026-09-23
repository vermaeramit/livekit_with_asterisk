# The GPU box — 10.130.9.248

Everything about this machine lives in this folder: what it is, what we decide
to run on it, and why. Added 23 Sep 2026, the day Soniox stalled on every
machine and network we could measure from and the question "what could we run
ourselves?" stopped being hypothetical.

It is **not** part of the call path today. Nothing on .243 or .244 points at it.

## What it is, exactly

Read off the box on 23 Sep 2026.

| | |
|---|---|
| OS | Ubuntu 24.04.4 LTS, kernel 6.8.0-139 |
| CPU | Intel Xeon Gold 6338 @ 2.00 GHz — **8 vCPU** (8 sockets × 1 core, so a VM) |
| RAM | **23 GiB** total, 21 GiB free |
| Disk | 490 GB on one LVM volume, **449 GB free** |
| GPU | **NVIDIA RTX A6000, 48 GB VRAM** (49140 MiB), compute capability 8.6 |
| Driver | 595.84 — supports CUDA up to 13.2 |
| CUDA toolkit | 12.0 installed (`nvcc`) |
| Docker | 29.6.2, with NVIDIA **CDI** devices registered (`nvidia.com/gpu=all`) |
| Python | 3.12.3 system-wide |
| Network | `ens33` 10.130.9.248/16, gateway 10.130.23.1 — the same gateway as .243 and .244 |
| Running | nothing but sshd. `docker0` is down, no containers |
| Uptime | 12 days, load 0.05 |

Two things worth keeping in mind from that list:

- **The GPU is large and the CPU is not.** 48 GB of VRAM will hold a great deal;
  8 vCPU is what audio resampling, HTTP serving and every other per-request cost
  has to share. For voice work the CPU is the likelier bottleneck, and it is the
  one that cannot be fixed by choosing a smaller model.
- **Docker already exposes the GPU through CDI**, so a container gets it with
  `--device nvidia.com/gpu=all`; no legacy `--gpus` runtime setup needed.

## Why it matters here

The calling stack buys three things per call: speech-to-text, a language model,
and text-to-speech. All three are outside our control today, and the last one
has been visibly unreliable for three days (see `docs/PROGRESS.md`, 21-23 Sep).
This box could host one or more of them, which changes the failure from "a
vendor is slow and we wait" to something we can watch and restart.

What it cannot change is the bar: a phone call needs audio **faster than real
time, continuously**. A model that renders 10 s of Hindi in 9 s on an idle GPU
is not good enough at four concurrent calls. Whatever we try here gets measured
the same way the vendors were — first audio, and stalls under load — before it
goes anywhere near a caller.

## Where we start

Decided 23 Sep 2026: **start on the 8 vCPU it has**, with one stream, and ask IT
for more only once a measurement says how much. One stream is enough to learn
what a stream costs; the rest is multiplication and a check for where it breaks.

When the ask does go in, two things matter besides the count:

- **Topology.** It is configured as 8 sockets × 1 core. That is a bad VMware
  layout - every vCPU looks like its own socket and both the scheduler and NUMA
  make worse decisions for it. Ask for **1 socket × N cores**.
- **RAM.** 23 GiB against 48 GB of VRAM is lopsided; a model has to pass through
  host memory to reach the card. 48 GB would balance it.

A starting point, to be replaced by measurement: 16 vCPU and 48 GB for TTS
alone; 32 and 64 if STT moves here too.

### Streaming candidates — what we are actually trying

Latency is the point of this box, so the shortlist is models that start speaking
early, not models that sound best in a studio. Two of them expose an
**OpenAI-compatible `/v1/audio/speech`**, and livekit's OpenAI TTS plugin takes a
`base_url` — so integrating either is a branch in `_build_tts` and a voice list
in the console. No new plugin.

| Model | Size | Hindi | Licence | Speed | Server |
|---|---|---|---|---|---|
| **Kokoro** | 82M | 4 voices, graded C by its own authors | Apache 2.0 | RTF ~0.03 | Kokoro-FastAPI, **OpenAI-compatible** |
| **Chatterbox Multilingual v3** | 0.5B | yes, among 25 languages | **MIT** | Turbo ~75 ms, ~6× real time | Chatterbox-TTS-Server, **OpenAI-compatible** |
| Magpie-TTS Multilingual | 357M | added in the v2602 checkpoint | NVIDIA Open Model (commercial OK) | built for voice agents | Riva / NIM — more work |
| Orpheus | 3B | multilingual preview | Apache 2.0 | ~130 ms TTFB | community FastAPI |
| NeuTTS Air | 0.5B | **English only** | — | — | ruled out |

Trying **Kokoro and Chatterbox together**, because they integrate the same way:
the small one sets the latency floor, the larger one is the better bet on Hindi.
Note Chatterbox embeds a **PerTh watermark** in every output - harmless on a
phone line, but a fact to know before shipping it.

### The non-streaming candidates, and the bar

The bar is not quality first. It is **audio faster than real time, continuously,
under load** - the same bar the vendors are failing this week. Quality decides
between the models that clear it.

| Model | Hindi | Licence | Streams? |
|---|---|---|---|
| **IndicF5** (AI4Bharat) | 11 Indian languages, 1417 h of Indian speech | to verify | renders a whole utterance |
| **Indic Parler-TTS** (AI4Bharat) | 20+ Indic | Apache 2.0 | autoregressive, slow |
| **Orpheus** | Hindi in the multilingual preview | Apache 2.0 | yes |
| **Magpie-TTS** (NVIDIA) | 9 languages incl. Hindi, 357M | NVIDIA Open Model, commercial use allowed | yes |

Licences are the first gate and are **not yet confirmed** - IndicF5's in
particular. A model we cannot ship is not a candidate however good it sounds.

First test: **IndicF5** for the quality bar, against the same Hindi sentences
`tts-bench` uses, so it can be compared with Sarvam and Soniox by ear; and
**Orpheus or Magpie** for streaming and speed. Measured the same way the vendors
were - first audio, then stalls at 1, 2, 4 and 8 concurrent streams.

## Open questions

- Which layer first: TTS (the one failing), STT, or the LLM?
- Which Hindi TTS, and how does it sound next to Sarvam and Soniox on the same
  sentences? Licence matters as much as quality.
- How many concurrent calls before the 8 vCPU or the GPU gives out?
- How does the agent reach it - an OpenAI-compatible endpoint, or a plugin of
  our own?
- Who restarts it at 2 a.m.? Self-hosting moves the outage rather than removing
  it, and this box has no monitoring today.
