import { useState } from 'react'
import {
  Activity,
  AudioLines,
  Ban,
  Check,
  ChevronRight,
  Copy,
  PhoneMissed,
  RotateCcw,
  Search,
  Send,
  TriangleAlert,
  Unplug,
} from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Card, Input } from '@/components/ui/primitives'
import { cn } from '@/lib/utils'
import { APP_ENV, ENV_LABEL } from '@/lib/version'

/**
 * What to run when something breaks, in the order worth running it.
 *
 * It SHOWS commands and never runs them. Running them from here would mean
 * giving the console a way to act on the host - the docker socket, a shell -
 * and a web page that can restart Asterisk is a far bigger risk than the
 * thirty seconds it saves over SSH.
 *
 * Every command is one that has been run on these boxes, most of them from
 * docs/COMMANDS.md. A debugging page with a command that does not work is
 * worse than none: it is reached for at the worst moment and believed.
 *
 * No `docker exec -i` with psql -c. -i attaches stdin, and when several lines
 * are pasted at once it swallows every line after it as its own input: the
 * rest of the command silently never runs. psql -c needs no stdin. Multi-line
 * commands are wrapped in ( ... ) as well, so bash reads the whole block
 * before running any of it.
 */

// The console is usually opened as http://<ip>, which is not a secure context,
// and there navigator.clipboard does not exist at all - the copy button would
// throw on exactly the boxes it is used on.
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through to the old way
  }
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  const ok = document.execCommand('copy')
  document.body.removeChild(ta)
  return ok
}

function Cmd({ children }: { children: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg border border-border/70 bg-muted/30 py-2.5 pl-3 pr-24 font-mono text-2xs leading-relaxed">
        {children}
      </pre>
      <Button
        variant="outline"
        size="sm"
        className="absolute right-2 top-2 h-7"
        onClick={async () => {
          if (await copyText(children)) {
            setCopied(true)
            window.setTimeout(() => setCopied(false), 2000)
          }
        }}
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

function Step({
  n,
  title,
  good,
  children,
}: {
  n: number
  title: string
  good?: React.ReactNode
  children?: React.ReactNode
}) {
  return (
    <div className="space-y-2">
      <div className="flex items-baseline gap-2">
        <span className="tnum text-2xs font-semibold text-muted-foreground">{n}.</span>
        <h3 className="text-sm font-medium">{title}</h3>
      </div>
      <div className="space-y-2 pl-5">
        {children}
        {good && (
          <p className="text-2xs leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">Good: </span>
            {good}
          </p>
        )}
      </div>
    </div>
  )
}

function Warn({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex gap-2 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-2xs leading-relaxed text-amber-800 dark:text-amber-300">
      <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0" />
      <div>{children}</div>
    </div>
  )
}

function Section({
  icon: Icon,
  title,
  when,
  defaultOpen = false,
  children,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  when: string
  defaultOpen?: boolean
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Card>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-start gap-3 px-4 py-3 text-left"
        aria-expanded={open}
      >
        <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold">{title}</div>
          <div className="text-2xs text-muted-foreground">{when}</div>
        </div>
        <ChevronRight
          className={cn('mt-0.5 h-4 w-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-90')}
        />
      </button>
      {open && <div className="space-y-5 border-t border-border/60 px-4 py-4">{children}</div>}
    </Card>
  )
}

function CallIdField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="flex items-center gap-2">
      <label htmlFor="debug-call-id" className="text-2xs font-medium text-muted-foreground">
        Call ID
      </label>
      <Input
        id="debug-call-id"
        value={value}
        onChange={(e) => onChange(e.target.value.replace(/\D/g, ''))}
        placeholder="e.g. 464"
        inputMode="numeric"
        className="h-8 w-32"
      />
      <span className="text-2xs text-muted-foreground">from the Calls page — fills the commands below</span>
    </div>
  )
}

