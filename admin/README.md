# Admin panel

Multi-tenant console for the voice stack: call logs with per-turn latency,
knowledge-base citations, and (later) campaign configuration and analytics.

```
admin/
├── backend/          FastAPI + asyncpg      → /api
├── frontend/         React + Vite + TS      → the panel
├── docker-compose.yml
├── bootstrap_env.py  generates admin/.env from the media stack's env
└── smoke_test.py     end-to-end API check, stdlib only
```

## Why it is a separate compose project

The media stack lives in `/opt/aivoice` and must not be restarted to ship a UI
change. This project joins the existing `aivoice_default` network purely to
reach postgres, so `up -d --build` here never touches Asterisk, LiveKit or the
agent workers — live calls keep running through a panel deploy.

## Deploy

Code is written on the workstation, pushed, and pulled on the server.

```bash
# on the server
cd /srv/aivoice
git pull
docker compose -f admin/docker-compose.yml up -d --build
```

First time only:

```bash
python3 admin/bootstrap_env.py          # writes admin/.env, chmod 600
docker compose -f admin/docker-compose.yml run --rm admin-api \
    python seed_admin.py --email you@example.com --name "Your Name"
```

Panel: `http://10.130.9.243:8080` · API (localhost only): `127.0.0.1:8090`

## Local development

The API stays bound to `127.0.0.1` on the server, so reach it through a tunnel:

```bash
ssh -L 8090:127.0.0.1:8090 root@10.130.9.243     # leave this running
```

```bash
cd admin/frontend
npm install
npm run dev            # http://localhost:5173
```

Vite proxies `/api` to the tunnel, so the browser only ever talks to
`localhost:5173`. Same origin — CORS never enters the picture, in development or
in production (nginx proxies `/api` there).

## Security decisions worth not undoing

**The access token is not trusted as a source of truth.** Every request re-reads
the user row, so deactivating an account or changing a role takes effect at once
instead of lingering until the 15-minute token expires.

**Refresh tokens are stored as SHA-256, never raw**, and rotate on every use. A
database leak cannot be replayed; a stolen token works at most once, and the real
client's next refresh fails loudly rather than silently sharing a session.

**Tenant isolation is resolved in exactly one place** — `deps.tenant_scope()` —
and every query takes its answer. A non-superadmin cannot widen its own scope
whatever it puts in the query string.

**Cross-tenant reads return 404, not 403.** A distinct 403 would let a client
probe which call ids exist elsewhere.

**Login verifies against a dummy hash when the email is unknown**, so a wrong
email and a wrong password take the same time. Otherwise response timing
enumerates valid accounts.

The refresh token sits in `localStorage`, which an XSS could read. An httpOnly
cookie resists that better but needs CSRF protection and a same-site deployment;
rotation is what limits the blast radius until then. Revisit when the panel is
exposed beyond the source-restricted firewall rule.

## Verify

```bash
python3 admin/smoke_test.py --email you@example.com
```

Checks refresh rotation, unauthenticated access, filters, and transcript
retrieval. Never prints tokens.
