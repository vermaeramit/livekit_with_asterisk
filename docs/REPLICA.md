# Cloning this server

A VM clone copies everything — including the handful of values that must **not**
be the same on the new box. This is the list of those values, what each one
breaks, and how to prove it is fixed.

It exists because `10.130.9.244` was cloned from `10.130.9.243` on 9 Sep 2026,
went to production, and its AI leg did not take a single call for two days.
Nothing alerted. Asterisk fell back to a human after 25 seconds, every caller
was served, and the only visible sign was that the agent workers sat idle.

Three values were wrong, and **each one failed differently and silently**:

| # | Value | Where | Symptom when it still points at the old box |
|---|---|---|---|
| 1 | `livekit` AOR contact | `pjsip.conf` | Call rings 25 s, `DIALSTATUS=NOANSWER`, falls back to a human. The local `sip` container logs **nothing at all** — the INVITE went to the other server. |
| 2 | `allowed_addresses` | SIP trunk, in **Redis** | Call rings and dies. No trunk matches, so the dispatch rule is never reached and no room is created. The agent is never asked. |
| 3 | `node_ip` | `livekit.yaml` | Call is answered, then **no audio**, then media-timeout. Everything looks connected. |
| 4 | `TRANSFER_SIP_HOST` | `.env` | Only shows up on a handoff: the caller is transferred to the **other box**. |
| 5 | `transfer_to` | `agent_config` table | Same as 4, for campaigns that name a SIP URI directly instead of using a dialler. |
| 6 | `APP_ENV` | `admin/.env` | The login page names the wrong box. Clone a development server and production greets you in amber as "Development"; clone the other way and a development box says "Production" in calm grey, which is the direction that matters. `deploy.sh` also reads it, and refuses to run if it is unset. |

1, 2 and 3 stack: fixing one at a time gives you three different failures in a
row, each of which looks like a new problem. Fix all of them before testing, or
budget three test calls.

---

## The steps

Everything below runs **on the new box**. Substitute its own address for
`<NEW_IP>` throughout.

> ⚠️ Both boxes present a shell prompt of `[root@localhost ~]#`. There is nothing
> on screen to tell you which one you are on. Run `hostname -I` first, and again
> any time you have been away from the terminal.

### 1. Asterisk → livekit-sip

The `livekit` endpoint still points at the old server. Pull the live file first —
the repo copy is behind the server, see [RUNBOOK](RUNBOOK.md) — edit the contact
to the new box, and reload.

```bash
asterisk -rx "pjsip show aor livekit"     # before
# edit /etc/asterisk/pjsip.conf: contact = sip:<NEW_IP>:5080
asterisk -rx "pjsip reload"
asterisk -rx "pjsip show aor livekit"     # after: contact must read <NEW_IP>
```

`pjsip show aor` reads the **running** config, so it proves the reload as well as
the edit. Do not `cat` `pjsip.conf` on a shared screen — it carries the SIP
password.

### 2. The SIP trunk and dispatch rule

These live in **Redis, not in files**. Copying `sip/objects/*.json` deploys
nothing; the JSON is only the source you feed to `lk`.

Recreating a trunk mints a **new trunk ID**, and the dispatch rule references the
old one — so both must be recreated, in this order, in one sitting. Stopping
halfway leaves calls arriving and matching nothing, which is the same silent
symptom you are trying to fix.

```bash
( cd /opt/aivoice
  set -a; . .env; set +a
  export LIVEKIT_URL=http://localhost:7880      # lk wants http://

  lk sip inbound list                           # note the old ST_... and its address
  lk sip dispatch list                          # note the old SDR_...

  cp sip/objects/inbound-trunk.json sip/objects/inbound-trunk.json.bak-$(date +%F-%H%M)
  sed -i 's#<OLD_IP>/32#<NEW_IP>/32#' sip/objects/inbound-trunk.json
  grep allowed_addresses sip/objects/inbound-trunk.json

  lk sip inbound  delete <OLD_TRUNK_ID>
  lk sip inbound  create sip/objects/inbound-trunk.json        # prints the NEW ST_...
  lk sip dispatch delete <OLD_RULE_ID>
  lk sip dispatch create --name lab-dispatch --trunks <NEW_TRUNK_ID> --individual call

  lk sip inbound list && lk sip dispatch list )
```

