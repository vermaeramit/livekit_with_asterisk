# Setup Progress Log

Detailed record of every step performed on `10.130.9.243`.
Overview and architecture live in [../README.md](../README.md).

**Currently at:** Step 9 — human transfer tool remaining
**Last completed:** Step 9 (KB) — PDF knowledge base with grounding. **Hallucinations eliminated.**

---

## ✅ Step 1 — Server Discovery

Baseline inventory of a freshly installed Rocky Linux VM before touching anything.

### Findings

| Item | Value |
|---|---|
| OS | Rocky Linux 8.10 (Green Obsidian), kernel 4.18.0-553 |
| Platform | VMware VM |
| CPU | 8 vCPU — Intel Xeon Gold 6338 @ 2.00 GHz |
| RAM | 11 GiB (8.1 GiB free) |
| Disk | 63 G root (59 G free), 31 G /home, 5.9 G swap |
| Interface | `ens33` — `10.130.9.243/16`, gateway `10.130.23.1` |
| DNS | `10.130.1.1` (internal) |
| Public egress | NAT via corporate ISP — Gurugram, Haryana, IN (address redacted) |
| SELinux | `enforcing`, targeted policy |
| firewalld | active, `public` zone — only ssh / cockpit / dhcpv6 |
| Installed | Nothing — no docker, podman, asterisk, python3, git, gcc |
| chronyd | Running, NTP synced ✅ |

### AI provider latency baseline

First pass mixed DNS into the connect time, so it was re-run in Step 2c with DNS
resolution split out. Final numbers:

| Provider | DNS | **TCP (RTT)** | TLS | Verdict |
|---|---|---|---|---|
| api.elevenlabs.io | 39 ms | **5 ms** | 95 ms | 🟢 Local edge |
| generativelanguage.googleapis.com | 103 ms | **6 ms** | 22 ms | 🟢 Local edge |
| api.openai.com | 38 ms | **7 ms** | 28 ms | 🟢 Local edge |
| api.anthropic.com | 8 ms | **24 ms** | 55 ms | 🟢 |
| api.sarvam.ai | 30 ms | **30 ms** | 61 ms | 🟢 India-hosted |
| api.deepgram.com | 40 ms | **286 ms** | 308 ms | 🔴 **Rejected** |

**Deepgram rejected.** DNS was only 40 ms, so 286 ms is genuine network distance —
there is no India edge. For streaming STT every audio chunk would pay ~143 ms one-way.

> ⚠️ Caveat: a 5–7 ms TCP connect measures the **TLS edge PoP**, not where inference runs.
> The request still travels to origin, but over the provider's private backbone rather than
> the public internet. Real end-to-end numbers get measured at Step 7.

---

## ✅ Step 2 — OS Preparation

### 2a — Updates, repos, tools

```bash
dnf -y update                       # only openssh needed updating; no kernel change → no reboot
dnf -y install epel-release dnf-plugins-core
/usr/bin/crb enable                 # CRB/PowerTools — many EPEL packages depend on it
dnf -y install vim wget curl git tar unzip bind-utils \
               net-tools tcpdump nmap-ncat lsof htop \
               policycoreutils-python-utils jq
timedatectl set-timezone Asia/Kolkata
```

Result: timezone `Asia/Kolkata (IST +0530)`, NTP active.

**Key find:** `python3.11` (3.11.13) and `python3.12` (3.12.13) are available as native
AppStream RPMs. This removes Rocky 8's Python 3.6 problem entirely — LiveKit Agents needs 3.9+.

### 2b — Kernel tuning for real-time audio

`/etc/sysctl.d/99-voip-tuning.conf`:

```ini
# ---- UDP buffers: prevents RTP / WebRTC packet loss under load ----
net.core.rmem_max        = 26214400
net.core.rmem_default    = 1048576
net.core.wmem_max        = 26214400
net.core.wmem_default    = 1048576
net.core.netdev_max_backlog = 5000

# ---- Ephemeral ports: many concurrent RTP streams ----
net.ipv4.ip_local_port_range = 20480 65000

# ---- Swapping causes 500 ms freezes in real-time audio ----
vm.swappiness = 1

# ---- TCP: for LLM / STT / TTS API calls ----
net.ipv4.tcp_fastopen = 3
net.core.somaxconn    = 4096
```

`/etc/security/limits.d/99-voip.conf`:

```
*  soft  nofile  65535
*  hard  nofile  65535
*  soft  nproc   32768
*  hard  nproc   32768
```

Plus `DefaultLimitNOFILE=65535` appended to `/etc/systemd/system.conf`.

All values verified applied.

---

## ✅ Step 3 — Docker

```bash
dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
dnf -y install docker-ce docker-ce-cli containerd.io \
               docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
```

Installed: **Docker 26.1.3**, **Compose v2.27.0**.
26.1.3 is the last el8 build (newer releases target RHEL 9+). Fine for compose + host networking.

