import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, TriangleAlert } from 'lucide-react'
import { addDays, format, parseISO, startOfDay, subDays } from 'date-fns'
import { PAGE, PageHeader } from '@/components/Layout'
import { DateRangeField } from '@/components/ui/daterange'
import { Button } from '@/components/ui/button'
import { Card, CardBody, EmptyState, Select, Skeleton } from '@/components/ui/primitives'
import { api, buildQuery, download } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Campaign, ProviderCostReport } from '@/types'

/**
 * What each combination of providers cost per minute, on calls that happened.
 *
 * The Price comparison page shows what providers CHARGE. This shows what they
 * were actually paid — which is a different number, because a call does not
 * spend its minutes evenly across listening, thinking and speaking.
 *
 * Per minute is total cost over total minutes, not the average of each call's
 * own figure. The second lets a five-second call weigh as much as a ten-minute
 * one, and a load test full of calls that died after the greeting would decide
 * the number.
 */

const lastSevenDays = () => ({
  from: format(subDays(new Date(), 6), 'yyyy-MM-dd'),
  to: format(new Date(), 'yyyy-MM-dd'),
})

const rupees = (n: number | null) =>
  n === null ? '—' : `₹${n < 1 ? n.toFixed(4) : n.toFixed(2)}`

const num = (n: number) => n.toLocaleString('en-IN')

/** One leg's price: the rate per minute, and what it came to over the window. */
function Money({ perMinute, total }: { perMinute: number | null; total: number | null }) {
  return (
    <td className="px-3 py-2 text-right">
      <div className="tabular-nums">
        {rupees(perMinute)}
        <span className="ml-1 text-2xs text-muted-foreground">/min</span>
      </div>
      <div className="text-2xs text-muted-foreground tabular-nums">{rupees(total)} total</div>
    </td>
  )
}

