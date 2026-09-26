import { useState } from 'react'
import {
  Activity,
  AudioLines,
  Ban,
  Check,
  ChevronRight,
  Copy,
  Cpu,
  Gauge,
  PhoneMissed,
  RotateCcw,
  Search,
  Send,
  Server,
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

/**
 * The GPU box is a DIFFERENT MACHINE, and that is why this is a tab rather
 * than another section.
 *
 * Everything else on this page runs on the call server as root. These run on
 * 10.130.9.248 as `gpu`, which is in the docker group and needs no sudo. In
 * one list the two would eventually be run on the wrong box, and a restart on
 * the wrong box drops calls.
 *
 * Three services live there, each its own compose project under
 * /srv/gpu-stack/. Every command below has been run on that box.
 */
function GpuTab() {
  return (
    <>
      <Warn>
        These run on the <strong>GPU box, 10.130.9.248</strong>, signed in as <code>gpu</code> — not
        on this server. Its prompt looks different (<code>gpu@gpu:~$</code>), which is easy to miss
        at 2am, so check <code>hostname -I</code> first.
      </Warn>

      <Section
        icon={Activity}
        title="Are all three up?"
        when="Start here. A campaign on our own voice or model has neither if this box is down."
        defaultOpen
      >
        <Step
          n={1}
          title="Containers, endpoints, card and memory"
          good={
            <>
              three containers <code>Up</code>, three <code>200</code>s, and around{' '}
              <code>47000 MiB</code> of <code>49140</code> used — the card is meant to be nearly
              full, because vLLM takes its budget at startup and never grows. RAM should show{' '}
              <code>0B</code> of swap.
            </>
          }
        >
          <Cmd>{String.raw`docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -s -o /dev/null -w 'kokoro    8880  %{http_code}\n' http://127.0.0.1:8880/v1/audio/voices
curl -s -o /dev/null -w 'qwen-asr  8001  %{http_code}\n' http://127.0.0.1:8001/v1/models
curl -s -o /dev/null -w 'qwen-llm  8002  %{http_code}\n' http://127.0.0.1:8002/v1/models
nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv
free -h | head -2`}</Cmd>
        </Step>
        <Step
          n={2}
          title="A container that is Up but answers nothing is still loading"
          good={
            <>
              <code>200</code> within about three minutes of a start: a minute of weights, a minute
              of <code>torch.compile</code>, half a minute capturing CUDA graphs. Longer than that
              and it is not loading, it is crash-looping — see <em>When vLLM will not start</em>.
            </>
          }
        >
          <Cmd>{String.raw`for i in $(seq 1 24); do code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8002/v1/models); echo "$(date +%T) http=$code"; [ "$code" = "200" ] && break; sleep 10; done`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={AudioLines}
        title="Make each one actually work"
        when="They answer /v1/models and the call still fails. A health check is not a synthesis."
      >
        <Step
          n={1}
          title="Speak — and count the bytes"
          good={
            <>
              <code>200</code> with tens of thousands of bytes. A <code>200</code> with{' '}
              <code>0 bytes</code> is a model that loaded and is not synthesising, which nothing
              else here would show.
            </>
          }
        >
          <Cmd>{String.raw`curl -s -o /tmp/say.wav -w 'tts http %{http_code}  bytes %{size_download}\n' \
  -X POST http://127.0.0.1:8880/v1/audio/speech \
  -H 'Content-Type: application/json' \
  -d '{"model":"kokoro","voice":"hf_alpha","input":"नमस्ते, आप कैसे हैं","response_format":"wav"}'`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Listen — to what we just said"
          good={
            <>
              the same sentence back, in about <code>800 ms</code>. This feeds our own voice to our
              own ears, so one command proves both and needs no audio file lying around.
            </>
          }
        >
          <Cmd>{String.raw`curl -s -w '\nstt %{time_total}s\n' \
  -X POST http://127.0.0.1:8001/v1/audio/transcriptions \
  -F file=@/tmp/say.wav -F model=qwen3-asr -F language=hi`}</Cmd>
        </Step>
        <Step
          n={3}
          title="Think — with thinking mode off"
          good={
            <>
              a short Hindi reply, no <code>&lt;think&gt;</code> anywhere. Qwen3 reasons before
              answering unless told not to; asked a one-line question with it on, it spent all 120
              tokens arguing with itself in English and never replied. The agent sends the same
              switch on every call.
            </>
          }
        >
          <Cmd>{String.raw`curl -s -w '\nllm %{time_total}s\n' \
  -X POST http://127.0.0.1:8002/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3-32b","max_tokens":100,"chat_template_kwargs":{"enable_thinking":false},"messages":[{"role":"user","content":"नमस्ते, एक लाइन में जवाब दीजिए।"}]}'`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={Unplug}
        title="From the call server, not from the box"
        when="Everything answers on the box and calls still fail."
      >
        <Step
          n={1}
          title="Does this server know where the box is, and can it reach it?"
          good={
            <>
              three addresses printed and three <code>200</code>s. An address missing here is the
              likeliest cause of all: the agent and the console read DIFFERENT env files, and a
              variable added to one and not the other has broken this twice.
            </>
          }
        >
          <Cmd>{String.raw`( set -a; . /opt/aivoice/.env; set +a
  echo "agent:   KOKORO_URL=$KOKORO_URL"
  echo "agent:   QWEN_STT_URL=$QWEN_STT_URL"
  echo "agent:   QWEN_LLM_URL=$QWEN_LLM_URL"
  grep -hE '^(KOKORO_URL|QWEN_STT_URL|QWEN_LLM_URL)=' /srv/aivoice/admin/.env | sed 's/^/console: /'
  curl -s -o /dev/null -w 'kokoro   %{http_code}\n' "$KOKORO_URL/audio/voices"
  curl -s -o /dev/null -w 'qwen-asr %{http_code}\n' "$QWEN_STT_URL/models"
  curl -s -o /dev/null -w 'qwen-llm %{http_code}\n' "$QWEN_LLM_URL/models" )`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Which campaigns actually point at the box"
          good={<>the providers a campaign uses. <code>kokoro</code>, <code>qwen</code> and <code>qwen-llm</code> are ours; the rest are vendors.</>}
        >
          <Cmd>{String.raw`docker exec postgres psql -U aivoice -d aivoice -c "SELECT campaign_id, name, stt_provider, llm_provider, llm_model, tts_provider, tts_voice, stt_fallback_provider, llm_fallback_provider, tts_fallback_provider FROM agent_config ORDER BY campaign_id;"`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={Gauge}
        title="Watch it during a call"
        when="What one call costs the box, and which of the three is the limit."
      >
        <Step
          n={1}
          title="The GPU, continuously"
          good={
            <>
              <code>sm</code> is how busy the GPU is and <code>fb</code> its memory. Use this rather
              than <code>watch</code>: a sentence renders in 200–400 ms and a once-a-second snapshot
              misses most of them.
            </>
          }
        >
          <Cmd>{String.raw`nvidia-smi dmon -s um -d 1`}</Cmd>
        </Step>
        <Step
          n={2}
          title="All three containers at once"
          good={
            <>
              This box has <strong>8 vCPU</strong> and <code>docker stats</code> counts one core as
              100%, so <code>800%</code> is the whole machine. Forty concurrent calls through Kokoro
              reached 101% — one core. The CPU has never been the limit here.
            </>
          }
        >
          <Cmd>{String.raw`docker stats kokoro qwen-asr qwen-llm`}</Cmd>
        </Step>
        <Step
          n={3}
          title="Logged to a file, to read after the call"
          good={<>Start it before the call and <code>Ctrl+C</code> after.</>}
        >
          <Cmd>{String.raw`( echo "time      gpu%  gpu_mem  kokoro  qwen-asr  qwen-llm"
  while true; do
    g=$(nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits | tr -d ',')
    c=$(docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' kokoro qwen-asr qwen-llm | awk '{printf "%s ", $2}')
    printf '%s  %s  %s\n' "$(date +%T)" "$g" "$c"
  done ) | tee /tmp/gpu-load.log`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={TriangleAlert}
        title="When vLLM will not start"
        when="A container that says Up and then Restarting, over and over."
      >
        <Warn>
          The error always blames memory and is usually <code>--max-model-len</code>. vLLM reserves
          KV cache for the FULL declared context whether a request uses it or not. This cost two
          hours across both services.
        </Warn>
        <Step
          n={1}
          title="Read the real exception, not the stack around it"
          good={
            <>
              the last lines carry the <code>ValueError</code>. The outer traceback says only
              &ldquo;Engine core initialization failed&rdquo;, which is true and useless.
            </>
          }
        >
          <Cmd>{String.raw`cd /srv/gpu-stack/qwen-llm && docker compose logs --no-color 2>&1 | grep -E "core.py:1366|ValueError|out of memory" | tail -8`}</Cmd>
        </Step>
        <Step
          n={2}
          title="What the settings are for"
          good={
            <>
              <code>qwen-llm</code> runs <code>--max-model-len=16384</code> at{' '}
              <code>--gpu-memory-utilization=0.75</code>; <code>qwen-asr</code> runs{' '}
              <code>4096</code> at <code>0.22</code>. A call&rsquo;s prompt is 1–2k tokens plus the
              knowledge base; an utterance is a few seconds of audio. The defaults — 65536 for the
              LLM — reserve gigabytes for a context nothing here will ever send.
            </>
          }
        >
          <Cmd>{String.raw`grep -nE "model|max-model-len|gpu-memory" /srv/gpu-stack/qwen-llm/docker-compose.yml /srv/gpu-stack/qwen-asr/docker-compose.yml`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={RotateCcw}
        title="Restart one, or bring the box back after a reboot"
        when="Something stopped answering, or the machine was rebooted."
      >
        <Warn>
          A restart cuts off any call using that service. A campaign with a fallback provider
          carries on; one without goes silent. Campaign 7 has no fallback on any layer.
        </Warn>
        <Step
          n={1}
          title="Restart just one"
          good={<>back to <code>Up</code>, then about three minutes before vLLM answers. Kokoro is quicker — its model is tiny.</>}
        >
          <Cmd>{String.raw`( cd /srv/gpu-stack/qwen-llm && docker compose restart )   # or qwen-asr, or /srv/gpu-stack for kokoro
sleep 20 && docker ps --format '{{.Names}}\t{{.Status}}'`}</Cmd>
        </Step>
        <Step
          n={2}
          title="After a reboot, the two things that do not survive one by themselves"
          good={
            <>
              the CDI file present and the LAN rule <code>active</code>. <code>/var/run</code> is
              tmpfs, so the GPU spec was written to <code>/etc/cdi</code> to survive; and Docker
              publishes ports by writing its own iptables rules, straight past ufw, so a separate
              unit keeps 8880, 8001 and 8002 away from anything outside{' '}
              <code>10.130.0.0/16</code>.
            </>
          }
        >
          <Cmd>{String.raw`ls -l /etc/cdi/nvidia.yaml
systemctl is-active docker-lan-only
sudo iptables -L DOCKER-USER -n --line-numbers | head -5
sudo ufw status | grep -E "8880|8001|8002"`}</Cmd>
        </Step>
        <Step
          n={3}
          title="Stop something for good"
          good={
            <>
              <code>down</code>, not <code>stop</code>. Every service here has{' '}
              <code>restart: unless-stopped</code>, and a stopped container comes back on the next
              reboot — Chatterbox held 5 GB of VRAM for three days that way after it had already
              lost.
            </>
          }
        >
          <Cmd>{String.raw`cd /srv/gpu-stack/qwen-asr && docker compose down`}</Cmd>
        </Step>
      </Section>

      <Section
        icon={Search}
        title="Logs and disk"
        when="It answered but the output was wrong, or the box is filling up."
      >
        <Step
          n={1}
          title="What each one has been asked for"
          good={
            <>
              Kokoro logs one <code>200 OK</code> per SENTENCE, not per answer — a dozen lines for
              one reply is normal. vLLM logs throughput and queue depth instead.
            </>
          }
        >
          <Cmd>{String.raw`docker logs kokoro --since 10m 2>&1 | tail -20
docker logs qwen-llm --since 10m 2>&1 | grep -E "Engine|Avg|throughput" | tail -10`}</Cmd>
        </Step>
        <Step
          n={2}
          title="Models and free space"
          good={
            <>
              models live under <code>/opt/models</code> so rebuilding an image does not re-download
              gigabytes — about 26 GB of weights sit there. Container logs are capped at 50 MB × 3
              each.
            </>
          }
        >
          <Cmd>{String.raw`df -h /
du -sh /opt/models/* 2>/dev/null
docker system df`}</Cmd>
        </Step>
      </Section>
    </>
  )
}

export function Debug() {
  const [callId, setCallId] = useState('')
  const [tab, setTab] = useState<'server' | 'gpu'>('server')
  const id = callId || '<call-id>'

  return (
    <div className={PAGE}>
      <PageHeader
        title="Debug"
        description="When something breaks: what to check, in order. Copy a command and run it on the server over SSH — this page never runs anything itself."
      />

      {/* Two machines, two sets of commands. Split because they are run in
          different SSH sessions on different hosts, and the one thing this
          page must never do is make it easy to run a command on the wrong
          box. */}
      <div className="flex gap-1 rounded-lg border border-border bg-card p-1">
        {([
          { key: 'server', label: 'Call server', icon: Server },
          { key: 'gpu', label: 'GPU box', icon: Cpu },
        ] as const).map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            aria-pressed={tab === key}
            className={cn(
              'flex flex-1 items-center justify-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              tab === key
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-muted hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4" />
            {label}
            <span className="text-2xs font-normal opacity-70">
              {key === 'server' ? ENV_LABEL[APP_ENV] : '10.130.9.248'}
            </span>
          </button>
        ))}
      </div>

      {tab === 'gpu' && <GpuTab />}

      {tab === 'server' && (
        <>
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
        </>
      )}
    </div>
  )
}