### `/etc/docker/daemon.json`

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "50m", "max-file": "5" },
  "live-restore": true,
  "exec-opts": ["native.cgroupdriver=systemd"],
  "default-ulimits": {
    "nofile": { "Name": "nofile", "Soft": 65535, "Hard": 65535 }
  }
}
```

**Why each setting matters:**

| Setting | Reason |
|---|---|
| `log-opts` rotation | Without it, voice containers fill 60 GB of disk within a week |
| `live-restore: true` | Docker daemon restarts do **not** drop in-progress calls |
| `native.cgroupdriver=systemd` | Host runs systemd; `cgroupfs` means two cgroup managers fighting over the same resources → unpredictable throttling → audio glitches under load. Changed while no containers existed, so it cost nothing. |

Verified: `Storage Driver: overlay2`, `Logging Driver: json-file`, `Live Restore: true`.

---

## ✅ Step 4 — Test Asterisk + Eyebeam

Purpose: prove the SIP + RTP + firewall + codec path end-to-end **before** adding any AI.

### Base image gotcha

The first build used `debian:12-slim` and failed:

```
Package asterisk is not available, but is referred to by another package.
E: Package 'asterisk' has no installation candidate
```

Asterisk was **removed from Debian 12 (bookworm)** before release over unresolved CVEs
and a maintenance gap. Switched to **`ubuntu:24.04`**, which ships **Asterisk 20 (LTS)** —
the version we want anyway.

### Deployed layout — `/opt/aivoice/` on the server

```
/opt/aivoice/
├── docker-compose.yml
└── asterisk/
    ├── Dockerfile
    ├── entrypoint.sh
    └── conf/
        ├── pjsip.conf
        ├── extensions.conf
        ├── rtp.conf
        └── modules.conf
