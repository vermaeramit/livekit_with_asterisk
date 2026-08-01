# AI Calling System — LiveKit + Asterisk

Low-latency AI voice agent for an existing Asterisk SIP dialer.
Real-time conversation with **STT → LLM → TTS**, live **barge-in**, and a **knowledge base**.

> **Current status:** Step 9 (knowledge base) complete. The agent holds a real conversation
> in Hindi over the phone, answers from a **PDF knowledge base**, and **no longer invents
> facts** — it says "I don't have that" and offers to escalate.
> See [docs/PROGRESS.md](docs/PROGRESS.md) for the detailed step log and
> [docs/RUNBOOK.md](docs/RUNBOOK.md) for day-to-day commands.

---

## 1. Goal

The company already runs a **working Asterisk + SIP dialer** placing outbound calls. This project
adds an **AI agent** into that same call path — replacing (or pre-qualifying for) the human agent.

Hard requirements:

| # | Requirement | Notes |
|---|---|---|
| 1 | **Lowest possible latency** | Target ≤ 850 ms mouth-to-ear |
| 2 | **Live barge-in** | Caller can interrupt the bot mid-sentence |
| 3 | **Knowledge base** | Grounded answers, not hallucinated |
| 4 | **Multi-language (Indic)** | Hindi + regional languages |
| 5 | **Enterprise ready** | Monitoring, fallbacks, no silent failure modes |

---

## 2. Architecture

```
                        ┌─────────── PHASE 2 (production) ───────────┐
   PSTN / SIP Trunk ───►│  Existing Asterisk Dialer  (separate box)  │
                        └────────────────────┬───────────────────────┘
                                             │ SIP trunk
   ┌─── PHASE 1 (now) ───┐                   │
   │  Eyebeam softphone  │──── SIP ──────────┤
   │  10.130.23.37       │                   │
   └─────────────────────┘                   ▼
   ╔══════════════════════════════════════════════════════════════╗
   ║              10.130.9.243   (Rocky Linux 8.10)               ║
   ║                                                              ║
   ║   Asterisk (test)                                            ║
   ║        │ SIP INVITE                                          ║
   ║        ▼                                                     ║
   ║   livekit-sip  ◄──► Redis                                    ║
   ║        │ WebRTC                                              ║
   ║        ▼                                                     ║
   ║   livekit-server                                             ║
   ║        │ WebRTC                                              ║
   ║        ▼                                                     ║
   ║   AI Agent (Python)                                          ║
   ║     ├─ VAD (Silero, local)      ─┐                           ║
   ║     ├─ Turn detector (local)     │  barge-in handled here    ║
   ║     ├─ STT  ─┐                   │                           ║
   ║     ├─ LLM   ├─ streaming       ─┘                           ║
   ║     └─ TTS  ─┘                                               ║
   ╚═════════════════════════┬════════════════════════════════════╝
                             │ HTTPS (outbound only)
                             ▼
                  Sarvam · Gemini · OpenAI
```

**Media never leaves the LAN.** Only AI API calls go to the internet.

### Why LiveKit and not raw Asterisk AudioSocket

| Approach | Latency | Effort | Verdict |
|---|---|---|---|
| **LiveKit SIP + Agents** | ~700–900 ms | Low | ✅ **Chosen** |
| Asterisk AudioSocket → custom Python service | ~600–800 ms | High | ❌ ~100 ms gain for 2 months of work |
| Speech-to-speech (Gemini Live / OpenAI Realtime) | ~400–600 ms | Low | 🔬 Worth benchmarking at Step 8 |

Barge-in, jitter buffering, turn detection and interruption handling are ~80 % of the work.
LiveKit ships all four production-grade. The custom path means rebuilding them.

---

## 3. Stack decisions

| Layer | Choice | Rationale |
|---|---|---|
| **STT** | Sarvam `saarika:v2.5` | 11 Indian languages native; India-hosted (30 ms RTT). Whisper-class models are weak on regional Indic. Needs `high_vad_sensitivity=true` — see below. |
| **LLM** | OpenAI **`gpt-4.1-mini`** | Picked by benchmark: 608 ms median TTFT with only an 85 ms spread |
| **TTS** | Sarvam `bulbul:v3` | Same 11 languages, natural Indic prosody |
| **VAD / turn detection** | Silero + LiveKit multilingual detector | Runs locally on CPU — zero network latency |
| **Media server** | LiveKit (self-hosted) | Keeps all media on the LAN |
| **Config store** | Postgres (pgvector) | Prompt/model/voice changes are a SQL update, not a deploy |

