# Setup Progress Log

Detailed record of every step performed on `10.130.9.243`.
Overview and architecture live in [../README.md](../README.md).

**Currently at:** Step 10 — monitoring + provider fallbacks remaining
**Last completed:** Step 10 (deployment + capacity) — **10 concurrent calls at full quality**, two
production-only bugs found and fixed.

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

---

## ✅ Step 9b — Human Transfer

Blind transfer via SIP REFER. Verified end to end.

### Flow

```
Eyebeam -> Asterisk -> livekit-sip -> LiveKit room -> AI agent
                                                        | transfer_to_human()
                                                        v
                                        LiveKit SIP TransferSIPParticipant
                                                        |  SIP REFER
                                                        v
                                        Asterisk -> extension 800
```

**Cold transfer, not warm.** The alternative — dialling the human into the room for a
three-way then dropping the AI — gives the human context but needs an outbound leg and an
exit dance. Cold transfer is what dialers do, and the architecture leaves room to add warm
transfer later without changing anything else.

### Announce, then transfer

The handoff line must finish playing **before** the REFER goes out, or the caller gets
abrupt silence and then a stranger. `RunContext` provides everything needed:

```python
context.disallow_interruptions()
handle = await context.session.say(msg, allow_interruptions=False)
await handle.wait_for_playout()      # <- the important line
# ... only now send the REFER
```

Verified in the timeline:

```
12:00:31.919  tool execution start
12:00:35.933  [assistant] "Ek minute, main aapko humare representative se jod raha hoon."
12:00:35.936  TOOL transfer_to_human(...) -> sip:800@10.130.9.243  participant=sip_1001
12:00:36.954  TRANSFER OK
```

### Asterisk side

The REFER arrives on the `livekit` endpoint, so the target extension must live in that
endpoint's context — `from-livekit` — and must be declared **before** the `_.` catch-all,
which would otherwise match and hang up the transfer.

```ini
[from-livekit]
; 800 -> transfer target. In production this becomes Dial(PJSIP/1002) or Queue(support).
exten => 800,1,NoOp(<-- TRANSFER landed: human agent)
 same => n,Answer()
 same => n,Wait(1)
 same => n,Playback(demo-thanks)
 same => n,Echo()
 same => n,Hangup()

exten => _.,1,NoOp(<-- Inbound from LiveKit: ${EXTEN})
 same => n,Hangup()
```

`res_pjsip_refer.so` must be loaded (it is, by default).

Confirmed in the Asterisk log — the caller's channel detached from the LiveKit bridge and
landed on 800:

```
Channel PJSIP/livekit-00000001 left 'simple_bridge'
Executing [800@from-livekit:1] NoOp("<-- TRANSFER landed: human agent")
Executing [800@from-livekit:4] Playback("demo-thanks")
```

### Config

| Column | Purpose |
|---|---|
| `agent_config.transfer_enabled` | Master switch |
| `agent_config.transfer_to` | SIP URI, e.g. `sip:800@10.130.9.243` |
| `agent_config.transfer_message` | Spoken before the REFER |
| `calls.transferred_to` / `transfer_reason` | Recorded for review |

```
15 | call_1001_R3AttWQGwj8P | transferred | sip:800@10.130.9.243 |
     Customer requested to speak with an agent | 49827 ms
```

### When it fires

Prompt rules keep it from triggering on answerable questions:

```
- Call transfer_to_human when the caller asks for a person, sounds frustrated,
  wants to complain, or asks something you still cannot answer after searching.
- Do NOT transfer for anything you can answer yourself.
- Do not announce the handoff yourself - the tool speaks the line and moves the call.
```

Same call: *"price kya hai?"* was answered from the prompt with `kb_tool_calls=0` and no
transfer; *"mujhe kisi agent se baat kara do"* transferred immediately.

---

### ⚠️ Open

1. **Turn 1 is slow** (~1560–1926 ms TTFT) until the prompt cache warms. Every call pays
   this on its first answer, which is the one that sets the caller's impression.
2. **`kb_tool` takes ~1569 ms** — the embedding API call is slow for a 7 ms-away endpoint.
   Only on tool-call turns now, but worth chasing.
3. **First call after a worker restart still only rings** (worker needs ~7 s to register).
4. Scanned PDFs unsupported — no OCR. Text-based only, which matches the requirement.
5. Minor: the `h` (hangup) extension falls through the `from-livekit` catch-all and logs
   noise. Harmless; exclude it when convenient.

---

## ✅ Step 10a — Deployment, fallback, capacity

### Result: **10 concurrent calls at full quality**

| | Single call | 10 concurrent |
|---|---|---|
| p50 | 1921 ms | **2001 ms** |
| p95 | ~2400 ms | **2776 ms** |
| max | — | 2947 ms |
| eou / llm / tts | 1030 / 915 / 241 | 966 / 898 / 271 |
| System load | — | 2.0–2.2 on 8 cores (**~27 %**) |
| RAM | — | 4.7 / 11 GB |

Latency essentially unchanged under load. Three workers, jobs distributed 0/4/3.

> Tested to 10, not 20. At ~27 % CPU the headroom suggests the 18–20 target is reachable
> with 4–5 workers, but that is an extrapolation, not a measurement.

---

### systemd — and the first-call fix

The agent was being started by hand, which is why the first call after a restart only rang:
the worker needs ~7 s to register (the inference executor alone takes ~4.7 s), and
livekit-sip will not answer until an agent subscribes to its track.

Two units:

| Unit | Purpose |
|---|---|
| `aivoice-agent@.service` | Template — one instance per worker |
| `aivoice-cache-warmer.service` | Keeps the OpenAI prompt cache hot |

Key settings and why:

```ini
ExecStart=... voice_agent.py start      # not `dev` - see the trap below
Restart=always
KillSignal=SIGINT                       # livekit-agents drains in-flight calls on SIGINT
TimeoutStopSec=180                      # so a deploy does not cut live conversations
Environment=AGENT_HTTP_PORT=808%i       # per-instance, see bug 2
Environment=LOAD_THRESHOLD=5.0          # see bug 1
```

### Dial timeout + fallback

`Dial(PJSIP/700@livekit,8)` and, on timeout, `Goto(from-livekit,800,1)` — the same
extension the agent transfers to. Before this, a down worker meant 30 s of ringing and a
dead call; one crashed worker would have stalled the whole dialer.

Verified by stopping the agent and dialling: ~8 s, then the human extension. Confirmed
again during load testing, where overflow calls fell through cleanly instead of dropping.

---

### 🔴 Two bugs that only exist in production mode

Both were invisible in `dev` and appeared the moment the agent moved to systemd.
`WorkerOptions` uses `ServerEnvOption(dev_default=..., prod_default=...)`, and the
production defaults are the dangerous ones:

| Option | dev_default | prod_default |
|---|---|---|
| `load_threshold` | `inf` | **0.7** |
| `port` | `0` (random) | **8081** (fixed) |
| `num_idle_processes` | `0` | 2 |

**Bug 1 — `load_threshold` blocked dispatch at 3 concurrent calls.**

```
12:29:01  received job request  (1)
12:29:02  received job request  (2)
12:29:03  received job request  (3)
12:29:04  worker is at full capacity   load: 1.0   threshold: 0.7
12:29:17  below capacity                            (13 s later)
```

Calls 4 and 5 were never dispatched and fell through to the human fallback. The machine was
**not** loaded — system load average was 0.94 across 8 cores (~12 %) at that moment. The
worker's own metric samples `psutil.cpu_percent()` over a short window, and spawning job
processes (each loading Silero + onnxruntime) spikes it.

> The load value is **clamped to 0–1**, so *any* threshold below 1.0 trips on a momentary
> 100 % sample. Raising 0.7 → 0.9 changed nothing; only a value above 1.0 disables it.
> Set to `5.0` and the real ceiling becomes latency rather than a proxy metric.

**Bug 2 — every extra worker instance silently crash-looped.**

```
OSError: [Errno 98] error while attempting to bind on address ('::', 8081):
         address already in use
```

All instances tried to bind the same fixed health-server port. Only one survived.

**`systemctl is-active` reported `active` for all three** — `Restart=always` kept restarting
the dead ones, so the unit looked healthy while the worker never registered. Three separate
load-test runs were interpreted on the assumption that three workers were running when only
one ever was.

Fixed with a per-instance port (`AGENT_HTTP_PORT=808%i`). Verification afterwards:

```
worker 1: active  registered=1     8081  pid 437017
worker 2: active  registered=1     8082  pid 437040
worker 3: active  registered=1     8083  pid 437018
```

> **Lesson: `systemctl is-active` is not proof a worker is working.** Check for
> `registered worker` in the journal and confirm the port is bound.

---

### The load test, and three wrong hypotheses

`loadtest.sh` originates N calls through `Local/s@loadtest` bridged to `700`, so real speech
flows and the whole STT → LLM → TTS path runs — silence would only have exercised transport.

Same 10-call test, four runs:

| Run | Config | Calls |
|---|---|---|
| 1 | 1 worker, `load_threshold=0.7` | 3 |
| 2 | 1 worker, `load_threshold=5.0` | 5 |
| 3 | "3 workers" (2 were crash-looping) | 7 |
| 4 | **3 real workers** | **10** |

Three explanations were proposed and disproved along the way — CPU saturation, the
`load_threshold` value, and burst arrival rate. Widening the originate stagger from 0.5 s to
2.5 s made results *worse*, which killed the burst theory. The actual cause was that the
extra workers had never started.

> The check that would have caught it — confirming `registered worker` after
> `systemctl enable --now` — was skipped. Three subsequent runs were built on that
> assumption.

### Measurement mistakes worth remembering

- `ps -C python` measured only the **parent** process (1.2 %); the work happens in spawned
  job processes. It made the agent look idle at 10–15 % CPU.
- `docker exec` **needs `-i`** to accept a heredoc. Without it psql receives nothing and
  prints nothing — no error.
- `--since "-4min"` windows repeatedly cut off the start of a test and produced phantom
  "only 7 of 10 connected" results. Query by call id instead.

---

### Prompt-cache warming — measured, then deliberately not pursued further

`cache_warmer.py` runs as its own service, sending the same system prompt every 120 s.
Cache persistence measured directly:

| | ttft | cached |
|---|---|---|
| cold | 1198 ms | 0 |
| +2 s | 805 ms | 1152 / 1343 |
| +60 s | 989 ms | 1152 |
| **+180 s** | 950 ms | 1152 |

But across 17 real calls the first-turn penalty was smaller than the noise:

| | count | avg | p50 | max |
|---|---|---|---|---|
| First turn | 17 | 1274 ms | 1091 ms | 3301 ms |
| Later turns | 51 | 915 ms | 852 ms | 2157 ms |

A ~240 ms p50 gap against a 682–3301 ms spread. Matching the warmer's prefix to the agent's
(which also sends tool definitions) would be real work for a gain that could not be reliably
observed. **The warmer stays because it is free; the tool-schema matching was dropped.**

