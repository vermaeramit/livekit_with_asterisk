import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  KeyRound,
  LogIn,
  Send,
  SlidersHorizontal,
  Wrench,
} from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Badge, Card, EmptyState, Select, Skeleton } from '@/components/ui/primitives'
import { api, buildQuery } from '@/lib/api'
import { cn, formatDateTime, formatRelative } from '@/lib/utils'
import type { ActivityFeed, ActivityEvent } from '@/types'

/**
 * Everything that asked for attention, in one place.
 *
 * Six things recorded themselves and each lived on its own page or on no page
 * at all. "What happened yesterday afternoon" meant opening five tabs and
 * knowing which five.
 *
 * Calls are deliberately not here. There are hundreds of them and they would
 * bury the rest — which is how an activity feed that shows all activity ends up
 * showing none of it. The Calls page is better at calls.
 */

const KINDS = [
  { value: '', label: 'Everything' },
  { value: 'alert', label: 'Alerts' },
  { value: 'error', label: 'Provider failures' },
  { value: 'config', label: 'Configuration changes' },
  { value: 'postback', label: 'Postbacks not delivered' },
  { value: 'tool', label: 'Tool failures' },
  { value: 'login', label: 'Sign-ins' },
]

const WINDOWS = [
  { value: '6', label: 'Last 6 hours' },
  { value: '24', label: 'Last 24 hours' },
  { value: '72', label: 'Last 3 days' },
  { value: '168', label: 'Last week' },
  { value: '720', label: 'Last 30 days' },
]

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  alert: AlertTriangle,
  error: AlertTriangle,
  config: SlidersHorizontal,
  postback: Send,
  tool: Wrench,
  login: LogIn,
}

function tone(e: ActivityEvent): 'danger' | 'warning' | 'muted' {
  if (e.severity === 'critical') return 'danger'
  if (e.severity === 'warning') return 'warning'
  return 'muted'
}

export function Activity() {
  const [kind, setKind] = useState('')
  const [hours, setHours] = useState('24')

  const q = useQuery({
    queryKey: ['activity', kind, hours],
    queryFn: () =>
      api<ActivityFeed>(`/activity${buildQuery({ kind: kind || undefined, hours })}`),
    // Ten seconds, not two. This reads the same database the agent writes
    // turns into during live calls, and a page left open on three screens all
    // day is the one part of this that could ever be felt on a call.
    refetchInterval: 10_000,
    // And not at all while nobody is looking at it.
    refetchIntervalInBackground: false,
  })

  const events = q.data?.events ?? []

  return (
    <div className={PAGE}>
      <PageHeader
        title="Activity"
        description="Everything that asked for attention — alerts, failures, changes and sign-ins."
      />

      <Card className="p-4">
        <div className="flex flex-wrap gap-4">
          <div className="min-w-48 flex-1 space-y-1.5">
            <label className="text-2xs font-medium text-muted-foreground" htmlFor="kind">
              Show
            </label>
            <Select id="kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </Select>
          </div>
          <div className="min-w-48 flex-1 space-y-1.5">
            <label className="text-2xs font-medium text-muted-foreground" htmlFor="window">
              When
            </label>
            <Select id="window" value={hours} onChange={(e) => setHours(e.target.value)}>
              {WINDOWS.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
      </Card>

      {q.isLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-14 w-full" />
          ))}
        </div>
      ) : !events.length ? (
        <EmptyState
          icon={KeyRound}
          title="Nothing to report"
          hint="No alerts, failures or changes in this window. On a quiet system that is the right answer."
        />
      ) : (
        <Card className="overflow-hidden">
          {events.map((e, i) => {
            const Icon = ICONS[e.kind] ?? AlertTriangle
            const t = tone(e)
            return (
              <div
                key={`${e.kind}-${e.at}-${i}`}
                className={cn(
                  'flex gap-3 border-b border-border/50 px-4 py-3 last:border-0',
                  t === 'danger' && 'bg-destructive/[0.03]',
                )}
              >
                <Icon
                  className={cn(
                    'mt-0.5 h-4 w-4 shrink-0',
                    t === 'danger'
                      ? 'text-destructive'
                      : t === 'warning'
                        ? 'text-amber-600 dark:text-amber-500'
                        : 'text-muted-foreground',
                  )}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs font-medium">{e.title}</span>
                    {e.campaign && <Badge tone="muted">{e.campaign}</Badge>}
                    {e.extra && (
                      <span className="text-2xs text-muted-foreground">{e.extra}</span>
                    )}
                  </div>
                  {e.detail && (
                    <p className="mt-0.5 break-words text-2xs leading-relaxed text-muted-foreground">
                      {e.detail}
                    </p>
                  )}
                  {e.actor && (
                    <p className="mt-0.5 text-2xs text-muted-foreground">by {e.actor}</p>
                  )}
                </div>
                {/* Relative first, because "20 minutes ago" is how somebody
                    reads a feed; the timestamp is how they match it against a
                    call that went wrong. */}
                <div className="shrink-0 text-right">
                  <p className="text-2xs text-muted-foreground">{formatRelative(e.at)}</p>
                  <p className="text-2xs text-muted-foreground/70">{formatDateTime(e.at)}</p>
                </div>
              </div>
            )
          })}
        </Card>
      )}

      <p className="text-2xs leading-relaxed text-muted-foreground">
        Calls are not in this list. There are hundreds of them and they would bury
        everything else — the <strong>Calls</strong> page is where they live. What is
        here is what asks for attention.
      </p>
    </div>
  )
}
