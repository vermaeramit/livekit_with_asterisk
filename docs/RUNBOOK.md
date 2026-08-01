# Runbook — Commands, Config, Debugging

Day-to-day operations reference for `10.130.9.243`.
Architecture is in [../README.md](../README.md); the build history is in [PROGRESS.md](PROGRESS.md).

**Everything below assumes you are `root` on the server unless stated otherwise.**

---

## 0. Load credentials into your shell

Almost every command needs this first. It is easy to forget and the errors are confusing
(`401`, `MISSING`, `no such table`).

```bash
export LIVEKIT_URL=ws://127.0.0.1:7880      # ws:// for the agent
set -a; source /opt/aivoice/.env; set +a
```

> The `lk` CLI wants **`http://`**, the agent worker wants **`ws://`**. Same port, different
> scheme. `lk` will silently fail to connect with `ws://`.

```bash
export LIVEKIT_URL=http://localhost:7880    # for lk CLI only
```

Verify keys are loaded without printing them:

```bash
for k in LIVEKIT_API_KEY LIVEKIT_API_SECRET SARVAM_API_KEY OPENAI_API_KEY DATABASE_URL; do
  v=$(eval echo \$$k)
  printf "%-20s %s\n" "$k" "$([ -n "$v" ] && echo "SET (${#v} chars)" || echo MISSING)"
done
```

> ⚠️ Do **not** use `${v:-MISSING}` here — when the variable is set that expands to the
> actual secret and prints it.

---

## 1. Start / stop everything

### Infrastructure (Docker)

```bash
cd /opt/aivoice

docker compose up -d              # start all
docker compose ps                 # status
docker compose down               # stop all (calls drop)
docker compose restart asterisk   # one service
```

| Service | What it is |
|---|---|
| `asterisk` | Test PBX — softphone registration, dialplan |
| `redis` | LiveKit coordination (loopback only) |
| `livekit` | SFU / media server |
| `sip` | SIP ↔ WebRTC gateway on port 5080 |
| `postgres` | Agent config, call logs, transcripts |

### The AI agent (systemd)

Three worker instances plus the prompt-cache warmer:

```bash
systemctl status  aivoice-agent@1 aivoice-agent@2 aivoice-agent@3
systemctl restart aivoice-agent@1 aivoice-agent@2 aivoice-agent@3
systemctl status  aivoice-cache-warmer

journalctl -u aivoice-agent@2 -f | grep -A 1 -E "voice-agent|ERROR"
journalctl -u aivoice-cache-warmer -f
```

Add a worker (each needs its **own port**, see below):

```bash
systemctl enable --now aivoice-agent@4     # binds 8084 via AGENT_HTTP_PORT=808%i
```

> 🚨 **`systemctl is-active` is NOT proof a worker is working.** With `Restart=always`, a
> crash-looping worker still reads `active`. Always verify registration:

```bash
for i in 1 2 3; do
  printf "worker %s: %-8s registered=%s errors=%s\n" $i \
    "$(systemctl is-active aivoice-agent@$i)" \
    "$(journalctl -u aivoice-agent@$i --since '-2min' --no-pager | grep -c 'registered worker')" \
    "$(journalctl -u aivoice-agent@$i --since '-2min' --no-pager | grep -c 'worker failed')"
done
ss -tlnp | grep -E ':808[0-9]'      # one listener per worker
```

This exact failure cost three load-test runs: two workers were crash-looping on
`[Errno 98] address already in use` (fixed port 8081) while systemd reported all three
healthy.

### Manual run (debugging only)

```bash
cd /opt/aivoice/agent && source .venv/bin/activate
export LIVEKIT_URL=ws://127.0.0.1:7880
set -a; source /opt/aivoice/.env; set +a
python voice_agent.py dev 2>&1 | grep -A 1 -E "voice-agent|ERROR|Traceback"
```

⚠️ Stop the systemd workers first, or jobs land randomly across them.

> **`dev` and `start` behave differently in ways that matter.** `WorkerOptions` uses
> separate dev/prod defaults, and the production ones caused two real outages:
>
> | Option | dev | **prod** |
> |---|---|---|
> | `load_threshold` | `inf` | **0.7** — blocked dispatch at 3 concurrent |
> | `port` | `0` (random) | **8081** (fixed) — extra workers crash-loop |
> | `num_idle_processes` | `0` | 2 — first call after restart only rang |
>
> A problem that does not reproduce in `dev` may still be real in production.