**Rejected:** Deepgram — 286 ms TCP RTT from this server, no India edge. Disqualifying for streaming STT.
**Not needed:** Anthropic, ElevenLabs — Sarvam covers Indic better for this use case.

### Measured latency (Step 8, real calls)

| Stage | Median | Note |
|---|---|---|
| Transport (RTP + WebRTC, both ways) | 205 ms | Measured in Step 7 |
| **Endpointing (`eou`)** | **1030 ms** | **Largest remaining item** — includes STT |
| ├ STT transcript | 327 ms | Was 1000 ms before `high_vad_sensitivity` |
| ├ Turn detector inference | 200–450 ms | Local ONNX, CPU-bound |
| └ `min_endpointing_delay` | 250 ms | |
| LLM TTFT | 738 ms | |
| TTS TTFB | 241 ms | |
| **Total mouth-to-ear** | **~2.1 s** | Range 1.2–2.8 s |

The original 830 ms estimate was optimistic on two counts: real transport is 205 ms (not
60 ms), and `gpt-4.1-mini` TTFT is ~610–740 ms (not 250 ms). Started at 2.5–4.7 s.

**Two findings worth carrying forward:**

- Sarvam's **server-side VAD** was costing ~700 ms per turn — it declared end-of-speech
  700 ms after the local Silero VAD did, and transcription only starts after that.
  `high_vad_sensitivity=true` removes almost all of it. The fine-grained VAD params are
  silently ignored on `saarika:*` (`supports_vad_params=False`).
- **Consistency beats average.** `gpt-4.1-mini` won over `gpt-4o-mini` mainly on variance
  (85 ms spread vs 800 ms) — a turn at 800 ms followed by one at 1550 ms is audible.

Sending the full knowledge base every turn would push LLM TTFT well past 1 s.
RAG must be a **tool call or pre-fetched**, not stuffed into the prompt (Step 9).

---

## 4. Open decisions

| # | Decision | Status |
|---|---|---|
| 1 | Language selection per call — pass from dialer via SIP header (0 ms) vs auto-detect (+300–500 ms) | ⏳ Does the CRM have a language field? |
| 2 | Production Asterisk flavour — vanilla / FreePBX / VICIdial | ⏳ Pending from dialer team |
| 3 | cgroup v2 migration (better CPU control + PSI metrics, needs a reboot) | ⏳ Optional |
| 4 | Scale-out plan for > 20 concurrent calls | 📌 Deferred — VM resize or extra worker box |

### Questions for the dialer team

1. Output of `asterisk -V` (gives version and flavour)
2. Channel driver — `chan_sip` or `chan_pjsip`?
3. Can a new SIP trunk be added, and calls routed to it?
4. Trunk codec — G.711 (ulaw/alaw) or G.729?

> Q4 is a hidden latency cost: G.729 forces transcoding on every call (~20–30 ms + CPU). G.711 is ideal.

---

## 5. Capacity

Current sizing target is **18–20 concurrent calls** on the existing 8 vCPU box.

Each call consumes roughly **0.35 vCPU** in the agent (VAD + turn detector + resampling).

| Concurrent calls | vCPU needed |
|---|---|
| 20 | ~8 (current box) |
| 50 | ~20 |
| 100 | ~39 |

Agent workers are **stateless** — scaling out later is an env-var change (`LIVEKIT_URL`),
not a redesign. Two options when the time comes:

- **A.** Resize this VMware VM to 24–32 vCPU (simple, but a single point of failure)
- **B.** Split: this box = LiveKit + SIP + Redis; add dedicated agent-worker boxes ← recommended

⚠️ On a single box, agent workers must have **CPU limits** so they cannot starve the LiveKit SFU.
A starved SFU degrades audio on **every** call, not just one.

---

## 6. Repository layout

