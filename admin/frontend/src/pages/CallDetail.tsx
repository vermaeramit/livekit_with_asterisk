import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  ArrowRightLeft,
  BookOpen,
  Bot,
  Clock,
  Coins,
  Cpu,
  MessageSquareOff,
  PhoneIncoming,
  Scissors,
  TriangleAlert,
  User,
  Wrench,
} from 'lucide-react'
import { RecordingPlayer } from '@/components/RecordingPlayer'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { cn, formatDateTime, formatDuration, formatMs, formatNumber, formatPercent, latencyTone } from '@/lib/utils'
import type { CallDetail as CallDetailType, KbChunk, ToolInvocation, Turn } from '@/types'
import { EndReasonBadge } from './Calls'

function Stat({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: React.ReactNode
  hint?: string
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <p className="mt-2 tnum text-xl font-semibold leading-none">{value}</p>
      {hint && <p className="mt-1.5 text-2xs text-muted-foreground">{hint}</p>}
    </Card>
  )
}

/**
 * Stacked breakdown of one agent turn.
 *
 * The three segments are the only three things we can actually act on: `eou` is
 * our own turn detection, `llm_ttft` and `tts_ttfb` are the providers. When the
 * total drifts, this is what says whose fault it is.
 *
 * `stt_ms` is deliberately not a segment - it is already inside `eou`, and
 * adding it double-counts (a mistake this project has made before).
 */
function LatencyBar({ turn, max }: { turn: Turn; max: number }) {
  const segs = [
    { key: 'eou', label: 'Turn detection', ms: turn.eou_ms, cls: 'bg-primary/70' },
    { key: 'llm', label: 'LLM first token', ms: turn.llm_ttft_ms, cls: 'bg-warning/70' },
    { key: 'tts', label: 'TTS first byte', ms: turn.tts_ttfb_ms, cls: 'bg-success/70' },
  ].filter((s) => s.ms != null) as { key: string; label: string; ms: number; cls: string }[]

  if (!segs.length) return null
  const tone = latencyTone(turn.total_ms)

  return (
    <div className="mt-2 space-y-1">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-muted">
        {segs.map((s) => (
          <div
            key={s.key}
            className={s.cls}
            style={{ width: `${(s.ms / max) * 100}%` }}
            title={`${s.label}: ${formatMs(s.ms)}`}
          />
        ))}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-2xs text-muted-foreground">
        {segs.map((s) => (
          <span key={s.key} className="inline-flex items-center gap-1">
            <span className={cn('h-1.5 w-1.5 rounded-full', s.cls)} />
            {s.label} <span className="tnum text-foreground/70">{formatMs(s.ms)}</span>
          </span>
        ))}
        <span
          className={cn(
            'ml-auto tnum font-medium',
            tone === 'success' && 'text-success',
            tone === 'warning' && 'text-warning',
            tone === 'danger' && 'text-danger',
          )}
        >
          {formatMs(turn.total_ms)} total
        </span>
      </div>
    </div>
  )
}