Readable log filter (keeps the timing continuation lines):

```bash
python voice_agent.py dev 2>&1 | grep -A 1 -E "voice-agent|ERROR|Traceback"
```

> `-A 1` matters. Per-turn timings are printed on a continuation line with no
> `voice-agent` prefix, so a plain grep silently drops them.
>
> 🚨 **Always keep `ERROR|Traceback` in the filter.** A crash in the entrypoint is logged
> under `livekit.agents`, not `voice-agent` — filtering on the agent name alone hides it,
> and the only symptom is a call that rings forever. That cost real debugging time twice.

Wider filter when debugging:

```bash
python voice_agent.py dev 2>&1 \
  | grep -A 1 -E "voice-agent|ERROR|Traceback|EOU metrics|LLM metrics|TTS metrics|eou prediction"
```

---

## 2. Test extensions

Dial these from the Eyebeam softphone (registered as `1001`).

| Ext | Behaviour | Proves |
|---|---|---|
| `600` | Asterisk's own echo test | SIP + RTP both directions, codec |
| `601` | `hello-world` playback | One-way RTP |
| **`700`** | **Route into a LiveKit room → AI agent** | The whole chain |
| `702` | Same as 700 but records rx/tx separately | Latency measurement (§7) |
| `1001` | Dial the softphone back | Outbound leg |

---

## 3. Configuration

### Agent behaviour — Postgres (no restart of infra needed)

Config is read at the **start of each call**, so a SQL update applies to the next call.
Restart the agent worker if you want it applied immediately to a warm process.

```bash
docker exec postgres psql -U aivoice -d aivoice -c \
  "SELECT name, language, llm_model, tts_voice, allow_interrupt FROM agent_config;"
```

```sql
-- change the prompt
UPDATE agent_config SET instructions = 'You are ...', updated_at = now()
 WHERE name = 'default';

-- change the greeting
UPDATE agent_config SET greeting = 'Namaste! ...' WHERE name = 'default';

-- swap the LLM  (benchmark first - see §7)
UPDATE agent_config SET llm_model = 'gpt-4.1-mini' WHERE name = 'default';

-- switch language (affects STT and TTS together)
UPDATE agent_config SET language = 'ta-IN' WHERE name = 'default';

-- change voice
UPDATE agent_config SET tts_voice = 'shubh' WHERE name = 'default';

-- turn barge-in off
UPDATE agent_config SET allow_interrupt = false WHERE name = 'default';
```

Interactive psql:

```bash
docker exec -it postgres psql -U aivoice -d aivoice
```

### Tuning knobs — env vars

Set in `/opt/aivoice/.env` to persist, or exported per-run to experiment.

| Var | Default | Effect |
|---|---|---|
| `SARVAM_HIGH_VAD` | `1` | **Biggest lever.** Cuts Sarvam's end-of-speech lag ~1000 ms → ~250 ms |
| `MIN_ENDPOINTING_DELAY` | `0.25` | Silence before deciding the caller stopped. Lower = snappier, but cuts people off mid-thought |
| `MAX_ENDPOINTING_DELAY` | `1.5` | Cap when the turn detector is unsure. The 4.0 default froze calls for 4 s on short closings |
| `SARVAM_STT_MODEL` | `saarika:v2.5` | `saaras:v3` also exposes fine-grained VAD params |
| `SARVAM_TTS_VOICE` | *(from DB)* | Sarvam speaker name |
| `NUM_IDLE_PROCESSES` | `3` | Pre-warmed job processes (`start` mode only) |
| `AGENT_CONFIG` | `default` | Which `agent_config` row to load |

Params that do **not** work on `saarika:*` — the plugin drops them silently because
`supports_vad_params=False`: `negative_frames_count`, `negative_speech_threshold`,
`min_speech_frames`, `positive_speech_threshold`.
`flush_signal` was measured and made no difference.

### Asterisk

