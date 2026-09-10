import { useQuery } from '@tanstack/react-query'
import { Check, HardDrive, PhoneCall, Send, Server, X } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Badge, Card, CardBody, CardHeader, CardTitle, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { cn, formatDateTime, formatNumber, formatRelative } from '@/lib/utils'
import type { SystemHealth } from '@/types'

/**
 * Is everything running — answerable here instead of over SSH.
 *
 * What it will not do is show a green tick it cannot justify. Asterisk is
 * native and listens only on UDP, so there is no socket to connect to and no
 * honest check to make; it gets "last call received", which is a fact rather
 * than a verdict. A monitoring page that says everything is fine without
 * knowing is worse than no page, because it is believed.
 */

function Dot({ ok }: { ok: boolean }) {
  return (
    <span
      className={cn(
        'flex h-4 w-4 shrink-0 items-center justify-center rounded-full',
        ok ? 'bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
           : 'bg-destructive/15 text-destructive',
      )}
    >
      {ok ? <Check className="h-2.5 w-2.5" /> : <X className="h-2.5 w-2.5" />}
    </span>
  )
}

export function System() {
  const q = useQuery({
    queryKey: ['system-health'],
    queryFn: () => api<SystemHealth>('/system/health'),
    // Fifteen seconds. Every refresh opens nine sockets and reads the calls
    // table; this shares a database with live calls, and a status page that
    // slowed them down would be measuring a problem it caused.
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  })

  if (q.isLoading) {
    return (
      <div className={PAGE}>
        <PageHeader title="System" description="Is everything running." />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  if (q.isError) {
    return (
      <div className={PAGE}>
        <PageHeader title="System" description="Is everything running." />
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs">
          {(q.error as Error).message}
        </div>
      </div>
    )
  }

  const h = q.data!
  const workers = h.checks.filter((c) => c.kind === 'worker')
  const services = h.checks.filter((c) => c.kind === 'service')
  const down = h.checks.filter((c) => !c.ok).length

  return (
    <div className={PAGE}>
      <PageHeader
        title="System"
        description="Is everything running — without opening a terminal."
      />

      {down > 0 && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs">
          <strong className="font-medium">
            {down} of {h.checks.length} not reachable.
          </strong>{' '}
          A worker that is down means fewer calls at once, not none — the others take
          them. A service that is down means no calls at all.
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              Agent workers
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-1.5">
            {workers.map((c) => (
              <div key={c.name} className="flex items-center gap-2 text-xs">
                <Dot ok={c.ok} />
                <span className="flex-1">{c.name}</span>
                <span className="text-2xs text-muted-foreground">{c.detail}</span>
              </div>
            ))}
            <p className="pt-1 text-2xs leading-relaxed text-muted-foreground">
              Each worker holds its own port, so this is per worker rather than one
              answer for all six. Reachable means the process is up and listening —
              not that it is taking calls.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="h-4 w-4 text-muted-foreground" />
              Services
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-1.5">
            {services.map((c) => (
              <div key={c.name} className="flex items-center gap-2 text-xs">
                <Dot ok={c.ok} />
                <span className="w-24 shrink-0">{c.name}</span>
                <span className="flex-1 text-2xs text-muted-foreground">{c.detail}</span>
              </div>
            ))}
            {/* The honest gap, stated where somebody would otherwise wonder why
                Asterisk is missing from a list of services. */}
            <div className="flex items-start gap-2 pt-1.5 text-2xs leading-relaxed text-muted-foreground">
              <PhoneCall className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                <strong className="font-medium text-foreground">Asterisk and Redis are
                not in this list</strong>, because neither can honestly be checked from
                here — Asterisk listens only on UDP, and Redis is published on the
                host&rsquo;s own loopback and deliberately reachable by nothing else. A red
                cross would say &ldquo;broken&rdquo; where the truth is &ldquo;cannot
                see&rdquo;. What <em>can</em> be said is when Asterisk last delivered a
                call:{' '}
                {h.last_call_at ? (
                  <>
                    <strong className="font-medium text-foreground">
                      {formatRelative(h.last_call_at)}
                    </strong>{' '}
                    ({formatDateTime(h.last_call_at)}). On a quiet evening a long gap
                    means nothing; at three in the afternoon it means everything.
                  </>
                ) : (
                  <strong className="font-medium text-foreground">never</strong>
                )}
              </span>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-muted-foreground" />
              Disk
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-2">
            {h.disks.map((d) => (
              <div key={d.path} className="space-y-1">
                <div className="flex items-center gap-2 text-xs">
                  <Dot ok={d.ok} />
                  <span className="flex-1">{d.name}</span>
                  <span className="tnum text-2xs text-muted-foreground">
                    {d.total_gb
                      ? `${formatNumber(d.free_gb)} GB free of ${formatNumber(d.total_gb)} · ${d.free_pct}%`
                      : 'not mounted'}
                  </span>
                </div>
                {d.total_gb > 0 && (
                  <div className="h-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn('h-full', d.ok ? 'bg-primary' : 'bg-destructive')}
                      style={{ width: `${Math.max(0, 100 - d.free_pct)}%` }}
                    />
                  </div>
                )}
              </div>
            ))}
            <p className="text-2xs leading-relaxed text-muted-foreground">
              Recordings grow with every call and nothing deletes them on its own.
              A full disk stops them being written, and the call still happens.
            </p>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Send className="h-4 w-4 text-muted-foreground" />
              Waiting to be delivered
            </CardTitle>
          </CardHeader>
          <CardBody className="space-y-3">
            <div className="flex gap-6">
              <div>
                <p className="tnum text-xl font-semibold">
                  {formatNumber(h.postbacks_pending)}
                </p>
                <p className="text-2xs text-muted-foreground">pending</p>
              </div>
              <div>
                <p
                  className={cn(
                    'tnum text-xl font-semibold',
                    h.postbacks_failed > 0 && 'text-destructive',
                  )}
                >
                  {formatNumber(h.postbacks_failed)}
                </p>
                <p className="text-2xs text-muted-foreground">given up on</p>
              </div>
            </div>
            <p className="text-2xs leading-relaxed text-muted-foreground">
              Finished calls on their way to the customer&rsquo;s system. Pending is
              normal and clears itself; anything in the second column is a call whose
              result never arrived.
            </p>
          </CardBody>
        </Card>

        {h.slots.length > 0 && (
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Call slots in use</CardTitle>
            </CardHeader>
            <CardBody className="space-y-2">
              {h.slots.map((s) => (
                <div key={s.campaign} className="space-y-1">
                  <div className="flex items-center gap-2 text-xs">
                    <span className="flex-1">{s.campaign}</span>
                    <Badge tone={s.in_use >= s.limit ? 'warning' : 'muted'}>
                      {s.in_use} of {s.limit}
                    </Badge>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn(
                        'h-full',
                        s.in_use >= s.limit ? 'bg-amber-500' : 'bg-primary',
                      )}
                      style={{
                        width: `${Math.min(100, (s.in_use / Math.max(1, s.limit)) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
              <p className="text-2xs leading-relaxed text-muted-foreground">
                Only campaigns with a limit set appear here. At the limit, further
                callers hear the hold message and are connected as slots free — they
                cost nothing in speech, language or voice while they wait.
              </p>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  )
}