The rule's `SipTrunks` column must show the **new** trunk ID. If it still shows
the old one, the rule is pointing at a trunk that no longer exists.

`--individual call` reproduces the existing room naming, `call_<caller>_<random>`.

livekit-sip reads the trunk from Redis on every call, so no restart is needed
here.

### 3. LiveKit's advertised address

```bash
( cd /opt/aivoice
  cp livekit/livekit.yaml livekit/livekit.yaml.bak-$(date +%F-%H%M)
  sed -i 's/node_ip: <OLD_IP>/node_ip: <NEW_IP>/' livekit/livekit.yaml
  grep -n "node_ip\|use_external_ip" livekit/livekit.yaml
  docker compose restart livekit sip
  docker compose ps livekit sip )
```

`sip` restarts alongside it because it holds a connection to LiveKit; restarting
LiveKit alone can leave it on the old one.

The workers reconnect on their own. Confirm rather than assume:

```bash
journalctl -u aivoice-agent@1 --no-pager -n 5     # expect a fresh "registered worker"
```

If the newest `registered worker` line is still from before the restart:
`systemctl restart aivoice-agent@{1,2,3,4,5,6}`.

### 4. Where a handoff goes

`TRANSFER_SIP_HOST` has a **hardcoded default of the original development box**
in `voice_agent.py`, and it is easy to miss because nothing fails until somebody
asks for a person. Set it explicitly on every box:

```bash
grep -c '^TRANSFER_SIP_HOST=' /opt/aivoice/.env     # 0 means it is defaulting
# add TRANSFER_SIP_HOST=<NEW_IP> to /opt/aivoice/.env, then:
systemctl restart aivoice-agent@{1,2,3,4,5,6}
```

And the campaigns that name a SIP URI directly:

```bash
docker exec -i postgres psql -U aivoice -d aivoice -c \
  "SELECT name, transfer_to FROM agent_config WHERE transfer_to LIKE '%<OLD_IP>%';"
```

Anything returned needs updating to `<NEW_IP>`.

### 5. What this box calls itself

```bash
grep '^APP_ENV=' admin/.env       # whatever the source box was
```

Set it to `production` or `development` for what this box actually is, then the
next `deploy.sh` picks it up. It is the one line the login page reads to name the
server, and `deploy.sh` refuses to run without it rather than assume.

Nothing breaks if it is wrong — which is the problem. A development box quietly
labelled "Production" is a box people will trust, and both servers are identical
on every other screen.

---

## Proving it

One test call, and look in three places. Each one answers a different question.

```bash
# 1. Did the INVITE reach THIS box's livekit-sip?
docker logs sip --tail 20
```

Expect today's date and `fromIP: <NEW_IP>`. A clone carries the old server's logs
forward, so entries dated before the clone prove nothing — check the **date**, not
just that there is output.

```bash
# 2. Did a worker get the job?
journalctl -u "aivoice-agent@*" --since "5 min ago" --no-pager | tail -20
```

Expect `call_id=… caller=… callee=…`. `systemctl is-active` says only that the
process is alive — it says nothing about whether it is being given work, and on
.244 six workers reported `active` for two days while receiving nothing.

```bash
# 3. Did the call reach the database?
docker exec -i postgres psql -U aivoice -d aivoice -c \
  "SELECT id, config_name, end_reason, turn_count FROM calls ORDER BY id DESC LIMIT 3;"
```

Then, separately, **test a transfer** — it is the one path none of the above
exercises, and step 4 is invisible until you do.