```bash
vi /opt/aivoice/asterisk/conf/extensions.conf   # dialplan
vi /opt/aivoice/asterisk/conf/pjsip.conf        # endpoints, trunk
cd /opt/aivoice && docker compose restart asterisk
```

Config is overlaid onto the image at startup, so a **restart** is enough — no rebuild.
Only Dockerfile changes need `docker compose up -d --build`.

### LiveKit SIP objects

```bash
export LIVEKIT_URL=http://localhost:7880
set -a; source /opt/aivoice/.env; set +a

lk sip inbound list
lk sip dispatch list
lk room list                    # only shows rooms during an active call
```

Recreate the dispatch rule (use **flags**, not a JSON file — see §6):

```bash
lk sip dispatch create --name lab-dispatch --trunks ST_xxxxx --individual call
```

---

## 3b. Knowledge base

### Add or update a document

```bash
cp new-policy.pdf /opt/aivoice/kb/inbox/

cd /opt/aivoice/agent && source .venv/bin/activate
set -a; source /opt/aivoice/.env; set +a
python ingest.py                 # unchanged files are skipped by sha256
python ingest.py --force         # re-ingest everything
```

Text-based PDFs only — there is no OCR. A scanned PDF reports
`no extractable text`.

| Case | What happens |
|---|---|
| Same file again | Hash matches → no-op |
| File changed | Old chunks deleted and new ones inserted in one transaction |
| New file | Added; nothing else is re-indexed |

### Inspect what is stored

```bash
docker exec postgres psql -U aivoice -d aivoice -c "
SELECT filename, page_count, chunk_count, enabled, updated_at
  FROM kb_documents ORDER BY filename;"

# dump every chunk with its heading path
python - <<'PY'
import asyncio, store
async def m():
    rows = await (await store.pool()).fetch(
        "SELECT seq, page, n_tokens, heading, content FROM kb_chunks ORDER BY seq")
    for r in rows:
        print("="*76)
        print(f"[{r['seq']}] p{r['page']} {r['n_tokens']}tok  {r['heading'] or '(none)'}")
        print(r["content"])
    await store.close()
asyncio.run(m())
PY
```

### Retire a document without deleting it

```bash
docker exec postgres psql -U aivoice -d aivoice -c \
  "UPDATE kb_documents SET enabled=false WHERE filename='old.pdf';"
```

### Test retrieval without making a call

```bash
python - <<'PY'
import asyncio, kb, store
async def m():
    for q in ["cancellation policy refund", "baggage allowance", "hotel name"]:
        hits = await kb.search(q, top_k=3, min_score=-1)   # -1 = show raw scores
        print(f"\nQ: {q}")
        for h in hits:
            print(f"  [{h['src']}] {h['score']:.3f}  {h['heading'] or '-'}")
    await store.close()
asyncio.run(m())
PY
```

> ⚠️ **Test with English queries** — that is what the agent's tool sends. Testing with
> Devanagari gives misleadingly bad scores (0.13–0.20 vs 0.44–0.48) and sent us chasing a
> non-existent bug. See PROGRESS.md §9.

### Two layers, and which one answered

```
kb=True(full, 1144 tok)     <- whole KB is in the prompt; no per-turn retrieval
kb=True(index, 8200 tok)    <- too big to inline; only headings are in the prompt
```

The switch is `agent_config.kb_inline_max_tokens` (default 6000). Below it the KB goes into
the prompt whole; above it only a heading index goes in and the model uses the tool.

A `TOOL search_knowledge_base(...)` line in the log means layer 1 did not cover the
question. Common questions should **not** produce one — that is the whole point.

### KB settings

```sql
UPDATE agent_config SET kb_enabled = true            WHERE name='default';
UPDATE agent_config SET kb_inline_max_tokens = 6000  WHERE name='default';
UPDATE agent_config SET kb_top_k = 3                 WHERE name='default';
UPDATE agent_config SET kb_min_score = 0.20          WHERE name='default';
```

> 🚨 **Adding a column to `agent_config` means also adding the field to the `AgentConfig`
> dataclass in `store.py`.** `load_config()` builds from `fields(AgentConfig)`, so a column
> missing there never reaches the agent — and the failure shows up as calls that ring
> forever with no visible error.

---

## 3c. Human transfer