export function Debug() {
  const [callId, setCallId] = useState('')
  const id = callId || '<call-id>'

  return (
    <div className={PAGE}>
      <PageHeader
        title="Debug"
        description="When something breaks: what to check, in order. Copy a command and run it on the server over SSH — this page never runs anything itself."
      />

      <Warn>
        This console is on <strong>{ENV_LABEL[APP_ENV]}</strong>. Both servers show the same{' '}
        <code>[root@localhost ~]#</code> prompt, so run <code>hostname -I</code> before anything that
        restarts or writes.
        {APP_ENV === 'production' && ' A restart here drops real callers.'}
      </Warn>

      <Section
        icon={Activity}
        title="Is everything up?"
        when="Start here, whatever the problem is."
        defaultOpen
      >
        <Step
          n={1}
          title="Services, workers, containers and the dialler's trunk"
          good={
            <>
              every line <code>active</code>, every container <code>Up</code>, and the dialler's IAX peer{' '}
              <code>OK</code>. The System page shows most of this at a glance.
            </>
          }
        >
          <Cmd>{String.raw`systemctl is-active asterisk docker
for i in 1 2 3 4 5 6; do printf "agent@%s: %s\n" $i "$(systemctl is-active aivoice-agent@$i)"; done
docker ps --format '{{.Names}}\t{{.Status}}'
asterisk -rx "core show channels" | tail -2
asterisk -rx "iax2 show peers"`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Are the workers registered with LiveKit?"
          good={
            <>
              <code>6</code>. Fewer means a worker is running but not taking calls — restart it (see{' '}
              <em>Restart something</em>).
            </>
          }
        >
          <Cmd>{String.raw`journalctl -u "aivoice-agent@*" --no-pager \
  --since "$(systemctl show -p ActiveEnterTimestamp --value aivoice-agent@1)" \
  | grep -c "registered worker"`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={PhoneMissed}
        title="Calls don't reach the bot, or ring and die"
        when="The dialler dials, but nobody answers or the call drops at once."
      >
        <Step
          n={1}
          title="Is the dialler's trunk reachable?"
          good={
            <>
              the dialler's peer shows <code>OK (n ms)</code>. <code>UNREACHABLE</code> is the network or the
              dialler's side, not ours.
            </>
          }
        >
          <Cmd>{String.raw`asterisk -rx "iax2 show peers"`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Watch a call come in"
          good="each step of the dialplan prints as the test call arrives. Leave with exit — it does not stop Asterisk."
        >
          <Cmd>{String.raw`asterisk -rvvv`}</Cmd>
        </Step>
        <Step
          n={3}
          title="Did livekit-sip accept it?"
          good={
            <>
              a line for each call. <code>inviteToAcceptMs</code> is how long the caller heard ringing.
            </>
          }
        >
          <Cmd>{String.raw`docker logs sip --since 30m 2>&1 | grep -E "inviteTo|Accepting|Joining room"`}</Cmd>
        </Step>
        <Step
          n={4}
          title="Did a worker pick it up?"
          good={
            <>
              <code>received job</code> then <code>call_id=</code> for each call. Missing means the call never
              reached an agent — go back to step 3.
            </>
          }
        >
          <Cmd>{String.raw`journalctl -u "aivoice-agent@*" --since '-15 min' --no-pager \
  | grep -E '"message": "(received job|call_id=)' \
  | sed -E 's/caller=[^ ]+/caller=XXXX/; s/call_[0-9]+/call_XXXX/g' | cut -c1-200`}</Cmd>
        </Step>
        <p className="text-2xs leading-relaxed text-muted-foreground">
          On a cloned server, also check nothing still points at the other box's address — that sent
          production's calls to development for two days. See <code>docs/REPLICA.md</code>.
        </p>
      </Section>

      <Section
        icon={AudioLines}
        title="The voice breaks, or audio is one-way"
        when="Callers hear the agent stutter, cut out, or not at all."
      >
        <Step
          n={1}
          title="Packets and CPU, during a live call"
          good={
            <>
              <code>Lost 0</code> on both legs, jitter in single digits, and the last <code>vmstat</code>{' '}
              column (<code>st</code>) at 0. Start a call, make the agent talk, then run this — it samples for
              about 35 seconds and saves to <code>/tmp/audio-debug.txt</code>.
            </>
          }
        >
          <Cmd>{String.raw`vmstat 1 1 | head -2
( for i in $(seq 1 10); do
    echo "===== $(date +%T)"
    asterisk -rx "pjsip show channelstats"
    asterisk -rx "iax2 show netstats"
    vmstat 1 2 | tail -1
    sleep 2
  done ) 2>&1 | tee /tmp/audio-debug.txt`}</Cmd>
          <Warn>
            Clean packets do not prove clean audio. livekit-sip sends a packet every 20 ms even when there is
            nothing to say, so a gap in the voice travels as perfectly regular silence. Run this on a call where
            the voice actually broke — a healthy call only proves the path can be healthy.
          </Warn>
        </Step>
        <Step n={2} title="Listen to that call's recording">
          <p className="text-2xs leading-relaxed text-muted-foreground">
            Open the call on the Calls page. The recording is made on our Asterisk, in the middle of the path:
          </p>
          <ul className="list-disc space-y-1 pl-5 text-2xs leading-relaxed text-muted-foreground">
            <li>
              <span className="font-medium text-foreground">Breaks in the recording too</span> → our side: the
              agent, the TTS provider, livekit-sip.
            </li>
            <li>
              <span className="font-medium text-foreground">Recording is clean</span> → beyond us: the dialler's
              Asterisk or the carrier.
            </li>
            <li>
              <span className="font-medium text-foreground">The greeting breaks as well</span> → not the TTS
              provider. The greeting plays from disk.
            </li>
          </ul>
        </Step>
        <Step
          n={3}
          title="Was the TTS itself slow? Check the call's own measurement"
          good={
            <>
              <code>stalled=0.0s</code> on every <code>TTS_STREAM</code> line. A large <code>stalled</code> with a{' '}
              <em>negative</em> <code>text_complete</code> means the text had all arrived and the TTS was still
              late — the provider, not the model. Each stall is a hole the caller heard.
            </>
          }
        >
          <Cmd>{String.raw`journalctl -u 'aivoice-agent@*' --since '-1 hour' --no-pager -o short-precise \
  | grep -E '"message": "TTS_(STREAM|STALLS)' \
  | sed -E 's/^[A-Za-z]+ [0-9]+ ([0-9:.]+).*"message": "(.{0,200}).*/\1  \2/'`}</Cmd>
        </Step>
        <Step
          n={4}
          title="Test the TTS on its own, outside any call"
          good={
            <>
              <code>stalls 0</code> on every line, for both <code>alone</code> and <code>pair</code>. Uses the
              campaign's own key, plugin and voice, and never prints the key. Stalls here with nothing else
              involved put the fault with the provider or the network to it.
            </>
          }
        >
          <Cmd>{String.raw`( set -a; . /opt/aivoice/.env; set +a
  cd /srv/aivoice && /opt/aivoice/agent/.venv/bin/python server-configs/tts-bench.py default )`}</Cmd>
        </Step>
        <Step
          n={5}
          title="While it is stalling: the connection itself, and the path"
          good={
            <>
              Run this <em>during</em> a bad spell — it only answers then. No <code>retrans</code> or{' '}
              <code>lost</code> on the socket and 0% at the last mtr hop while the bench stalls puts the
              fault beyond the provider's edge, with the provider. Either appearing puts it on the path,
              and that is a conversation with the ISP.
            </>
          }
        >
          <Cmd>{String.raw`( set -a; . /opt/aivoice/.env; set +a; cd /srv/aivoice
/opt/aivoice/agent/.venv/bin/python server-configs/tts-bench.py default --providers soniox --runs 2 &
BENCH=$!
HOST=$(docker exec postgres psql -U aivoice -d aivoice -At -c "SELECT CASE WHEN region IS NULL OR region='us' THEN 'tts-rt.soniox.com' ELSE 'tts-rt.'||region||'.soniox.com' END FROM provider_keys WHERE provider='soniox' ORDER BY campaign_id NULLS LAST LIMIT 1")
[ -n "$HOST" ] || HOST=tts-rt.soniox.com
IP=$(getent ahostsv4 "$HOST" | awk '{print $1; exit}')
echo "watching $HOST at $IP"
for i in $(seq 1 20); do date +%T; ss -tin "dst $IP" | tail -2; sleep 3; done
mtr -rwzc 30 "$IP" | tail -8
wait $BENCH )`}</Cmd>
          <p className="text-2xs leading-relaxed text-muted-foreground">
            A clean window to compare against, measured 23 Sep 2026 while Soniox was behaving: 2,900
            segments in with no <code>retrans</code> field at all, rtt 2.2–8 ms, <code>pmtu 1500</code>,{' '}
            <code>Recv-Q 0</code> throughout, and 0.0% loss at the destination. A middle hop showing heavy
            loss is ICMP rate limiting, not loss, when the hops after it are clean.
          </p>
        </Step>
      </Section>

      <Section
        icon={Search}
        title="What happened on one call"
        when="A specific call went wrong and you want its story."
      >
        <CallIdField value={callId} onChange={setCallId} />
        <Step
          n={1}
          title="Its turns, and every warning or error it logged"
          good={
            <>
              <code>interrupted = t</code> on an agent turn means it was cut off mid-sentence; a large{' '}
              <code>total_ms</code> is a slow reply; an <code>ERROR</code> line usually names the provider.
            </>
          }
        >
          <Cmd>{String.raw`(
ID=${id}
docker exec postgres psql -U aivoice -d aivoice -c "
SELECT seq, role, interrupted, total_ms, left(text, 50) AS text
  FROM turns WHERE call_id = $ID ORDER BY seq;"
JOB=$(journalctl -u 'aivoice-agent@*' --since '-3 days' --no-pager \
      | grep "\"call_id=$ID caller=" | grep -o '"job_id": "[^"]*"' | head -1 | cut -d'"' -f4)
if [ -z "$JOB" ]; then echo "no worker log for call $ID in the last 3 days"; else
  echo "job: $JOB"
  journalctl -u 'aivoice-agent@*' --since '-3 days' --no-pager -o short-iso | grep "$JOB" \
    | grep -E '"level": "(WARNING|ERROR)"' | grep -v deprecated \
    | sed -E 's/call_[0-9]+/call_XXXX/g' | cut -c1-300
fi
)`}</Cmd>
        </Step>
        <Step n={2} title="Follow calls live, as they happen">
          <Cmd>{String.raw`journalctl -u "aivoice-agent@*" -f -o short-precise`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={Unplug}
        title="A call stuck in the live monitor, or the dialler sees too few free slots"
        when="A call shows as in progress long after it ended."
      >
        <p className="text-2xs leading-relaxed text-muted-foreground">
          This matters beyond the monitor: the dialler's capacity API counts every open call as a used slot.
          A sweeper closes any call open past its campaign's maximum duration and half again, within a minute.
        </p>
        <Step
          n={1}
          title="Which calls are open right now?"
          good="only calls that are genuinely in progress."
        >
          <Cmd>{String.raw`docker exec postgres psql -U aivoice -d aivoice -c "SELECT id, callee, config_name, to_char(started_at AT TIME ZONE 'Asia/Kolkata','DD Mon HH24:MI') AS started_ist, now() - started_at AS open_for FROM calls WHERE ended_at IS NULL ORDER BY started_at;"`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Is the sweeper working, and did any worker die?"
          good={
            <>
              no sweep failures, and <code>0</code> workers killed. A number above 0 is a worker that hung while
              shutting down a call — see call 464 in <code>docs/PROGRESS.md</code>.
            </>
          }
        >
          <Cmd>{String.raw`docker logs admin-api --since 1h 2>&1 | grep -iE "sweep failed|no worker will ever close"
journalctl -u 'aivoice-agent@*' --since '-24 hours' --no-pager | grep -c "process did not exit in time"`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={Send}
        title="A call's result never reached the client's system"
        when="Send to API is on, but the client says a call is missing."
      >
        <CallIdField value={callId} onChange={setCallId} />
        <Step
          n={1}
          title="Which of three things happened?"
          good={
            <>
              <code>postback_enabled = f</code> → nothing to send. A <code>status</code> → a delivery problem;
              use Retry on the call's page. <code>t</code> with no row → it was never written; rebuild it in
              step 2.
            </>
          }
        >
          <Cmd>{String.raw`docker exec postgres psql -U aivoice -d aivoice -c "SELECT c.id, c.config_name, c.end_reason, ac.postback_enabled, p.status, p.attempts, p.last_status_code FROM calls c JOIN agent_config ac ON ac.name=c.config_name LEFT JOIN call_postbacks p ON p.call_id=c.id WHERE c.id=${id};"`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Rebuild it from the transcript"
          good="the dry run prints what would be sent. It refuses a call that already has a row, so it cannot send twice."
        >
          <Cmd>{String.raw`cd /srv/aivoice/agent
/opt/aivoice/agent/.venv/bin/python requeue_postback.py ${id} --dry-run
/opt/aivoice/agent/.venv/bin/python requeue_postback.py ${id}`}</Cmd>
        </Step>
        <Step
          n={3}
          title="The client's API was down: resend everything that failed today"
          good={
            <>
              the one test row comes back <code>sent</code> with <code>200</code>; only then run the second
              command. Same as the Retry button: <code>attempts</code> back to 0, and the current URL and auth are
              used. Today means since midnight IST.
            </>
          }
        >
          <Cmd>{String.raw`# 1. how many, and why
( TODAY="date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'"
docker exec postgres psql -U aivoice -d aivoice -c "SELECT cam.name AS campaign, p.last_status_code AS code, count(*) AS failed, left(max(p.last_error), 80) AS sample_error FROM call_postbacks p LEFT JOIN campaigns cam ON cam.id = p.campaign_id WHERE p.status = 'failed' AND p.created_at >= $TODAY GROUP BY 1, 2 ORDER BY 1, 2;" )

# 2. one row first - if the API is still broken, one row finds out, not all of them
( TODAY="date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'"
PID=$(docker exec postgres psql -U aivoice -d aivoice -qAt -c "UPDATE call_postbacks SET status='pending', attempts=0, next_attempt_at=now() WHERE id = (SELECT id FROM call_postbacks WHERE status='failed' AND created_at >= $TODAY ORDER BY created_at DESC LIMIT 1) RETURNING id;")
sleep 20
docker exec postgres psql -U aivoice -d aivoice -c "SELECT id, call_id, status, last_status_code, left(last_error, 80) AS error FROM call_postbacks WHERE id = $PID;" )

# 3. only after the row above says sent: the rest
( TODAY="date_trunc('day', now() AT TIME ZONE 'Asia/Kolkata') AT TIME ZONE 'Asia/Kolkata'"
docker exec postgres psql -U aivoice -d aivoice -qAt -c "WITH u AS (UPDATE call_postbacks SET status='pending', attempts=0, next_attempt_at=now() WHERE status='failed' AND created_at >= $TODAY RETURNING 1) SELECT count(*) || ' requeued' FROM u;" )`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={RotateCcw}
        title="Restart something"
        when="After a fix, or when one piece is stuck."
      >
        <Step
          n={1}
          title="First: is anyone on a call?"
          good={
            <>
              <code>0 active calls</code>. Everything below except the console drops calls in progress.
            </>
          }
        >
          <Cmd>{String.raw`asterisk -rx "core show channels" | tail -2`}</Cmd>
        </Step>
        <Step n={2} title="Agent workers — all six, or just one">
          <Cmd>{String.raw`systemctl restart aivoice-agent@{1,2,3,4,5,6}
systemctl restart aivoice-agent@3`}</Cmd>
          <Warn>Drops the calls on the workers restarted.</Warn>
        </Step>
        <Step
          n={3}
          title="The console (this page)"
          good="back in about a minute — the API loads the agent's modules before it answers. Never touches calls."
        >
          <Cmd>{String.raw`docker restart admin-api admin-web`}</Cmd>
          <p className="text-2xs leading-relaxed text-muted-foreground">
            For new code use the deploy below instead. A <code>docker compose up --build</code> typed by hand
            cannot know the version and stamps the build <code>unknown</code>.
          </p>
        </Step>
        <Step n={4} title="LiveKit and livekit-sip">
          <Cmd>{String.raw`cd /opt/aivoice && docker compose restart livekit sip`}</Cmd>
          <Warn>Drops every call in progress.</Warn>
        </Step>
        <Step n={5} title="Asterisk">
          <Cmd>{String.raw`systemctl restart asterisk`}</Cmd>
          <Warn>Drops every call, including ones already passed to a person.</Warn>
        </Step>
        <Step
          n={6}
          title="Deploy — development follows main, production takes a release"
          good={
            <>
              ends with <code>Workers : all six active</code> and the API reporting the version just deployed.
              With calls in progress it updates the console, leaves the workers alone, and says so.
            </>
          }
        >
          <Cmd>{String.raw`cd /srv/aivoice && server-configs/deploy.sh            # development
cd /srv/aivoice && server-configs/deploy.sh vX.Y.Z     # production`}</Cmd>
        </Step>
        <p className="text-2xs leading-relaxed text-muted-foreground">
          Rebooting the whole server: read <code>docs/RUNBOOK.md</code> §9 first. The first reboot here lost the
          LiveKit SIP trunk, and every call rang and died with nothing in any log to say why.
        </p>
      </Section>

      <Card className="border-destructive/30 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Ban className="h-4 w-4 text-destructive" />
          Never run these
        </div>
        <ul className="mt-2 space-y-1.5 text-2xs leading-relaxed text-muted-foreground">
          <li>
            <code>cat /opt/aivoice/.env</code> — prints every key. To see which are set, mask them:{' '}
            <code>{String.raw`sed -E 's/=(.{0,4}).*/=\1***/' /opt/aivoice/.env`}</code>
          </li>
          <li>
            <code>{'asterisk -rx "pjsip show auth <id>"'}</code> — prints the SIP password in plain text.
          </li>
          <li>
            <code>cat /etc/asterisk/pjsip.conf</code> — same reason.
          </li>
        </ul>
      </Card>
    </div>
  )
}
