import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { PhoneOff, Radio, TriangleAlert } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Badge, Card, EmptyState, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatMs, latencyTone } from '@/lib/utils'
import type { LiveCall, LiveSummary } from '@/types'

const POLL_MS = 3000

function elapsed(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = sec % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function Row({ call, onOpen }: { call: LiveCall; onOpen: () => void }) {
  const tone = latencyTone(call.last_latency_ms)
  // Past the guardrail the call will be cut, so show how close it is.
  const progress = Math.min(100, (call.elapsed_sec / call.max_duration_sec) * 100)

  return (
    <div
      onClick={onOpen}
      className="flex cursor-pointer flex-wrap items-center gap-x-4 gap-y-2 border-b border-border/70 px-4 py-3 transition-colors last:border-0 hover:bg-accent/50"
    >
      <span className="relative flex h-2 w-2 shrink-0">
        {!call.stale && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-75" />
        )}
        <span
          className={cn(
            'relative inline-flex h-2 w-2 rounded-full',
            call.stale ? 'bg-warning' : 'bg-success',
          )}
        />
      </span>

      <div className="min-w-[9rem]">
        <p className="tnum text-sm font-medium">
          {call.caller ?? 'unknown'} <span className="text-muted-foreground">→</span>{' '}
          {call.callee ?? '—'}
        </p>
        <p className="text-2xs text-muted-foreground">{call.campaign_name ?? 'unrouted'}</p>
      </div>

      <div className="min-w-[5rem]">
        <p className="tnum text-sm">{elapsed(call.elapsed_sec)}</p>
        <div className="mt-1 h-1 w-16 overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              'h-full rounded-full',
              progress > 85 ? 'bg-warning' : 'bg-primary',
            )}
            style={{ width: `${progress}%` }}
          />
        </div>
      </div>

      <div className="min-w-[4rem]">
        <p className="tnum text-2xs text-muted-foreground">{call.turn_count} turns</p>
        <p
          className={cn(
            'tnum text-sm',
            tone === 'success' && 'text-success',
            tone === 'warning' && 'text-warning',
            tone === 'danger' && 'text-danger',
          )}
        >
          {formatMs(call.last_latency_ms)}
        </p>
      </div>

      <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
        {call.last_text ?? '—'}
      </p>

      {call.stale && (
        <Badge tone="warning" title="Open far past its duration limit — likely a worker that died mid-call">
          <TriangleAlert className="h-3 w-3" />
          stale
        </Badge>
      )}
    </div>
  )
}

export function Live() {
  const navigate = useNavigate()
  const { user } = useAuth()

  const live = useQuery({
    queryKey: ['live-calls'],
    queryFn: () => api<LiveSummary>('/live/calls'),
    refetchInterval: POLL_MS,
    // Polling every 3s already keeps it fresh; refetching on focus as well just
    // doubles the queries when someone tabs back and forth.
    refetchOnWindowFocus: false,
  })

  const data = live.data
  const overCapacity = (data?.active ?? 0) > (data?.verified_capacity ?? 10)

  return (
    <div className={PAGE}>
      <PageHeader
        title="Live monitor"
        description={
          data
            ? `${data.active} call${data.active === 1 ? '' : 's'} in progress`
            : 'Loading…'
        }
        actions={
          <span className="flex items-center gap-1.5 text-2xs text-muted-foreground">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
            </span>
            updating every {POLL_MS / 1000}s
          </span>
        }
      />

      {overCapacity && (
        <Card className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div>
              <p className="font-medium">
                {data!.active} concurrent calls — above the {data!.verified_capacity} that
                have been load-tested
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                Not a failure, but latency past this point is unmeasured. Watch the p95 on
                the dashboard.
              </p>
            </div>
          </div>
        </Card>
      )}

      {(data?.stale ?? 0) > 0 && (
        <Card className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div>
              <p className="font-medium">
                {data!.stale} call{data!.stale === 1 ? '' : 's'} open past their duration
                limit
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                A call is only marked ended when the worker writes it. These are almost
                certainly workers that died mid-call — check{' '}
                <code>journalctl -u aivoice-agent@1</code> for a traceback.
              </p>
            </div>
          </div>
        </Card>
      )}

      <Card className="overflow-hidden">
        {live.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : live.isError ? (
          <EmptyState
            icon={TriangleAlert}
            title="Could not load live calls"
            hint={(live.error as Error).message}
            action={
              <Button size="sm" variant="outline" onClick={() => live.refetch()}>
                Try again
              </Button>
            }
          />
        ) : !data?.calls.length ? (
          <EmptyState
            icon={PhoneOff}
            title="No calls in progress"
            hint={
              user?.role === 'superadmin'
                ? 'Calls appear here the moment a worker picks one up.'
                : 'Calls to your campaigns appear here as they connect.'
            }
          />
        ) : (
          <div>
            {data.calls.map((c) => (
              <Row key={c.id} call={c} onOpen={() => navigate(`/calls/${c.id}`)} />
            ))}
          </div>
        )}
      </Card>

      <p className="flex items-center gap-1.5 text-2xs text-muted-foreground">
        <Radio className="h-3 w-3" />
        Rows come from the call log, so a turn appears here as soon as it is written —
        a second or two behind the caller.
      </p>
    </div>
  )
}
