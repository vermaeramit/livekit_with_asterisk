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

## Still to measure

- **Chatterbox Multilingual v3** on the same sentences, for a quality comparison
  at the same latency bar.
- CPU and GPU utilisation during the 8-and-16 runs - the client side cannot see
  which of the two is the limit.
- A long soak: this has only been sampled for minutes, and the fault we are
  running from was one that only showed up over hours.
