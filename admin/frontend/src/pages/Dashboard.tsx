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
import { addDays, differenceInCalendarDays, format, parseISO, startOfDay, subDays } from 'date-fns'
import { PAGE, PageHeader } from '@/components/Layout'
import { DateRangeField } from '@/components/ui/daterange'
import { Button } from '@/components/ui/button'
import { Card, CardBody, CardHeader, CardTitle, EmptyState, Select, Skeleton } from '@/components/ui/primitives'
import { api, buildQuery } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDuration, formatMs, formatNumber, formatPercent, latencyTone } from '@/lib/utils'
import type { AnalyticsSummary, Campaign, TimeBucket } from '@/types'

/** The window when nothing else is chosen: the last seven days, today included -
 *  the same span as the picker's own "Last 7 days" preset. */
function lastSevenDays() {
  return {
    from: format(subDays(new Date(), 6), 'yyyy-MM-dd'),
    to: format(new Date(), 'yyyy-MM-dd'),
  }
}

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
  const [range, setRange] = useState(lastSevenDays)
  const [campaignId, setCampaignId] = useState('')

  const query = buildQuery({
    // LOCAL day boundaries, exactly as the calls list sends them - see the note
    // there. new Date('2026-09-09') reads UTC midnight, which is 05:30 IST, and
    // most of the chosen first day would fall outside its own chart.
    date_from: range.from ? startOfDay(parseISO(range.from)).toISOString() : undefined,
    // Inclusive in the picker, exclusive in the query: the start of the next day.
    // No end means "until now", which the API supplies.
    date_to: range.to ? startOfDay(addDays(parseISO(range.to), 1)).toISOString() : undefined,
    campaign_id: campaignId || undefined,
  })

  // Calendar days in the window, for choosing axis labels. Must agree with the
  // API's rule for hourly buckets - two days or fewer, in timeseries() in
  // analytics.py - or hourly buckets get day-only labels that all read the same.
  const spanDays = range.from
    ? differenceInCalendarDays(range.to ? parseISO(range.to) : new Date(), parseISO(range.from)) + 1
    : 7

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
      ...(spanDays <= 2 ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
    }),
    uncached: Math.max(0, (b.prompt_tokens ?? 0) - (b.cached_tokens ?? 0)),
  }))

  const transferRate = s && s.calls ? (s.transferred / s.calls) * 100 : 0
  // null when the viewer may not see usage - the API leaves the counts out
  // rather than sending zeros, so the tile drops rather than reading "0".
  const cacheRate =
    s?.prompt_tokens && s.cached_tokens != null
      ? (s.cached_tokens / s.prompt_tokens) * 100
      : null
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
            {/* The same picker as the calls list, so a date range means the same
                thing on both pages. Wide enough for "8 Sept - 14 Sept 2026". */}
            <div className="w-60">
              <DateRangeField
                from={range.from}
                to={range.to}
                // No "Any date" and no clear button. On the calls list an empty
                // range is a real filter; here there is no chart of every call
                // ever made, so offering one would put "Any date" above a week
                // of data. The fallback stays as the guard if an empty range
                // arrives anyway.
                allowAny={false}
                onChange={(r) => setRange(r.from || r.to ? r : lastSevenDays())}
              />
            </div>
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
          hint={`caller's wait, across ${formatNumber(s?.latency.turns)} answers`}
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
        {s?.prompt_tokens != null && (
          <Stat
            icon={Coins}
            label="Prompt tokens"
            value={formatNumber(s.prompt_tokens)}
            hint={cacheRate != null ? `${formatPercent(cacheRate)} from cache` : undefined}
          />
        )}
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
              label="Highest per minute"
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
          subtitle="The caller's wait for each answer, median and p95 — measured from 22 Sep 2026"
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

        {/* Dropped entirely without usage.read. An empty chart titled "LLM
            tokens" reads as a quiet period rather than as something withheld. */}
        <ChartCard
          title="LLM tokens"
          subtitle="Cached prompt tokens are billed at a fraction of the rest — this is the cost driver"
          empty={!buckets.some((b) => (b.prompt_tokens ?? 0) > 0)}
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

      {/* Last, and deliberately plain prose. Response time is three numbers
          added together and a percentile on top of that, and everyone who has
          asked about it has asked the same two questions - what is being timed,
          and why there are two of them. The caveat at the end is the one thing
          the figures cannot say about themselves. */}
      <Card>
        <CardHeader>
          <CardTitle>What these numbers mean</CardTitle>
        </CardHeader>
        <CardBody className="max-w-4xl space-y-3 text-2xs leading-relaxed text-muted-foreground">
          <p>
            <strong className="font-medium text-foreground">Response time</strong> is the
            silence the caller sits through: from the moment they stop speaking to the
            first sound of the reply. It adds three things — noticing they have finished
            (which includes transcription), the language model&rsquo;s first token, and the
            first audio out of the voice provider. It is not how long the reply lasts,
            and it is measured per turn, not per call.
          </p>
          <p>
            <strong className="font-medium text-foreground">Median</strong> is the middle
            turn — half were faster than this.{' '}
            <strong className="font-medium text-foreground">p95</strong> is the 95th:
            ninety-five turns in a hundred were faster, and five were worse. The median
            says what the system usually feels like; p95 says when it feels broken. A
            call runs fifteen or twenty turns, so that worst 5% turns up in most of them
            — it is the pause where a caller says &ldquo;hello?&rdquo;.
          </p>
          <p>
            <strong className="font-medium text-foreground">
              Turns that searched the knowledge base read lower than they really were.
            </strong>{' '}
            A tool call runs the language model twice — once to decide to search, once to
            answer — and only the first is timed. The search itself and the second
            request are not in these figures, so on a campaign that leans on its
            knowledge base the real wait is longer than what is shown here.
          </p>
        </CardBody>
      </Card>
    </div>
  )
}