The agent hands off with a SIP REFER. The caller's channel detaches from LiveKit and lands
on an extension in Asterisk's `from-livekit` context.

### Settings

```sql
UPDATE agent_config SET transfer_enabled = true                    WHERE name='default';
UPDATE agent_config SET transfer_to = 'sip:800@10.130.9.243'       WHERE name='default';
UPDATE agent_config SET transfer_message = 'Ek minute, main aapko jod raha hoon.'
                                                                    WHERE name='default';
```

### Point it at a real destination

`800` is currently a stand-in that plays a prompt and echoes. For production, edit
`/opt/aivoice/asterisk/conf/extensions.conf`:

```ini
[from-livekit]
exten => 800,1,NoOp(<-- TRANSFER landed)
 same => n,Dial(PJSIP/1002,30)          ; a human's extension
 ; or: same => n,Queue(support)
 same => n,Hangup()
```

then `docker compose restart asterisk`.

> 🚨 The target extension **must** be in `from-livekit` (that is the `livekit` endpoint's
> context, where the REFER arrives) and **must come before the `_.` catch-all**, which
> would otherwise match it and hang up the transfer.

### Verify a transfer

```bash
# agent side
#   TOOL transfer_to_human('...') -> sip:800@10.130.9.243  participant=sip_1001
#   TRANSFER OK -> sip:800@10.130.9.243

# asterisk side - this is the proof the call actually landed
docker compose logs --tail=40 asterisk | grep -E 'TRANSFER landed|left .simple_bridge|Playback'

# call record
docker exec postgres psql -U aivoice -d aivoice -c "
SELECT id, end_reason, transferred_to, transfer_reason, duration_ms
  FROM calls WHERE transferred_to IS NOT NULL ORDER BY id DESC LIMIT 5;"
```

`TRANSFER OK` in the agent log only means LiveKit **sent** the REFER. The Asterisk log is
what confirms the call reached the destination.

### Troubleshooting

| Symptom | Cause |
|---|---|
| Handoff line cut off mid-sentence | `wait_for_playout()` not awaited before the REFER |
| Call drops instead of transferring | Target extension missing from `from-livekit`, or shadowed by the `_.` catch-all |
| `transfer failed` in the agent log | Check `res_pjsip_refer.so` is loaded: `docker exec asterisk asterisk -rx "module show like refer"` |
| Transfers on questions it could answer | Tighten the HANDOFF rules in the prompt |

---

## 3d. Load testing

```bash
/opt/aivoice/loadtest.sh <concurrent> [stagger_seconds]
/opt/aivoice/loadtest.sh 10 0.5
```

Originates N calls through `Local/s@loadtest` bridged to `700`, so real speech flows and the
full STT → LLM → TTS path runs. It samples load, channels, per-container CPU and rooms every
2 s to `/tmp/loadtest-*.log`.

⚠️ Costs real API calls — each test call runs ~8 turns of STT + LLM + TTS.

### Reading the result

```bash
# by call id, NOT a time window - "--since -4min" repeatedly cut off the start
# of a test and produced phantom "only 7 of 10" results
docker exec -i postgres psql -U aivoice -d aivoice -c \
 "SELECT id, room_name, duration_ms FROM calls ORDER BY id DESC LIMIT 12;"

docker exec -i postgres psql -U aivoice -d aivoice -c "
SELECT count(*) AS turns,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms))  AS p50,
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY total_ms)) AS p95,
       round(avg(eou_ms)) AS eou, round(avg(llm_ttft_ms)) AS llm, round(avg(tts_ttfb_ms)) AS tts
  FROM turns t JOIN calls c ON c.id=t.call_id
 WHERE c.id BETWEEN <first> AND <last> AND t.role='agent' AND t.total_ms>0;"

# job distribution across workers
for i in 1 2 3; do
  printf "worker %s: " $i
  journalctl -u aivoice-agent@$i --since "HH:MM" --until "HH:MM" --no-pager \
    | grep -c "received job request"
done

# calls that fell through to the human fallback
docker compose -f /opt/aivoice/docker-compose.yml logs --since 10m asterisk \
  | grep -c "AI UNAVAILABLE"
```

### Measured

