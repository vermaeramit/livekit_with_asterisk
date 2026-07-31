# Server Inventory & Runbook

Quick-reference facts for `10.130.9.243`. For the setup history see [PROGRESS.md](PROGRESS.md).

---

## Host

| | |
|---|---|
| **IP (LAN)** | `10.130.9.243/16` on `ens33` |
| **Gateway** | `10.130.23.1` |
| **DNS** | `10.130.1.1` |
| **Public egress** | NAT via corporate ISP — Gurugram, Haryana (address redacted) |
| **OS** | Rocky Linux 8.10, kernel 4.18.0-553 |
| **Platform** | VMware VM |
| **CPU** | 8 vCPU — Intel Xeon Gold 6338 @ 2.00 GHz |
| **RAM** | 11 GiB |
| **Disk** | `/` 63 G (LVM `rl-root`) · `/home` 31 G · swap 5.9 G |
| **Timezone** | Asia/Kolkata (IST +0530), chronyd synced |
| **SELinux** | `enforcing` (targeted) — not disabled |
| **Firewall** | firewalld active |

## Workstation (softphone)

| | |
|---|---|
| **LAN IP** | `10.130.23.37` ← the one that matters |
| Other NICs | `192.168.137.1` (hotspot), `172.18.16.1` (WSL/Hyper-V) — ignore |
| Ping to server | 3–7 ms, 0 % loss |

---

## Ports

| Port | Proto | Service | Exposure |
|---|---|---|---|
| 22 | TCP | SSH | LAN |
| 5060 | UDP/TCP | Asterisk SIP | `voip` zone — `10.130.23.37/32` only |
| 5080 | UDP/TCP | livekit-sip SIP | Internal — Asterisk only |
| 6379 | TCP | Redis | `127.0.0.1` only |
| 7880 | TCP | LiveKit API / WebSocket | Internal |
| 7881 | TCP | LiveKit ICE/TCP fallback | Internal |
| 7882 | UDP | LiveKit RTC mux | Internal |
| 10000–19999 | UDP | Asterisk RTP | `voip` zone — `10.130.23.37/32` only |
| 20000–29999 | UDP | livekit-sip RTP | Internal |
| 30000–65000 | — | Kernel ephemeral (`ip_local_port_range`) | — |

> ⚠️ **No overlap is allowed between the RTP ranges and the kernel ephemeral range.**
> Overlap causes intermittent "address already in use" failures that appear only under
> load. This is why the ephemeral range starts at 30000.

### firewalld zones

| Zone | Sources | Contents |
|---|---|---|
| `public` | interface `ens33` | ssh, cockpit, dhcpv6-client |
| `voip` | `10.130.23.37/32` | ssh, 5060/udp, 5060/tcp, 10000-20000/udp |

> ⚠️ A source-based zone **overrides** the interface zone for traffic from that source.
> Any host added to `voip` must also have `ssh` in that zone or it loses SSH access.

---

## Credentials

| What | Value | Note |
|---|---|---|
| SIP extension | `1001` | Test softphone |
| SIP password | *(set on server, not committed)* | 🔴 Lab secret — rotate before production |
| LiveKit API key / secret | `/opt/aivoice/.env` | `chmod 600`. Generated with `openssl rand -hex`. Never committed. |
| Sarvam / Gemini / OpenAI keys | *(Step 8)* | Same `.env` pattern — never commit |

Load LiveKit credentials into a shell:

```bash
export LIVEKIT_URL=http://localhost:7880
set -a; source /opt/aivoice/.env; set +a
```

---

## Filesystem layout on the server

```
/opt/aivoice/
├── .env                        # LiveKit API key + secret (chmod 600)
├── docker-compose.yml
├── asterisk/
│   ├── Dockerfile              # ubuntu:24.04 + Asterisk 20 (LTS)
│   ├── entrypoint.sh           # overlays conf/ onto /etc/asterisk at startup
│   └── conf/
│       ├── pjsip.conf          # transport, endpoint 1001, livekit trunk
│       ├── extensions.conf     # 600 echo, 601 playback, 700 -> LiveKit
│       ├── rtp.conf            # RTP range 10000-19999
│       └── modules.conf        # autoload + noloads to silence log noise
├── livekit/
│   └── livekit.yaml            # use_external_ip:false, node_ip pinned to LAN
├── redis/
│   └── redis.conf              # 127.0.0.1 only, persistence off
└── sip/
    ├── config.yaml             # sip_port 5080, rtp 20000-29999 (chmod 600)
    └── objects/
        ├── inbound-trunk.json
        └── dispatch-rule.json
```