```

Local mirror of these files: [`../server-configs/asterisk/`](../server-configs/asterisk/)

**Config-overlay pattern:** the entrypoint copies `/conf-override/*.conf` over Ubuntu's
defaults at startup instead of bind-mounting all of `/etc/asterisk`. This keeps distro
defaults intact (no missing-config warnings) and means a config change needs only a
container **restart**, not a rebuild.

### Firewall — dedicated source-restricted zone

```bash
firewall-cmd --permanent --new-zone=voip
firewall-cmd --permanent --zone=voip --add-source=10.130.23.37/32
firewall-cmd --permanent --zone=voip --add-service=ssh
firewall-cmd --permanent --zone=voip --add-port=5060/udp
firewall-cmd --permanent --zone=voip --add-port=5060/tcp
firewall-cmd --permanent --zone=voip --add-port=10000-20000/udp
firewall-cmd --reload
```

Ports are open **only to the workstation**, not the whole `/16`.

> 🚨 **`--add-service=ssh` is mandatory here.** In firewalld a source-based zone *overrides*
> the interface zone. Omitting ssh from the `voip` zone instantly locks SSH out from that
> same machine. Classic firewalld lockout.

### Multi-homed softphone mitigation

The workstation has three IPs (`10.130.23.37` LAN, `192.168.137.1` hotspot,
`172.18.16.1` WSL/Hyper-V). Softphones frequently advertise the wrong one in the SIP
Contact / SDP — signalling connects but **audio never flows**.

Handled in `pjsip.conf`:

| Setting | Effect |
|---|---|
| `rewrite_contact=yes` | Asterisk uses the real source IP:port, ignoring the Contact header |
| `rtp_symmetric=yes` | RTP is returned to wherever it arrives from, ignoring the SDP address |

### Log noise cleanup

`modules.conf` `noload`s optional backends absent from the container (`cdr_pgsql`,
`cdr_radius`, `cel_tds`, `app_voicemail_imap`, `chan_unistim`, `pbx_dundi`, HEP modules).
Their "declined to load" errors are harmless but drown out real SIP/RTP errors during
Step 6–7 debugging.

### Eyebeam settings

| Field | Value |
|---|---|
| User name / Auth user | `1001` |
| Password | *(set on the server, not committed)* |
| Domain | `10.130.9.243` |
| Register with domain | Yes |
| Topology → IP address | `10.130.23.37` (**not** Auto — three NICs) |
| STUN | Disabled (pure LAN) |

### ✅ Result

| Test | Outcome |
|---|---|
| Endpoint `1001` registration | ✅ `Avail` |
| Extension `600` — echo test | ✅ **Working** |
| Extension `601` — playback | ✅ **Working** |

SIP signalling, RTP media, firewall rules and G.711 codec negotiation all confirmed.

---

## ✅ Step 5 — LiveKit Server + Redis

### Versions deployed

| Component | Version |
|---|---|
| `livekit/livekit-server` | **v1.13.4** |
| `redis` | **7.4-alpine** |
| `livekit-cli` (`lk`) | 2.18.2 |

All images are **pinned**. `latest` drifts and eventually breaks something without warning.

### API keys

Generated with `openssl` and stored in `/opt/aivoice/.env` (`chmod 600`), injected via
`LIVEKIT_KEYS` env in compose — never written into the YAML config.

```bash
LK_KEY="API$(openssl rand -hex 8)"
LK_SECRET="$(openssl rand -hex 32)"
```

Hex output avoids all YAML/shell quoting problems.

### `/opt/aivoice/livekit/livekit.yaml`

```yaml
port: 7880

rtc:
  tcp_port: 7881
  udp_port: 7882          # single-port UDP mux - firewall friendly
  use_external_ip: false  # CRITICAL - see below
  node_ip: 10.130.9.243

redis:
  address: 127.0.0.1:6379

turn:
  enabled: false          # everything is on the LAN

logging:
  level: info
  json: false
```

> 🚨 **`use_external_ip: false` is the most important line in this file.**
> The default is `true`, which makes LiveKit STUN-discover its public NAT address and
> advertise **that** in ICE candidates. It is unreachable from the internal
> components — media would fail. `node_ip` pins it to the LAN address instead.
> Confirmed in the startup log: `nodeIP: 10.130.9.243`.

### `/opt/aivoice/redis/redis.conf`

```
bind 127.0.0.1
port 6379
protected-mode yes
save ""             # persistence off - this is coordination state, not durable data.
appendonly no       # disk I/O on this box adds jitter to real-time audio.
maxmemory 256mb
maxmemory-policy allkeys-lru
```

### Verification

| Check | Result |
|---|---|
| `redis-cli ping` | `PONG` |
| Redis bind | `127.0.0.1:6379` — not exposed |
| 7880 / 7881 TCP, 7882 UDP | Listening |
| `lk room list` | Returns cleanly → HTTP API + JWT auth + Redis all working |

No firewall changes were needed — LiveKit's ports are only used from inside this box.

---

## ✅ Step 6 — LiveKit SIP Gateway

`livekit/sip` **v1.8.0**, listening on **5080** (UDP + TCP).

### Port map — the collision that was fixed first

Step 2b set the kernel ephemeral range to `20480-65000`. Putting livekit-sip's RTP range
inside that window would have caused intermittent "address already in use" failures **only
under load** — extremely hard to diagnose. Ranges were re-laid out with no overlap:

| Range | Owner |
|---|---|
| `5060` | Asterisk SIP |
| `5080` | livekit-sip SIP |
| `7880`/`7881` TCP, `7882` UDP | LiveKit server |
| `10000-19999` UDP | Asterisk RTP |
| `20000-29999` UDP | livekit-sip RTP |
| `30000-65000` | kernel ephemeral (`net.ipv4.ip_local_port_range`) |

Asterisk and livekit-sip both need port 5060 by default and both run on host networking —
hence livekit-sip on **5080**. Asterisk sends INVITEs to `10.130.9.243:5080`.

> Loopback `127.0.0.1` was deliberately **not** used. livekit-sip replies from
> `10.130.9.243`; if the request had gone to `127.0.0.1` the source/destination mismatch
> would break SIP dialogs. Traffic still stays inside the kernel via `lo`, so there is no
> latency cost.

### `/opt/aivoice/sip/config.yaml`

```yaml
api_key: <from .env>
api_secret: <from .env>
ws_url: ws://127.0.0.1:7880

redis:
  address: 127.0.0.1:6379

sip_port: 5080
rtp_port: 20000-29999

use_external_ip: false

logging:
  level: info
```

### Asterisk trunk — appended to `pjsip.conf`

```ini
[livekit]
type=endpoint
context=from-livekit
disallow=all
allow=ulaw
allow=alaw
aors=livekit
direct_media=no
rtp_symmetric=yes
force_rport=yes
rewrite_contact=yes

[livekit]
type=aor
contact=sip:10.130.9.243:5080

[livekit]
type=identify
endpoint=livekit
match=10.130.9.243
```

### Dialplan — appended to `extensions.conf`

```ini
; 700 -> route the call into a LiveKit room
exten => 700,1,NoOp(--> Routing to LiveKit AI)
 same => n,Set(CALLERID(num)=${CALLERID(num)})
 same => n,Dial(PJSIP/700@livekit,30)
 same => n,Hangup()

; calls coming back from LiveKit (Phase 2 - outbound)
[from-livekit]
exten => _.,1,NoOp(<-- Inbound from LiveKit: ${EXTEN})
 same => n,Hangup()
```

### LiveKit SIP objects

**Inbound trunk** — `ST_eSZhZNk5XgHB`

```json
{
  "trunk": {
    "name": "asterisk-lab",
    "allowed_addresses": ["10.130.9.243/32"]
  }
}
```

`numbers` is left empty so the trunk matches any called number. Security comes from
`allowed_addresses` — only our Asterisk can use it. Real DIDs get added in Phase 2.

**Dispatch rule** — created with **flags, not a JSON file**:

```bash
lk sip dispatch create \
  --name lab-dispatch \
  --trunks ST_eSZhZNk5XgHB \
  --individual call
```

> ⚠️ **CLI quirk:** `lk sip dispatch create <file.json>` fails with
> `proto: syntax error (line 1:1): invalid value sip` — the CLI treats the argument as
> *inline JSON*, not a file path, so `sip/objects/...` parses as the token `sip`.
> `lk sip inbound create <file.json>` accepts a path fine — inconsistent between
> subcommands. Use flags, or `create - < file.json` for stdin.

`--individual call` means **every call gets its own room** named `call_<caller>_<id>` —
one call, one AI session. Rooms are ephemeral: they exist only while the call is up, so
`lk room list` is empty between calls. This is expected, not a fault.

### ✅ Result

Dialling `700` from Eyebeam produced:

```
RoomID            Name                      Participants   Publishers
RM_6Hjvtv2CkSkj   call_1001_oiPdHJjbn88j    1              1
```

**`Publishers: 1`** is the signal that matters — the SIP participant is actually publishing
an audio track. The full transport chain is proven:

```
Eyebeam → Asterisk :5060 → livekit-sip :5080 → LiveKit room → live audio track
```

Silence on the call is correct at this stage — nothing is in the room to talk back yet.

---

## ✅ Step 7 — Echo Agent + Latency Baseline

### Environment

| | |
|---|---|
| Python | 3.12.13 (Rocky 8 AppStream RPM) |
| venv | `/opt/aivoice/agent/.venv` |
| `livekit-agents` | **1.6.7** |
| `livekit` (rtc) | 1.1.13 |
| `livekit-plugins-silero` / `-turn-detector` | 1.6.7 |
| `onnxruntime` | 1.28.0 |

Agent runs **natively** during development for fast iteration. Containerised at Step 10.

### Result: **205 ms round-trip** ✅

Measured `Asterisk → livekit-sip → LiveKit → agent → back to Asterisk`.
Gate was < 300 ms.

| Hop | ~ms |
|---|---|
| Asterisk → livekit-sip (RTP) | 40 |
| livekit-sip → LiveKit (WebRTC) | 10 |
| LiveKit → agent + jitter | 30 |
| **Agent AudioSource queue** (tunable) | **60** |
| Return path | 65 |
| **Total** | **205** |

Transport floor is therefore ~145 ms; 60 ms is our own buffer setting.

Also measured: room connect **82–104 ms**, agent per-frame hop **5.4–6.4 ms**
(this includes queue backpressure await, not just CPU — it stayed flat over 25 s,
which is the healthy signal).

---

### 🔴 Blocker hit: `livekit-sip` never answered — "Waiting for track subscription(s)"

Calls to `700`/`702` rang for 30 s then failed with SIP `487`. Asterisk logs only showed
`PJSIP/livekit-xxx is ringing` repeatedly. The livekit-sip log had the answer:

```
sip/inbound.go:1021   Waiting for track subscription(s)
```

**livekit-sip does not answer the SIP call until something subscribes to the track it
publishes.** The echo agent was not running, so nothing subscribed. Call stats confirmed
it: `audio_packets: 0, audio_rx: 0, audio_tx: 0` — no RTP ever flowed.

This is deliberate behaviour: it stops the caller getting dead air.

> Note: seeing a room in `lk room list` does **not** mean the call connected. The room is
> created at "join room", several steps before the SIP `200 OK`. Step 6 was verified on
> room presence alone, which was an incomplete check.

**Production implication (for Step 10):** if the agent worker is down, calls ring forever.
`Dial()` needs a short timeout plus a fallback route:

```ini
same => n,Dial(PJSIP/700@livekit,8)
same => n,NoOp(AI unavailable - route to human)
```

Otherwise one crashed worker stalls the whole dialer.

---

### Two self-inflicted measurement errors (worth remembering)

**1. The startup beep pre-filled the audio queue.**
The agent published a 250 ms beep in a tight loop, which instantly filled the 200 ms
`AudioSource` queue. From then on the queue stayed full, adding a permanent 200 ms.
Fixed with `ECHO_BEEP=0` and `ECHO_QUEUE_MS=60` env vars.

**2. Threshold-based onset detection was unreliable.**
The first script found the first sample above 35 % of peak in each recording. Any earlier
noise (breath, handling the phone) false-triggered and inflated the gap — it reported
**1228 ms**. Replaced with **FFT cross-correlation of the 5 ms energy envelopes**, which
uses the whole signal instead of one transient. Same call, corrected reading: **205 ms**,
confidence 1.00.

### Measurement rig

Extension `702` records the caller's rx and tx streams to separate, time-aligned files:

```ini
exten => 702,1,NoOp(--> LATENCY TEST)
 same => n,Answer()
 same => n,Wait(1)
 same => n,MixMonitor(lat.wav,r(/var/spool/asterisk/monitor/lat-in.wav)t(/var/spool/asterisk/monitor/lat-out.wav))
 same => n,Dial(PJSIP/700@livekit,30)
 same => n,StopMixMonitor()
 same => n,Hangup()
```

Measuring at Asterisk (not at the softphone) deliberately excludes Eyebeam's jitter
buffer, isolating the LiveKit hop — the part being optimised.

Procedure: start the agent, dial `702`, stay silent 2 s, **one sharp clap**, silent 2 s,
hang up. Then:

```bash
docker cp asterisk:/var/spool/asterisk/monitor/lat-in.wav  /tmp/lat-in.wav
docker cp asterisk:/var/spool/asterisk/monitor/lat-out.wav /tmp/lat-out.wav
python measure_latency.py
```

`measure_latency.py` prints ASCII envelopes of both streams plus a confidence score.
Trust the number only when confidence ≥ 0.9.

> The loud block at the start of the TX envelope is Asterisk's in-band **ringback**
> (because `702` answers before dialling). It does not correlate with RX, so it does not
> affect the result.

### Other findings

- Negotiated codec: **PCMU/8000** (G.711 μ-law) — no transcoding penalty
- `inviteToRingingMs: 4` — SIP signalling is effectively instant
- livekit-sip jitter buffer is **off** (`jitterBuf: false`) — good for latency
- Cold start **2.3 s** on first job (`no warmed process available`). Dev mode defaults to
  `num_idle_processes=0`; production `start` mode pre-warms. **Will get much worse at
  Step 8** once Silero VAD and the turn detector need loading — needs `prewarm_fnc`.
- Harmless log noise: `failed to get server reflexive address udp6 stun:...google.com`.
  Everything is on the LAN so STUN is pointless here; it can be disabled later.

### Implication for the AI latency budget

| Stage | ms |
|---|---|
| Caller → agent (one-way transport) | ~100 |
| VAD + endpointing | 250 |
| STT | 150 |
| LLM TTFT | 250 |
| TTS TTFB | 120 |
| Agent → caller | ~105 |
| **Total** | **~975 ms** |

Under 1 s, but tighter than the original 830 ms estimate because real transport is 205 ms
rather than the 60 ms assumed. Acceptable to proceed; tunable later.

---

## ✅ Step 8 — Real STT → LLM → TTS Pipeline

Working AI conversation in Hindi over the phone, with barge-in.

### Stack as shipped

| Layer | Choice | Notes |
|---|---|---|
| **STT** | Sarvam `saarika:v2.5` | `high_vad_sensitivity=true` — see tuning below |
| **LLM** | OpenAI **`gpt-4.1-mini`** | Chosen by benchmark, not by guess |
| **TTS** | Sarvam `bulbul:v3`, speaker `shubh` | Default voice |
| **VAD** | Silero (local) | Loaded in `prewarm_fnc` |
| **Turn detection** | LiveKit `MultilingualModel` (local ONNX) | |
| **Config** | Postgres `agent_config` | Model/prompt/voice changes are a SQL update, no deploy |

### Latency: 2.5–4.7 s → **0.96–2.6 s** (median 1.92 s)

| Metric | Before tuning | After | Median |
|---|---|---|---|
| `stt` (transcription_delay) | 750–1440 ms | 266–374 ms | **327 ms** |
| `eou` (end-of-utterance) | 1130–1850 ms | 943–1502 ms | **1030 ms** |
| `llm_ttft` | 680–2160 ms | 682–977 ms | **738 ms** |
| `tts_ttfb` | 218–1260 ms | 223–265 ms | **241 ms** |
| **total** | 2150–4700 ms | 961–2625 ms | **1921 ms** |

Plus ~205 ms transport → roughly **1.2–2.8 s mouth-to-ear**, median ~2.1 s.

---

### Tuning: what actually worked, and what didn't

**1. `max_endpointing_delay` 4.0 → 1.5 s — saved up to 2.7 s on the worst turns**

Short closings like `"ओके थैंक यू"` scored `eou_probability 0.0037` (below the 0.0398
threshold), so the turn detector stalled for the full 4 s. Capping at 1.5 s bounds it.

**2. `high_vad_sensitivity=true` on Sarvam STT — saved ~700 ms**

The single biggest win. Evidence from the logs:

```
258.358  Silero (local): "user stopped"
259.061  Sarvam END_SPEECH        <- 703 ms later
259.079  plugin sends flush
259.187  transcript ready         <- only 126 ms after flush
```

Transcription itself takes 126 ms. The other ~700 ms was purely waiting for Sarvam's
server-side VAD to agree the user had stopped.

> The plugin has **no hook** for the local Silero VAD to force a flush — `_should_flush`
> is set in exactly one place, on Sarvam's `END_SPEECH`. So server-side VAD params were
> the only lever.

**3. `flush_signal=true` — no effect.** Tested in isolation: `transcription_delay` stayed
at 830–1440 ms, identical to baseline. Dropped.

**4. The fine-grained VAD params are a dead end on saarika.**

```
saarika:v2.5   supports_vad_params = False
saaras:v2.5    supports_vad_params = False
saaras:v3      supports_vad_params = True
```

`negative_frames_count`, `negative_speech_threshold`, `min_speech_frames` etc. are
**silently dropped** by the plugin for `saarika:*`. Reading `MODEL_CONFIGS` in the source
saved hours of tuning values that were never being sent. `saaras:v3` would expose them if
this is revisited.

**5. `gpt-4o-mini` → `gpt-4.1-mini`, chosen by measurement**

Real TTFT from this server, same Hindi prompt, 5 runs each:

| model | min | median | max |
|---|---|---|---|
| gpt-4o-mini | 747 ms | 805 ms | **1547 ms** |
| **gpt-4.1-mini** | 580 ms | **608 ms** | **665 ms** |
| gpt-4.1-nano | 568 ms | 630 ms | 833 ms |

Median only improved ~200 ms, but the **variance** collapsed: 85 ms spread vs 800 ms.
On a call, consistency matters more than average — one turn at 800 ms and the next at
1550 ms is audible as a stutter. Confirmed in the follow-up call: all turns landed in
682–977 ms with no spikes.

> **Prompt caching is not available here.** `prompt_cached_tokens` was 0 on every call
> because OpenAI's caching needs a ≥1024-token prefix; our prompts are 100–250 tokens.

---

### ✅ Barge-in works, including the part most implementations miss

```
user:      "रुको।"
assistant: "क्या आप मोबाइल ऐप, इंटरनेट बैंकिंग, या मिस्ड कॉल से बैलेंस"   <- cut mid-sentence
assistant: "जी, मैं रुक गया हूँ। जब आप तैयार हों तो बताइए।"
```

TTS stopped, the output buffer was flushed, **and the LLM context recorded only the words
actually spoken** — so the agent knew it had been interrupted rather than assuming the full
message was delivered. `AgentSession` handles all three; no manual queue flushing needed.

> Earlier note about manually flushing the `AudioSource` queue applies to raw `rtc.AudioSource`
> usage (the Step 7 echo agent), not to `AgentSession`.

---

### Bugs hit along the way

| Symptom | Cause |
|---|---|
| `process initialization failed: no job context found` | `MultilingualModel()` was in `prewarm_fnc`. It talks to the inference-executor process and needs a job context. Only `silero.VAD.load()` belongs in prewarm; the turn detector is instantiated in the entrypoint (cheap — the executor is pre-warmed separately at worker startup). |
| `total_ms` looked far too high | `eou` already **includes** `stt` (`transcription_delay`). Summing all four double-counted. Correct: `eou + llm_ttft + tts_ttfb`. |
| Timing lines missing from output | They are continuation lines without the `voice-agent` prefix, so `grep` dropped them. Use `grep -A 1`. |
| Two "patched" messages, no actual change | Python `str.replace()` returns silently when the target is absent. Patches now `assert` the target exists. |

---

### ⚠️ Open issues

**1. First call after starting the worker only rings; the second connects.**

Worker startup takes ~7 s (inference executor ~4.7 s, then registration). A call arriving
before `registered worker` has no agent to dispatch to, so livekit-sip never gets a
subscriber and never answers. `dev` mode also sets `num_idle_processes=0`, adding a ~2.3 s
cold spawn on the first job.

Fix planned for Step 10:
- systemd unit running `voice_agent.py **start**` (production mode, pre-warmed processes)
- `num_idle_processes` set explicitly
- `Dial(PJSIP/700@livekit,8)` + fallback route, so a down worker fails over in 8 s instead
  of ringing for 30 s

**2. The LLM hallucinates specifics.** It invented a Kotak missed-call balance number
(`9215676766`). For a real dialer this is a compliance problem — which is exactly what
Step 9's knowledge base grounding is for.

**3. Occasional TTS spike** — one turn hit `tts_ttfb 1.26 s` against a 0.23 s norm.
Needs a fallback provider (Step 10).

**4. Deprecation warnings** (all still functional in 1.6.7, pinned):
`min_endpointing_delay`/`max_endpointing_delay`/`allow_interruptions`/`turn_detection` →
`turn_handling=TurnHandlingOptions(...)`; `metrics_collected` → `session_usage_updated`;
`RoomInputOptions` → `RoomOptions`.

---

### Files

| Path | Purpose |
|---|---|
| `/opt/aivoice/agent/voice_agent.py` | The agent |
| `/opt/aivoice/agent/store.py` | Postgres config + call/turn logging |
| `/opt/aivoice/agent/bench_llm.py` | TTFT benchmark |
| `/opt/aivoice/agent/measure_latency.py` | Transport round-trip (Step 7) |
| `/opt/aivoice/.env` | Keys + `SARVAM_HIGH_VAD=1` |

Run:

```bash
cd /opt/aivoice/agent && source .venv/bin/activate
set -a; source /opt/aivoice/.env; set +a
export LIVEKIT_URL=ws://127.0.0.1:7880
python voice_agent.py dev 2>&1 | grep -A 1 -E "voice-agent - \[|user transcript"
```

---

## ✅ Step 9 — Knowledge Base + Grounding

PDF knowledge base with a two-layer design. **The agent no longer invents facts.**

### Final architecture

```
Layer 1  prompt   small KB injected whole (or its heading index if large)
                  -> 0 ms per turn, answers the common questions
Layer 2  tool     search_knowledge_base(query)
                  -> only fires when layer 1 falls short
```

The design came from the user's suggestion: *keep the common answers in the prompt and
only reach for the KB when the prompt does not cover the question.*

### Why not retrieve on every turn

The first implementation pre-fetched on every user turn. Measured cost: **390–1244 ms on
every turn**, needed or not.

| Design | Cost |
|---|---|
| Pre-fetch every turn | +400–1200 ms on 100 % of turns |
| Prompt + tool fallback | 0 ms on ~85 % of turns, +~1500 ms on the rest → **~225 ms average** |

### The finding that decided it: cross-script retrieval collapses

Sarvam STT emits Devanagari. The KB is English. Same question, three scripts:

| Question | Devanagari | Romanized | English |
|---|---|---|---|
| package price | **0.188** ← wrong chunk | 0.416 | 0.442 ✅ |
| which hotel in option 1 | **0.199** | 0.441 | 0.475 ✅ |
| flight from Delhi | **0.147** ← wrong chunk | 0.478 | 0.455 ✅ |

Not a threshold problem — the correct chunk did not even rank. Any threshold low enough to
admit the right answer also admits everything else.

**Auto-transliteration was tried and rejected.** ITRANS/HK/IAST/SLP1 all tested:

```
पैकेज  -> paikeja     (not "package")
प्राइस -> praisa      (not "price")
फ्लाइट -> phlaita     (not "flight")
```

Scores rose from 0.13–0.20 to 0.23–0.31, but only **1 of 4** queries retrieved the right
chunk. Transliteration is phonetic, so the English loanwords never reconstruct. Ranking is
what matters, and ranking stayed wrong.

**The tool design solved it for free.** When the *model* writes the search query, it writes
English — and the tool's docstring instructs it to. Observed live:

```
[user]  ओके तो इसमें कैंसिलेशन पॉलिसी क्या रहेगी
TOOL    search_knowledge_base('cancellation policy refund Dubai package')
        -> 3 hits  ['0.61', '0.56', '0.55']
```

0.61 against 0.19 for the same question asked directly. No translation pipeline, no dual
index, no re-ingest.

---

### Extraction: plain `get_text()` is not enough

`fitz.get_text()` reads designed layouts in geometric order, not reading order. On a
two-column pricing block it fused both options into one unusable chunk:

```
Land: INR 150,000  Flight: INR 30,000  Land: INR 120,000  Flight: INR 30,000
HOTELS  HOTELS  Pullman Dubai Downtown  The Creekside Hotel
```

*"Which hotel is in Option 1?"* had no chunk that could answer it. A manual y-band sort
made it worse — it read **across** the columns.

**`pymupdf4llm`** resolves reading order and emits markdown. Same page after:

```
Option 1  Recommended
**TOTAL PRICE** INR 180,000
HOTELS  Pullman Dubai Downtown ★★★★ 3 N

**Option 2**
**TOTAL PRICE** INR 150,000
HOTELS  The Creekside Hotel ★★★★★ 3 N
```

It also turned the flight schedule into a real markdown table, which fixed the
*"flight timing"* query that had been retrieving the BBQ-dinner chunk.

Retrieval went from **3/6 to 5/6** answerable queries on that change alone.

> PyMuPDF prints `Consider using the pymupdf_layout package...` on every ingest with plain
> extraction. That hint was the fix.

### Chunking

- Split on markdown headings, tracking depth as a stack, so each chunk carries its path
  (`Dubai Package > DAY-BY-DAY ITINERARY > Desert Safari with BBQ Dinner`). Short questions
  retrieve measurably better when a chunk knows where it came from.
- ~250 tokens with 50-token overlap; markdown tables kept atomic.
- Chunks under 40 tokens are folded into a neighbour — a 5-token chunk answers nothing but
  still competes in retrieval and displaces a real result.

### Hybrid search

Vector (pgvector HNSW cosine) **+** lexical (`pg_trgm`), merged on best score.

> Use `word_similarity(query, content)`, **not** `similarity()`. The latter compares whole
> strings, so a 250-token chunk against a 6-word question scores ~0 and the lexical leg
> never fires. That bug was live until the logs showed every hit tagged `[vec]` and none
> `[lex]`. After the fix, lexical took the top slot on *"what is the total price"* (0.565).

### Ingestion — built for easy updates

```bash
cp new-doc.pdf /opt/aivoice/kb/inbox/
python ingest.py                # unchanged files are skipped by sha256
python ingest.py --force        # re-ingest regardless
```

| Case | Behaviour |
|---|---|
| Same file again | Hash matches → no-op |
| File changed | Old chunks deleted, new inserted, **one transaction** — a failed ingest leaves the previous version intact |
| Document retired | `kb_documents.enabled = false`, content kept |

`ingest_file()` is the single entry point; the folder watcher and Step 11 admin UI will
call the same function rather than reimplementing the pipeline.

---

### 🎉 Prompt caching activated as a side effect

Step 8 showed `prompt_cached_tokens: 0` on every call — OpenAI's caching needs a ≥1024-token
prefix and the prompt was ~250 tokens. Injecting the KB crossed that line, and the KB is
identical on every turn:

| Turn | prompt | cached | llm_ttft |
|---|---|---|---|
| 1 | 1624 | **0** | **1926 ms** |
| 2 | 2304 | 1536 | 1160 ms |
| 3 | 2359 | 2176 | **852 ms** |
| 4 | 2422 | **2304 (95 %)** | 1021 ms |

Net cost of carrying the whole KB: **+114 ms** on cached turns (852 vs 738 ms baseline).

### Grounding

```
[user]      नहीं मुझे होटल का फ़ोन नंबर दे दो
[assistant] Mujhe maaf kijiye, hotel ka phone number mere paas available nahi hai,
            par main aapko Skywing Travels ke number +918802807777 par jod sakta hoon.
```

Refused to invent the hotel number, and the number it did give is genuinely in the KB.
Compare Step 8, where it produced a fabricated bank helpline. **Zero hallucinations across
the test call.**

Two rules did the work:
- *"If the retrieved information is only loosely related, treat it as NOT having the answer."*
  Retrieval scores cannot separate "answers the question" from "same topic" — a question with
  no answer in the KB still returns chunks at 0.35 while a correctly-answered one can sit at
  0.21. The model can make that call; a number cannot.
- An empty result returns **explicit text** saying so. Returning `""` reads as permission to
  answer from general knowledge.

### Latency

| | |
|---|---|
| Turn 1 | 3728 ms — cold prompt cache |
| Steady state | **2015–2773 ms** |
| Step 8 baseline (no KB) | 1921 ms median |

Grounding costs ~300–500 ms. Acceptable for eliminating fabricated answers.

### Bugs hit

| Symptom | Cause |
|---|---|
| Call rang, never connected, no error visible | `kb_enabled` etc. were added to the DB but **not to the `AgentConfig` dataclass** in `store.py`. `load_config()` builds from `fields(AgentConfig)`, so the attribute was missing and the entrypoint raised. Hidden because the log filter was `grep -E "voice-agent"` — the traceback came from `livekit.agents`. **Always grep `ERROR\|Traceback` too.** |
| `operator does not exist: text %% text` | `%` was escaped as `%%`. asyncpg uses `$1` placeholders and does **not** do `%` formatting. |
| Retrieval returned 0 hits in the agent but worked standalone | The standalone test used romanized queries; the agent got Devanagari from STT. **The test was wrong, not the code.** |

### ⚠️ Open

1. **Turn 1 is slow** (~1926 ms TTFT) until the prompt cache warms.
2. **`kb_tool` takes ~1569 ms** — the embedding API call is slow for a 7 ms-away endpoint.
   Now only on tool-call turns, but worth chasing.
3. **Human transfer tool** not built yet.
4. Scanned PDFs are unsupported — no OCR. Text-based only, which matches the requirement.

---

## ⏭️ Next

- Human transfer tool (completes Step 9)
- Step 10 — systemd service, first-call fix, provider fallbacks, monitoring, load test
- Step 11 — admin panel

---

## Useful commands

```bash
# --- Asterisk ---
cd /opt/aivoice
docker compose ps
docker compose logs -f --tail=50 asterisk
docker compose restart asterisk                     # after a conf/ change
docker compose up -d --build                        # after a Dockerfile change

docker exec asterisk asterisk -rx "pjsip show endpoints"
docker exec asterisk asterisk -rx "pjsip show contacts"
docker exec asterisk asterisk -rx "pjsip show transports"
docker exec asterisk asterisk -rx "core show channels"     # during a live call
docker exec asterisk asterisk -rx "pjsip set logger on"    # full SIP trace
docker exec asterisk asterisk -rx "rtp set debug on"       # RTP trace

# --- Firewall ---
firewall-cmd --zone=voip --list-all
firewall-cmd --get-active-zones

# --- Health ---
ss -ulnp | grep 5060
docker stats --no-stream
htop
```
