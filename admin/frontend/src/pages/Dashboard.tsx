import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  ArrowRightLeft,
  Clock,
  Coins,
  PhoneCall,
  Timer,
  TriangleAlert,
  Wallet,
} from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, CardTitle, EmptyState, Select, Skeleton } from '@/components/ui/primitives'
import { api, buildQuery } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDuration, formatMs, formatNumber, formatPercent, latencyTone } from '@/lib/utils'
import type { AnalyticsSummary, Campaign, TimeBucket } from '@/types'

const RANGES = [
  { days: 1, label: 'Last 24 hours' },
  { days: 7, label: 'Last 7 days' },
  { days: 30, label: 'Last 30 days' },
  { days: 90, label: 'Last 90 days' },
]

// Kept consistent with the call-detail latency bar, so the same stage is the
// same colour wherever it appears.
const STAGE = {
  eou_ms: { label: 'Turn detection', color: 'hsl(var(--primary))' },
  llm_ttft_ms: { label: 'LLM first token', color: 'hsl(var(--warning))' },
  tts_ttfb_ms: { label: 'TTS first byte', color: 'hsl(var(--success))' },
}

const REASON_COLOR: Record<string, string> = {
  completed: 'hsl(var(--success))',
  transferred: 'hsl(var(--primary))',
  limit: 'hsl(var(--warning))',
  error: 'hsl(var(--danger))',
  unknown: 'hsl(var(--muted-foreground))',
}

/** latencyTone() also returns 'muted' for "no data", which is not a colour here. */
function tone(ms: number | null | undefined) {
  const t = latencyTone(ms)
  return t === 'muted' ? undefined : t
}

/** Money in the currency the API blended it into. */
function money(currency: string, v: number, dp = 2) {
  const symbol = currency === 'INR' ? '₹' : '$'
  return `${symbol}${v.toFixed(dp)}`
}

function Stat({
  icon: Icon,
  label,
  value,
  hint,
  tone,
}: {
  icon: React.ComponentType<{ className?: string }>
  label: string
  value: React.ReactNode
  hint?: string
  tone?: 'success' | 'warning' | 'danger'
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="h-3.5 w-3.5" />
        {label}
      </div>
      <p
        className={cn(
          'mt-2 tnum text-2xl font-semibold leading-none',
          tone === 'success' && 'text-success',
          tone === 'warning' && 'text-warning',
          tone === 'danger' && 'text-danger',
        )}
      >
        {value}
      </p>
      {hint && <p className="mt-1.5 text-2xs text-muted-foreground">{hint}</p>}
    </Card>
  )
}

function ChartCard({
  title,
  subtitle,
  children,
  empty,
}: {
  title: string
  subtitle?: string
  children: React.ReactNode
  empty: boolean
}) {
  return (
    <Card>
      <CardHeader className="block">
        <CardTitle>{title}</CardTitle>
        {subtitle && <p className="mt-0.5 text-2xs text-muted-foreground">{subtitle}</p>}
      </CardHeader>
      <CardBody className="pt-5">
        {empty ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            Nothing recorded in this window.
          </p>
        ) : (
          <div className="h-64 w-full">{children}</div>
        )}
      </CardBody>
    </Card>
  )
}

const axis = {
  stroke: 'hsl(var(--muted-foreground))',
  fontSize: 11,
  tickLine: false,
  axisLine: false,
}

/**
 * percentile_cont interpolates, so a p95 arrives as 2828.4999999999995. Two
 * decimals everywhere, and no trailing ".00" on whole numbers.
 */
