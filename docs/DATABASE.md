# Database — backup, restore, and the thing that makes it useless

Postgres holds everything this system knows: every call and transcript, every
campaign's prompt and voice and limits, the knowledge base and its vectors, the
tools, and the **encrypted** provider keys.

It is on one disk, and until now nothing was copying it anywhere.

---

## Read this before anything else

**A database backup on its own cannot restore this system.**

Provider keys, tool auth values and the postback credential are Fernet-encrypted
in these rows. The key that decrypts them is `SECRETS_KEY`, and it lives in
`/opt/aivoice/.env` — not in the database. Restore the dump without it and you
get a console that lists every key by its last four characters and cannot use
any of them. Every campaign then refuses calls, because a campaign with no
usable key is declined rather than billed to someone else's account.

`SECRETS_KEY` never changes. Store it **once**, somewhere that is not this
server — a password manager, wherever your other credentials live:

```bash
# prints ONLY the secrets key, nothing else from the file
grep '^SECRETS_KEY=' /opt/aivoice/.env
```

> ⚠️ That is the one command in this repo that deliberately prints a secret.
> Do not run it on a shared screen, and do not paste the output into a ticket.

The nightly backup deliberately does **not** copy `.env` next to the dumps.
Keeping the ciphertext and its key in one directory undoes the point of
encrypting them.

---

## Nightly backup

```bash
# on the server, once
sudo cp /srv/aivoice/server-configs/systemd/aivoice-backup.{service,timer} \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now aivoice-backup.timer

# run it now rather than waiting until 02:30
sudo systemctl start aivoice-backup
journalctl -u aivoice-backup --no-pager -n 20
```

Expect:

```
backup ok: /opt/aivoice/backups/aivoice-2026-08-25-1430.dump (2.1M)
kept: 1 dumps, 14 day retention
```

Check the timer is really armed:

```bash
systemctl list-timers aivoice-backup
```

> 🚨 **If it fails with `status=203/EXEC`**, it is SELinux, not permissions.
> `/srv` is labelled `var_t` on Rocky and systemd may not execute a `var_t`
> file — `ls -l` shows 755 and looks entirely fine. `ausearch -m avc -ts recent`
> says so in one line. The unit runs the script through `/bin/sh` for exactly
> this reason; `chcon -t bin_t` also works but is undone by the next `git pull`,
> which rewrites the file and hands it the directory's label again. That failure
> is silent.

### What the script does that a bare `pg_dump` does not

- **Verifies every dump** by listing it with `pg_restore --list`. A backup nobody
  has ever read back is a file, not a backup, and the difference shows up on
  exactly one day.
- **Refuses a suspiciously small dump.** A database that answers but returns
  almost nothing produces a valid, useless file that would then rotate the good
  ones out.
- **Writes to a `.partial` name** and moves it only once it verifies, so an
  interrupted dump never sits there looking healthy.
- **Prunes after 14 days** (`BACKUP_KEEP_DAYS`).

---

## By hand

```bash
docker exec postgres pg_dump -U aivoice -Fc -d aivoice > aivoice-$(date +%F).dump
```

`-Fc` is the custom format: compressed, and `pg_restore` can pull a single table
out of it later. A plain SQL dump cannot, and "restore just the campaigns" is
the request that actually arrives.

Look inside one without restoring it:

```bash
docker exec -i postgres pg_restore --list < aivoice-2026-08-25.dump | head -40
```

---

## Restore

### Practise this before you need it

A restore procedure that has never been run is a guess. Do it into a scratch
database — it touches nothing live:

```bash
docker exec postgres createdb -U aivoice aivoice_restoretest
docker exec -i postgres pg_restore -U aivoice -d aivoice_restoretest \
    < /opt/aivoice/backups/aivoice-2026-08-25-0230.dump

# does it hold what you expect?
docker exec postgres psql -U aivoice -d aivoice_restoretest -c \
  "SELECT (SELECT count(*) FROM calls) calls,
          (SELECT count(*) FROM campaigns) campaigns,
          (SELECT count(*) FROM kb_chunks) chunks,
          (SELECT count(*) FROM provider_keys) keys;"

docker exec postgres dropdb -U aivoice aivoice_restoretest
```

### For real

Stop the writers first. A restore while calls are landing produces a database
that matches neither the backup nor the present.

```bash
systemctl stop aivoice-agent@{1,2,3,4,5,6}
docker compose -f /srv/aivoice/admin/docker-compose.yml stop admin-api

docker exec postgres dropdb -U aivoice aivoice
docker exec postgres createdb -U aivoice aivoice
docker exec -i postgres pg_restore -U aivoice -d aivoice < <the dump>

# pgvector - the dump carries CREATE EXTENSION, but check rather than assume
docker exec postgres psql -U aivoice -d aivoice -c "\dx"

docker compose -f /srv/aivoice/admin/docker-compose.yml start admin-api
systemctl start aivoice-agent@{1,2,3,4,5,6}
```

Then make one call. That is the only thing that proves it.

---

## What else has to survive

The database is not the whole system.

| | Where | Backed up by |
|---|---|---|
| **`SECRETS_KEY`** | `/opt/aivoice/.env` | **You, once, somewhere else.** Without it the dump is undecryptable |
| Other `.env` values | `/opt/aivoice/.env` | Provider keys are also in the database; these are the platform defaults and the LiveKit keys |
| `gcp/sa.json` | `/opt/aivoice/gcp/` | Re-downloadable from Google, but know that |
| Call recordings | `/var/spool/asterisk/recordings` | Nothing. ~550 KB per call, 90-day retention |
| Code and config | git | ✅ already |

Recordings are deliberately not in this backup: they are large, they already
expire at 90 days, and they are evidence rather than state — the system runs
without them. If they need to survive, that is a separate copy to separate
storage, not a bigger dump.

---

## The honest limit

These dumps sit on **the same disk as the database**.

That covers the case that actually happens — a bad migration, a `DELETE` without
a `WHERE`, a campaign someone wiped — and it covers none of the cases that end
the project: the disk, the VM, the host.

Making it real means one more line, once there is somewhere to send it:

```bash
# whatever you already use - rsync to another box, an S3 bucket, a NAS
rsync -a /opt/aivoice/backups/ backup-host:/aivoice/
```

Until that exists, this is a good backup of the thing most likely to go wrong,
and not a disaster recovery plan. Worth being clear about which one you have.