function Citations({ turn, chunks }: { turn: Turn; chunks: Record<string, KbChunk> | undefined }) {
  const [open, setOpen] = useState(false)
  if (!turn.kb_chunk_ids?.length) return null

  return (
    <div className="mt-2">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-md bg-primary/10 px-2 py-1 text-2xs font-medium text-primary transition-colors hover:bg-primary/20"
      >
        <BookOpen className="h-3 w-3" />
        {turn.kb_chunk_ids.length} knowledge-base {turn.kb_chunk_ids.length === 1 ? 'source' : 'sources'}
        {open ? ' — hide' : ' — show'}
      </button>

      {open && (
        <div className="mt-2 space-y-2">
          {turn.kb_chunk_ids.map((id, i) => {
            const chunk = chunks?.[String(id)]
            const score = turn.kb_scores?.[i]
            return (
              <div key={id} className="rounded-md border border-border bg-muted/40 p-2.5">
                <div className="flex items-center justify-between gap-2 text-2xs text-muted-foreground">
                  <span className="truncate font-medium text-foreground/80">
                    {chunk ? (chunk.title || chunk.filename) : `chunk #${id}`}
                    {chunk?.heading ? ` · ${chunk.heading}` : ''}
                    {chunk?.page != null ? ` · p.${chunk.page}` : ''}
                  </span>
                  {score != null && (
                    <Badge tone={score >= 0.5 ? 'success' : 'muted'} className="tnum shrink-0">
                      {score.toFixed(3)}
                    </Badge>
                  )}
                </div>
                {chunk && (
                  <p className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                    {chunk.content.length > 700
                      ? `${chunk.content.slice(0, 700)}…`
                      : chunk.content}
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * One HTTP tool call, shown in line with the transcript.
 *
 * In line rather than in a table of its own, because the question is never
 * "what tools ran" — it is "the caller asked X, why did the agent answer Y".
 * That is only answerable next to the turns either side of it.
 *
 * The arguments are the part worth reading. A tool that "did not work" is
 * usually a tool the model called with a wrong or empty argument, and the
 * transcript alone never shows that.
 */
function ToolRow({ tool }: { tool: ToolInvocation }) {
  const [open, setOpen] = useState(false)
  const failed = Boolean(tool.error) || (tool.status_code ?? 0) >= 400
  const timedOut = tool.error === 'timeout'
  const args = Object.entries(tool.arguments ?? {})

  return (
    <div className="flex gap-3 bg-muted/25 px-4 py-2.5">
      <div
        className={cn(
          'mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full',
          failed ? 'bg-danger/15 text-danger' : 'bg-muted text-muted-foreground',
        )}
      >
        <Wrench className="h-3.5 w-3.5" />
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-muted-foreground">
          <span className="font-mono text-xs font-medium text-foreground/80">{tool.name}</span>
          <span className="tnum">{formatDateTime(tool.created_at)}</span>

          {timedOut ? (
            <Badge tone="danger">timed out</Badge>
          ) : tool.error ? (
            <Badge tone="danger">failed</Badge>
          ) : (
            <Badge tone={failed ? 'danger' : 'success'} className="tnum">
              HTTP {tool.status_code ?? '—'}
            </Badge>
          )}

          <span className="tnum">{formatMs(tool.duration_ms)}</span>
        </div>

        {args.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1">
            {args.map(([k, v]) => (
              <span
                key={k}
                className="inline-flex max-w-full items-center gap-1 rounded-md bg-background px-1.5 py-0.5 text-2xs ring-1 ring-inset ring-border"
              >
                <span className="text-muted-foreground">{k}</span>
                <span className="truncate font-mono text-foreground/90">
                  {typeof v === 'string' ? v || '(empty)' : JSON.stringify(v)}
                </span>
              </span>
            ))}
          </div>
        )}

        {timedOut && (
          <p className="mt-1.5 text-2xs text-danger">
            The caller heard silence for this long, and the agent had to answer without the data.
          </p>
        )}
        {tool.error && !timedOut && (
          <button
            onClick={() => setOpen((o) => !o)}
            className="mt-1.5 block max-w-full truncate text-left text-2xs text-danger hover:underline"
          >
            {open ? tool.error : tool.error.slice(0, 120)}
          </button>
        )}
      </div>
    </div>
  )
}

function TurnRow({
  turn,
  chunks,
  max,
}: {
  turn: Turn
  chunks: Record<string, KbChunk> | undefined
  max: number
}) {
  const isAgent = turn.role !== 'user'
  return (
    <div className="flex gap-3 px-4 py-3">
      <div
        className={cn(
          'mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full',
          isAgent ? 'bg-primary/15 text-primary' : 'bg-muted text-muted-foreground',
        )}
      >
        {isAgent ? <Bot className="h-3.5 w-3.5" /> : <User className="h-3.5 w-3.5" />}
      </div>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-2xs text-muted-foreground">
          <span className="font-medium text-foreground/80">{isAgent ? 'Agent' : 'Caller'}</span>
          <span className="tnum">{formatDateTime(turn.ts)}</span>
          {turn.interrupted && (
            <Badge tone="warning">
              <Scissors className="h-3 w-3" />
              barged in
            </Badge>
          )}
        </div>

        <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed">
          {turn.text || <span className="italic text-muted-foreground">(no transcript)</span>}
        </p>

        <LatencyBar turn={turn} max={max} />
        <Citations turn={turn} chunks={chunks} />
      </div>
    </div>
  )
}

/**
 * Mirrors the agent's own split — agent/voice_agent.py, _PROMPT_ATTRS against
 * _RECORD_ONLY_ATTRS. Worth showing, because "the model knew the caller's name
 * but was never told the lead id" is the difference between a prompt bug and a
 * dialler bug, and both look identical in a transcript.
 */
const DIALLER_FIELDS: Record<string, { label: string; toModel: boolean }> = {
  'dialer.cus_name': { label: 'Caller name', toModel: true },
  'dialer.modalname': { label: 'Product they own', toModel: true },
  'dialer.calltype': { label: 'Call type', toModel: true },
  'dialer.lead_id': { label: 'Lead ID', toModel: false },
  'dialer.sr_id': { label: 'Service request', toModel: false },
  'dialer.call_unique': { label: 'Dialler call ID', toModel: false },
  'dialer.language': { label: 'Language requested', toModel: false },
}

function DiallerCard({ ctx }: { ctx: Record<string, string> }) {
  const rows = Object.entries(ctx).map(([k, v]) => ({
    // An unknown key still renders: the dialler added seven fields once without
    // telling anyone, and the next one should be visible without a deploy.
    ...(DIALLER_FIELDS[k] ?? { label: k.replace(/^dialer\./, '').replace(/_/g, ' '), toModel: false }),
    key: k,
    value: v,
  }))

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <PhoneIncoming className="h-3.5 w-3.5 text-muted-foreground" />
          From the dialler
        </CardTitle>
      </CardHeader>
      <CardBody className="space-y-1.5 text-sm">
        {rows.map((r) => (
          <div key={r.key} className="flex items-baseline justify-between gap-4 border-b border-border/40 pb-1.5">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              {r.label}
              {r.toModel && (
                <span
                  className="rounded bg-primary/10 px-1 py-px text-2xs font-medium text-primary"
                  title="Given to the model, so the agent could use it in conversation"
                >
                  in prompt
                </span>
              )}
            </span>
            <span className="truncate text-right font-medium">{r.value}</span>
          </div>
        ))}
        <p className="pt-1 text-2xs text-muted-foreground">
          Identifiers are stored but never shown to the model — a model given a lead ID will
          eventually read it out to the caller.
        </p>
      </CardBody>
    </Card>
  )
}

/**
 * What actually served the call, always — not only when a fallback fired.
 *
 * "Which voice was this call?" was previously answered by finding the campaign,
 * checking what it was set to today, and hoping nobody had changed it since.
 */
function ProvidersCard({ c }: { c: CallDetailType }) {
  const rows: [string, string | null][] = [
    ['Speech to text', c.stt_provider_used],
    ['Language model', c.llm_provider_used],
    ['Voice', c.tts_provider_used],
  ]

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Cpu className="h-3.5 w-3.5 text-muted-foreground" />
          Handled by
        </CardTitle>
      </CardHeader>
      <CardBody className="space-y-1.5 text-sm">
        {rows.map(([label, v]) => {
          // A comma means the primary failed partway and the fallback took over.
          const chain = v ? v.split(',').filter(Boolean) : []
          return (
            <div key={label} className="flex items-baseline justify-between gap-4 border-b border-border/40 pb-1.5">
              <span className="text-muted-foreground">{label}</span>
              {chain.length === 0 ? (
                <span className="text-muted-foreground">—</span>
              ) : (
                <span className="flex items-center gap-1 font-medium">
                  {chain.map((p, i) => (
                    <span key={`${p}-${i}`} className="flex items-center gap-1">
                      {i > 0 && <span className="text-warning">→</span>}
                      <span className={cn(i > 0 && 'text-warning')}>{p}</span>
                    </span>
                  ))}
                </span>
              )}
            </div>
          )
        })}
        <p className="pt-1 text-2xs text-muted-foreground">
          Recorded per call, so this stays true even after the campaign is changed.
        </p>
      </CardBody>
    </Card>
  )
}

export function CallDetail() {
  const { id } = useParams<{ id: string }>()

  const call = useQuery({
    queryKey: ['call', id],
    queryFn: () => api<CallDetailType>(`/calls/${id}`),
    // A call with no ended_at is still in progress. Poll so the transcript grows
    // while you watch it, and stop the moment it ends rather than polling every
    // finished call in the archive forever.
    refetchInterval: (q) => (q.state.data?.ended_at ? false : 3000),
  })

  const chunks = useQuery({
    queryKey: ['call-kb', id],
    queryFn: () => api<Record<string, KbChunk>>(`/calls/${id}/kb-chunks`),
    // only fetch once we know the transcript actually cites something
    enabled: Boolean(call.data?.turns.some((t) => t.kb_chunk_ids?.length)),
  })

  if (call.isLoading) {
    return (
      <div className="mx-auto max-w-5xl space-y-5 p-5 lg:p-7">
        <Skeleton className="h-8 w-48" />
        <div className="grid gap-3 sm:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <Skeleton className="h-96" />
      </div>
    )
  }

  if (call.isError || !call.data) {
    return (
      <div className="mx-auto max-w-5xl p-6">
        <EmptyState
          icon={TriangleAlert}
          title="Call not found"
          hint={(call.error as Error | null)?.message ?? 'It may belong to another tenant.'}
          action={
            <Link to="/calls">
              <Button size="sm" variant="outline">
                Back to calls
              </Button>
            </Link>
          }
        />
      </div>
    )
  }

  const c = call.data
  const timed = c.turns.filter((t) => t.total_ms != null)
  const sorted = [...timed].map((t) => t.total_ms!).sort((a, b) => a - b)
  const p50 = sorted.length ? sorted[Math.floor(sorted.length / 2)] : null
  const worst = sorted.length ? sorted[sorted.length - 1] : null
  // scale every bar against the slowest turn so they are comparable down the page
  const barMax = Math.max(
    1,
    ...c.turns.map((t) => (t.eou_ms ?? 0) + (t.llm_ttft_ms ?? 0) + (t.tts_ttfb_ms ?? 0)),
  )

  const cached = c.usage.llm_prompt_cached_tokens ?? 0
  const prompt = c.usage.llm_prompt_tokens ?? 0
  const cacheRate = prompt > 0 ? (cached / prompt) * 100 : null

  // Turns and tool calls in one list, ordered by time. Ties go to the turn: a
  // tool recorded in the same millisecond as a turn was triggered BY it.
  const tools = c.tools ?? []
  const timeline = [
    ...c.turns.map((t) => ({ kind: 'turn' as const, at: Date.parse(t.ts), turn: t })),
    ...tools.map((t) => ({ kind: 'tool' as const, at: Date.parse(t.created_at), tool: t })),
  ].sort((a, b) => a.at - b.at || (a.kind === 'turn' ? -1 : 1))
  const toolsFailed = tools.filter((t) => t.error || (t.status_code ?? 0) >= 400).length

  return (
    <div className="mx-auto max-w-5xl space-y-5 p-5 lg:p-7">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link
            to="/calls"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            All calls
          </Link>
          <h1 className="mt-1.5 flex flex-wrap items-center gap-2 text-xl font-semibold tracking-tight">
            <span className="tnum">{c.caller ?? 'unknown'}</span>
            <span className="text-muted-foreground">→</span>
            <span className="tnum">{c.callee ?? 'unknown'}</span>
            <EndReasonBadge call={c} />
          </h1>
          {!c.ended_at && (
            <span className="mt-1.5 inline-flex items-center gap-1.5 rounded-full bg-success/10 px-2 py-0.5 text-2xs font-medium text-success ring-1 ring-inset ring-success/25">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-success" />
              </span>
              in progress
            </span>
          )}
          <p className="mt-0.5 text-xs text-muted-foreground">
            {formatDateTime(c.started_at)}
            {c.campaign_name ? ` · ${c.campaign_name}` : ''}
            {c.language ? ` · ${c.language}` : ''}
            {c.room_name ? ` · ${c.room_name}` : ''}
          </p>
        </div>
      </div>

      {c.transferred_to && (
        <Card className="border-primary/30 bg-primary/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <ArrowRightLeft className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
            <div>
              <p className="font-medium">Handed to a human</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Transferred to <span className="tnum">{c.transferred_to}</span>
                {c.transfer_reason ? ` — “${c.transfer_reason}”` : ''}
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* A comma in any of these means the primary provider failed mid-call and
          the fallback took over. Before this existed, the only evidence was a
          resampling line in the worker journal - twenty minutes to find, and
          only because we already suspected it. */}
      {[
        ['Speech to text', c.stt_provider_used],
        ['Language model', c.llm_provider_used],
        ['Voice', c.tts_provider_used],
      ].some(([, v]) => typeof v === 'string' && v.includes(',')) && (
        <Card className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div>
              <p className="font-medium">A provider fallback fired during this call</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {([
                  ['Speech to text', c.stt_provider_used],
                  ['Language model', c.llm_provider_used],
                  ['Voice', c.tts_provider_used],
                ] as [string, string | null][])
                  .filter(([, v]) => v?.includes(','))
                  .map(([label, v]) => `${label}: ${v!.split(',').join(' → ')}`)
                  .join(' · ')}
                . The call carried on, but the primary was failing — check that
                provider's key and credits.
              </p>
            </div>
          </div>
        </Card>
      )}

      {/* Treated like the fallback and guardrail banners because it has the
          same shape: the call completed, so nothing looks wrong from the
          outside, but the caller was given an apology instead of an answer. */}
      {toolsFailed > 0 && (
        <Card className="border-danger/30 bg-danger/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
            <div>
              <p className="font-medium">
                {toolsFailed} tool call{toolsFailed === 1 ? '' : 's'} failed during this call
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {tools
                  .filter((t) => t.error || (t.status_code ?? 0) >= 400)
                  .map((t) => `${t.name}: ${t.error ?? `HTTP ${t.status_code}`}`)
                  .join(' · ')}
                . The agent had to answer without that data — see the transcript below for what it
                said instead.
              </p>
            </div>
          </div>
        </Card>
      )}

      {c.limit_hit && (
        <Card className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div>
              <p className="font-medium">Stopped by a guardrail</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                The <span className="font-mono">{c.limit_hit}</span> limit was reached, so the call
                was ended deliberately rather than running up cost.
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          icon={Clock}
          label="Duration"
          value={formatDuration(c.duration_ms)}
          hint={`${c.turn_count ?? c.turns.length} turns`}
        />
        <Stat
          icon={Clock}
          label="Median response"
          value={formatMs(p50)}
          hint={worst ? `slowest ${formatMs(worst)}` : undefined}
        />
        <Stat
          icon={Coins}
          label="Prompt tokens"
          value={formatNumber(prompt)}
          hint={cacheRate != null ? `${formatPercent(cacheRate)} served from cache` : undefined}
        />
        <Stat
          icon={Coins}
          label="Completion tokens"
          value={formatNumber(c.usage.llm_completion_tokens)}
          hint={
            c.usage.tts_characters != null
              ? `${formatNumber(c.usage.tts_characters)} TTS chars`
              : undefined
          }
        />
      </div>

      {c.recording_available && (
        <RecordingPlayer callId={c.id} sizeBytes={c.recording_bytes} />
      )}

      <div className={cn('grid gap-3', c.dialer_context && 'lg:grid-cols-2')}>
        {c.dialer_context && <DiallerCard ctx={c.dialer_context} />}
        <ProvidersCard c={c} />
      </div>

      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-2">
          <CardTitle>Transcript</CardTitle>
          <span className="flex items-center gap-2 text-2xs text-muted-foreground">
            <span>
              {timed.length} timed turn{timed.length === 1 ? '' : 's'}
            </span>
            {tools.length > 0 && (
              <Badge tone={toolsFailed ? 'danger' : 'muted'}>
                <Wrench className="h-3 w-3" />
                {tools.length} tool call{tools.length === 1 ? '' : 's'}
                {toolsFailed ? ` · ${toolsFailed} failed` : ''}
              </Badge>
            )}
          </span>
        </CardHeader>
        {timeline.length === 0 ? (
          <EmptyState
            icon={MessageSquareOff}
            title="No transcript"
            hint="The call ended before any turn was recorded."
          />
        ) : (
          <div className="divide-y divide-border/60">
            {timeline.map((e) =>
              e.kind === 'turn' ? (
                <TurnRow key={`t${e.turn.seq}`} turn={e.turn} chunks={chunks.data} max={barMax} />
              ) : (
                <ToolRow key={`x${e.tool.id}`} tool={e.tool} />
              ),
            )}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Usage</CardTitle>
        </CardHeader>
        <CardBody className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
          {(
            [
              ['LLM prompt tokens', formatNumber(c.usage.llm_prompt_tokens)],
              ['— of which cached', formatNumber(c.usage.llm_prompt_cached_tokens)],
              ['LLM completion tokens', formatNumber(c.usage.llm_completion_tokens)],
              ['TTS characters', formatNumber(c.usage.tts_characters)],
              [
                'TTS audio',
                c.usage.tts_audio_seconds != null ? `${c.usage.tts_audio_seconds.toFixed(1)}s` : '—',
              ],
              [
                'STT audio',
                c.usage.stt_audio_seconds != null ? `${c.usage.stt_audio_seconds.toFixed(1)}s` : '—',
              ],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="flex justify-between gap-4 border-b border-border/40 pb-1.5">
              <span className="text-muted-foreground">{label}</span>
              <span className="tnum font-medium">{value}</span>
            </div>
          ))}
        </CardBody>
      </Card>
    </div>
  )
}