| Concurrent | p50 | p95 | System load (8 cores) |
|---|---|---|---|
| 1 | 1921 ms | ~2400 ms | — |
| **10** | **2001 ms** | **2776 ms** | 2.0–2.2 (~27 %) |

Three workers, jobs distributed 0/4/3. Above 10 is untested.

### If calls do not connect

| Check | |
|---|---|
| All workers registered? | See §1 — `is-active` alone is not enough |
| `AI UNAVAILABLE` in Asterisk log | Dial timed out; agent did not join in 8 s |
| `full capacity` in the journal | `load_threshold` blocking — it clamps to 0–1, so any value below 1.0 trips on a momentary spike. Use > 1.0 to disable. |
| `processing invite` vs `received job request` | SIP took the call but no agent was dispatched |

---

## 4. Health checks

```bash
cd /opt/aivoice
docker compose ps
docker stats --no-stream
ps aux | grep -c "[v]oice_agent"     # 0 = agent is not running

# ports
ss -tulnp | grep -E '5060|5080|6379|7880|7881|7882'

# asterisk
docker exec asterisk asterisk -rx "pjsip show endpoints"     # 1001 -> Avail
docker exec asterisk asterisk -rx "core show channels"       # during a call

# livekit + db
docker exec redis redis-cli ping                             # PONG
docker exec postgres pg_isready -U aivoice -d aivoice

# disk / memory
df -h / ; free -h
```

---

## 5. Logs

```bash
cd /opt/aivoice

docker compose logs -f --tail=50 sip         # SIP gateway - most useful
docker compose logs -f --tail=50 asterisk
docker compose logs -f --tail=50 livekit
docker compose logs --tail=50 postgres

# full SIP signalling trace (very verbose - turn off after)
docker exec asterisk asterisk -rx "pjsip set logger on"
docker exec asterisk asterisk -rx "rtp set debug on"
docker exec asterisk asterisk -rx "pjsip set logger off"
docker exec asterisk asterisk -rx "rtp set debug off"
```

Docker log rotation is set to 50 MB × 5 files per container in
`/etc/docker/daemon.json`. Without it, voice containers fill the disk within a week.

### Call history from the database

```bash
docker exec postgres psql -U aivoice -d aivoice -c "
SELECT id, room_name, caller, language, duration_ms, end_reason
  FROM calls ORDER BY id DESC LIMIT 10;"

# transcript + per-turn latency for the last call
docker exec postgres psql -U aivoice -d aivoice -c "
SELECT seq, role, left(text,60) AS text, eou_ms, stt_ms, llm_ttft_ms, tts_ttfb_ms, total_ms
  FROM turns WHERE call_id = (SELECT max(id) FROM calls) ORDER BY seq;"

# median latency across the last 100 turns
docker exec postgres psql -U aivoice -d aivoice -c "
SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY eou_ms)      AS eou_p50,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY llm_ttft_ms) AS llm_p50,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY tts_ttfb_ms) AS tts_p50,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY total_ms)    AS total_p50,
       percentile_cont(0.9) WITHIN GROUP (ORDER BY total_ms)    AS total_p90
  FROM (SELECT * FROM turns WHERE role='agent' AND total_ms > 0
        ORDER BY id DESC LIMIT 100) t;"
```

---

## 6. Symptom → cause

### Call rings and never connects

**Check the SIP gateway log first.** Asterisk can only tell you "180 Ringing, no 200 OK";
only livekit-sip knows why.

```bash
docker compose logs --tail=60 sip
```

| Log line | Cause | Fix |
|---|---|---|
| `Waiting for track subscription(s)` | **No agent in the room.** livekit-sip will not answer until something subscribes to its track | Start the agent; check `ps aux \| grep voice_agent` |
| `no dispatch rule matched` | Dispatch rule missing | `lk sip dispatch list`, recreate (§3) |
| `no trunk matched` | Source IP not in `allowed_addresses` | `lk sip inbound list` |
| `failed to connect to livekit` | Wrong `ws_url` or key in `/opt/aivoice/sip/config.yaml` | |
| Nothing at all | Call never reached livekit-sip | Check the Asterisk dialplan and trunk |

**First call after starting the agent always rings; the second works.** Known issue — the
worker takes ~7 s to register, and `dev` mode keeps no idle processes. Wait for
`registered worker` plus a few seconds before dialling. Real fix is Step 10.

