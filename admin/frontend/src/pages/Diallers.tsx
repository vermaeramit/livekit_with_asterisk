import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Radio, Trash2 } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Toggle } from '@/components/ui/field'
import { Badge, Card, EmptyState, Input, Label, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatRelative } from '@/lib/utils'
import type { Dialler } from '@/types'

type Draft = {
  id: number | null
  name: string
  peer: string
  description: string
  active: boolean
}

const BLANK: Draft = { id: null, name: '', peer: '', description: '', active: true }

export function Diallers() {
  const toast = useToast()
  const qc = useQueryClient()
  const { user } = useAuth()
  const [draft, setDraft] = useState<Draft | null>(null)

  // A dialler is half a row here and half a peer in iax.conf. Only somebody who
  // can create that peer should be adding rows that point at one.
  const canEdit = Boolean(user?.all_tenants)

  const diallers = useQuery({
    queryKey: ['diallers'],
    queryFn: () => api<Dialler[]>('/diallers'),
  })

  const save = useMutation({
    mutationFn: (d: Draft) =>
      api(d.id ? `/diallers/${d.id}` : '/diallers', {
        method: d.id ? 'PUT' : 'POST',
        body: {
          name: d.name.trim(),
          peer: d.peer.trim(),
          description: d.description.trim() || null,
          active: d.active,
        },
      }),
    onSuccess: () => {
      setDraft(null)
      qc.invalidateQueries({ queryKey: ['diallers'] })
      toast.success('Dialler saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save that'),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api(`/diallers/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['diallers'] })
      toast.success('Dialler removed')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not remove that'),
  })

  return (
    <div className={PAGE}>
      <PageHeader
        title="Diallers"
        description="Where a campaign's calls go when the agent hands them to a person. The credentials live in iax.conf on the server; this is which campaign uses which."
        actions={
          canEdit ? (
            <Button size="sm" onClick={() => setDraft({ ...BLANK })}>
              <Plus className="h-3.5 w-3.5" />
              Add a dialler
            </Button>
          ) : undefined
        }
      />

      <Card className="overflow-hidden">
        {diallers.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : !diallers.data?.length ? (
          <EmptyState
            icon={Radio}
            title="No diallers"
            hint="Add the peer to iax.conf on the server first, then a row here naming it. Until then campaigns keep using the transfer target typed on their own page."
          />
        ) : (
          <div className="divide-y divide-border/70">
            {diallers.data.map((d) => (
              <div key={d.id} className="flex items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{d.name}</span>
                    {/* The name people read is not the name Asterisk dials. */}
                    <span className="font-mono text-2xs text-muted-foreground">
                      IAX2/{d.peer}
                    </span>
                    {!d.active && <Badge tone="muted">inactive</Badge>}
                    <Badge tone={d.campaign_count ? 'default' : 'muted'}>
                      {d.campaign_count} campaign{d.campaign_count === 1 ? '' : 's'}
                    </Badge>
                  </div>
                  {d.description && (
                    <p className="mt-0.5 text-2xs text-muted-foreground">{d.description}</p>
                  )}
                  <p className="mt-0.5 text-2xs text-muted-foreground">
                    changed {formatRelative(d.updated_at)}
                  </p>
                </div>

                {canEdit && (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setDraft({
                          id: d.id,
                          name: d.name,
                          peer: d.peer,
                          description: d.description ?? '',
                          active: d.active,
                        })
                      }
                    >
                      Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => remove.mutate(d.id)}
                      aria-label={`Remove ${d.name}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Dialog
        open={draft !== null}
        onClose={() => setDraft(null)}
        title={draft?.id ? 'Edit dialler' : 'Add a dialler'}
      >
        {draft && (
          <div className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="d-name">Name</Label>
              <Input
                id="d-name"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                placeholder="Hero MotoCorp dialler"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="d-peer">iax.conf peer</Label>
              <Input
                id="d-peer"
                value={draft.peer}
                onChange={(e) => setDraft({ ...draft, peer: e.target.value })}
                placeholder="dialler-76"
                className="font-mono"
              />
              <p className="text-2xs leading-relaxed text-muted-foreground">
                {/* This is the field that fails late if it is wrong. */}
                Must match a section in <span className="font-mono">iax.conf</span> exactly —
                Asterisk dials <span className="font-mono">IAX2/&lt;peer&gt;/&lt;extension&gt;</span>.
                The host, port, username and secret live there, not here. A name that
                does not exist only fails at the last step, after the caller has been
                told to hold.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="d-desc">Description</Label>
              <Input
                id="d-desc"
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                placeholder="10.130.8.76 — sales floor"
              />
            </div>

            <Toggle
              label="Active"
              checked={draft.active}
              onChange={(v) => setDraft({ ...draft, active: v })}
              hint="Turning this off stops transfers to it immediately — including for campaigns already pointed at it, which then fall through to the no-route message. Use it to take a dialler out of service without editing every campaign."
            />

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setDraft(null)}>
                Cancel
              </Button>
              <Button onClick={() => save.mutate(draft)} loading={save.isPending}>
                Save
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
