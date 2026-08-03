import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowRightLeft,
  ChevronLeft,
  ChevronRight,
  PhoneOff,
  RotateCcw,
  Search,
  TriangleAlert,
  X,
} from 'lucide-react'
import { PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Badge, Card, EmptyState, Input, Label, Select, Skeleton } from '@/components/ui/primitives'
import { api, buildQuery } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDateTime, formatDuration, formatNumber } from '@/lib/utils'
import type { Campaign, CallListItem, CallListResponse } from '@/types'

const PAGE_SIZE = 25

const END_REASONS = [
  { value: '', label: 'Any outcome' },
  { value: 'completed', label: 'Completed' },
  { value: 'transferred', label: 'Transferred' },
  { value: 'limit', label: 'Hit a limit' },
  { value: 'error', label: 'Error' },
]

export function EndReasonBadge({ call }: { call: CallListItem }) {
  if (call.limit_hit) {
    return (
      <Badge tone="warning" title={`Guardrail: ${call.limit_hit}`}>
        <TriangleAlert className="h-3 w-3" />
        {call.limit_hit}
      </Badge>
    )
  }
  if (call.transferred_to) {
    return (
      <Badge tone="info" title={call.transferred_to}>
        <ArrowRightLeft className="h-3 w-3" />
        transferred
      </Badge>
    )
  }
  if (!call.end_reason) return <Badge tone="muted">—</Badge>
  return (
    <Badge tone={call.end_reason === 'error' ? 'danger' : 'success'}>{call.end_reason}</Badge>
  )
}

