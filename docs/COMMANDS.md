
# Everyday commands

The short list. Everything here is something that gets typed on a normal day —
for anything deeper, see [RUNBOOK.md](RUNBOOK.md).

Two paths, and they are not the same thing:

| | |
|---|---|
| `/srv/aivoice` | the git checkout — **all code runs from here** |
| `/opt/aivoice` | `.env`, `gcp/sa.json`, the agent's venv, the media stack's compose |

Two **boxes**, and they are not the same thing either: `10.130.9.243` is
development, `10.130.9.244` is production, and changes go to .243 first. Both
prompts read `[root@localhost ~]#` — run `hostname -I` before anything that
writes. Setting up another clone: [REPLICA.md](REPLICA.md).

---

## Deploy

One script, and it behaves differently on each box:

```bash
cd /srv/aivoice
server-configs/deploy.sh                # development — follows main
server-configs/deploy.sh v0.7.0         # production  — checks out that release
```

Which box it is comes from `APP_ENV` in `admin/.env`, **never from an IP** — a
hardcoded address is what sent production's calls to the development box for two
days ([REPLICA.md](REPLICA.md)). A box that has not been told what it is refuses
to deploy rather than guessing.

It does, in this order: get the code, apply any pending migrations, rebuild the
console, restart the workers, then check that what is running is what it just
deployed. Migrations before services, always — the other way round runs new code
against an old schema, and the failure lands on a live call.

**If calls are in progress** the console is updated and the workers are left
alone, because restarting them drops those calls. It prints the command to
finish with; `--force` restarts anyway.

**Migrations are tracked now**, in a `schema_migrations` table, so "which ones
has this box had" is a question with an answer instead of an inference from
whichever commit it happens to be checked out at. They are still all safe to
re-run.

---

## Release a version

The version **only changes when code goes to production**. Once the changes are
proven on development, cut the release **from a clone that can push** — the
machine the code is written on, not a server. The servers pull and have no push
credentials, and a tag belongs to the commit rather than to the machine that
named it.

```bash
server-configs/release.sh 0.7.0         # note: no v, it is added
```

That tags `v0.7.0` and pushes it — it deploys nothing. The tag message carries
every commit since the last release, so `git show v0.7.0` answers "what was in
it" months later.

Then on production: `server-configs/deploy.sh v0.7.0`.

The number on the login page is `git describe` from the box it was built on, so
its shape tells you where you are:

| Shows | Means |
|---|---|
| `v0.7.0` | sitting exactly on a release — production |
| `v0.7.0-20-gabc1234` | 20 commits past it — development, unreleased |
| `unknown` | brought up by hand instead of through `deploy.sh` |

Development cannot show a clean version by accident, and production showing a
dirty one is labelled `(untagged)` on the login page.

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

## Who is holding a call slot

Live, while calls are happening. The slot count is what a campaign's concurrency
limit counts - a call in the queue is NOT in here, which is the point: it has not
reached the agent, so it is costing nothing in STT, TTS or LLM.

```bash
watch -n1 'echo "--- slots ---"; asterisk -rx "group show channels"; echo "--- channels ---"; asterisk -rx "core show channels concise" | cut -d! -f1,5'

# what the dialplan reads for an extension
asterisk -rx 'dialplan eval function ODBC_QUEUECFG(700)'
```

More channels than slots means calls are waiting. Equal numbers mean nobody is.
See [RUNBOOK.md](RUNBOOK.md) for what the answer's fields mean.

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

## A call's result never reached the customer

First, which of the three it is — they look the same in the console:

```bash
docker exec -i postgres psql -U aivoice -d aivoice -c "SELECT c.id, c.config_name, c.end_reason, ac.postback_enabled, p.status, p.attempts, p.last_status_code FROM calls c JOIN agent_config ac ON ac.name=c.config_name LEFT JOIN call_postbacks p ON p.call_id=c.id WHERE c.id=590;"
```

`postback_enabled=f` → nothing to send. A row with a `status` → delivery problem,
use **Retry** in the console. **`t` and no row** → the row was never written.

For the third, rebuild it from the transcript, which is still in the database:

```bash
cd /srv/aivoice/agent
/opt/aivoice/agent/.venv/bin/python requeue_postback.py 590 --dry-run
/opt/aivoice/agent/.venv/bin/python requeue_postback.py 590
```

Refuses a call that already has a row, so it cannot double-send. Prefix
`POSTBACK_EXTRACT_TIMEOUT=120` for a long call on a slow gateway.

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

## Backups

```bash
# run one now
systemctl start aivoice-backup && journalctl -u aivoice-backup -n 10 --no-pager

# is the nightly one armed?
systemctl list-timers aivoice-backup
```

Restoring, and the reason a dump alone is not enough, are in
[DATABASE.md](DATABASE.md).

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