---

### ⚠️ Open

1. **Capacity above 10 is untested.** ~27 % CPU at 10 concurrent suggests room, but 18–20
   needs verifying — ideally with `sipp` rather than Asterisk Local channels, which added
   their own noise here.
2. **No provider fallbacks.** A Sarvam TTS spike of 1.26 s against a 0.23 s norm was
   observed; a Sarvam outage would leave the agent mute.
3. **`transfer_to_human` can fail** — seen once as `503 Service Unavailable`. Currently only
   apologises; no retry or alternate route.
4. **No monitoring or cost guardrails.**
5. **`kb_tool` takes ~1569 ms** — slow for a 7 ms-away embedding endpoint.

---

---

## 🔬 Step 10b — Provider fallbacks: benchmarked, not yet wired

Every candidate was measured before writing any integration code. The chain below is
**decided but not implemented** — `voice_agent.py` still constructs providers directly.

### Chosen chain

| Layer | Primary | Fallback | Cost when it fires |
|---|---|---|---|
| STT | Sarvam `saarika:v2.5` | OpenAI `gpt-4o-mini-transcribe` | Lower Indic accuracy |
| TTS | Sarvam `bulbul:v3` | OpenAI `gpt-4o-mini-tts` | +650 ms, voice changes |
| **LLM** | OpenAI `gpt-4.1-mini` | **Google `gemini-flash-lite-latest`** | ~none |

`livekit-agents` ships `FallbackAdapter` for all three layers, so the integration is
straightforward — but the **default timeouts are wrong for voice**:

```
stt.FallbackAdapter  attempt_timeout = 10.0 s   <- caller sits in silence that long
llm.FallbackAdapter  attempt_timeout =  5.0 s   <- our TTFT is ~900 ms
```

If the primary hangs, the call is already lost before the fallback is even attempted.
Both need to be ~3 s. `tts.FallbackAdapter` also needs `sample_rate=22050` (Sarvam's
native rate) so the **primary** path never resamples — only the fallback pays that cost.
And `stt.FallbackAdapter` needs `vad=` passed, because a non-streaming fallback STT
cannot chunk audio without it.

---

### TTS — measured, warm, 4 runs each

| Provider / model | TTFB | Verdict |
|---|---|---|
| **Sarvam `bulbul:v3`** | **241 ms** | primary |
| **OpenAI `gpt-4o-mini-tts`** | **889–980 ms** (cold 2543) | ✅ **chosen fallback** |
| Gemini `2.5-flash-lite-preview-tts` | 3557–4024 ms | ❌ |
| Gemini `3.1-flash-tts-preview` | 3964–4078 ms | ❌ |
| Gemini `2.5-flash-tts` | 4173–5367 ms | ❌ |
| Gemini `2.5-pro-tts` | 5463–**15431** ms + a timeout | ❌❌ |

**All four Gemini TTS models sit at a ~4 s floor.** It is consistent across runs, not a
cold-start artefact. Four seconds of silence before every reply would make the call feel
broken — worse than the voice simply changing. **Do not revisit this without new evidence
that Google has changed something.**

Getting to that answer took real setup, all of which now exists and works:

| Step | Note |
|---|---|
| Gemini API key | AI Studio. Free tier hit `limit: 0` on some models — upgraded to Tier 1 |
| Service account | Blocked by org policy `iam.disableServiceAccountKeyCreation` on `ai-worxpertise-org`; disabled at org level |
| Vertex AI | `google.TTS` routes through **Vertex**, not Cloud TTS — needs `aiplatform.googleapis.com` enabled |
| IAM role | `roles/aiplatform.user`. "Vertex AI Tuning Service Agent" does **not** include `aiplatform.endpoints.predict`. The console role picker did not list it; `gcloud projects add-iam-policy-binding` worked. |
| Audio encoding | Default `PCM` returns `400 Unsupported audio encoding`. **`LINEAR16` + `use_streaming=True`** works. |

### LLM — Gemini is the real win

| Model | min | median | max |
|---|---|---|---|
| `gpt-4.1-mini` (primary) | 580 ms | 608 ms | 665 ms |
| **`gemini-flash-lite-latest`** | **579 ms** | **650 ms** | 1152 ms |
| `gemini-flash-latest` | 1997 ms | 2554 ms | 3475 ms |
| `gemini-2.0-flash` | — | quota `limit: 0` on free tier | — |

`gemini-flash-lite-latest` matches the primary. This is the **only layer with true provider
diversity** — STT and TTS both fall back within reach of a single vendor, so a full OpenAI
outage would still cost speech and hearing, but not thought.

> ⚠️ A free-tier fallback cannot absorb the load it exists to catch. When the primary fails,
> *all* traffic shifts at once and free-tier limits return 429 immediately. Billing is now on
> Tier 1, which is why this is viable.

### Also worth knowing

- `google.STT` and `google.TTS` are **Cloud/Vertex** products — they take
  `credentials_info`/`credentials_file`, **not** `api_key`. A Gemini API key alone is not
  enough. `google.LLM` does take `api_key`.
- Sarvam's plugin cannot be exercised outside a job context
  (`Attempted to use an http session outside of a job context`) — it uses the agent's shared
  HTTP session. Benchmark it from a real call, not a standalone script.

### Nothing is wired yet — schema included

The columns below are **not** in the database; the ALTER was drafted but never run. Whoever
picks this up starts from a clean slate:

```sql
ALTER TABLE agent_config
    ADD COLUMN fallback_enabled      BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN stt_fallback_provider TEXT, ADD COLUMN stt_fallback_model TEXT,
    ADD COLUMN tts_fallback_provider TEXT, ADD COLUMN tts_fallback_model TEXT,
    ADD COLUMN llm_fallback_provider TEXT, ADD COLUMN llm_fallback_model TEXT;
ALTER TABLE calls ADD COLUMN fallback_used TEXT;
```

> When adding these, extend `store.py`'s `AgentConfig` dataclass **in the same change**.
> Extra DB columns are harmless — `load_config()` only reads fields the dataclass declares —
> but a dataclass field with no column raises on every call, and the symptom is calls that
> ring forever with no visible error. That cost real time twice in this project.

---

---

## ✅ Step 10c — Cost guardrails + monitoring

### Guardrails — the system had no brake at all

`agent_config` has carried `max_turns` (40) and `max_duration_sec` (600) since Step 8, but
**nothing ever read them**. A call could run indefinitely and a looping LLM had nothing
stopping it. Now enforced, plus a token cap:

| Limit | Default | Enforced by |
|---|---|---|
| `max_duration_sec` | 600 | Watchdog task, 5 s poll |
| `max_turns` | 40 | Counter on each assistant turn |
| **`max_prompt_tokens`** | 150000 | Cumulative across the call |

The token cap is the one that actually bounds spend. A single observed call used
**32,816 prompt tokens** — the knowledge base rides along on every request, so prompt
tokens dominate, not completions.

**Breaching a limit does not cut the caller off.** The agent speaks `limit_message`, waits
for playout, then `ctx.delete_room()`:

```python
h = await session.say(msg, allow_interruptions=False)
await h.wait_for_playout()     # never cut mid-sentence
await ctx.delete_room()
```

Verified with `max_duration_sec=45`:

```
id 57 | limit | max_duration_sec=45 | 4 turns | 52199 ms | 3619 tokens | 697 tts chars
```

52 s against a 45 s limit — the extra 7 s is the 5 s watchdog poll plus the closing
message. Expected.

### Usage recorded per call

`UsageSummary` is stored on every call: prompt/cached/completion tokens, TTS characters,
TTS and STT audio seconds, turn count, and which limit fired.

> **Usage is stored, not cost.** Rates change; usage does not. Multiply at query time with
> whatever the rates are that day. Hard-coding prices into the schema would age badly.

### Monitoring — Grafana straight onto Postgres

No Prometheus, no exporter. Every call metric already lives in `calls` and `turns`, so
Grafana queries Postgres directly.

> The worker's HTTP server (`:8081`) answers `/` with `200 OK` but has **no `/metrics`** —
> checked before building anything. `prometheus-client` being a dependency does not mean an
> endpoint is exposed. System metrics (CPU, worker health) would still need
> node_exporter + Prometheus; that is a separate, less urgent piece.

`grafana/provisioning/` holds the datasource (fixed `uid: aivoice`, reachable as
`postgres:5432` over the compose network) and the dashboard JSON, so both come up
automatically on a fresh deploy.

**Dashboard** — `http://<server>:3000/d/aivoice-ops`, firewalled to the workstation only:

| Panel | Reads |
|---|---|
| Calls / p50 / p95 / transfer % / limit hits | Top-row stats, thresholded |
| **Where the time goes** | eou vs llm_ttft vs tts_ttfb, stacked — **eou rising is our machine; llm/tts rising is the provider** |
| Turn latency | p50 and p95 over time |
| How calls ended | completed / transferred / limit |
| LLM tokens | cached vs uncached, stacked — the cost driver |
| Recent calls | Full per-call record |

> ⚠️ **`Transferred to human: 70.8%` in the first screenshot is a load-test artefact** — the
> synthetic `demo-thanks` audio was repeatedly read as "customer wants an agent". On real
> traffic this is the single most important business metric: a high rate means the agent is
> not earning its keep.

### Grafana gotcha

The admin password is read from `GF_SECURITY_ADMIN_PASSWORD` **only on first boot**; after
that it lives in Grafana's own database. Changing `.env` later does nothing. Either:

```bash
docker exec -it grafana grafana cli admin reset-admin-password "$GRAFANA_PASSWORD"
# or, if nothing is saved yet:
docker compose rm -sf grafana && docker volume rm aivoice_grafana-data && docker compose up -d grafana
```

---

## 🚧 Step 11 — Admin panel

Grafana goes away. Two dashboards to maintain was one too many, so the graphs move
into the panel itself and Grafana is retired once Phase 3 lands.

### What it has to be

| Requirement | Consequence |
|---|---|
| Our team **and** clients log in | Role-based access, and tenant isolation as a hard boundary |
| Multi-tenant, multi-campaign | `tenants → campaigns`; a campaign owns prompt + KB + voice |
| Enterprise feel | React + TypeScript, real alerting, not a CRUD skeleton |
| Call logs with latency + tokens | Already recorded per turn — this is presentation, not new plumbing |
| Call recordings | Asterisk `MixMonitor` writes them; needs an auth'd streaming endpoint |

Campaign is the unit of configuration: one client runs sales / support / collection
side by side, each with its own prompt, knowledge base and voice.

### Deploy workflow (new from here on)

Code is written on the Windows box, pushed to GitHub, pulled on the server. No more
editing files in place over SSH — the server stops being the source of truth.

```bash
# on the server, once
git clone https://github.com/vermaeramit/livekit_with_asterisk /srv/aivoice

# every update
cd /srv/aivoice && git pull
docker compose -f admin/docker-compose.yml up -d --build
```