### Registers but no audio

Signalling is fine, RTP is not.

```bash
firewall-cmd --zone=voip --list-all           # is the RTP range open?
docker exec asterisk asterisk -rx "rtp set debug on"
```

The workstation has three NICs (`10.130.23.37` LAN, plus hotspot and WSL adapters) and
softphones often advertise the wrong one in Contact/SDP. `pjsip.conf` already sets
`rewrite_contact=yes` and `rtp_symmetric=yes` so Asterisk ignores the advertised address
and replies to the real source. If it still fails, pin the Eyebeam Topology IP to
`10.130.23.37`.

### One-way audio

Almost always the same multi-homed-client problem. Pin the softphone IP.

### Choppy or robotic audio

```bash
docker stats --no-stream        # is a container pegged?
htop
sysctl net.core.rmem_max        # should be 26214400
```

On this single box, agent workers are CPU-capped precisely so they cannot starve the
LiveKit SFU — a starved SFU degrades audio on **every** call, not just one.

### Agent crashes on startup

| Error | Cause |
|---|---|
| `no job context found, are you running this code inside a job entrypoint?` | Something needing a job context was put in `prewarm_fnc`. Only `silero.VAD.load()` belongs there; `MultilingualModel()` goes in the entrypoint |
| `ModuleNotFoundError: No module named 'store'` | Not running from `/opt/aivoice/agent` |
| `KeyError: 'DATABASE_URL'` | `.env` not sourced (§0) |
| `401` from OpenAI/Sarvam | Key missing or wrong. OpenAI keys start `sk-`, Sarvam `sk_` |

### `lk` CLI: `proto: syntax error (line 1:1): invalid value sip`

`lk sip dispatch create <file.json>` treats the argument as **inline JSON**, not a path, so
`sip/objects/...` parses as the token `sip`. (`lk sip inbound create` accepts a path fine —
the subcommands are inconsistent.) Use flags, or stdin:

```bash
lk sip dispatch create - < dispatch-rule.json
```

### Agent invents facts

Known and expected until Step 9. It produced a plausible-looking bank phone number that
did not exist. Knowledge-base grounding is the fix; do not put a caller-facing agent into
production before then.

### `lk room list` is empty

Normal. The dispatch rule is `--individual`, so a room is created per call and deleted on
hangup. Watch during a call:

```bash
watch -n 1 lk room list
```

---

## 7. Measuring latency

### Per-turn, from the agent log

The fastest signal — read it live:

```
[assistant] कृपया बताएं आपका बैंक कौन सा है?
            eou=1030ms  stt=327ms  llm_ttft=738ms  tts_ttfb=241ms  total=1921ms
```

| Field | Meaning | Healthy |
|---|---|---|
| `stt` | Caller stopped speaking → final transcript | 250–400 ms |
| `eou` | → end-of-turn decision. **Includes `stt`** | 900–1100 ms |
| `llm_ttft` | → first LLM token | 600–800 ms |
| `tts_ttfb` | → first audio byte | 220–260 ms |
| `total` | `eou + llm_ttft + tts_ttfb` | ~1900 ms |

> `total` is **not** the sum of all four. `eou` already contains `stt`; adding both
> double-counts.

Add ~205 ms of transport for the real mouth-to-ear figure.

### Transport only (no AI)

Isolates the LiveKit hop by measuring at Asterisk, which excludes the softphone's jitter
buffer.

```bash
# 1. run the echo agent
cd /opt/aivoice/agent && source .venv/bin/activate
export ECHO_BEEP=0 ECHO_QUEUE_MS=60
python echo_agent.py dev

# 2. clear old recordings, dial 702, stay silent 2s, ONE sharp clap, silent 2s, hang up
docker exec asterisk sh -c 'rm -f /var/spool/asterisk/monitor/lat*.wav'

# 3. analyse
docker cp asterisk:/var/spool/asterisk/monitor/lat-in.wav  /tmp/lat-in.wav
docker cp asterisk:/var/spool/asterisk/monitor/lat-out.wav /tmp/lat-out.wav
python measure_latency.py
```

