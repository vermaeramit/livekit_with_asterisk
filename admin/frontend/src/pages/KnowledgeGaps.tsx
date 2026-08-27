import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { BookOpenCheck, Check, SearchX, TriangleAlert, Unplug } from 'lucide-react'
import { PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Card, CardBody, EmptyState, Input, Label, Select, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { formatDateTime, formatRelative } from '@/lib/utils'
import type { Campaign, KnowledgeGap } from '@/types'

type KindMeta = {
  label: string
  icon: React.ComponentType<{ className?: string }>
  tone: 'danger' | 'warning'
}

const KIND: Record<string, KindMeta> = {
  kb_miss: { label: 'Nothing found', icon: SearchX, tone: 'danger' },
  kb_weak: { label: 'Barely found', icon: TriangleAlert, tone: 'warning' },
  tool_failed: { label: 'Lookup failed', icon: Unplug, tone: 'danger' },
}

function kindOf(k: string): KindMeta | undefined {
  return KIND[k]
}

export function KnowledgeGaps() {
  const toast = useToast()
  const qc = useQueryClient()
  const [campaign, setCampaign] = useState<string>('')
  const [kind, setKind] = useState<string>('')
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
      api('/gaps/acknowledge', {
        method: 'POST',
        body: JSON.stringify({
          campaign_id: g.campaign_id,
          kind: g.kind,
          query_key: g.query_key,
          note: note.trim() || null,
        }),
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

  const items = gaps.data ?? []

  return (
    <div className="space-y-5">
      <PageHeader
        title="Knowledge gaps"
        description="Questions the agent could not answer, most asked first — so the top of this list is the next thing worth writing."
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="w-56">
          <Label>Campaign</Label>
          <Select value={campaign} onChange={(e) => setCampaign(e.target.value)}>
            <option value="">All campaigns</option>
            {campaigns.data?.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </Select>
        </div>
        <div className="w-52">
          <Label>Kind</Label>
          <Select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Everything</option>
            <option value="kb_miss">Nothing found</option>
            <option value="kb_weak">Barely found</option>
            <option value="tool_failed">Lookup failed</option>
          </Select>
        </div>
        <Button variant="outline" size="sm" onClick={() => setOpenOnly((o) => !o)}>
          {openOnly ? 'Show handled too' : 'Hide handled'}
        </Button>
      </div>

      {gaps.isLoading ? (
        <Skeleton className="h-40" />
      ) : !items.length ? (
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
        <div className="space-y-2.5">
          {items.map((g) => {
            const meta = kindOf(g.kind)
            const Icon = meta?.icon ?? BookOpenCheck
            const handled = g.open_occurrences === 0
            return (
              <Card key={g.campaign_id + '-' + g.kind + '-' + g.query_key}>
                <CardBody className="space-y-2.5">
                  <div className="flex flex-wrap items-start gap-x-3 gap-y-1.5">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                    <p className="min-w-0 flex-1 break-words text-sm font-medium leading-snug">
                      {g.query}
                    </p>
                    <Badge tone={handled ? 'muted' : meta?.tone ?? 'muted'}>
                      {meta?.label ?? g.kind}
                    </Badge>
                    {/* The count is the whole reason this page groups. It is
                        what tells you which gap to fill first. */}
                    <Badge tone={handled ? 'muted' : 'default'} className="tnum">
                      asked {g.occurrences}×
                    </Badge>
                  </div>

                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
                    {g.campaign_name && <span>{g.campaign_name}</span>}
                    <span>last {formatRelative(g.last_seen)}</span>
                    <span title={formatDateTime(g.first_seen)}>
                      first {formatRelative(g.first_seen)}
                    </span>
                    {g.worst_score != null && (
                      <span className="tnum">best match {g.worst_score.toFixed(2)}</span>
                    )}
                    {g.open_occurrences > 0 && g.open_occurrences < g.occurrences && (
                      <span>{g.open_occurrences} since it was last handled</span>
                    )}
                  </div>

                  {g.detail && (
                    <p className="break-words font-mono text-2xs text-muted-foreground">
                      {g.detail}
                    </p>
                  )}

                  {g.note && (
                    <p className="rounded-md border border-border bg-muted/40 p-2 text-2xs text-muted-foreground">
                      <span className="font-medium text-foreground/80">Handled</span>
                      {g.acknowledged_by_email ? ' by ' + g.acknowledged_by_email : ''}
                      {g.acknowledged_at ? ' · ' + formatRelative(g.acknowledged_at) : ''}
                      {' — '}{g.note}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-2">
                    {/* Listening to one is usually faster than reading about
                        it: the caller's own words say what they wanted. */}
                    {g.call_ids.slice(0, 4).map((id) => (
                      <Link key={id} to={'/calls/' + id}
                            className="text-2xs text-primary hover:underline">
                        call {id}
                      </Link>
                    ))}
                    {!handled && (
                      <Button size="sm" variant="outline" className="ml-auto"
                              onClick={() => { setHandling(g); setNote('') }}>
                        <Check className="size-3.5" />
                        Mark handled
                      </Button>
                    )}
                  </div>
                </CardBody>
              </Card>
            )
          })}
        </div>
      )}

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
            <div>
              <Label>What did you do about it?</Label>
              <Input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Uploaded the Splendor Flex brochure"
                autoFocus
              />
              <p className="mt-1.5 text-2xs leading-relaxed text-muted-foreground">
                Read by whoever finds this open again. If the question comes back
                it reappears as a new entry — that is the point: it means whatever
                was done did not close the gap.
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setHandling(null)}>
                Cancel
              </Button>
              <Button onClick={() => acknowledge.mutate(handling)}
                      disabled={acknowledge.isPending}>
                {acknowledge.isPending ? 'Saving…' : 'Mark handled'}
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