---

## Emptying the clone

**After** it is proved working, not before. A clone arrives carrying the source
box's calls, transcripts, knowledge base, audit trail and provider keys, and a
client logging in to find somebody else's campaigns is noticed once and
remembered for a long time.

The order matters: the test call above needs a campaign to run, and the wipe
removes every campaign. Prove the wiring first, then empty it, then build the
real campaigns.

```bash
cd /srv/aivoice
server-configs/reset-production-data.sh                        # dry run
server-configs/reset-production-data.sh --confirm <NEW_IP>
server-configs/reset-production-data.sh --confirm <NEW_IP> --recordings
```

| Kept | Removed |
|---|---|
| `tenants`, `users`, `roles`, `role_permissions` | calls, turns, errors, postbacks, tool invocations |
| `provider_rates`, `platform_settings` | campaigns, agent configs, prompt versions, routes, tools |
| tenant-level `alert_rules` | the knowledge base, chat, fired alerts, audit trail, sessions |
| | **provider keys** and diallers |

Three things the script does that are worth knowing about:

- **`--confirm` takes this box's IP**, and it is checked against the interfaces
  the machine actually holds. It is not a "type yes to continue" prompt — that
  guards against haste, and the mistake being guarded here is being on the wrong
  server, which feels exactly like being on the right one.
- **It backs up first**, to `/opt/aivoice/backups/pre-reset/`, and verifies the
  dump by listing it before deleting anything. Deliberately not the nightly
  directory, whose 14-day retention would delete the only copy of what was
  removed a fortnight later.
- **It runs in one transaction and checks the kept tables afterwards.** If any of
  them lost a row to a cascade nobody predicted, it raises and the whole thing
  rolls back. The cascade graph has 48 edges and `calls → campaigns` is `SET
  NULL`, not `CASCADE` — deleting campaigns leaves every call row in place with
  a null campaign, which is exactly the sort of thing that is noticed a week
  later.

**Provider keys are removed**, so the box will not place a call until keys are
entered again in the console. They cannot be read back out of the dump by hand —
they are Fernet-encrypted — but the same rows still exist on the source box,
which shares `SECRETS_KEY`.

Recordings are files under `/var/spool/asterisk/recordings/` and no database row
points at them once the calls are gone. `--recordings` deletes the `.wav` files;
leaving them costs only disk.

---

## What must NOT change

Worth stating, because "it mentions a host, change it" is the wrong rule:

| Value | Why it is already correct |
|---|---|
| `LIVEKIT_URL` | Absent from `.env` on purpose; livekit-agents defaults to `ws://127.0.0.1:7880`, which is right on any box |
| `DATABASE_URL` | Already `127.0.0.1` — each box has its own Postgres |
| `AGENT_HTTP_PORT=808%i` | Per-worker, not per-host |
| `SECRETS_KEY` | **Must stay identical to the source box.** Provider keys, tool auth and the postback credential are Fernet-encrypted in the database the clone carries; a new key makes every one of them unreadable, and every call fails for want of a key that is sitting right there. |

---

## Two boxes, one set of credentials

A clone does not only duplicate configuration. Things to decide deliberately
rather than discover:

- **Provider keys are shared.** Development traffic bills the same OpenAI,
  Soniox and Sarvam accounts as production, and counts against the same rate
  limits.
- **Postback URLs are shared.** A test call on the development box sends a real
  result to the client's real endpoint. There is no "dev" flag in the payload.
- **Backups.** Both boxes now run the nightly dump. If it ever writes anywhere
  shared, the second one overwrites the first — and the one that survives is
  whichever ran last, not whichever mattered.
- **The dialler decides which box is live.** Nothing here prevents both from
  answering; the only thing pointing traffic at one of them is the dialler
  team's IAX configuration.

See [SERVER.md](SERVER.md) for the inventory of each box.