const round2 = (v: unknown) => {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

const msTick = (v: number) => `${Math.round(v)}ms`
const msValue = (v: unknown) => `${round2(v)} ms`

const tooltipStyle = {
  contentStyle: {
    background: 'hsl(var(--card))',
    border: '1px solid hsl(var(--border))',
    borderRadius: 8,
    fontSize: 12,
  },
  labelStyle: { color: 'hsl(var(--muted-foreground))', fontSize: 11 },
}

export function Dashboard() {
  const { user } = useAuth()
  const [days, setDays] = useState(7)
  const [campaignId, setCampaignId] = useState('')

  const query = buildQuery({ days, campaign_id: campaignId || undefined })

  const summary = useQuery({
    queryKey: ['analytics-summary', query],
    queryFn: () => api<AnalyticsSummary>(`/analytics/summary${query}`),
  })
  const series = useQuery({
    queryKey: ['analytics-series', query],
    queryFn: () => api<TimeBucket[]>(`/analytics/timeseries${query}`),
  })
  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Campaign[]>('/campaigns'),
    staleTime: 5 * 60 * 1000,
  })

  const s = summary.data
  const buckets = (series.data ?? []).map((b) => ({
    ...b,
    // shorter axis labels than a full timestamp
    t: new Date(b.bucket).toLocaleString('en-IN', {
      day: '2-digit',
      month: 'short',
      ...(days <= 2 ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
    }),
    uncached: Math.max(0, b.prompt_tokens - b.cached_tokens),
  }))

  const transferRate = s && s.calls ? (s.transferred / s.calls) * 100 : 0
  const cacheRate = s && s.prompt_tokens ? (s.cached_tokens / s.prompt_tokens) * 100 : null
  const reasonData = Object.entries(s?.end_reasons ?? {}).map(([name, value]) => ({ name, value }))

  if (summary.isLoading) {
    return (
      <div className={PAGE}>
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-72" />
      </div>
    )
  }

  if (summary.isError) {
    return (
      <div className={PAGE}>
        <EmptyState
          icon={TriangleAlert}
          title="Could not load analytics"
          hint={(summary.error as Error).message}
          action={
            <Button size="sm" variant="outline" onClick={() => summary.refetch()}>
              Try again
            </Button>
          }
        />
      </div>
    )
  }

  return (
    <div className={PAGE}>
      <PageHeader
        title="Dashboard"
        description="Call volume, response latency and cost across the selected window."
        actions={
          <div className="flex items-center gap-2">
            {(user?.role === 'superadmin' || (campaigns.data?.length ?? 0) > 1) && (
              <Select
                value={campaignId}
                onChange={(e) => setCampaignId(e.target.value)}
                className="w-52"
              >
                <option value="">All campaigns</option>
                {campaigns.data?.map((c) => (
                  <option key={c.id} value={c.id}>
                    {user?.role === 'superadmin' ? `${c.tenant_name} · ${c.name}` : c.name}
                  </option>
                ))}
              </Select>
            )}
            <Select
              value={String(days)}
              onChange={(e) => setDays(Number(e.target.value))}
              className="w-40"
            >
              {RANGES.map((r) => (
                <option key={r.days} value={r.days}>
                  {r.label}
                </option>
              ))}
            </Select>
          </div>
        }
      />

      {/* headline */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat
          icon={PhoneCall}
          label="Calls"
          value={formatNumber(s?.calls)}
          hint={`${formatNumber(s?.total_turns)} turns`}
        />
        <Stat
          icon={Timer}
          label="AHT"
          value={s?.avg_duration_ms ? formatDuration(s.avg_duration_ms) : '—'}
          hint={
            s?.max_duration_ms
              ? `longest ${formatDuration(s.max_duration_ms)}`
              : 'average handle time'
          }
        />
        <Stat
          icon={Clock}
          label="Median response"
          value={formatMs(s?.latency.p50)}
          tone={tone(s?.latency.p50)}
          hint={`across ${formatNumber(s?.latency.turns)} timed turns`}
        />
        <Stat
          icon={Clock}
          label="p95 response"
          value={formatMs(s?.latency.p95)}
          tone={tone(s?.latency.p95)}
          hint={s?.latency.worst ? `worst ${formatMs(s.latency.worst)}` : undefined}
        />
        <Stat
          icon={ArrowRightLeft}
          label="Handed to a human"
          value={formatPercent(transferRate)}
          tone={transferRate > 40 ? 'warning' : undefined}
          hint={`${formatNumber(s?.transferred)} of ${formatNumber(s?.calls)} calls`}
        />
      </div>

      {/* Five and five. Six tiles in a five-column grid left Prompt tokens
          stranded on a row of its own, and a four-column second row lined up
          with nothing above it. */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Stat
          icon={Coins}
          label="Prompt tokens"
          value={formatNumber(s?.prompt_tokens)}
          hint={cacheRate != null ? `${formatPercent(cacheRate)} from cache` : undefined}
        />
        {s?.cost && (
          <>
            <Stat
              icon={Wallet}
              label={`Spend (${s.cost.currency})`}
              value={money(s.cost.currency, s.cost.total, 2)}
              hint={`${money(s.cost.currency, s.cost.per_call, 2)} per call`}
            />
            <Stat
              icon={Wallet}
              label="Cost per minute"
              value={money(s.cost.currency, s.cost.per_minute_avg, 2)}
              hint="total spend over total minutes"
            />
            <Stat
              icon={Wallet}
              label="Worst per minute"
              value={
                s.cost.per_minute_max != null
                  ? money(s.cost.currency, s.cost.per_minute_max, 2)
                  : '—'
              }
              hint={
                s.cost.per_minute_max_call_id
                  ? `call ${s.cost.per_minute_max_call_id} · calls over ${s.cost.per_minute_max_floor_sec}s only`
                  : `no call over ${s.cost.per_minute_max_floor_sec}s`
              }
            />
            <Stat
              icon={Coins}
              label="Priced"
              value={`${formatNumber(s.cost.priced_calls)} of ${formatNumber(
                s.cost.priced_calls + s.cost.unpriced_calls,
              )}`}
              tone={s.cost.unpriced_calls > 0 ? 'warning' : undefined}
              hint={
                s.cost.unpriced_calls > 0
                  ? `${formatNumber(s.cost.unpriced_calls)} with no rate — spend is short`
                  : 'every call has a rate'
              }
            />
          </>
        )}
      </div>

      {(s?.limit_hit ?? 0) > 0 && (
        <Card className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div>
              <p className="font-medium">
                {formatNumber(s!.limit_hit)} call{s!.limit_hit === 1 ? '' : 's'} stopped by a
                guardrail
              </p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                They reached a turn, duration or token limit and were ended deliberately rather than
                left to run up cost.
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="grid gap-5 lg:grid-cols-2">
        <ChartCard
          title="Call volume"
          subtitle="Total calls, and how many were handed to a human"
          empty={!buckets.length}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={buckets} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="t" {...axis} />
              <YAxis {...axis} allowDecimals={false} width={40} />
              <Tooltip {...tooltipStyle} cursor={{ fill: 'hsl(var(--accent))' }} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
              <Bar dataKey="calls" name="Calls" fill="hsl(var(--primary))" radius={[3, 3, 0, 0]} />
              <Bar
                dataKey="transferred"
                name="Transferred"
                fill="hsl(var(--warning))"
                radius={[3, 3, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Response latency"
          subtitle="Median and p95 per turn — p95 is what a caller notices"
          empty={!buckets.some((b) => b.p50 != null)}
        >
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={buckets} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="t" {...axis} />
              {/* tickFormatter rather than unit="ms": the unit is appended after
                  the width is reserved, so the label overflows and is clipped */}
              <YAxis {...axis} width={70} tickFormatter={msTick} />
              <Tooltip {...tooltipStyle} formatter={msValue} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
              <Line
                type="monotone"
                dataKey="p50"
                name="p50"
                stroke="hsl(var(--primary))"
                strokeWidth={2}
                dot={false}
              />
              <Line
                type="monotone"
                dataKey="p95"
                name="p95"
                stroke="hsl(var(--danger))"
                strokeWidth={2}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="Where the time goes"
          subtitle="Median per stage. Turn detection rising is our machine; the other two are the providers"
          empty={!buckets.some((b) => b.eou_ms != null)}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={buckets} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="t" {...axis} />
              <YAxis {...axis} width={70} tickFormatter={msTick} />
              <Tooltip {...tooltipStyle} formatter={msValue} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
              {(Object.keys(STAGE) as (keyof typeof STAGE)[]).map((k) => (
                <Area
                  key={k}
                  type="monotone"
                  dataKey={k}
                  name={STAGE[k].label}
                  stackId="1"
                  stroke={STAGE[k].color}
                  fill={STAGE[k].color}
                  fillOpacity={0.25}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </ChartCard>

        <ChartCard
          title="LLM tokens"
          subtitle="Cached prompt tokens are billed at a fraction of the rest — this is the cost driver"
          empty={!buckets.some((b) => b.prompt_tokens > 0)}
        >
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={buckets} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
              <XAxis dataKey="t" {...axis} />
              <YAxis {...axis} width={72} tickFormatter={(v) => formatNumber(v as number)} />
              <Tooltip {...tooltipStyle} formatter={(v) => formatNumber(v as number)} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
              <Bar
                dataKey="cached_tokens"
                name="Cached"
                stackId="t"
                fill="hsl(var(--success))"
                radius={[0, 0, 0, 0]}
              />
              <Bar
                dataKey="uncached"
                name="Uncached"
                stackId="t"
                fill="hsl(var(--primary))"
                radius={[3, 3, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </ChartCard>
      </div>

      <div className="grid gap-5 lg:grid-cols-3">
        <ChartCard title="How calls ended" empty={!reasonData.length}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={reasonData}
                dataKey="value"
                nameKey="name"
                innerRadius={52}
                outerRadius={82}
                paddingAngle={2}
              >
                {reasonData.map((d) => (
                  <Cell
                    key={d.name}
                    fill={REASON_COLOR[d.name] ?? 'hsl(var(--muted-foreground))'}
                  />
                ))}
              </Pie>
              <Tooltip {...tooltipStyle} />
              <Legend iconType="circle" wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
        </ChartCard>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Totals for this window</CardTitle>
          </CardHeader>
          <CardBody className="grid gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            {(
              [
                ['Calls', formatNumber(s?.calls)],
                ['Total talk time', formatDuration(s?.total_duration_ms)],
                ['Turns', formatNumber(s?.total_turns)],
                ['Handed to a human', `${formatNumber(s?.transferred)} (${formatPercent(transferRate)})`],
                ['Stopped by a guardrail', formatNumber(s?.limit_hit)],
                ['Errors', formatNumber(s?.errors)],
                ['Prompt tokens', formatNumber(s?.prompt_tokens)],
                ['— of which cached', formatNumber(s?.cached_tokens)],
                ['Completion tokens', formatNumber(s?.completion_tokens)],
                ['TTS characters', formatNumber(s?.tts_characters)],
              ] as const
            ).map(([label, value]) => (
              <div
                key={label}
                className="flex justify-between gap-4 border-b border-border/40 pb-1.5"
              >
                <span className="text-muted-foreground">{label}</span>
                <span className="tnum font-medium">{value}</span>
              </div>
            ))}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