Prints ASCII envelopes of both streams plus a confidence score. **Only trust the number
when confidence ≥ 0.9.** Baseline is 205 ms.

> The loud block at the start of the TX envelope is Asterisk's in-band ringback (`702`
> answers before dialling). It does not correlate with RX, so it does not affect the result.
> Clap — do not speak. Cross-correlation needs a sharp transient with silence around it.

### LLM TTFT across models

```bash
cd /opt/aivoice/agent && source .venv/bin/activate
set -a; source /opt/aivoice/.env; set +a
python bench_llm.py
```

Runs each model 5× with a realistic Hindi prompt and prints min/median/max.
**Judge on the max, not the median** — a turn at 800 ms followed by one at 1550 ms is
audible as a stutter, and that is exactly why `gpt-4.1-mini` beat `gpt-4o-mini`.

---

## 8. Reference

### Ports

| Port | Proto | Service | Exposure |
|---|---|---|---|
| 22 | TCP | SSH | LAN |
| 5060 | UDP/TCP | Asterisk SIP | `voip` zone — workstation only |
| 5080 | UDP/TCP | livekit-sip | Internal |
| 6379 | TCP | Redis | `127.0.0.1` only |
| 7880 / 7881 | TCP | LiveKit API / ICE-TCP | Internal |
| 7882 | UDP | LiveKit RTC mux | Internal |
| 5432 | TCP | Postgres | `127.0.0.1` only |
| 10000–19999 | UDP | Asterisk RTP | `voip` zone |
| 20000–29999 | UDP | livekit-sip RTP | Internal |
| 30000–65000 | — | Kernel ephemeral | — |

> RTP ranges must never overlap `net.ipv4.ip_local_port_range`. Overlap causes
> intermittent "address already in use" that only appears under load.

### Firewall

```bash
firewall-cmd --zone=voip --list-all
firewall-cmd --get-active-zones

# allow another host (e.g. the production Asterisk)
firewall-cmd --permanent --zone=voip --add-source=10.130.X.X/32
firewall-cmd --reload
```

> 🚨 A source-based zone **overrides** the interface zone for that source. Any host added
> to `voip` must also have `ssh` in that zone, or it loses SSH access to this box.

### Files

| Path | What |
|---|---|
| `/opt/aivoice/.env` | All secrets + tuning vars (`chmod 600`) |
| `/opt/aivoice/docker-compose.yml` | Service definitions, CPU limits |
| `/opt/aivoice/asterisk/conf/` | Dialplan, endpoints, RTP range |
| `/opt/aivoice/livekit/livekit.yaml` | `use_external_ip:false`, `node_ip` pinned |
| `/opt/aivoice/sip/config.yaml` | SIP port 5080, RTP range |
| `/opt/aivoice/agent/voice_agent.py` | The agent |
| `/opt/aivoice/agent/store.py` | Postgres config + call logging |
| `/etc/sysctl.d/99-voip-tuning.conf` | UDP buffers, port range, swappiness |
| `/etc/docker/daemon.json` | Log rotation, live-restore, systemd cgroups |

### Versions

| Component | Version |
|---|---|
| Rocky Linux | 8.10 |
| Docker / Compose | 26.1.3 / v2.27.0 |
| Asterisk | 20 (Ubuntu 24.04 base) |
| livekit-server | v1.13.4 |
| livekit-sip | v1.8.0 |
| livekit-cli (`lk`) | 2.18.2 |
| livekit-agents | 1.6.7 |
| Python | 3.12.13 |
| Postgres | pgvector/pg16 |
| Redis | 7.4 |

---

## 9. Restart order

Dependencies matter — Redis and LiveKit must be up before livekit-sip.

```bash
cd /opt/aivoice
docker compose up -d postgres redis
sleep 5
docker compose up -d livekit
sleep 5
docker compose up -d sip asterisk
sleep 5
docker compose ps

# then the agent, and wait for "registered worker"
cd /opt/aivoice/agent && source .venv/bin/activate
export LIVEKIT_URL=ws://127.0.0.1:7880
set -a; source /opt/aivoice/.env; set +a
python voice_agent.py start
```

`live-restore: true` in `/etc/docker/daemon.json` means restarting the Docker **daemon**
does not drop running containers — but `docker compose down` does drop calls.