```
livekit_with_asterisk/
├── README.md                  ← this file: architecture, decisions, latency
├── .env.example               ← required env vars, no values
├── docs/
│   ├── RUNBOOK.md             ← 🔧 commands, config, debugging — start here day-to-day
│   ├── PROGRESS.md            ← full build log, including what did NOT work
│   └── SERVER.md              ← inventory, ports, credentials map
├── agent/
│   ├── voice_agent.py         ← the agent (STT → LLM → TTS, barge-in, metrics)
│   ├── store.py               ← Postgres config + call/turn logging
│   ├── echo_agent.py          ← transport-only echo (Step 7)
│   ├── measure_latency.py     ← round-trip measurement via cross-correlation
│   ├── bench_llm.py           ← per-model TTFT benchmark
│   └── requirements.txt
└── server-configs/            ← mirror of what is deployed on the server
    ├── docker-compose.yml
    ├── postgres-schema.sql
    ├── asterisk/{Dockerfile,entrypoint.sh,conf/*}
    ├── livekit/livekit.yaml
    ├── redis/redis.conf
    └── sip/config.yaml.template
```

**Secrets are never committed.** The real `.env` lives only on the server; `.gitignore`
blocks `.env`, `*.key`, `*.pem`, and the substituted `sip/config.yaml`.

### Tags

| Tag | Milestone |
|---|---|
| `v0.3.0` | Step 8 — working AI voice pipeline |
| `v0.4.0` | Step 9 — knowledge base + tools *(planned)* |
| `v0.5.0` | Step 10 — production hardening *(planned)* |
| `v1.0.0` | Step 11 — admin panel *(planned)* |

---

## 7. Roadmap

| Step | Description | Status |
|---|---|---|
| 1 | Server discovery — specs, network, SELinux, firewall, AI latency baseline | ✅ Done |
| 2 | OS prep — updates, EPEL/CRB, timezone, kernel tuning, ulimits | ✅ Done |
| 3 | Docker CE + daemon config (log rotation, live-restore) | ✅ Done |
| 4 | Test Asterisk + Eyebeam registration, echo test | ✅ Done |
| 5 | LiveKit Server + Redis | ✅ Done |
| 6 | LiveKit SIP gateway + Asterisk trunk wiring | ✅ Done |
| 7 | Echo agent — **end-to-end latency baseline** | ✅ **205 ms** |
| 8 | Real pipeline: Sarvam STT → `gpt-4.1-mini` → Sarvam TTS + barge-in | ✅ **~1.9 s median** |
| 9 | Knowledge base + grounding | ✅ **no hallucinations** |
| 9b | Human transfer tool | ⏭️ **Next** |
| 10 | systemd service, first-call fix, fallbacks, monitoring, load test | ⬜ |
| 11 | Admin panel — agent config, live monitoring, call review | ⬜ |

### Knowledge base — two layers

```
Layer 1  prompt   small KB injected whole (heading index if large)  ->  0 ms/turn
Layer 2  tool     search_knowledge_base(query)                      ->  only on a miss
```

Retrieving on every turn was measured at **390–1244 ms per turn** whether or not the
question needed it. This way common questions cost nothing and only the rare ones pay.

Two results worth carrying forward:

- **Cross-script retrieval collapses.** STT emits Devanagari, the KB is English: the same
  question scored **0.19 and ranked the wrong chunk** in Devanagari vs **0.44** in English.
  Auto-transliteration was tested and rejected (phonetic output — `package` → `paikeja`).
  Making the KB a *tool* fixed it for free: the model writes the search query, in English.
- **Plain `get_text()` is not enough for real PDFs.** On a two-column layout it fused both
  pricing options into one unusable chunk. `pymupdf4llm` resolves reading order and emits
  markdown; retrieval went from 3/6 to 5/6 answerable queries on that change alone.

Injecting the KB also pushed the prompt past OpenAI's 1024-token caching threshold —
**95 % of the prompt is now cached**, so carrying the whole KB costs only ~114 ms.

### Carried into Step 10

- **First call after a worker restart only rings.** Worker startup takes ~7 s and `dev`
  mode keeps no idle processes. Fix: systemd unit running `start` mode with
  `num_idle_processes`, plus `Dial(...,8)` and a fallback route so a down worker fails over
  in 8 s instead of ringing for 30 s.
- **The LLM invents specifics** (it produced a fake Kotak missed-call number). Step 9's
  grounding is the fix.
- **Occasional TTS spike** — one turn at 1.26 s against a 0.23 s norm. Needs a fallback provider.
