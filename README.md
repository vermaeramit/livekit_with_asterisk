# AI Calling System — LiveKit + Asterisk

Low-latency AI voice agent for an existing Asterisk SIP dialer.
Real-time conversation with **STT → LLM → TTS**, live **barge-in**, and a **knowledge base**.

> **Current status:** live on the client's dialler. The agent holds a real Hindi
> conversation over the phone, answers from a **PDF and Word knowledge base**,
> **calls the client's own APIs mid-call**, **hands over to a human** on request
> with the caller's confirmation, **ends the call itself** when the conversation
> is done, **closes out** when nobody answers, and **posts each finished call to
> the client's API**. Everything is configured per campaign from a web console —
> prompts, voices, provider keys, tools, limits.
>
> [docs/COMMANDS.md](docs/COMMANDS.md) is the short list — deploy, restart,
> logs, health. [docs/RUNBOOK.md](docs/RUNBOOK.md) is the full operations
> reference, and [docs/PROGRESS.md](docs/PROGRESS.md) is the build log,
> including what did not work.

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
   ┌──────────────────────────────────────────────────────────────┐
   │  Client's Asterisk dialler   10.130.8.76                     │
   │  Places the call, waits for a human, THEN hands it to us      │
   └──────────────────────────┬───────────────────────────────────┘
                              │ IAX2  (not SIP - it is what they run)
                              │ per-call context as IAX variables
                              ▼
   ╔══════════════════════════════════════════════════════════════╗
   ║              10.130.9.243   (Rocky Linux 8.10)               ║
   ║              32 vCPU · 48 GB                                 ║
   ║                                                              ║
   ║   Asterisk 20.20.1  (native, not a container)                ║
   ║        │ SIP INVITE + X- headers, MixMonitor recording       ║
   ║        ▼                                                     ║
   ║   livekit-sip  ◄──► Redis (persisted - see below)            ║
   ║        │ WebRTC                                              ║
   ║        ▼                                                     ║
   ║   livekit-server                                             ║
   ║        │ WebRTC                                              ║
   ║        ▼                                                     ║
   ║   AI Agent  ×6 workers (systemd, 6 warm processes each)      ║
   ║     ├─ VAD (Silero, local)      ─┐                           ║
   ║     ├─ Turn detector (local)     │  barge-in handled here    ║
   ║     ├─ STT  ─┐                   │                           ║
   ║     ├─ LLM   ├─ streaming       ─┘                           ║
   ║     ├─ TTS  ─┘                                               ║
   ║     ├─ Knowledge base  (pgvector)                            ║
   ║     └─ HTTP tools      (the client's own APIs)               ║
   ║                          │                                   ║
   ║   Admin console ─────────┤  Postgres                         ║
   ║   (React + FastAPI)      │  config · calls · transcripts     ║
   ║        └─ postback sweeper ──────────► the client's API      ║
   ╚═════════════════════════┬════════════════════════════════════╝
                             │ HTTPS (outbound only)
                             ▼
                  Sarvam · Soniox · OpenAI
```

**Media never leaves the LAN.** Only AI API calls and the client's own tool
endpoints go out.

**The dialler hands over already-connected calls.** Everything the caller hears
before the agent speaks is dead air to a person who is already on the line,
which is why the startup path has been measured to the millisecond.

> ⚠️ Redis holds the SIP trunk and dispatch rule. It runs with `save 60 1`
> because the first reboot of this box lost them, and every call then rang and
> died with nothing in any log to say why.

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
| **Config store** | Postgres (pgvector) | Everything is per campaign and edited in the console — no deploy |
| **Provider choice** | Per campaign, keys encrypted per client | STT, TTS and their fallbacks are columns, not constants |

**Rejected:** Deepgram — 286 ms TCP RTT from this server, no India edge. Disqualifying for streaming STT.

**Measured and not adopted:** Soniox. Three to four times slower than Sarvam on
every layer across 144 turns against 661. It stays wired and selectable — the
campaign currently runs on it by choice — and finding that out produced two
things worth having: `tts-rt-v1` is removed on 31 Aug 2026 and was the
hardcoded default, and the console's voice list is now read from the provider
per model rather than held as a literal that had already gone stale.

**Not needed:** Anthropic, ElevenLabs — Sarvam covers Indic better for this use case.

### Measured latency

Two numbers matter, and they are not the same one.

**Before the caller hears anything** — the dialler hands over a connected human,
so this is silence to someone already holding a phone:

| Stage | Was | Now |
|---|---|---|
| invite → 180 Ringing (livekit-sip) | 5 ms | 5 ms |
| invite → participant in room | 43 ms | 43 ms |
| `ctx.connect()` | 312 ms | 312 ms |
| config + keys + prompt | **1154 ms** | **339 ms** |
| session build | 159 ms | 159 ms |
| **ring the caller hears** | **~2.07 s** | **~0.75 s** |
| **invite → first spoken word** | **~4.03 s** | **~2.4 s** |

The 1154 ms was `import kb` sitting inside `build_instructions`. It pulls in
pymupdf4llm — PyMuPDF, a large C extension used only for PDF *ingestion* — plus
tiktoken and the OpenAI SDK. A job process handles exactly one call and then
exits, so it was paid **once per caller**. Moved to `prewarm`, where the process
is idle anyway.

LiveKit's share of the original 4030 ms was 355 ms, which is worth knowing: the
dialler team proposed moving off it, reasonably, based on another project.

**Per turn**, once talking:

| Stage | Sarvam | Soniox |
|---|---|---|
| Turn detection (`eou`, includes STT) | ~1050 ms | 1454 → **1178 ms** |
| STT transcript | ~240 ms | 1067 → **750 ms** |
| TTS first byte | ~280 ms | ~950 ms |
| **Total** | **~1.9 s** | 3076 → **2695 ms** |

Soniox is the chosen provider despite the gap. Its endpointing level was at the
default (0) and had simply never been set; level 3 took ~380 ms off every turn.

**Three findings worth carrying:**

- Sarvam's **server-side VAD** cost ~700 ms per turn until
  `high_vad_sensitivity=true`. The fine-grained VAD params are silently ignored
  on `saarika:*` (`supports_vad_params=False`).
- **Consistency beats average.** `gpt-4.1-mini` won over `gpt-4o-mini` mainly on
  variance (85 ms spread vs 800 ms) — a turn at 800 ms followed by one at
  1550 ms is audible.
- **Prompt caching is real and fragile.** 1198 ms cold against 805 ms warm, and
  it needs a byte-identical prefix. Per-caller context therefore arrives as a
  separate chat message, never inside `instructions`.
---

## 4. Open decisions

Ranked by what happens if nobody acts.

| # | Decision | Why it matters |
|---|---|---|
| 1 | **Database backups** | Script and timer exist — see [docs/DATABASE.md](docs/DATABASE.md). Not yet installed on the server, and `SECRETS_KEY` still has to be stored somewhere off this box or the dumps are undecryptable. |
| 2 | **`HANDOFF_EXTEN` is empty** | Transfer works end to end — the agent asks, the caller agrees, the REFER goes out — and stops at extension `800`. Needs one queue number from the dialler team. |
| 3 | **Firewall is off, and the IAX peer is unauthenticated** | `permit=0.0.0.0/0`, `requirecalltoken=no`, `insecure=port,invite`. Fine on an isolated LAN, and a decision that should be made rather than inherited. |
| 4 | **IAX register credential is `76SERVER:76SERVER`** | Username and password are the same string. |
| 5 | Recording disclosure on campaign 1 is a single dot | Every call is recorded unconditionally by the dialplan. Callers are told nothing. |
| 6 | Real DIDs are not mapped | Needs the dialler to send `${EXTEN}`, and `_7XX` widened. Everything routes through 700 today. |
| 7 | Capacity on native Asterisk is assumed, not measured | 20/20 was proven under Docker. Deferred to last by request, to avoid spending provider credit on synthetic calls. |

### Settled

- **Trunk** — the dialler runs IAX2, not SIP. A SIP trunk was built first and
  declared working before anyone checked the channel name; it was never used.
- **Asterisk flavour** — moved out of Docker to a native 20.20.1 build, because
  the team who operate telephony do not work with containers. The usual argument
  against Asterisk in Docker never applied here: `network_mode: host` meant no
  NAT, no port mapping, and no measurable difference.
- **Codec** — G.711 ulaw end to end. No transcoding.
- **Language** — per campaign in the console, not auto-detected.

---

## 5. Capacity

The box was resized mid-project to **32 vCPU / 48 GB**.

**20 concurrent calls, 0 dropped** — proven, but under the Docker Asterisk. The
native install has not been re-measured.

The ceiling that mattered was never CPU. Dispatch stopped dead at 6 concurrent
while the box sat at 12 % across 32 cores, because LiveKit weights workers by
`1 - w.Load()` and livekit-agents reports **system-wide CPU** as that load.
Every worker on one box therefore reports the same number and they all lose
capacity at the same instant. The fix is on the agent side — `load_fnc` counting
jobs instead — and concurrency is now capped by
`MAX_JOBS_PER_WORKER × workers`, nowhere else.

Agent workers are **stateless**, so scaling out is an env-var change
(`LIVEKIT_URL`), not a redesign:

- **A.** Resize this VM further — simple, single point of failure
- **B.** Split: this box = LiveKit + SIP + Redis; add agent-worker boxes ← recommended

⚠️ On a single box, agent workers need **CPU limits** so they cannot starve the
LiveKit SFU. A starved SFU degrades audio on **every** call, not just one.

---

## 6. Repository layout

```
livekit_with_asterisk/
├── README.md                  ← this file: architecture, decisions, latency
├── .env.example               ← required env vars, no values
├── docs/
│   ├── COMMANDS.md            ← ⚡ the short list — deploy, restart, logs, health
│   ├── RUNBOOK.md             ← 🔧 the long version: config, debugging, recovery
│   ├── PROGRESS.md            ← full build log, including what did NOT work
│   ├── DATABASE.md            ← 💾 backup, restore, and what a dump alone cannot restore
│   └── SERVER.md              ← inventory, ports, credentials map
├── migrations/                ← numbered SQL, every one safe to re-run
│   └── 001…021_*.sql
├── agent/
│   ├── voice_agent.py         ← the agent: pipeline, markers, silence, handoff
│   ├── store.py               ← Postgres: config, calls, turns, tools, postbacks
│   ├── prompt.py              ← instruction assembly (the cacheable prefix)
│   ├── kb.py                  ← PDF + Word ingestion, chunking, hybrid retrieval
│   ├── ingest.py              ← CLI: ingest everything in the inbox
│   ├── tools.py               ← per-campaign HTTP tools, with fillers + timeouts
│   ├── toolfmt.py             ← placeholder fill + response path, shared with the console
│   ├── postback.py            ← extract a finished call into the client's fields
│   ├── crypto.py              ← Fernet; the console imports this one, never a copy
│   └── requirements.txt
├── admin/
│   ├── docker-compose.yml     ← its own project: rebuilding never touches a call
│   ├── backend/               ← FastAPI: config, calls, KB, tools, postback sweeper
│   └── frontend/              ← React console
└── server-configs/            ← mirror of what is deployed
    ├── docker-compose.yml     ← media stack (livekit, sip, redis, postgres)
    ├── postgres-schema.sql
    ├── asterisk/conf/*        ← dialplan, iax/pjsip templates, logger
    ├── systemd/asterisk.service
    ├── livekit/livekit.yaml
    ├── redis/redis.conf       ← save 60 1 — see the architecture note
    ├── backup-db.sh           ← nightly dump, verified before it is kept
    ├── loadtest.sh
    ├── tool-stub-api.py       ← /slow, /fail, /huge — for testing tools honestly
    ├── provider-catalog.py    ← ask a provider what it offers, key never on screen
    └── prompt-glamourx.md     ← the live campaign prompt, and why it reads that way
```

**Secrets are never committed.** The real `.env` lives only on the server;
`.gitignore` blocks `.env`, `*.key`, `*.pem`, `pjsip.conf`, `iax.conf` and the
substituted `sip/config.yaml`. Provider keys, tool auth values and the postback
credential are Fernet-encrypted in Postgres and never returned by the API — the
console works from a four-character hint.

### Tags

| Tag | Milestone |
|---|---|
| `v0.3.0` | Step 8 — working AI voice pipeline |

> The only tag ever cut. Steps 9 through 12 are all done and on `main`; the
> history is in [docs/PROGRESS.md](docs/PROGRESS.md), which is the honest record
> and considerably more useful than a tag would have been.

---

## 7. Roadmap

### Done

| Step | Description | Result |
|---|---|---|
| 1–3 | Server prep, Docker, kernel and ulimit tuning | ✅ |
| 4–6 | Asterisk, LiveKit Server, Redis, livekit-sip trunk | ✅ |
| 7 | Echo agent — transport-only latency baseline | ✅ **205 ms** |
| 8 | Real pipeline: STT → `gpt-4.1-mini` → TTS + barge-in | ✅ **~1.9 s median** |
| 9 | Knowledge base + grounding | ✅ no hallucinations |
| 9b | Human transfer over SIP REFER | ✅ verified end to end |
| 10a | systemd, fallback route, load test | ✅ |
| 10b | Provider fallback chains — STT, TTS, LLM | ✅ |
| 10c | Cost guardrails + monitoring | ✅ |
| 11 | Admin console — auth, RBAC, tenants, campaigns, KB, analytics, recordings, live monitor, alerting | ✅ |
| — | Capacity | ✅ **20 concurrent, 0 dropped** (under Docker Asterisk) |
| — | Dispatch ceiling — root cause was CPU-based load reporting | ✅ |
| — | Asterisk out of Docker — native 20.20.1, SELinux labels, recordings migrated | ✅ |
| 12.1 | Per-client encrypted provider keys; STT/TTS chosen per campaign | ✅ |
| 12.2 | Dialler trunk over IAX2; per-call context into the prompt and the database | ✅ |
| 12.3 | HTTP tools — schema, executor, console CRUD, test button, activity log | ✅ |
| 12.4 | Call diagnostics — tool calls in the transcript, dialler context, providers used | ✅ |
| 12.5 | Startup latency — 4.03 s to first word cut to **~2.4 s** | ✅ |
| 12.6 | End-of-call and transfer markers, silence handling, transfer confirmation | ✅ |
| 12.7 | Postback — extract a finished call and POST it, with retries and a log | ✅ |
| 12.8 | Word documents in the knowledge base | ✅ |

### Next

Ranked by consequence, not by effort.

| | Work | Why now |
|---|---|---|
| 1 | **Install the backup timer** | Written, not running. And store `SECRETS_KEY` off this box — a dump without it restores rows nobody can decrypt. |
| 2 | **Wire `HANDOFF_EXTEN`** | A caller who asks for a person gets confirmation, a transfer, and then nothing. Blocked on one number from the dialler team. |
| 3 | **Decide the firewall and IAX authentication** | Currently open, on purpose, on an isolated LAN. It should stay that way by decision rather than by default. |
| 4 | **A real recording disclosure** | Every call is recorded. Campaign 1 says nothing. |
| 5 | **Verify the postback against a real endpoint** | Built and deployed; nothing has actually been received by anyone yet. |
| 6 | **Load test 20 concurrent on native Asterisk** | Deliberately last — synthetic calls spend real provider credit. |

### Not planned, and why

- **Excel in the knowledge base.** A price or dealer list is an exact lookup;
  vector search answers those approximately. That data belongs in a tool, which
  is where `dealer_by_pincode` already lives.
- **Pre-rendered greeting audio.** The greeting carries the caller's name, so
  there is nothing constant to cache.
- **Shipping agent logs into the console.** The per-call diagnostics answer the
  recurring questions. Raw logs matter when something *crashes*, which is rare —
  if the server still gets SSH'd into regularly, that is the measurement that
  justifies the pipeline.

---

### Provider fallback — wired, and half of it is proven

The TTS chain earned its keep: Sarvam ran out of credits mid-load-test and every call
switched to OpenAI TTS without dropping, logging
`livekit.plugins.sarvam.tts.TTS error, switching to next TTS`.

The LLM chain did **not** hold in the same run — `all LLMs are unavailable, retrying..`
means OpenAI and Gemini failed together, and three calls ended `end_reason='error'`.
Untangling that is deferred, not done.

> A dead primary is not free. `FallbackAdapter` tries the primary first **every
> utterance**, so while Sarvam was out of credits TTS time-to-first-byte went from
> ~240 ms to ~2200 ms — the failed attempt, not OpenAI being slow. Restore the credits or
> switch the campaign's provider; leaving a dead primary in place is the worst of both.

| Layer | Primary | Fallback |
|---|---|---|
| STT | Sarvam `saarika:v2.5` | OpenAI `gpt-4o-mini-transcribe` |
| TTS | Sarvam `bulbul:v3` (241 ms) | OpenAI `gpt-4o-mini-tts` (889 ms) |
| **LLM** | OpenAI `gpt-4.1-mini` (608 ms) | **Gemini `flash-lite-latest`** (650 ms) |

**All four Gemini TTS models were tested and rejected** — 3.5–15 s TTFB, consistent across
runs. Four seconds of silence before every reply is worse than a changed voice. The GCP
setup it took to find that out (org policy, service account, Vertex AI role, `LINEAR16`
encoding) is all in place and documented.

LLM is the only layer with genuine provider diversity: Gemini flash-lite matches the
primary's latency, so a full OpenAI outage costs speech and hearing but not thought.
### Guardrails and monitoring

Until Step 10c the system had **no brake at all** — `max_turns` and `max_duration_sec` had
been in the config since Step 8 but nothing read them, so a call could run indefinitely and
a looping LLM had nothing stopping it. Three limits now enforce, and a breach makes the
agent speak a closing line and wait for playout before ending the call:

| Limit | Default |
|---|---|
| `max_duration_sec` | 600 |
| `max_turns` | 40 |
| `max_prompt_tokens` | 150000 |

The token cap is what actually bounds spend — one observed call used **32,816 prompt
tokens**, because the knowledge base rides along on every request.

Monitoring reads **Postgres directly** — every call metric already lives in
`calls`/`turns`, so no Prometheus or exporter is involved. The worker's HTTP port was
checked first: it answers `/` but exposes no `/metrics`.

This started as Grafana and moved into the admin panel, for two reasons: two places
to maintain the same charts, and Grafana had no notion of tenants — every client
would have seen everyone's calls.

The most useful panel is **"Where the time goes"** — `eou` vs `llm_ttft` vs `tts_ttfb`
stacked. A rising `eou` is our machine (VAD and turn detection run locally); rising
`llm`/`tts` is the provider. That one chart separates the two.

### Measured capacity

| Concurrent | p50 | p95 | System load (8 cores) |
|---|---|---|---|
| 1 | 1921 ms | ~2400 ms | — |
| **10** | **2001 ms** | **2776 ms** | 2.0–2.2 (~27 %) |

Three worker instances; latency essentially flat under load.

**20 concurrent now passes** — 20 requested, 20 got an agent, 0 fell to the human
fallback — on 32 cores / 48 GB with six workers and `MAX_JOBS_PER_WORKER=10`.

Getting there took a day, because the ceiling was not where any dial suggested. It did
not move for worker count (1/3/6), `load_threshold` (0.7/5.0/`inf`), warm-pool size,
arrival stagger, or a hardware upgrade from 8 cores/12 GB. LiveKit picks a worker by
weighting `max(0, 1 - w.Load())`, and `w.Load()` is whatever the worker reports —
livekit-agents defaults to **system-wide CPU**, clamped to 1.0. One live call pins a core,
the value saturates, the weight hits zero, and since the metric is system-wide every
worker on the box loses its weight at the same instant. That is precisely why adding
workers, cores and RAM achieved nothing.

The fix is to report what actually limits us: `load_fnc` returns
`len(active_jobs) / MAX_JOBS_PER_WORKER`. CPU never described this workload — STT, LLM and
TTS are network calls, and a conversation is mostly waiting.

**Two more production-only defaults** bit along the way. `WorkerOptions` carries separate
dev and prod values:

- `load_threshold` (dev `inf`, prod `0.7`) gates the worker's own availability check — a
  gate LiveKit never consults. An afternoon went into tuning it for nothing.
- `port` (dev random, prod fixed `8081`) made every extra worker crash-loop on
  `address already in use`. `systemctl is-active` reported all three healthy while only one
  had registered — three load-test runs were interpreted on that false assumption.

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

### Three problems from Step 8, and where they went

- **First call after a worker restart only rang.** Worker startup took ~7 s and
  `dev` mode keeps no idle processes. Fixed: systemd units running `start` mode
  with `num_idle_processes`, plus a fallback route so a down worker fails over
  rather than ringing for thirty seconds.
- **The LLM invented specifics** — it produced a fake missed-call number.
  Grounding fixed it, and the campaign prompt now says plainly what may not be
  invented.
- **Occasional TTS spike** — one turn at 1.26 s against a 0.23 s norm. Fallback
  chains cover it, and `providers_used` records per call which provider actually
  served it, so a fallback that fired is visible instead of being inferred from
  a resampling line in a worker log.

---

The sections above are the short version. [docs/PROGRESS.md](docs/PROGRESS.md)
is the long one — every step, every measurement, and the wrong answers that
were tried first, which is usually the part worth reading.