export function ProviderCost() {
  const { user, can } = useAuth()
  const [range, setRange] = useState(lastSevenDays)
  const [tenantId, setTenantId] = useState('')
  const [campaignId, setCampaignId] = useState('')

  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Campaign[]>('/campaigns'),
  })

  // The client list comes out of the campaigns rather than a second request:
  // every campaign already carries its tenant, and a client with no campaign
  // has nothing to report on anyway.
  const clients = useMemo(() => {
    const seen = new Map<number, string>()
    for (const c of campaigns.data ?? []) {
      if (c.tenant_name) seen.set(c.tenant_id, c.tenant_name)
    }
    return [...seen].sort((a, b) => a[1].localeCompare(b[1]))
  }, [campaigns.data])

  const visibleCampaigns = (campaigns.data ?? []).filter(
    (c) => !tenantId || String(c.tenant_id) === tenantId,
  )

  const query = buildQuery({
    // LOCAL day boundaries, the same as the dashboard and the calls list send.
    // new Date('2026-09-09') reads UTC midnight, which is 05:30 IST, and most
    // of the chosen first day would fall outside its own report.
    date_from: range.from ? startOfDay(parseISO(range.from)).toISOString() : undefined,
    // Inclusive in the picker, exclusive in the query: the start of the next day.
    date_to: range.to ? startOfDay(addDays(parseISO(range.to), 1)).toISOString() : undefined,
    tenant_id: tenantId || undefined,
    campaign_id: campaignId || undefined,
  })

  const report = useQuery({
    queryKey: ['report-provider-cost', query],
    queryFn: () => api<ProviderCostReport>(`/reports/provider-cost${query}`),
  })

  return (
    <div className={PAGE}>
      <PageHeader
        title="Cost by provider"
        description="What each combination of providers was actually paid, per minute of call."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {(user?.role === 'superadmin' || clients.length > 1) && (
              <Select
                value={tenantId}
                onChange={(e) => { setTenantId(e.target.value); setCampaignId('') }}
                className="w-44"
              >
                <option value="">All clients</option>
                {clients.map(([id, name]) => (
                  <option key={id} value={id}>{name}</option>
                ))}
              </Select>
            )}
            <Select
              value={campaignId}
              onChange={(e) => setCampaignId(e.target.value)}
              className="w-52"
            >
              <option value="">All campaigns</option>
              {visibleCampaigns.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
            <div className="w-60">
              <DateRangeField
                from={range.from}
                to={range.to}
                allowAny={false}
                onChange={(r) => setRange(r.from || r.to ? r : lastSevenDays())}
              />
            </div>
            {/* Its own permission. A figure read here stays here; a file does
                not — see permissions.py. The button is hidden rather than
                disabled, because a disabled control invites a question nobody
                on this page can answer. */}
            {can('reports.export') && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => download(`/reports/provider-cost.csv${query}`)}
                disabled={!report.data?.rows.length}
              >
                <Download className="h-3.5 w-3.5" />
                CSV
              </Button>
            )}
          </div>
        }
      />

      {report.isLoading && <Skeleton className="h-64" />}

      {report.data?.caveats.map((c) => (
        <Card key={c} className="border-warning/30 bg-warning/5">
          <CardBody className="flex items-start gap-2 text-2xs leading-relaxed">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
            <span>{c}</span>
          </CardBody>
        </Card>
      ))}

      {report.data && !report.data.rows.length && (
        <EmptyState
          icon={TriangleAlert}
          title="No calls in this range"
          hint="Widen the dates, or clear the client and campaign filters."
        />
      )}

      {report.data && report.data.rows.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border text-2xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left font-medium">Speech to text</th>
                  <th className="px-3 py-2 text-left font-medium">Language model</th>
                  <th className="px-3 py-2 text-left font-medium">Voice</th>
                  <th className="px-3 py-2 text-right font-medium">Calls</th>
                  <th className="px-3 py-2 text-right font-medium">Minutes</th>
                  <th className="px-3 py-2 text-right font-medium">LLM tokens</th>
                  <th className="px-3 py-2 text-right font-medium">STT audio</th>
                  <th className="px-3 py-2 text-right font-medium">TTS chars</th>
                  {/* Two numbers per cell: the rate per minute, and what
                      that came to over the window. Per minute is what you
                      compare between rows; the total is what you paid. */}
                  <th className="px-3 py-2 text-right font-medium">Speech to text</th>
                  <th className="px-3 py-2 text-right font-medium">Language model</th>
                  <th className="px-3 py-2 text-right font-medium">Voice</th>
                  <th className="px-3 py-2 text-right font-medium">All three</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/60">
                {report.data.rows.map((r, i) => (
                  <tr key={i} className="align-top">
                    <td className="px-3 py-2">{r.stt ?? '—'}</td>
                    <td className="px-3 py-2">{r.llm ?? '—'}</td>
                    <td className="px-3 py-2">{r.tts ?? '—'}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{num(r.calls)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.minutes.toFixed(1)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{num(r.llm_tokens)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.stt_seconds.toFixed(0)}s</td>
                    <td className="px-3 py-2 text-right tabular-nums">{num(r.tts_characters)}</td>
                    <Money perMinute={r.inr_stt_per_minute} total={r.inr_stt} />
                    <Money perMinute={r.inr_llm_per_minute} total={r.inr_llm} />
                    <Money perMinute={r.inr_tts_per_minute} total={r.inr_tts} />
                    <td className="px-3 py-2 text-right">
                      <div className="font-medium tabular-nums">{rupees(r.inr_per_minute)}<span className="ml-1 text-2xs font-normal text-muted-foreground">/min</span></div>
                      <div className="text-2xs text-muted-foreground tabular-nums">{rupees(r.inr_total)} total</div>
                      {/* Said on the row that is short, not only in the banner.
                          A reader comparing two rows needs to know which one is
                          missing a leg at the moment they compare them. */}
                      {r.missing_rates.length > 0 && (
                        <div className="text-2xs font-normal text-warning">
                          short — no rate for {r.missing_rates.join(', ')}
                        </div>
                      )}
                      {/* "0 of 23 calls priced" read like a missing rate when
                          it was nothing of the kind: those calls ended after
                          the greeting, which plays from a cached file, so no
                          provider was ever asked for anything. Say that
                          instead - there is nothing to go and fix. */}
                      {!r.any_usage ? (
                        <div className="text-2xs font-normal text-muted-foreground">
                          no usage — ended after the greeting
                        </div>
                      ) : r.priced_calls < r.calls ? (
                        <div className="text-2xs font-normal text-muted-foreground">
                          {r.priced_calls} of {r.calls} calls priced
                        </div>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {report.data?.usd_to_inr && (
        <p className="text-2xs text-muted-foreground">
          Dollar prices converted at ₹{report.data.usd_to_inr} to $1, the rate set
          under Provider rates. Per minute is the row&rsquo;s total cost over its
          total minutes.
        </p>
      )}
    </div>
  )
}