`/opt/aivoice` (media stack, `.env`, `gcp/sa.json`) stays where it is. The admin
stack is a **separate compose project** that only joins the existing network, so
rebuilding the panel can never bounce Asterisk, LiveKit or the agent workers.

### ✅ Migration 001 — multi-tenant foundation

`migrations/001_multitenant.sql`, applied live. Deliberately additive: `campaign_id`
is added *alongside* `config_name` and backfilled, nothing is renamed or dropped, so
the running workers never noticed. Migration 002 switches the agent over to
`campaign_id` and only then drops `config_name`.

New: `tenants`, `users`, `campaigns`, `config_audit`, `user_sessions`.
Extended: `campaign_id` on `agent_config` / `kb_documents` / `kb_chunks` / `calls`,
plus a denormalised `calls.tenant_id` (every list and chart filters by tenant;
joining through `campaigns` on each query is waste).

Verified after apply: 10 tables, `default/default` tenant+campaign seeded,
`agent_config.campaign_id = 1`, 60/60 existing calls linked.

Two constraints worth remembering, both enforced in the schema:

```sql
role IN ('superadmin','tenant_admin','agent','viewer')
-- a superadmin has no tenant; everyone else must have one
(role = 'superadmin' AND tenant_id IS NULL) OR (role <> 'superadmin' AND tenant_id IS NOT NULL)
```

### ✅ Phase 1 — backend: auth, RBAC, calls API

`admin/backend/`

| File | What it does |
|---|---|
| `app/config.py` | Settings from env — DB URL, JWT secret, token lifetimes, CORS |
| `app/db.py` | asyncpg pool (2–10 connections, 15 s command timeout) |
| `app/security.py` | argon2 hashing, access-token sign/verify, refresh-token pair |
| `app/deps.py` | `current_user`, `require_roles`, `tenant_scope` |
| `app/routers/auth.py` | login / refresh / logout / me |
| `app/routers/calls.py` | list (filter + paginate), detail (transcript), KB citations |
| `seed_admin.py` | creates the first superadmin, password via `getpass` |

**Decisions that matter later:**

*The access token is not trusted as a source of truth.* Every request re-reads the
user row, so deactivating an account or changing a role takes effect immediately
instead of lingering for up to 15 minutes.

*Refresh tokens are stored as SHA-256, never raw.* A database leak cannot be
replayed, and a session can be revoked without rotating the signing key. Refresh
**rotates** — the old token dies on use, so a stolen one works at most once and the
real client's next refresh fails loudly rather than silently sharing a session.

*Tenant isolation is resolved in exactly one place* (`tenant_scope`) and every query
takes its answer. A non-superadmin cannot widen its own scope no matter what it puts
in the query string.

*A call belonging to another tenant returns 404, not 403.* A distinct 403 would let a
client probe which call ids exist elsewhere.

*Login verifies against a dummy hash when the email is unknown*, so a wrong email and
a wrong password take the same time — otherwise response timing enumerates accounts.

**Traps hit:**

- `EmailStr` needs `email-validator` installed or the module raises at *import* time,
  not on first request — an easy one to miss until the container won't boot.
- The refresh handler's row has `id` = **session** id and `user_id` = user id. Passing
  the row straight into the token issuer would have minted tokens under the wrong
  subject. `_issue()` now takes explicit arguments instead of a row.

### ✅ Phase 1 — frontend: React console

`admin/frontend/` — React 18 + Vite + TypeScript + Tailwind. No component-library
CLI in the loop; the primitives are hand-written in the same idiom, so there is
nothing to re-generate and no build-time network dependency.

| Screen | What it shows |
|---|---|
| Login | Split brand panel; errors inline, never a blank redirect |
| Calls | Filter (search / campaign / outcome / date), paginate, click through |
| Call detail | Transcript, per-turn latency breakdown, KB citations, token usage |

**Design correction.** The first cut defaulted to dark, and the dark itself sat at
9% lightness — it read as an unlit screen rather than a designed surface. Enterprise
consoles are read for hours in lit offices and pasted into tickets, so:

- **light is now the default**, dark is opt-in and remembered
- dark was rebuilt on slate (13–16% lightness), not black
- a pre-paint script in `index.html` applies the stored theme before React boots,
  otherwise a dark-theme user gets a white flash on every navigation
- system font stack only (Segoe UI / SF) — the panel must render identically with
  no network access

**The latency bar is the point of the detail screen.** Three stacked segments:
turn detection (ours), LLM first token, TTS first byte. When a call feels slow this
says whose fault it is. `stt_ms` is deliberately *not* a segment — it is already
inside `eou`, and adding it double-counts, which is a mistake made once already in
this project.

**Token handling.** The access token lives in memory; only the refresh token is
persisted. Concurrent refreshes share one promise — since the backend rotates on
use, two parallel refreshes would revoke each other and log the user out at random.

### ✅ Phase 2a — clients, users, campaigns

Until this landed the panel had exactly one hand-seeded superadmin and no way to
create anything, so no client could sign in. Everything else was blocked behind it.

**Migration 002** (additive): `must_change_password`, `created_by`,
`password_changed_at`, plus slug-format CHECKs on `tenants` and `campaigns` —
campaign slugs end up inside `agent_config.name`, which the workers key on, so
they are constrained in the database rather than trusted from the UI.

| Screen | Notable behaviour |
|---|---|
| Clients | create, suspend, reactivate |
| Users | create with generated password, reset, disable, delete |
| Campaigns | create (with its agent config), enable/disable, delete |

**Decisions worth keeping:**

*There is no delete for a client.* It would cascade through campaigns, users and
the knowledge base and detach every historical call — irreversible, behind one
click. Suspend instead.

*Creating a campaign creates its `agent_config` in the same transaction.* A
campaign without one cannot take a call, so a half-created pair is not a state
worth allowing. The config name is `{tenant-slug}-{campaign-slug}` — derived from
slugs, not labels, so it stays stable when migration 003 switches the workers to
`campaign_id`.

*A campaign with call history cannot be deleted*, only disabled. At that point
deletion is destroying evidence, not tidying up.

*`must_change_password` is enforced server-side*, by a new `active_user`
dependency that every data endpoint depends on. An admin picks the initial
password, so until it is replaced that admin can sign in as the user — a
client-side redirect is a suggestion, not a control. `current_user` (identity
only) still backs `/auth/me`, `/auth/change-password` and `/auth/logout`, which a
half-onboarded user must be able to reach.

*A suspended tenant is refused at login and on every request*, not just hidden in
the UI.

*Resetting or changing a password revokes every session for that user.* A reset
prompted by a suspected compromise is pointless if the intruder keeps a week-long
refresh token.

**RBAC rules that are tested, not just intended:** a `tenant_admin` cannot mint a
superadmin, cannot reach another tenant (404, not 403 — a distinct 403 confirms
the id exists), and cannot deactivate or delete itself. The last active
superadmin cannot be removed.

**Trap:** TypeScript does not narrow a union on `'to' in item` when the other
variant declares `to?: never` — the nav needed a real `kind` discriminant. Six
type errors, all pointing at the wrong line.

### ✅ Phase 2b — agent config editor + knowledge base

Per-campaign editing of prompt, greeting, voice, model, retrieval tuning, cost
guardrails and handoff, plus PDF upload with a chunk preview and a change history.

Saves apply from the **next call**. `store.load_config()` runs inside the job
entrypoint, so nothing in progress is disturbed and no worker restart is needed.

**Three things are deliberately not in the editor**, and the reasoning matters
more than the omission:

