# Everyday commands

The short list. Everything here is something that gets typed on a normal day —
for anything deeper, see [RUNBOOK.md](RUNBOOK.md).

Two paths, and they are not the same thing:

| | |
|---|---|
| `/srv/aivoice` | the git checkout — **all code runs from here** |
| `/opt/aivoice` | `.env`, `gcp/sa.json`, the agent's venv, the media stack's compose |

---

## Deploy

The one command. `git pull`, rebuild the console, restart the workers — with a
guard so a call in progress is never cut off.

```bash
cd /srv/aivoice && git pull && \
  asterisk -rx "core show channels" | grep -q "^0 active calls" && \
  docker compose -f admin/docker-compose.yml up -d --build && \
  systemctl restart aivoice-agent@{1,2,3,4,5,6} && sleep 8 && \
  systemctl is-active aivoice-agent@{1,2,3,4,5,6}
```

If it stops after `git pull` with nothing else printed, a call was live. Wait
and run it again.

**When there is a new migration**, apply it between the pull and the rebuild:

```bash
docker exec -i postgres psql -U aivoice -d aivoice < migrations/0NN_name.sql
```

Every migration in this repo is safe to re-run.

---

## Restart one thing

```bash
# agent workers — after any agent/ change. DROPS CALLS IN PROGRESS.
systemctl restart aivoice-agent@{1,2,3,4,5,6}

# the console — after any admin/ change. Never touches calls.
docker compose -f /srv/aivoice/admin/docker-compose.yml up -d --build

# Asterisk — after a dialplan change
systemctl restart asterisk

# LiveKit / livekit-sip
cd /opt/aivoice && docker compose restart livekit sip
```

Changing something in the **console** needs no restart at all. Config is read at
the start of every call.

---

## Is everything up?

```bash
systemctl is-active asterisk docker
for i in 1 2 3 4 5 6; do printf "agent@%s: %s\n" $i "$(systemctl is-active aivoice-agent@$i)"; done
docker ps --format '{{.Names}}\t{{.Status}}'
asterisk -rx "core show channels" | tail -2
asterisk -rx "iax2 show peers"          # the dialler's trunk
```

Workers registered with LiveKit — anchored to when they actually started,
because `--since '-10min'` has given a false answer here twice:

```bash
journalctl -u "aivoice-agent@*" --no-pager \
  --since "$(systemctl show -p ActiveEnterTimestamp --value aivoice-agent@1)" \
  | grep -c "registered worker"        # expect 6
```

---

## Logs

```bash
# the last call, step by step
journalctl -u "aivoice-agent@*" --no-pager -o short-precise -n 600 \
  | grep -E "TIMING|received job|tts_ttfb=|TRANSFER|silence|end-of-call|TOOL "

# live, while a call is happening
journalctl -u "aivoice-agent@*" -f -o short-precise

# SIP signalling — the invite-to-answer timings live here
docker logs sip --since 30m 2>&1 | grep -E "inviteTo|Accepting|Joining room"

# the console's own API
docker logs admin-api --tail 100

# Asterisk, with dialplan detail
asterisk -rvvv
```

`TIMING` lines say where a call's first seconds went. `inviteToAcceptMs` in the
sip log is how long the caller heard ringing.

---

## The database

```bash
docker exec -it postgres psql -U aivoice -d aivoice
```

The two queries that get run most:

```bash
# recent calls, in IST, with the latency split
docker exec -i postgres psql -U aivoice -d aivoice -c "SELECT c.id, to_char(c.started_at AT TIME ZONE 'Asia/Kolkata','HH24:MI') ist, c.stt_provider_used stt, c.tts_provider_used tts, count(*) turns, round(avg(tn.eou_ms)) eou, round(avg(tn.stt_ms)) stt_ms, round(avg(tn.tts_ttfb_ms)) tts, round(avg(tn.total_ms)) total FROM calls c JOIN turns tn ON tn.call_id=c.id GROUP BY 1,2,3,4 ORDER BY c.id DESC LIMIT 10;"

# per-provider averages, all time
docker exec -i postgres psql -U aivoice -d aivoice -c "SELECT c.stt_provider_used stt, c.tts_provider_used tts, count(*) turns, round(avg(tn.eou_ms)) eou, round(avg(tn.stt_ms)) stt_ms, round(avg(tn.tts_ttfb_ms)) tts, round(avg(tn.total_ms)) total FROM turns tn JOIN calls c ON c.id=tn.call_id WHERE tn.total_ms IS NOT NULL GROUP BY 1,2 ORDER BY turns DESC;"
```

Timestamps come back **UTC** — the container runs on it. `AT TIME ZONE
'Asia/Kolkata'` is why the queries above look the way they do; without it a call
at 15:43 IST reads as 10:13 and it is easy to think you are looking at the wrong
one.

---

## Ask a provider what it offers

Without the key ever reaching the terminal:

```bash
cd /srv/aivoice
docker exec -i admin-api python - < server-configs/provider-catalog.py soniox /v1/tts-models
docker exec -i admin-api python - < server-configs/provider-catalog.py soniox models
```

---

## A test API for tools

```bash
python3 /srv/aivoice/server-configs/tool-stub-api.py
```

`/service?reg=X` · `/book` · `/slow?ms=4000` · `/fail?code=503` · `/huge?kb=64`

`/slow` is the one worth using — it is how you hear what a caller hears when a
tool is late.

---

## Rebooting the box

See [RUNBOOK.md §9](RUNBOOK.md) — check what is `enabled` **before** you reboot,
not after. The first reboot of this server lost the LiveKit SIP trunk and every
call rang and died with nothing in any log to explain it.

---

## Never

- **`cat /opt/aivoice/.env`** — mask it instead:
  `sed -E 's/=(.{0,4}).*/=\1***/' /opt/aivoice/.env`.
  Do not use `${VAR:-MISSING}` either; it prints the value when it is set.
- **`asterisk -rx "pjsip show auth <id>"`** — prints the SIP password in
  plaintext. To check one, `grep -c` for the exact value and read the 0 or 1.
- **`cat /etc/asterisk/pjsip.conf`** — same reason.
