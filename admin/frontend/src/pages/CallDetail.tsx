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
  MessageSquareOff,
  Scissors,
  TriangleAlert,
  User,
} from 'lucide-react'
import { RecordingPlayer } from '@/components/RecordingPlayer'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { cn, formatDateTime, formatDuration, formatMs, formatNumber, formatPercent, latencyTone } from '@/lib/utils'
import type { CallDetail as CallDetailType, KbChunk, Turn } from '@/types'
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

      <Card>
        <CardHeader className="flex items-center justify-between">
          <CardTitle>Transcript</CardTitle>
          <span className="text-2xs text-muted-foreground">
            {timed.length} timed turn{timed.length === 1 ? '' : 's'}
          </span>
        </CardHeader>
        {c.turns.length === 0 ? (
          <EmptyState
            icon={MessageSquareOff}
            title="No transcript"
            hint="The call ended before any turn was recorded."
          />
        ) : (
          <div className="divide-y divide-border/60">
            {c.turns.map((t) => (
              <TurnRow key={t.seq} turn={t} chunks={chunks.data} max={barMax} />
            ))}
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