export function Calls() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [params, setParams] = useSearchParams()

  // Filters live in the URL so a filtered view can be shared or bookmarked -
  // "look at this call" is the most common thing anyone will want to send.
  const page = Number(params.get('page') ?? 1)
  const search = params.get('search') ?? ''
  const campaignId = params.get('campaign_id') ?? ''
  const endReason = params.get('end_reason') ?? ''
  const dateFrom = params.get('date_from') ?? ''
  const dateTo = params.get('date_to') ?? ''

  const [searchDraft, setSearchDraft] = useState(search)
  useEffect(() => setSearchDraft(search), [search])

  // debounce so typing a phone number does not fire a query per keystroke
  useEffect(() => {
    if (searchDraft === search) return
    const t = setTimeout(() => update({ search: searchDraft, page: '1' }), 350)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft])

  function update(next: Record<string, string>) {
    const merged = new URLSearchParams(params)
    for (const [k, v] of Object.entries(next)) {
      if (v) merged.set(k, v)
      else merged.delete(k)
    }
    setParams(merged, { replace: true })
  }

  const query = useMemo(
    () =>
      buildQuery({
        page,
        page_size: PAGE_SIZE,
        search: search || undefined,
        campaign_id: campaignId || undefined,
        end_reason: endReason === 'transferred' ? undefined : endReason || undefined,
        transferred: endReason === 'transferred' ? true : undefined,
        date_from: dateFrom ? new Date(dateFrom).toISOString() : undefined,
        date_to: dateTo ? new Date(dateTo).toISOString() : undefined,
      }),
    [page, search, campaignId, endReason, dateFrom, dateTo],
  )

  const calls = useQuery({
    queryKey: ['calls', query],
    queryFn: () => api<CallListResponse>(`/calls${query}`),
    // keeps the table on screen while the next page loads instead of flashing
    // an empty state on every filter change
    placeholderData: (prev) => prev,
  })

  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Campaign[]>('/campaigns'),
    staleTime: 5 * 60 * 1000,
  })

  const hasFilters = Boolean(search || campaignId || endReason || dateFrom || dateTo)
  const total = calls.data?.total ?? 0
  const lastPage = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const showTenant = user?.role === 'superadmin'

  const th = 'px-4 py-2.5 text-left text-2xs font-semibold uppercase tracking-wider text-muted-foreground'
  const td = 'whitespace-nowrap px-4 py-3'

  return (
    <div className="mx-auto max-w-[1500px] space-y-5 p-5 lg:p-7">
      <PageHeader
        title="Calls"
        description={
          calls.isLoading
            ? 'Loading…'
            : `${formatNumber(total)} call${total === 1 ? '' : 's'}${hasFilters ? ' matching your filters' : ''}`
        }
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => calls.refetch()}
            loading={calls.isFetching && !calls.isLoading}
          >
            <RotateCcw className="h-3.5 w-3.5" />
            Refresh
          </Button>
        }
      />

      {/* filters */}
      <Card className="p-4">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <div className="space-y-1.5 lg:col-span-2">
            <Label htmlFor="q">Search</Label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                id="q"
                value={searchDraft}
                onChange={(e) => setSearchDraft(e.target.value)}
                placeholder="Caller, callee or room name"
                className="pl-8"
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="campaign">Campaign</Label>
            <Select
              id="campaign"
              value={campaignId}
              onChange={(e) => update({ campaign_id: e.target.value, page: '1' })}
            >
              <option value="">All campaigns</option>
              {campaigns.data?.map((c) => (
                <option key={c.id} value={c.id}>
                  {showTenant ? `${c.tenant_name} · ${c.name}` : c.name}
                </option>
              ))}
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="outcome">Outcome</Label>
            <Select
              id="outcome"
              value={endReason}
              onChange={(e) => update({ end_reason: e.target.value, page: '1' })}
            >
              {END_REASONS.map((r) => (
                <option key={r.value} value={r.value}>
                  {r.label}
                </option>
              ))}
            </Select>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <div className="space-y-1.5">
              <Label htmlFor="from">From</Label>
              <Input
                id="from"
                type="date"
                value={dateFrom}
                onChange={(e) => update({ date_from: e.target.value, page: '1' })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="to">To</Label>
              <Input
                id="to"
                type="date"
                value={dateTo}
                onChange={(e) => update({ date_to: e.target.value, page: '1' })}
              />
            </div>
          </div>
        </div>

        {hasFilters && (
          <div className="mt-4 flex justify-end border-t border-border pt-3">
            <Button variant="ghost" size="sm" onClick={() => setParams({}, { replace: true })}>
              <X className="h-3.5 w-3.5" />
              Clear filters
            </Button>
          </div>
        )}
      </Card>

      {/* table */}
      <Card className="overflow-hidden">
        {calls.isError ? (
          <EmptyState
            icon={TriangleAlert}
            title="Could not load calls"
            hint={(calls.error as Error).message}
            action={
              <Button size="sm" variant="outline" onClick={() => calls.refetch()}>
                Try again
              </Button>
            }
          />
        ) : calls.isLoading ? (
          <div className="space-y-2.5 p-4">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : !calls.data?.items.length ? (
          <EmptyState
            icon={PhoneOff}
            title="No calls found"
            hint={hasFilters ? 'Try widening the filters.' : 'Calls appear here as they complete.'}
          />
        ) : (
          <div className="scrollbar-thin overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-muted/50">
                <tr className="border-b border-border">
                  <th className={th}>Started</th>
                  <th className={th}>Caller</th>
                  <th className={th}>Callee</th>
                  {showTenant && <th className={th}>Tenant</th>}
                  <th className={th}>Campaign</th>
                  <th className={cn(th, 'text-right')}>Duration</th>
                  <th className={cn(th, 'text-right')}>Turns</th>
                  <th className={th}>Outcome</th>
                </tr>
              </thead>
              <tbody className={cn(calls.isFetching && 'opacity-50 transition-opacity')}>
                {calls.data.items.map((c) => (
                  <tr
                    key={c.id}
                    tabIndex={0}
                    onClick={() => navigate(`/calls/${c.id}`)}
                    onKeyDown={(e) => e.key === 'Enter' && navigate(`/calls/${c.id}`)}
                    className="cursor-pointer border-b border-border/70 transition-colors last:border-0 hover:bg-accent/50 focus:bg-accent focus:outline-none"
                  >
                    <td className={cn(td, 'tnum text-muted-foreground')}>
                      {formatDateTime(c.started_at)}
                    </td>
                    <td className={cn(td, 'tnum font-medium')}>{c.caller ?? '—'}</td>
                    <td className={cn(td, 'tnum')}>{c.callee ?? '—'}</td>
                    {showTenant && (
                      <td className={cn(td, 'text-muted-foreground')}>
                        {campaigns.data?.find((x) => x.id === c.campaign_id)?.tenant_name ?? '—'}
                      </td>
                    )}
                    <td className={cn(td, 'text-muted-foreground')}>{c.campaign_name ?? '—'}</td>
                    <td className={cn(td, 'text-right tnum')}>{formatDuration(c.duration_ms)}</td>
                    <td className={cn(td, 'text-right tnum text-muted-foreground')}>
                      {c.turn_count ?? '—'}
                    </td>
                    <td className={cn(td, 'py-2')}>
                      <EndReasonBadge call={c} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* pagination */}
      {total > PAGE_SIZE && (
        <div className="flex items-center justify-between text-sm">
          <span className="tnum text-muted-foreground">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of{' '}
            {formatNumber(total)}
          </span>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={page <= 1}
              onClick={() => update({ page: String(page - 1) })}
            >
              <ChevronLeft className="h-3.5 w-3.5" />
              Previous
            </Button>
            <span className="tnum px-1 text-xs text-muted-foreground">
              Page {page} of {lastPage}
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={page >= lastPage}
              onClick={() => update({ page: String(page + 1) })}
            >
              Next
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