Docker named volumes: `aivoice_asterisk-spool`, `aivoice_asterisk-log`.

### Running containers

| Container | Image | CPU limit |
|---|---|---|
| `asterisk` | `aivoice-asterisk` (ubuntu:24.04 + Asterisk 20) | 1.5 |
| `redis` | `redis:7.4-alpine` | 0.5 |
| `livekit` | `livekit/livekit-server:v1.13.4` | 2.0 |
| `sip` | `livekit/sip:v1.8.0` | 1.0 |
| *(agent workers — Step 8)* | | ~3.0 remaining |

All use `network_mode: host`. CPU limits exist so agent workers cannot starve the SFU —
a starved SFU degrades audio on **every** call, not just one.

---

## Test extensions

| Extension | Behaviour |
|---|---|
| `600` | Answer → `demo-echotest` prompt → `Echo()` — your voice comes back |
| `601` | Answer → `hello-world` playback → hangup |
| `700` | **Routes the call into a LiveKit room** via livekit-sip |
| `1001` | Dials the registered softphone back |

## LiveKit SIP objects

| Object | ID / value |
|---|---|
| Inbound trunk | `ST_eSZhZNk5XgHB` — `asterisk-lab`, allowed `10.130.9.243/32` |
| Dispatch rule | `lab-dispatch` — individual, room prefix `call` |

```bash
lk sip inbound list
lk sip dispatch list
lk room list          # only shows rooms while a call is active
```

> Rooms are **ephemeral**. `--individual` creates `call_<caller>_<id>` per call and deletes
> it on hangup, so an empty `lk room list` between calls is normal.

---

## Runbook

### Restart after a config change

```bash
cd /opt/aivoice
docker compose restart asterisk        # conf/*.conf changed
docker compose up -d --build           # Dockerfile changed
```

### Diagnose a call

```bash
docker exec asterisk asterisk -rx "pjsip set logger on"   # full SIP trace
docker exec asterisk asterisk -rx "rtp set debug on"      # RTP trace
docker compose logs -f --tail=100 asterisk
docker exec asterisk asterisk -rx "core show channels"    # during the call
```

Turn tracing back off — it is very verbose:

```bash
docker exec asterisk asterisk -rx "pjsip set logger off"
docker exec asterisk asterisk -rx "rtp set debug off"
```

### Common symptoms

| Symptom | Likely cause | Check |
|---|---|---|
| Softphone will not register | Firewall, or wrong domain/password | `firewall-cmd --zone=voip --list-all`, then `pjsip set logger on` |
| Registers, but **no audio** | RTP blocked, or softphone advertising the wrong IP | RTP range open? `rtp_symmetric` / `rewrite_contact` set? |
| **One-way** audio | NAT / multi-homed client | Pin the softphone's Topology IP to `10.130.23.37` |
| Choppy or robotic audio | CPU starvation or UDP buffer overflow | `docker stats`, `htop`, `sysctl net.core.rmem_max` |
| Container will not start | Config syntax error | `docker compose logs --tail=50 asterisk` |

### Health check

```bash
docker compose ps
docker stats --no-stream
ss -ulnp | grep 5060
df -h /
free -h
docker exec asterisk asterisk -rx "pjsip show endpoints"
```

---

## Applied host tuning

| File | Purpose |
|---|---|
| `/etc/sysctl.d/99-voip-tuning.conf` | UDP buffers, ephemeral port range, `swappiness=1`, TCP fast-open |
| `/etc/security/limits.d/99-voip.conf` | `nofile` 65535, `nproc` 32768 |
| `/etc/systemd/system.conf` | `DefaultLimitNOFILE=65535` |
| `/etc/docker/daemon.json` | Log rotation, live-restore, systemd cgroup driver, ulimits |

---

## Known constraints

| # | Constraint | Impact |
|---|---|---|
| 1 | 8 vCPU → ~18–20 concurrent AI calls | Resize the VM or add worker boxes to go beyond |
| 2 | Single box — no HA | Any failure takes down all calls |
| 3 | Docker 26.1.3 is the final el8 build | No further Docker upgrades on Rocky 8 |
| 4 | cgroup v1 (v2 available via reboot) | v2 would add PSI metrics, useful for diagnosing audio glitches |
| 5 | Rocky 8 default Python is 3.6 | Mitigated — 3.11/3.12 in AppStream, and agents run containerised |
