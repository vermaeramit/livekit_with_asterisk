import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BookOpenCheck, Check, SearchX, TriangleAlert, Unplug } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Card, EmptyState, Input, Label, Select, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { cn, formatDateTime, formatRelative } from '@/lib/utils'
import type { Campaign, KnowledgeGap } from '@/types'

type KindMeta = {
  label: string
  icon: React.ComponentType<{ className?: string }>
  /** Matches the severity dot on Alerts, so the two pages read the same way. */
  dot: string
}

const KIND: Record<string, KindMeta> = {
  kb_miss: { label: 'Nothing found', icon: SearchX, dot: 'bg-danger' },
  kb_weak: { label: 'Barely found', icon: TriangleAlert, dot: 'bg-warning' },
  tool_failed: { label: 'Lookup failed', icon: Unplug, dot: 'bg-danger' },
}

export function KnowledgeGaps() {
  const toast = useToast()
  const qc = useQueryClient()
  const [campaign, setCampaign] = useState('')
  const [kind, setKind] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [handling, setHandling] = useState<KnowledgeGap | null>(null)
  const [note, setNote] = useState('')

  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Campaign[]>('/campaigns'),
  })

  const params = new URLSearchParams()
  if (campaign) params.set('campaign_id', campaign)
  if (kind) params.set('kind', kind)
  params.set('open_only', String(openOnly))

  const gaps = useQuery({
    queryKey: ['gaps', campaign, kind, openOnly],
    queryFn: () => api<KnowledgeGap[]>('/gaps?' + params.toString()),
  })

  const acknowledge = useMutation({
    mutationFn: (g: KnowledgeGap) =>
      // api() serialises the body itself - stringifying here as well sends a
      // JSON string where the endpoint expects an object, and pydantic answers
      // "Input should be a valid dictionary".
      api('/gaps/acknowledge', {
        method: 'POST',
        body: {
          campaign_id: g.campaign_id,
          kind: g.kind,
          query_key: g.query_key,
          note: note.trim() || null,
        },
      }),
    onSuccess: () => {
      setHandling(null)
      setNote('')
      qc.invalidateQueries({ queryKey: ['gaps'] })
      qc.invalidateQueries({ queryKey: ['gaps-unread'] })
      toast.success('Marked as handled')
    },
    onError: (e) =>
      toast.error(e instanceof ApiError ? e.message : 'Could not save that'),
  })

  return (
    <div className={PAGE}>
      <PageHeader
        title="Knowledge gaps"
        description="Questions the agent could not answer, most asked first — so the top of this list is the next thing worth writing."
        actions={
          <Button variant="outline" size="sm" onClick={() => setOpenOnly((o) => !o)}>
            {openOnly ? 'Show handled too' : 'Hide handled'}
          </Button>
        }
      />

      <Card className="p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="gap-campaign">Campaign</Label>
            <Select
              id="gap-campaign"
              value={campaign}
              onChange={(e) => setCampaign(e.target.value)}
            >
              <option value="">All campaigns</option>
              {campaigns.data?.map((c) => (
                <option key={c.id} value={c.id}>{c.name}</option>
              ))}
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="gap-kind">Kind</Label>
            <Select id="gap-kind" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">Everything</option>
              <option value="kb_miss">Nothing found</option>
              <option value="kb_weak">Barely found</option>
              <option value="tool_failed">Lookup failed</option>
            </Select>
          </div>
        </div>
      </Card>

      <Card className="overflow-hidden">
        {gaps.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : gaps.isError ? (
          <EmptyState
            icon={TriangleAlert}
            title="Could not load knowledge gaps"
            hint={(gaps.error as Error).message}
          />
        ) : !gaps.data?.length ? (
          <EmptyState
            icon={BookOpenCheck}
            title={openOnly ? 'Nothing outstanding' : 'Nothing recorded yet'}
            hint={
              openOnly
                ? 'Every question the agent could not answer has been dealt with.'
                : 'This fills itself as calls come in — when a search finds nothing, when it barely finds something, or when a lookup fails.'
            }
          />
        ) : (
          <div className="divide-y divide-border/70">
            {gaps.data.map((g) => {
              const meta = KIND[g.kind]
              const handled = g.open_occurrences === 0
              return (
                <div
                  key={g.campaign_id + '-' + g.kind + '-' + g.query_key}
                  className="flex items-start gap-3 px-4 py-3"
                >
                  <span
                    className={cn(
                      'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                      meta?.dot ?? 'bg-muted-foreground',
                      handled && 'opacity-30',
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {meta?.label ?? g.kind}
                      </span>
                      <span className="text-2xs text-muted-foreground">
                        {g.campaign_name ?? 'no campaign'}
                      </span>
                      {/* The count is why this page groups at all: it is what
                          says which gap to fill first, and it sets the order. */}
                      <Badge tone={handled ? 'muted' : 'default'} className="tnum">
                        asked {g.occurrences}×
                      </Badge>
                      {g.worst_score != null && (
                        <span className="tnum text-2xs text-muted-foreground">
                          best match {g.worst_score.toFixed(2)}
                        </span>
                      )}
                      <span
                        className="ml-auto tnum text-2xs text-muted-foreground"
                        title={formatDateTime(g.last_seen)}
                      >
                        {formatRelative(g.last_seen)}
                      </span>
                    </div>

                    <p className="mt-0.5 break-words text-sm text-muted-foreground">
                      {g.query}
                    </p>

                    {g.detail && (
                      <p className="mt-1 break-words font-mono text-2xs text-muted-foreground">
                        {g.detail}
                      </p>
                    )}

                    <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
                      {/* Listening to one is faster than reading about it —
                          the caller's own words say what they wanted. */}
                      {g.call_ids.slice(0, 4).map((id) => (
                        <Link
                          key={id}
                          to={'/calls/' + id}
                          className="text-primary hover:underline"
                        >
                          call {id}
                        </Link>
                      ))}
                      {g.open_occurrences > 0 && g.open_occurrences < g.occurrences && (
                        <span>{g.open_occurrences} since it was last handled</span>
                      )}
                    </div>

                    {g.note && (
                      <p className="mt-1 text-2xs text-muted-foreground">
                        handled by {g.acknowledged_by_email ?? 'someone'}
                        {g.acknowledged_at ? ' ' + formatRelative(g.acknowledged_at) : ''}
                        {' — '}
                        {g.note}
                      </p>
                    )}
                  </div>

                  {!handled && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setHandling(g)
                        setNote('')
                      }}
                    >
                      <Check className="h-3.5 w-3.5" />
                      Mark handled
                    </Button>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </Card>

      <Dialog
        open={handling !== null}
        onClose={() => setHandling(null)}
        title="Mark as handled"
      >
        {handling && (
          <div className="space-y-4">
            <p className="text-sm leading-relaxed text-muted-foreground">
              “{handling.query}” — asked {handling.occurrences} times.
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="gap-note">What did you do about it?</Label>
              <Input
                id="gap-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Uploaded the Splendor Flex brochure"
                autoFocus
              />
              <p className="text-2xs leading-relaxed text-muted-foreground">
                Read by whoever finds this open again. If the question comes back it
                reappears as a new entry — that is the point: it means whatever was
                done did not close the gap.
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setHandling(null)}>
                Cancel
              </Button>
              <Button
                onClick={() => acknowledge.mutate(handling)}
                loading={acknowledge.isPending}
              >
                Mark handled
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