- `stt_provider` / `llm_provider` / `tts_provider` — [voice_agent.py:241](../agent/voice_agent.py#L241)
  builds `sarvam.STT`, `openai.LLM` and `sarvam.TTS` unconditionally and never
  reads those columns. A dropdown would have been a lie. They return with the
  fallback chain.
- `agent_config.enabled` — `load_config()` selects `WHERE name = $1 AND enabled`
  and raises when it misses, which makes calls **ring forever with no visible
  error**. That is failure mode #8 from this log, twice. Not a switch to expose.
- Campaign enable/disable is labelled in the UI as not yet affecting calls,
  because it does not: the workers still choose their config from `AGENT_CONFIG`.
  A control that quietly does nothing is worse than an absent one.

The UI also warns that `SARVAM_STT_MODEL` and `SARVAM_TTS_VOICE` in the server
`.env` override two of the fields, with the command to check.

#### Knowledge base ingestion runs inside admin-api

`agent/kb.py` is **mounted read-only**, not copied:

```yaml
volumes:
  - ../agent:/app/kblib:ro
  - kb-files:/data/kb
```

Two copies of the chunking logic would drift, and retrieval quality would then
differ by which route ingested a document with nothing to say so. `app/kblib.py`
is the shim that puts it on `sys.path` and reports clearly when the mount is
missing instead of failing at upload time.

Cost of this choice, accepted knowingly: `OPENAI_API_KEY` now lives in
`admin/.env` too, and the image carries pymupdf. The panel is behind a
source-restricted firewall and already holds the database credentials — which is
to say, every transcript.

**`ingest_file()` now offloads extraction and chunking to a thread.** pymupdf4llm
holds the GIL for seconds on a 50-page document; on the CLI that only makes the
prompt wait, but inside the API it froze every other request for the length of an
upload. It also takes an optional `campaign_id` so panel uploads are tenant-scoped,
with `COALESCE` on update so a CLI re-ingest cannot orphan a scoped document.

**Upload handling:** streamed to disk with the size checked as it arrives (reading
it into memory first would let one upload decide the container's RAM), magic-byte
checked rather than trusting the extension, and filename-sanitised — `Path().name`
plus a character filter, with a check that what remains is not empty or `..`.

Files are kept in a volume so re-ingest after a chunking change does not mean
asking the client for the PDF again. Documents from before the panel existed live
in `/opt/aivoice/kb/inbox` and report that plainly instead of failing obscurely.

The chunk viewer is the point of the screen: a PDF that extracted badly reads as
nonsense there, which is far easier to catch than diagnosing it from one wrong
answer on a live call.

### 🔥 The deploy gap — agent changes were never reaching production

Worth writing down in full, because everything about it looked fine.

The git workflow was introduced for the admin panel: write locally, push, `git
pull` on the server into `/srv/aivoice`. The systemd units, written earlier, ran
`/opt/aivoice/agent/voice_agent.py`. So `git pull` updated a copy nothing
executed, and `systemctl restart` relaunched the old code — registering cleanly,
logging normally, entirely healthy. **The tenant-stamping fix appeared to deploy
and did nothing.**

It only surfaced because the smoke test asserted every call has a campaign, a
real call arrived between two runs, and the count went 60 → 61.

Fixed by pointing the units at the checkout. The venv stays in `/opt` — it is not
in git and expensive to rebuild — as do `.env` and `gcp/sa.json`, which must never
live in a checkout.

**Four traps in one hour, all now in the runbook:**

| What happened | Why it fooled us |
|---|---|
| `cp` of the unit file silently did nothing | root's `cp` is aliased to `cp -i` on Rocky; in a pasted block the next line answers the prompt |
| Moved `/opt/.../*.py` aside before verifying the unit had changed | Left the workers with no code at all. **A destructive step must come after verification, never in the same block** |
| `registered=0` twice, and an empty `ss` | A worker takes **~11 s** to register. Both checks ran immediately after the restart |
| `errors=0` while nothing could start | Python prints `can't open file`, which matches neither `ERROR` nor `Traceback` |

The empty `ss` also produced a wrong conclusion that got written into the runbook
— that `start` mode never binds `AGENT_HTTP_PORT`. It does; the check was simply
early. Corrected, and the port check reinstated.

### ✅ Phase 4 — call recordings

90-day retention, converted to Opus. Disclosure goes in the campaign greeting.

#### Correlating a recording to a call took three attempts

Asterisk **dials** LiveKit, so there are two SIP legs with two different Call-IDs.
Getting this wrong is silent: you end up with recordings and call rows that never
join, and a column that looks populated.

| Attempt | Result |
|---|---|
| Record on the caller's channel | Its Call-ID is not the one LiveKit sees |
| Record in a `Dial()` `b()` pre-dial handler, name by `${CHANNEL(pjsip,call-id)}` | ✅ runs on the outbound leg; Call-ID **is** allocated by then — verified, not assumed |
| Agent stores `sip.callID` | ✗ that is LiveKit's own id, `SCL_7c3USwsGRuui` |
| Agent stores **`sip.callIDFull`** | ✅ exactly the filename |

The answer came from dumping the participant's attributes on a live call rather
than from guessing a fourth key. There is deliberately **no fallback** from
`callIDFull` to `callID`: storing the wrong one leaves a column that looks fine
and never matches a file.

Recording on the outbound leg has a second benefit — once bridged, its rx is the
caller and its tx is the agent, so one `MixMonitor` captures both sides including
everything after a transfer to a human.

Sizes came in better than estimated: a 32-second call is **82 KB** at ~20 kbps.
90 days at 20 calls/day is under a gigabyte.

#### What the endpoint has to get right

**HTTP Range, written out rather than assumed.** Without it a browser's audio
element cannot seek — the scrubber moves and the audio does not. The smoke test
asserts 206, the exact byte count, `Content-Range`, 416 past the end, and the
suffix form `bytes=-64`, which means the **last** 64 bytes and is the easiest
part of the spec to implement backwards.

**Availability is read from disk, never stored.** Retention deletes files without
touching the database, so a column would go stale and offer a player for audio
that no longer exists.

**`sip_call_id` is regex-checked before touching the filesystem** — it arrives in
a SIP header.

**The player fetches through the token into a blob.** `<audio src>` cannot carry
an `Authorization` header and this endpoint is not public.

#### Traps

| What happened | Why |
|---|---|
| `[recsetup]` swallowed 702 and 1001 | A context header claims every extension after it. **Second time** in this project. Now last in the file, and verified by parsing rather than reading |
| Every endpoint failed to register with `401` | The documented deploy copied `pjsip.conf` from git — which held `password=CHANGEME`. Only the template is tracked now |
| `docker compose logs asterisk` was empty | Asterisk logs to a file. And it is `messages.log`, not `messages`, with no `verbose` level — so dialplan `NoOp` output needs an added channel |
| Smoke test crashed on the recording | It parsed every body as JSON. This one is audio |
| Four header checks "failed" | Looked up `Content-Range`; Starlette emits lowercase. The mechanics had been passing all along |

### ✅ Migration 004 — campaign-aware routing

Two panel controls were cosmetic until this: disabling a campaign stopped
nothing, and a second campaign could not take a call at all. Every worker served
one config, chosen by `AGENT_CONFIG`.

**Routing key is the dialled number** (`campaign_routes.did` → campaign), read
from `sip.trunkPhoneNumber`. It is how a caller already distinguishes a client's
sales line from their collections line. DIDs are `UNIQUE` across *all* tenants —
two clients claiming the same number must fail at configuration time, not by
sending one client's caller to another's agent.

Config now loads **after** `ctx.connect()`, because the dialled number arrives
with the SIP participant. Measured no cost: p50 stayed at 2013 ms.

Three outcomes, kept distinct on purpose:

| | |
|---|---|
| Routed | that campaign's config |
| Unmapped number | **refused** — room deleted, Asterisk falls through to the human extension |
| Disabled or suspended | same treatment |
| No dialled number at all | falls back to `AGENT_CONFIG` — a manual `dev` run has nothing to route on |

> 🔥 The unmapped case shipped as a *fallback to the default agent*, and the
> commit that introduced it said in as many words that a silent fallback would
> serve one client's agent to another client's caller. It was written as a
> warning log and left in.
>
> It stayed invisible because the dialplan only forwarded `700`, which was
> routed. Widening it to `_7XX` exposed it immediately — every number in the
> range answered whether configured or not, which made the routing list
> decorative. **Writing the danger down is not the same as not shipping it.**

That last one needed care. Simply returning leaves the caller ringing for the
full 25 s Dial timeout, because livekit-sip does not answer until an agent
subscribes. `CampaignUnavailable` is its own exception for the same reason: a
missing config is a deployment fault and should be loud, while a paused campaign
is a normal state whose caller deserves an answer rather than silence.

Verified both paths on the server — a routed call logs `config=default` with no
fallback warning, and a disabled campaign logs
`DECLINED call to 700: campaign 'Default Campaign' is disabled` with the caller
reaching the human extension in seconds.

**Not in scope:** dropping `config_name` (knowledge-base retrieval keys on it),
and per-campaign recording — Asterisk starts the recording and does not read the
database, so that needs `func_odbc` or similar. Recording is still global.

### ✅ Phase 5 — live monitoring and alerting

**Live monitor** reads `calls` rather than the LiveKit API. The rows are already
tenant-scoped and already carry campaign, caller and per-turn latency, so asking
LiveKit would mean reconciling two sources of truth for no extra information.

A call is "in progress" while `ended_at IS NULL` — which is also exactly how a
worker that died mid-call looks. Staleness is therefore *computed*: past its own
duration guardrail plus half again, a row is flagged rather than hidden, with a
pointer at the journal. A stuck call that quietly vanishes is worse than one that
looks wrong.

The page warns above 10 concurrent because that is the load-tested figure. It
says "unmeasured past here" rather than inventing a limit.

**Alerting** runs in the API process — it needs that pool and nothing else, and a
second process is one more thing to notice had died. Noted in the code that this
assumes a single instance; two replicas would double-fire.

Decisions that shape whether anyone actually reads the alerts:

*Written to `alerts` before the webhook is attempted.* The row is the record,
delivery is best effort. A chat tool being down stores `delivery failed` with the
reason instead of losing the alert.

*Edge-triggered.* One alert when a rule starts breaching, then silence until it
clears. Level-triggered would have produced sixty alerts an hour for one bad
afternoon — and people mute those. Editing a threshold **re-arms** the rule, so
turning the noise down cannot accidentally mute the next real breach.

*Percentage rules honour `min_calls`.* Two calls, one of which errored, is not a
50% error rate worth waking anyone for.

*Thresholds come from measurements.* p50 sits at ~2.0s and p95 at ~3.3s, so the
latency rule fires at 4000 ms — a regression, not variance.

*The webhook URL is a credential* — anyone holding it can post into the channel.
Never returned by the API, never logged, never in the audit trail; only whether
one is set, plus a 30-character hint.

**Trap:** `GET /alert-webhook` returned 400 to a superadmin, and it was right to.
The webhook belongs to a client and a superadmin is in none of them, so an
unscoped request is genuinely ambiguous. The console had the same gap — fixed
with a client selector rather than by making the API guess.

### ⏭️ Remaining phases

| Phase | Scope |
|---|---|
| 3 | Analytics in-panel (replaces Grafana), then retire it |
| 4 | Call recordings — storage, retention, auth'd playback |
| 5 | Live call monitoring + alerting |

---

## Capacity — 20 concurrent (4 Aug 2026)

**Result: 20 requested, 20 reached the dialplan, 20 got an agent, 0 fell to the
human.** Six workers, `MAX_JOBS_PER_WORKER=10`, 32 cores / 48 GB.

### The ceiling was CPU-based load reporting

Dispatch stopped dead between 2 and 6 concurrent calls all day. It did not move
for **any** of these:

| Changed | Ceiling |
|---|---|
| workers 1 → 3 → 6 | 2 → 6 → 6 |
| `LOAD_THRESHOLD` 0.7 → 5.0 → `inf` | unchanged |
| `NUM_IDLE_PROCESSES` 3 → 8 | unchanged |
| stagger 0.7 s → 3 s | *worse* |
| 8 cores/12 GB → 32 cores/48 GB | unchanged |

Two LiveKit functions decide it, and neither is the worker's own gate:

```go
// psrpc claim - pkg/service/agentservice.go
affinity += max(0, h.targetLoad - w.Load())      // DefaultTargetLoad = 0.7

// then worker selection
normalized := max(0, 1 - w.Load())               // hardcoded 1, ignores targetLoad
```

`w.Load()` is whatever the worker reports. livekit-agents defaults to
`psutil.cpu_percent()` — **system-wide CPU, clamped to 1.0**. One live call pins
roughly a core, the value saturates, the weight goes to zero. And because it is
system-wide, every worker on the box reports the same saturated number at the
same instant — which is exactly why adding workers, cores and RAM did nothing.

The fix is `load_fnc`: report `len(active_jobs) / MAX_JOBS_PER_WORKER` instead.
CPU never described this workload — STT, LLM and TTS are network calls and a
conversation is mostly spent waiting. `agents.target_load: 5.0` in
`livekit.yaml` is also needed, but on its own it only moves the refusal from
`no servers available` to `no workers with sufficient capacity`.

### What the failure looked like from outside

Nothing pointed at LiveKit. Workers stayed `WS_AVAILABLE`, never logged `full
capacity`, declined nothing, and three of six sat idle through runs they were
refused for. `_answer_availability()` rejects **silently** — the "full capacity"
line lives in a different function — so "no full-capacity logs" was true and
worthless. Six wrong causes were proposed and killed by measurement before the
right one: CPU saturation, spawn contention, memory, a blocking load function,
warm-pool exhaustion, and a leak in LiveKit's job accounting.

### Two unrelated faults found on the way

**A reboot silently deleted the SIP configuration.** `redis.conf` had
`save ""` / `appendonly no` under a comment saying it held only LiveKit
coordination state. It also holds the SIP inbound trunk and dispatch rule. After
the reboot Redis had two keys, and every call rang and died with SIP 486 —
refused *before* any rule lookup, so no log anywhere said "no rule". Persistence
is on now, with a named volume, and `maxmemory-policy` is `noeviction` (it was
`allkeys-lru`, free to evict the same two keys mid-production).

**livekit-sip rate-limits a burst from one source** with 486 and
`"reason": "flood"`, which is indistinguishable from "no agent" at the Asterisk
end. `loadtest.sh` now counts it separately.

### Provider limits are the next wall, not the platform

At 20 concurrent, Sarvam ran out of credits (402) and the **TTS FallbackAdapter
switched to OpenAI mid-call, as designed** — its first real proof. The LLM chain
did not hold: `all LLMs are unavailable, retrying..` means both OpenAI and
Gemini failed, and three calls ended `end_reason='error'`. Latency from that run
(p50 2221 / p95 3918) is not a valid sample.

---

## Asterisk out of Docker (10 Aug 2026)

The team who operate the telephony side do not work with containers. A PBX
nobody on call can debug is the wrong trade, so it now runs natively.

Worth saying plainly: **the usual argument against Asterisk in Docker never
applied here.** Everything ran `network_mode: host`, so there was no NAT, no
port mapping, and no measurable difference — `inviteToRingingMs: 4`, and 20
concurrent calls sat inside a 1.5-CPU container limit. This move is
operational, not technical.

| | |
|---|---|
| Version | 20.20.1 from source; EPEL only has 18, which is **EOL upstream** |
| Service | `systemctl {status,restart} asterisk` |
| Config | `/etc/asterisk` — five files ours, ~100 stock |
| Recordings | `/var/spool/asterisk/recordings`, bind-mounted read-only into `admin-api` |
| Rollback | compose service kept behind a `rollback` profile |

### SELinux is the part that will catch the next person

Asterisk started, then exited immediately:

```
ASTdb initialization failed.  ASTERISK EXITING!
Unable to open Asterisk database '/var/lib/asterisk/astdb.sqlite3'
```

Meanwhile `sudo -u asterisk touch /var/lib/asterisk/.writetest` succeeded. DAC
said yes; SELinux said no, and the message named neither.

`/usr/sbin/asterisk` carries `asterisk_exec_t`, so Rocky's policy confines the
process to `asterisk_t` and expects `asterisk_var_lib_t` on its data. A source
build creates those directories as plain `var_lib_t`. The policy was already
right — only the labels were wrong:

```bash
restorecon -Rv /etc/asterisk /var/lib/asterisk /var/log/asterisk                /var/spool/asterisk /usr/lib64/asterisk
```

`ausearch -m avc` gave the answer in one line and should have been the first
thing checked, not the fourth.

### Three things that would have broken quietly

- **Recordings.** They lived in the `aivoice_recordings` volume that the panel
  mounted. All 131 were copied to the host path — copied, not moved, and the
  volume left in place. Missed, every recording made before today would have
  disappeared from the console with nothing in any log to say so.
- **`rec-postprocess.sh` and ffmpeg.** ffmpeg is not in Rocky 8's repos.
  Without it the script keeps the WAV and logs — recordings survive, at ten
  times the disk, and nobody notices until the disk does.
- **`loadtest.sh`.** Four `docker exec asterisk` calls, plus a `docker stats`
  read for the ASTERISK column that would simply have gone blank.

### Not verified

Capacity was not re-measured natively. 20/20 was proven under Docker; on the
native install it is assumed. Run `loadtest.sh 20` before relying on it.

---

## Per-client keys, providers and context (11–13 Aug 2026)

Four features that share one theme: **things that used to live in `.env` and
apply to everyone now live in the database and apply per campaign.** Each
client brings their own keys, their own providers, and their own data.

### Provider keys per campaign (migration 008)

Keys were in `/opt/aivoice/.env` — one OpenAI key, one Sarvam key, every client
billed to the same account. Now they are rows in `provider_keys`, encrypted
with Fernet (`agent/crypto.py`, `SECRETS_KEY`), resolved at job start.

Three rules, all deliberate:

- **The API never returns a key.** Not masked, not partially — the response
  carries a four-character hint and nothing else. A field that *can* return a
  secret eventually does, through a log, a cache, or a browser extension.
- **The agent and the panel share one crypto module**, mounted at `/app/kblib`
  rather than copied. Two copies is how a panel encrypts one way and an agent
  cannot read it back — and that failure is invisible until a call drops,
  because the console shows the key as configured either way.
- **A campaign without a required key fails at start**, not mid-call
  (`ProviderKeyMissing`). By the user's decision, keys are mandatory per
  campaign; falling back to a shared key would silently bill the wrong account.

### STT and TTS chosen per campaign (migration 011)

`stt_provider` / `tts_provider` plus an explicit fallback column each. The
agent builds its stack from the campaign row, not from a constant.

**Soniox was added, measured, and not adopted.** It works, and it is wired in —
but the voice was judged poor and the latency was not better. It stays
selectable so the judgement can be revisited with a real comparison rather than
a rebuild.

Resampling is the trap here: TTS native rates differ (`sarvam` 22050,
`openai` 24000, `soniox` 24000) and a mismatch is not an error, it is a quiet
resample that costs latency on every turn.

### What the dialler sends (migration 012)

Their system passes per-call context as IAX2 variables → SIP headers →
participant attributes: name, product, call type, and their own lead / SR /
call identifiers.

The split is the important part:

| | |
|---|---|
| **To the model** | name, product, call type — as a *separate* chat message |
| **Stored only** | `lead_id`, `sr_id`, `call_unique`, `language` |

Two reasons it is a separate message and not part of `instructions`: the
instructions are the cacheable prefix, byte-identical across every call, which
is what earns OpenAI's prompt cache (**1198 ms cold against 805 ms warm**).
Putting a caller's name in them makes every prefix unique and the cache never
hits again — silently. And a model handed a lead ID will eventually read it out
to the caller.

Greetings take `{{cus_name}}`, `{{modalname}}` etc. with `|fallback` defaults,
substituted only into spoken strings.

### HTTP tools per campaign (migration 013)

The agent can call the client's API mid-conversation. Defined in the console,
built at job start, executed by `agent/tools.py`.

Everything in that module exists because **a tool call happens while someone is
listening**: a 2500 ms default timeout because past that the caller hears
silence, an 8 KB response cap because the whole body otherwise lands in the
next prompt, and `ToolError` rather than an exception so the model has
something it can say out loud.

Two findings worth keeping:

- **The default User-Agent is a WAF magnet.** Three separate public APIs
  answered aiohttp and urllib with Cloudflare error 1010 while `curl` succeeded
  from the same host. A client API behind a WAF would have failed identically,
  mid-call. Both the agent and the console's test button now send
  `AIVoice-Agent/1.0`.
- **The test button lied once.** It applied neither `response_path` nor the
  same substitution as the agent, so it showed a whole document where the model
  would have seen one field. A test that does not match reality is worse than
  no test, because it is believed. Both now import `agent/toolfmt.py` — shared,
  not reimplemented.

`TOOL_BLOCK_PRIVATE_HOSTS` is **off** by decision: clients host their APIs
wherever they like. Worth knowing what off means — the URL is fetched by this
server, from inside the network, and the model decides when.

### Not verified

**The timeout path has never been heard on a real call.** `/slow?ms=4000`
against a 2500 ms tool proves the panel records it; it does not prove what the
caller hears. That is still the one test that matters.

---

## Call diagnostics in the console (13 Aug 2026)

Every question asked while debugging this week — which voice served that call,
did the dialler context arrive, did the tool fire, what did the model send —
was answered by SSH and `journalctl`. All of it was already in the database and
none of it was on screen.

Three additions to the call detail page, no new plumbing:

- **Tool calls interleaved into the transcript**, ordered by time. In line, not
  in a table of their own: the question is never "what tools ran", it is "the
  caller asked X, why did the agent answer Y" — and that is only answerable
  next to the turns either side. The **arguments the model chose** are shown,
  because a tool that "did not work" is usually a tool called with a wrong or
  empty argument, and the transcript never shows that.
- **From the dialler** — every attribute they sent, marked with whether it
  reached the model. "The model knew the name but was never told the lead ID"
  is the difference between a prompt bug and a dialler bug, and both look
  identical in a transcript.
- **Handled by** — the STT / LLM / TTS that actually served the call, *always*,
  not only when a fallback fired. Recorded per call, so it stays true after the
  campaign is edited.

A failed tool now raises a banner alongside the fallback and guardrail ones,
because it has the same shape: the call completed and nothing looks wrong from
outside, but the caller got an apology instead of an answer.

**Response bodies are deliberately not stored.** A client API answers with
customer records, and keeping them would put personal data in a table nobody
thinks of as holding it.

### What a tool call records, and why the URL was added late

`tool_invocations` stored the arguments the model chose. That was not enough.

A tool went out with `pincode={pin}` — **single** braces, which nothing
substitutes, so the literal string reached the API. It answered *"No dealer
details found for the given pincode"*, and every stored field looked correct:
the arguments were right (`pin: 124001`), the status was a plausible 404, and
the same request from Postman worked. The fault existed only in the URL that
was actually sent, and that was the one thing not recorded.

Migration 014 adds it. Arguments **plus** the resolved URL are enough to replay
any invocation through the test button, which is why the **response body is
still not stored** — a failure can be reproduced without keeping a client API's
customer records in our database.

Rejected on save now, in both forms:

| Written | Result before | Result now |
|---|---|---|
| `{pin}` | sent literally, API answers plausibly | rejected — "placeholders need two braces" |
| `{{pin}}` with no `pin` in properties | substituted to empty, `?pincode=` | rejected — "declares nothing" |
| `required: [pin]`, properties has `registration` | model never sends it | rejected |

The Tools tab also gained **Recent activity**: real invocations across the
campaign's calls, newest first, failures-only filter, link to each call. The
test button proves a tool works when you press it; this says whether the model
is calling it during real conversations, and with what.

### A form whose errors could not be acted on

Three faults stacked, each hiding the next:

1. `messageOf()` dropped `loc` from every FastAPI validation error, so **no form
   in the console had ever named its bad field**. A fourteen-field dialog
   answered "String should match pattern `^[a-z][a-z0-9_]{2,47}$`".
2. Fixing that named the field — and it still could not be acted on, because
   the banner sits at the foot of the dialog and the field was above the fold.
3. Scrolling to it did nothing, because **the dialog had no height cap**. It
   grew past the viewport and the wrapper scrolled instead — and a flex item
   taller than its `items-center` container overflows equally above and below,
   with the top half unreachable. The first field was permanently off screen.

Now: errors render against their own input, the first bad field is scrolled to
and focused, and every dialog caps to the viewport with its body scrolling and
its header and footer pinned.

### What this is not

Raw agent logs. Those are in `journalctl` on the host, and `admin-api` runs in
a container that cannot see the host journal — surfacing them means shipping
logs somewhere, with retention and another service to run. Deferred on purpose:
these three cover the recurring questions, and raw logs are only needed when
something *crashes*. If the server still gets SSH'd into regularly after this,
that is the measurement that justifies the log pipeline.

---

## The ringing before an answer (14 Aug 2026)

The dialler team asked for the ringback to be removed: they hand over calls
that are **already connected to a human**, so every ring is a person listening
to nothing. Their suggestion was to move the connection off LiveKit, based on
another project where that had been the fix.

Ringback is not the cause. `Dial(PJSIP/700@livekit,...)` leaves our inbound leg
unanswered, so LiveKit's 180 Ringing passes straight through — and livekit-sip
holds the INVITE at 180 until an agent subscribes to the caller's track. **The
ring is a measurement of our own startup time.**

### Where four seconds went

| | |
|---|---|
| invite → 100 Trying | **0 ms** |
| invite → 180 Ringing | **5 ms** |
| invite → participant in room | **43 ms** |
| `ctx.connect()` | **312 ms** |
| config + keys + prompt | **1154 ms** |
| session build | 159 ms |
| greeting TTS first byte | **1817 ms** |

LiveKit's share of a 4030 ms budget is 355 ms. Removing it would have cost a
rewrite of the entire pipeline and bought a third of a second.

### The 1154 ms was an import

`build_instructions` did `import kb` lazily. `kb.py` pulls in **pymupdf4llm**
(PyMuPDF, a large C extension used only for PDF *ingestion*), tiktoken, the
OpenAI SDK, and loads the cl100k_base vocabulary at module scope. None of it is
needed to answer a phone.

A job process handles exactly one call and then exits, so this was paid **once
per caller**, not once per process. Moved to `prewarm`, where it takes
1284–3081 ms while the process sits idle in the warm pool.

It also explains why a provider connection warm-up added an hour earlier
reported `warm_done=False` after 1625 ms: a synchronous import blocks the event
loop, so the task it was racing was never scheduled.

| | Before | After |
|---|---|---|
| config + keys + prompt | 1466–1681 ms | **339 ms** |
| session started (ring stops here) | 1625–1865 ms | **524 ms** |
| ring heard by the caller | ~2.07 s | **~0.75 s** |
| invite → first spoken word | ~4.03 s | **~2.4 s** |

### Three wrong guesses, killed by measurement

- *"Six sequential database round trips."* They take **8 ms** between them.
- *"ICE/TURN discovery is slowing the connect."* `use_external_ip: false` and
  TURN disabled were already correct; connect is 312 ms.
- *"The greeting's TTS is a cold connection."* Warming DNS/TCP/TLS to all three
  provider hosts changed `tts_ttfb` by nothing at all (1817 → 1614 ms, inside
  normal variance).

### What is left, and what it is not

`tts_ttfb` on the greeting is ~1.6 s and **is not ringing** — it is silence
after the call has been answered. Length explains part of it and not all: across
801 recorded turns, 60–89 characters averages 309 ms and 243+ averages 806 ms,
while our 119-character greeting consistently measures over 1500 ms. The
shortest bucket (2–29 chars) is the slowest of all at 863 ms, which no
length-proportional theory survives.

Unresolved on purpose. The complaint was the ring, the ring is a third of what
it was, and the next change should follow a fresh listen rather than another
guess.

---

## Soniox, measured (14 Aug 2026)

Tried once before and rejected on the voice, which was not a fair test - the
voice was `Priya` on `tts-rt-v1-preview`, and neither had been chosen: the model
was the plugin's default and the voice was a guess. Retested properly.

### The numbers

Per-turn averages, from `turns` joined to `calls.stt_provider_used` /
`tts_provider_used`:

| | turns | STT | TTS TTFB | eou | total |
|---|---|---|---|---|---|
| **Sarvam** | 661 | ~300 ms | ~280 ms | ~1050 ms | **~1950 ms** |
| **Soniox** | 144 | 1067 ms | 946 ms | 1454 ms | **~3080 ms** |

Three to four times slower on every layer, about **1.2 s per turn**. Moving to
`tts-rt-v2` changed nothing: the two calls made on it measured `tts_ttfb` 952
and 991 ms.

**Not adopted.** The earlier verdict was right for the wrong reason.

### Two things worth having found

- **`tts-rt-v1` is removed on 31 Aug 2026**, and `tts-rt-v1-preview` is an alias
  of it. That was the agent's hardcoded default. A campaign on Soniox would
  have gone silent mid-call on a date nothing here would have warned about.
- **The console's voice list was wrong and could not know it.** It was hardcoded
  as the union of two models, so it offered Meera, Maya, Noah, Jack, Claire,
  Sofia and Elise - none of which exist on `tts-rt-v2` - and a voice the model
  does not have raises inside `TTS.__init__`, killing the job before the call
  is answered. The list is now read from the provider per model.

Finding the voices took three wrong endpoints: `/v1/voices` returns **cloned**
voices only and is empty on an account that has never made one; `/v1/models`
returns **STT** models only; the built-in voices hang off `/v1/tts-models`.
`server-configs/provider-catalog.py` asks any of them without the key touching
the terminal.

### The mistake that made the latency hunt harder

The campaign had been left on Soniox after the first trial. Every call analysed
during the ringback investigation - including the 1912 ms greeting and the
2873 ms STT - was **Soniox**, while being described as Sarvam throughout. The
per-call `providers_used` columns say so plainly and were not consulted until
the end.

The correction changes the remaining work: the ~1.6 s of silence after answer
was not a TTS cold start to be engineered around. It was the provider.

---

## Ending, waiting, handing over (17 Aug 2026)

Four things a call needs that it did not have, plus the bugs each one exposed.

### End of call, and handing over, on a marker

The model writes `[EOC]` or `[TRANSFER]`; the marker is stripped before TTS and
the action happens once the sentence carrying it has finished playing.

The filter holds back a tail that could still become a marker. This is not
optional cleverness: an LLM streams without regard for token boundaries, so
`[EOC]` routinely arrives as `[EO` then `C]`, and a naive filter passes both -
the caller hears "bracket E O C". With two markers sharing a `[` prefix, a
chunk ending in `[` must be held until it is clear which, or neither, it
belongs to. Matching is case-insensitive: asked for `[TRANSFER]`, the model
wrote `[Transfer]`.

The action deliberately does NOT happen in `tts_node` - audio is still being
produced there, and hanging up would cut off the sentence.

### The confirmation the agent gave itself

Transfer can ask first, so a caller who says "no, wait" is heard. From a live
call:

```
12:14:48  asking the caller first                      <- the tool
12:14:52  refused, the caller has not answered yet      <- the marker
12:14:53  end-of-call marker seen - closing call 292
```

Two routes for one job. The tool asked and set a boolean; the marker, in the
same response, found it set and transferred - the caller silent throughout. The
gate now records a counter that **only the caller's own speech advances**, which
holds however many routes exist. And the transfer tool is removed when a marker
is configured.

Then the `[EOC]` in that same response hung up on someone waiting to be
transferred. Nothing the model writes may end a call that is mid-handoff.

The first attempt to remove the tool did nothing at all: it filtered on
`t.name`, and a method decorated with `@function_tool` is still a function - the
name lives in the tool info the decorator attaches. Matched nothing, removed
nothing, raised nothing. The tool list is logged at startup now.

### Silence

N lines, one per timeout, then the call ends as `no_response`. The array's
length **is** the attempt count - no separate field, because two values that
must agree eventually do not, and the failure mode is a caller hung up on with
no warning.

### While a tool runs

A tool call is silence on the line, up to 2500 ms of it. A per-tool line covers
it - but only if the tool has not answered within 600 ms, and cancelled the
moment it does. Saying it every time makes things worse: the dealer lookup
answers in 74 ms, and "कृपया एक पल रुकिए" takes about twenty times that to say.
Started rather than awaited, so it overlaps the request instead of preceding it.

### A 404 is not a failure

```
caller gives pincode 485056
dealer_by_pincode -> HTTP 404
agent: "अभी dealer की जानकारी नहीं मिल पा रही है"
```

The lookup worked. The answer was "there are no dealers near you", and the
caller was sent away for a reason that was not true. Per-tool wording keyed by
status, `timeout`, or `default`.

Configured, saved, and still ignored: `load_tools` parses JSONB columns back
from text **by name**, and `error_messages` was not on the list. **Third time** a
JSONB column has arrived as text in this code path. It never fails loudly - the
value is a string, whatever reads it quietly does the wrong thing, and the only
symptom is a feature that appears not to work.

### The prompt was reading JSON aloud

A caller heard `{ "customer_name": "", "uses":` spoken. The instructions asked
for a FINAL CALL DATA document as the model's reply, and a reply is what gets
synthesised - while nothing captured it, because there is no tool or column for
it. Also removed: a reference to a test-ride booking tool that does not exist,
and one caller's name hardcoded into the cacheable prefix.

`server-configs/prompt-glamourx.md` holds the corrected version.

---

## Ending calls, sending them on (17–25 Aug 2026)

### The call can now end itself, and knows when nobody is there

`[EOC]` in the model's reply closes the call once the sentence carrying it has
finished playing; `[TRANSFER]` hands over the same way. Both are stripped before
TTS by a filter that holds back a tail, because an LLM streams without regard
for token boundaries and `[EOC]` routinely arrives as `[EO` then `C]` - a naive
filter passes both and the caller hears "bracket E O C".

Neither acts inside `tts_node`. Audio is still being produced there, and ending
or transferring would cut off the sentence the marker was attached to.

Silence handling is N lines, one per attempt, then the call closes as
`no_response`. There is no attempt-count field: the array's length IS the count,
because two fields that must agree eventually do not.

### Three faults in one handoff, each hiding the next

1. Asked for `[TRANSFER]`, the model wrote `[Transfer]`. Matching was
   case-sensitive, so nothing fired and the text was read aloud.
2. The same reply carried both markers. Both were honoured, so the caller heard
   a farewell and then a hold message. Transfer now wins and clears the other.
3. **The confirmation gate was satisfied by the agent talking to itself.** Given
   both a tool and a marker, the model used both: the tool asked, and the marker
   milliseconds later found the flag set and transferred. The one feature whose
   entire purpose is to let someone say "no, wait" did not let them.

The gate is no longer a boolean. It records a counter that only the caller's own
speech advances, and a transfer requires that counter to have moved. That holds
however many routes exist. The tool is also removed when a marker is configured
- one job, one route.

### Sending each call to the client's API

Split along what the agent *can* do. It extracts and queues; the console
delivers, because the job process exits when the call ends and cannot retry
anything.

Extraction is one LLM pass over the transcript **after** the call. A mid-call
tool would put a round trip inside a turn budget that took a day to bring down,
and doing it afterwards means the schema can change and old calls can be
re-processed. Every field is optional and nullable - a required field makes the
model invent a value rather than admit the conversation never covered it.

The field list does two jobs: it is the schema handed to the model and it is the
payload shape. The payload separates `call` (measured by us), `dialer` (passed
through untouched) and `extracted` (read by a model, and the only part that can
be wrong). Timestamps are IST with the offset kept.

**Values the caller was never told.** A dealer lookup returns a code and a name;
the agent reads names aloud, so the code is nowhere in the transcript.
Migration 021 stores the tool's answer - opt-in per tool, successful bodies
only, nulled after 30 days - and extraction is given it under a labelled
heading. The raw answer travels in the payload too, so if the model and the
client's records ever disagree about a code, their own bytes are right there.

### Word documents in the knowledge base

One extractor, not a second pipeline: everything downstream already worked on
`[(page, markdown)]`. Word suits it because the chunker splits on markdown
headings and a `.docx` already has real ones.

The document body is walked in order. `python-docx` exposes `.paragraphs` and
`.tables` as two independent lists, and iterating them separately puts every
table after every paragraph - silently detaching each from the text explaining
it.

**Excel was discussed and deliberately not done.** A price or dealer list is an
exact lookup, and a vector search answers those approximately. That data belongs
in a tool.

### A recording that played from a plain server and not from the console

Three days of "the browser could not decode this". The file was valid - ffmpeg
decoded it end to end - and Chrome played the same bytes from a `python3 -m
http.server` on the same box.

`Cache-Control: private, max-age=3600` with no validator. One bad entry stuck
for the full hour, Chrome answered from it without touching the network, and the
empty stream decoded as corrupt. `Ctrl+Shift+R` did not help: a hard reload
governs the page and its assets, while a `fetch()` started by script afterwards
still uses the default cache mode. Nobody could clear it.

What actually found it was making the player compare what arrived against the
size the API had already reported: "Only 0 of 543,569 bytes arrived". The
browser's own word for a short stream is "cannot decode", which points at the
one thing that was never wrong.

### Worth keeping

- `pyflakes` is now part of editing Python here. `ast.parse` is happy with an
  undefined name, and `NameError: name 'base' is not defined` took every upload
  down after a change that had been "syntax checked".
- JSONB arrives from asyncpg as **text**. That has now caused three separate
  silent failures - a configured 404 message that never matched, and two
  others. Every JSONB column has to be named in the parse list, and forgetting
  one never raises.

---

## The wait that was not ours, and the greeting that was (25 Aug 2026)

Reported as "Turn detection kaafi fluctuate kar raha hai". It was not the turn
detection, and finding that out took five read-only probes of the installed
livekit package rather than any change to our own code.

### Reading the library instead of guessing at it

Twice before, an API was assumed and the traceback arrived on a live call. So
this ran the other way round: `tools/probe-*.py` print what is actually
installed - `say()`'s signature, the events a session emits and their fields,
the soniox options, and the plugin's own source around endpointing. They are
kept because the next question of this kind starts the same way.

What they established, in order:

- `eou_ms - stt_ms` was a near-constant 370-380 ms across every turn. Our turn
  detection never fluctuated at all. The whole spread lived in `stt_ms`.
- `max_endpoint_delay_ms` **is** put on the wire - the plugin sends it
  unconditionally. So "we never sent it" was not the answer.
- The plugin emits a final transcript **only** on an end token from Soniox. The
  words arrive before that and sit in an accumulator. Soniox had the sentence;
  it had not yet agreed the caller had stopped talking.
- Zero reconnects all day, so the connection was not it either.
- `transcription_delay = max(last_final_transcript_time - last_speaking_time, 0)`
  and the endpointing sleep is `delay + (last_speaking_time - now)`. That is
  negative once the wait exceeds the delay, so **livekit was committing the turn
  the instant it could**. No setting on our side could have made it earlier;
  `max_delay` can only ever make it later. That closed the question by
  arithmetic rather than by opinion.

### One knob, never set

`endpoint_sensitivity` - "how readily the model emits speech endpoints" - was
`NULL`. `endpoint_latency_adjustment_level`, set to 3 on 18 Aug, works *after*
cessation is detected and does nothing to detect it, which is exactly why it
improved the median by 380 ms and left the tail untouched.

Set to `0.5`:

| | before | after |
|---|---|---|
| worst `stt_ms` | **6684 ms** | **1219 ms** |
| median | ~1550 ms | **~325 ms** |

The five-to-seven second turns are gone. `eou_ms` now stops dead on 1500-1501,
which is `MAX_ENDPOINTING` finally acting as a ceiling - it never could before,
because Soniox had not answered by the time it expired. The feared cost, one
reply split across two turns, did not materialise: a 16-turn booking ran clean.

The slowest leg is now our own EOU model, around 950 ms. That is a smaller and
different problem, and `preemptive_generation` addresses it.

### One fix had quietly undone another

Two of five test calls were abandoned before the caller heard anything. Their
greetings took 3946 ms and 6835 ms; a good run is ~1470 ms, against 619-701 ms
for every later turn in the same call.

The journal had been saying why on every call: `warm_done=False`. The provider
warm-up takes ~820 ms and the session starts at ~390 ms, so it finishes after
the greeting has already been asked for. Its docstring claimed it "finishes
inside time that was already being spent" - true when startup took four seconds.
Removing `import kb` cut startup to 390 ms and made that sentence false without
touching it. The warm-up still helps the LLM leg, which nothing needs for
several more seconds. It never helped the greeting.

The greeting is the same sentence, in the same voice, on every call, at the one
moment that cannot absorb a delay. `agent/greeting_cache.py` renders it once to
a wav and hands the frames to `say(audio=...)`; the text still reaches the
transcript and the model unchanged.

| call | greeting `tts_ttfb_ms` |
|---|---|
| 337 - cache empty | 1629 |
| 338 - cache warm | **none: no request was made** |

The file name is a hash of text, provider, model and voice, so editing the
greeting or the voice in the console renders new audio on its own. There is
nothing to clear. A call that finds the cache empty speaks the ordinary way and
renders afterwards, so no caller ever waits for it, and every failure path falls
back to synthesising.

### The cost had not gone. It had moved.

The table above was written after two calls and it was wrong about what it
proved. The greeting had been the first synthesis in the process, so it paid for
the connection and every later turn was cheap. Removing it did not remove that
bill - it handed it to the caller's first question.

Call 339: the caller asked, heard nothing, said "हेलो" - and that word
interrupted the answer at the moment it finally arrived, 6286 ms of tts_ttfb
later. 0.35 s of it was ever spoken. They hung up sixteen seconds after that. As
the greeting this cost was merely slow; as the reply it ended the call.

The greeting is 7.2 seconds of audio the caller is already listening to, and
nothing in a call is a better place to spend a handshake. Six characters are now
synthesised there, so the connection is open before anybody speaks.

| | greeting | first reply |
|---|---|---|
| before any of this | 1458-6835 ms | ~650 ms |
| cache alone | none | **6286 ms** |
| cache + warm | none | **639 ms** |

Worth keeping: the first version looked like a clean win on the numbers it
happened to collect. It took a caller hanging up to show what those numbers had
left out.

### A ceiling on the STT, written and withdrawn

Call 342 showed the sensitivity fix has a floor it cannot reach past. The caller
trailed off with "लेकिन।" and waited **15926 ms**. Soniox will not send its end
token until it agrees the caller has stopped, and `max_endpoint_delay_ms` bounds
the wait *after* that agreement - so it never applies to the case that hurts.

The words were in our hands throughout: the plugin streams interims and
withholds only the FINAL. So `stt_node` was given its own ceiling - promote the
last interim ourselves once the words stop arriving, and drop the provider's
final when it turns up.

It broke two calls. 343 replied once and went quiet; 344 never replied at all,
and the caller said "आवाज़ नहीं आ रही". Notably the ceiling never fired - there
is not one "STT ceiling" line in the journal - so whatever it did wrong, it did
without ever promoting anything. `STT_FINAL_CEILING_MS=0` restored service
immediately and it is staying off.

Reverted rather than iterated on, deliberately. The 15926 ms case has appeared
on one call; the fix for it broke two. Left in the tree behind the switch,
because the reasoning still holds and the next attempt should start from the
journal of call 344 rather than from another theory.

Three of my hypotheses about this problem were wrong today - that level 3 caused
the tail, that the greeting cache had removed a cost, that leaking interims were
interrupting the agent. Each time the log said otherwise and reading it settled
in minutes what reasoning about it had not.

### Two bugs found by reading, not by failing

**The silence watchdog talked to calls that had ended.** It exited on a limit or
a transfer and on nothing else - neither of which happens when somebody simply
hangs up. Every ended call left two `RuntimeError: AgentSession isn't running`
tracebacks. Nobody heard it, which is the only reason it went unnoticed, and a
log with routine tracebacks in it is a log people stop reading.

**A caller's turn was counted several times.** `user_input_transcribed` fires for
interim transcripts too - `is_final` is a field on the event - and the counter
took every one. The transfer confirmation gate waits for the caller to answer by
watching that number, so it could be satisfied by the first fragment of a
half-heard word. It looked like it was working, and on a busy line it usually
would.

### Left deliberately

The gate has a second weakness, seen on call 336: the caller asked
**"क्यों?"**, the model explained and transferred in the same response, and the
gate allowed it because the caller had *spoken*, not because they had *agreed*.
Fixing it means the gate reading the reply for a yes or a no, which changes how
transfer behaves and needs the word lists agreed first. Deferred by the user.

---

## Where an answer came from, and what day it is (26 Aug 2026)

The knowledge base went live - 26 documents, 524 chunks, **108,273 tokens**.
Eighteen times what fits in a prompt, so `index` mode, and the index itself is
only **726 tokens**. Every product question now goes through search: five
searches in five turns on the first real call, scores 0.51-0.78, and the answers
carried real figures rather than plausible ones.

### Four working pieces and a filter in the middle

The console could always have shown which documents answered a turn. There is an
endpoint that resolves chunk ids to a filename, heading and page; a component
that renders them under the turn with their scores; a column in the schema; and
`store.log_turn` reading both fields. None of it had ever run, because the turn
was written with

    **{k: v for k, v in t.items() if k.endswith("_ms")}

and the two fields do not end in `_ms`. They were dropped without a word, every
turn, since the table was created. The whole feature looked unbuilt when it was
90% built and disconnected by one comprehension.

It matters now more than it did. With 108k tokens behind a search, the first
question about a wrong answer is whether the documents are wrong or the model
read them wrong, and there was no way to tell from the console.

The very first call after it worked showed why:

    TOOL search_knowledge_base('engine capacity Lender Plus Flex')
      -> 3 hit(s)  ['0.57', '0.53', '0.52']

The caller asked about **Splendor Plus Flex**. Soniox heard "लेंडा प्लस फ्लेक्स",
the model searched for "Lender Plus Flex", and retrieval confidently returned
**Pleasure Plus XTEC** at 0.57. The agent then quoted the wrong bike's engine.
Retrieval was not at fault and neither was the prompt - STT mangled the model
name and nothing anywhere said so. Soniox's `context` option can be given the
model names; noted, not touched.

### The agent did not know the date

Callers asked outright on call 345 and were told it could not say. The larger
cost was quieter: "कल आ जाऊँगा, सुबह 10 बजे" cannot become a real date without
knowing today's, so the postback carried the words and not the appointment.

One line, appended at the very end of the prompt, once per call. Two decisions
worth keeping:

**Outside `build_instructions`, not in it.** That module exists because the
agent and the cache warmer must emit a byte-identical prefix - a clock inside
would differ by a second each time and silently create a second cache, the exact
failure its own docstring warns about. Appended afterwards, the warmer's output
stays an exact PREFIX of the agent's, so the index and every rule still cache.

**Once per call, not per turn.** A clock that ticked every turn would make every
turn a cache miss. The price is that on a three-minute call the time can be two
minutes old, which matters to nobody asking about tomorrow morning.

The timezone is validated by the API rather than left to the agent, which falls
back to +05:30 and carries on. A typo would raise nothing at all - it would just
tell every caller the wrong time in a zone nobody chose.

---

### Noise cancellation: measured, and declined

Raised as a campaign-wise toggle, and worth taking seriously for a reason beyond
audio quality - the standing explanation for call 342's 15926 ms wait was that
constant line noise stopped Soniox accepting the caller had gone quiet. If true,
removing the noise treats it at the source.

It is not true. From that call's own recording:

| window | RMS | peak |
|---|---|---|
| 16-30 s, the wait | **-79.88 dB** | -65.16 dB |
| 5-13 s, caller speaking | -24.73 dB | -3.21 dB |

`-79.88 dB` is digital silence, 55 dB below speech, and ffmpeg's own
`silencedetect` recorded one unbroken silence from 14.26 s to 33.33 s. Soniox
was given **nineteen seconds of nothing** and still did not send an end token,
under a `max_endpoint_delay_ms` of 1500.

So there is nothing to cancel, and the theory the feature rested on is dead.
Declined: the plugin is not installed and is very likely LiveKit Cloud only,
this deployment is self-hosted, and 8 kHz telephony would blunt it even if both
of those went the other way.

Worth revisiting only on evidence, and the evidence is transcripts: today's are
clean. Real customers calling from a street or a room with a television are a
different case, and one that cannot be measured from a quiet desk.

The finding matters more than the decision. It rules out the line and leaves the
provider - which puts the withdrawn stt_node ceiling back as the right idea
badly executed, rather than the wrong idea.

---

### Something to say while it searches, and a payload the client can parse

Two small things, both from watching the knowledge base go live.

**A filler on `search_knowledge_base`.** Campaign tools have had one since
migration 018; the built-in KB tool never did, because it was the rarer path
then. It is now the common one - a search costs 810-1860 ms and runs on very
nearly every question - so that is a second of silence each time, and silence is
what makes a caller say "hello?". Same shape as the tool filler: started rather
than awaited, cancelled the moment the answer lands, so a fast search stays
quiet. Kept out of the chat context, unlike the tool version - it is a noise
made while waiting, not something the agent said.

**The postback envelope became optional, and the field set became stable.**

`call / dialer / extracted / tools` exists so a reader can tell a measured fact
from a model's reading of a conversation. That is right when the reader is ours.
The client's endpoint is a different reader: it asked for six fields and wants
an object with six keys. With `postback_full_payload` off it gets exactly that.
Defaults to on, so nothing already delivering changes shape on the morning of a
migration, and the console states what is lost - a flat payload carries no id,
so the receiving end cannot tie the record back to a call. Chosen anyway, with
that understood.

Every configured field is now present on every call, `null` where the
conversation did not establish one. They used to be dropped, on the reasoning
that "absent" is the honest description of a field never reached. It is - but it
made the payload a different shape each time, so a client has to guard every key
and still cannot tell "not discussed" from "the field list changed". A stable
object with nulls says the same thing and can just be read. That holds when
extraction fails outright too: the full shape with nulls, never an empty object.

---

## What a call costs, and what it could not answer (26-29 Aug 2026)

### Costing

Everything needed was already recorded per call. What was missing was the price
of each unit, and which model incurred it - `llm_model` lives on agent_config,
so moving a campaign from gpt-4.1-mini to gpt-4.1 would have re-priced every
call ever made at about five times what it cost. Three columns now record what
actually ran.

Three ways the arithmetic goes wrong, none of which announce themselves:

- **`llm_prompt_tokens` already includes the cached ones.** On one call that is
  3,06,984 against a true uncached 14,376 - twenty times over.
- **Providers quote per million tokens and per audio hour**; we count tokens and
  seconds.
- **A missing rate is not a zero.** 0.00 reads as free.

Prices are data. Nothing is seeded by a migration, because a migration runs on
every deployment forever and a price in one reinstates itself long after it
stopped being true. `server-configs/seed-rates.sql` was run once, on a date, and
the date is on every row.

**Soniox's rates were solved from the account's own usage page** rather than
copied from a price list - better evidence, because it is what was actually
charged. Three models gave three equations agreeing to the last cent: $4.00/1M
text tokens, $2.00/1M STT audio tokens, $21.50/1M TTS audio tokens.

**And Sarvam bills rupees.** Holding its Rs 30/hour as a converted dollar figure
would have made its rupee cost drift every time the exchange rate was edited,
while Sarvam went on charging Rs 30. The currency now belongs to the rate. A
call using both - the Sarvam campaigns run OpenAI for the LLM - reports as
unpriced without an exchange rate rather than adding rupees to dollars.

Worth knowing from the first real figures: **TTS is the expensive leg, not the
LLM.** Soniox TTS is $0.74 per audio hour against STT's $0.076 - ten times. And
the prompt cache is doing most of the work on the LLM side.

### Refusing to guess was the wrong instinct once

`pick_rate` declined to choose between two candidate rates when a call did not
record its model, on the reasoning that a guess does not belong in a bill.

The two rates were **1.5% apart**. So the price of that rigour was showing
Rs 0.00 for text-to-speech on every historical call - and 0.00 does not read as
"we are unsure", it reads as free. A wrong number wearing a straight face.

The rule is not "never assume". It is **measure what the assumption costs and
say so**, which is what the backfill script and the caveats on the page now do.

### Knowledge gaps

Three signals, all already flowing through code we control and none costing
anything: a search that found nothing, one that barely found something
(RidgeMax MR scored 0.34 against a 0.25 floor and answered from it), and a
lookup that failed.

Deliberately not inferred from what the agent SAID. The grounding rules make it
admit when it does not know, but the wording varies by language and turn, and a
feature that decides what to teach the bot cannot rest on a string match against
Hindi.

Grouped by question before it reaches the console, because nobody works from
occurrences - they work from "asked fourteen times", and that number is the sort
order.

### Four working pieces and a filter in the middle

The console could always have shown which documents answered a turn: an endpoint
that resolves chunk ids to a filename and page, a component that renders them, a
column, and a writer that reads it. None of it had ever run, because the turn
was written with

    **{k: v for k, v in t.items() if k.endswith("_ms")}

and the two fields do not end in `_ms`. The feature looked unbuilt when it was
90% built and disconnected by one comprehension.

The first call after it worked showed why it was worth having: the caller asked
about **Splendor Plus Flex**, Soniox heard "लेंडा प्लस फ्लेक्स", the model
searched "Lender Plus Flex", and retrieval confidently returned **Pleasure Plus
XTEC** at 0.57. Neither retrieval nor the prompt was at fault.

### Two bugs of the same shape, a week apart

**The silence prompt talked over the agent.** Three times on call 368, each on a
turn that spoke and called a tool together: the preamble was still playing as
the state moved to "thinking", the watchdog only skipped while "speaking", and
the caller was asked whether they could hear us over the answer they had asked
for. "Is it speaking" was never the question; "is it waiting on the caller" was.

**The extractor did not know the date.** Call 373 recorded a test ride as
2024-04-28 from a conversation held in August 2026. The agent got a date line
last week; the extractor is a separate call with its own system prompt and never
got one. The same gap, one layer along - and this is the layer where it matters
more, because the agent's answer is heard once while this becomes a row somebody
acts on.

### Seven widths across twelve pages

896px to 1500px. Each defensible on its own page, none defensible next to each
other: moving between Campaigns and Campaign config the content jumped 400px.
One `PAGE` constant now, at 1300px. Calls and Dashboard lose 200px of table; a
console that looks like one thing is worth more than that.

---

## ⏭️ Next

- **Aggregate cost** - per campaign and per day, on the Dashboard. The per-call
  figure is in; the roll-up is not
- **Sarvam's own usage page**, to check the seeded rupee rates the way Soniox's
  were checked - solved from a bill rather than read from a page
- **The STT ceiling, from the journal of call 344** - it broke two calls without
  ever firing, and why is not yet known. `STT_FINAL_CEILING_MS=0` until it is
- **The warm-up still bills the greeting** - the request_id filter does not
  catch it; call 345 reports a 1782 ms greeting that was played from disk
- **STT mangles model names** - "Splendor Plus Flex" reached search as "Lender
  Plus Flex" and matched a different bike at 0.57. Soniox takes a `context` list
- **Click-to-insert placeholder chips** on greeting and messages, filled from
  the `dialer.*` fields real calls actually carry
- **Transfer gate reads consent, not just speech** - "क्यों?" currently opens it
  (word lists to be agreed - see 25 Aug)
- **`preemptive_generation`** - hides llm_ttft entirely, at no cost to quality.
  The first turn of a call pays ~2950 ms on a cold prompt cache, later turns
  ~1000-1400 ms, and on a KB turn the console under-reports it: a tool call
  makes two LLM round trips and only the first TTFT is kept
- **`MAX_ENDPOINTING` is now the single largest number** - 1501, 1501, 1500 on
  three consecutive turns of the 26 Aug date-and-time call, against stt of 108,
  244 and 549 ms. The transcript was ready in a tenth of a second and the turn
  detector then spent the full ceiling being unsure. Lowering it trades against
  cutting off callers who pause, which is what it was put there to prevent
- A model change was considered and left: gpt-4.1-nano would save perhaps 400 ms
  of ~1200, and gpt-4.1-mini was chosen for VARIANCE, not average - it cut the
  spread from 800 ms to 85 ms. 25 Aug showed why that matters more: one 6286 ms
  turn ended a call whose average was 650 ms. A weaker model also loosens the
  grounding rules, on a knowledge base full of prices and specifications
- Move off `min/max_endpointing_delay`, deprecated in 1.6.7 for
  `turn_handling=TurnHandlingOptions(...)`
- **Why the LLM FallbackAdapter failed at 20 concurrent** — both legs down at
  once, 3 calls lost. TTS held; LLM did not
- Re-measure latency at 20 once Sarvam credits are restored
- **Change the SIP password** — `1002` was printed to a shared terminal by
  `pjsip show auth`
- Recording disclosure line in each campaign greeting — recording is global and
  callers are not told
- Per-campaign recording (needs Asterisk to read the DB, e.g. `func_odbc`)
- Estimated-spend panel from stored usage (credits cannot be read from the
  provider APIs)
- Drop `config_name` — needs the KB migrated off it
- Ask the dialler team: `asterisk -V`, `chan_sip` vs `chan_pjsip`, new SIP trunk,
  trunk codec, registration interval

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
