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
  host: string
  port: string
  username: string
  // Never loaded from the server - it is not sent to the browser. Empty on an
  // edit means "leave the stored one alone", which is what the field says.
  secret: string
  hadSecret: boolean
}

const BLANK: Draft = {
  id: null, name: '', peer: '', description: '', active: true,
  host: '', port: '', username: '', secret: '', hadSecret: false,
}

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
          host: d.host.trim() || null,
          port: d.port.trim() ? Number(d.port) : null,
          username: d.username.trim() || null,
          // Omitted rather than sent empty: empty is an instruction to clear
          // it, and the commonest edit here is changing something else.
          secret: d.secret || null,
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
        description="Where a campaign's calls go when the agent hands them to a person. Add one here and it works on the next transfer — no server access, no restart."
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
            hint="Add one with the host, port, username and password from the dialler team. Until then campaigns keep using the transfer target typed on their own page."
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
                    {/* Which of the two kinds this is. Worth a glance before
                        wondering why an edit here changed nothing. */}
                    {!d.host && <Badge tone="muted">iax.conf</Badge>}
                    <Badge tone={d.campaign_count ? 'default' : 'muted'}>
                      {d.campaign_count} campaign{d.campaign_count === 1 ? '' : 's'}
                    </Badge>
                  </div>
                  {d.host && (
                    <p className="mt-0.5 font-mono text-2xs text-muted-foreground">
                      {d.username ?? d.peer}@{d.host}:{d.port ?? 4569}
                      {!d.has_secret && ' · no password set'}
                    </p>
                  )}
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
                          host: d.host ?? '',
                          port: d.port ? String(d.port) : '',
                          username: d.username ?? '',
                          secret: '',
                          hadSecret: d.has_secret,
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
              <Label htmlFor="d-peer">Peer name</Label>
              <Input
                id="d-peer"
                value={draft.peer}
                onChange={(e) => setDraft({ ...draft, peer: e.target.value })}
                placeholder="dialler-76"
                className="font-mono"
              />
              <p className="text-2xs leading-relaxed text-muted-foreground">
                What Asterisk calls this trunk —{' '}
                <span className="font-mono">IAX2/&lt;peer&gt;/&lt;extension&gt;</span>. Any
                name you like, as long as no two diallers share one. It is also the
                username sent, unless you set a different one below.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="d-desc">Description</Label>
              <Input
                id="d-desc"
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                placeholder="Sales floor, Worxpertise"
              />
            </div>

            {/* The connection itself. Everything above is ours; this is theirs,
                and it is what the dialler team hands over. */}
            <div className="space-y-3 rounded-lg border border-border/70 bg-muted/30 p-3">
              <div>
                <p className="text-xs font-medium">Connection</p>
                <p className="mt-0.5 text-2xs leading-relaxed text-muted-foreground">
                  From the dialler team. Saved here, it takes effect on the next
                  transfer — no restart, and nothing to change on the server.
                </p>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="col-span-2 space-y-1.5">
                  <Label htmlFor="d-host">Host</Label>
                  <Input
                    id="d-host"
                    value={draft.host}
                    onChange={(e) => setDraft({ ...draft, host: e.target.value })}
                    placeholder="10.130.8.76"
                    className="font-mono"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="d-port">Port</Label>
                  <Input
                    id="d-port"
                    value={draft.port}
                    onChange={(e) => setDraft({ ...draft, port: e.target.value })}
                    placeholder="4569"
                    inputMode="numeric"
                    className="font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="d-user">Username</Label>
                  <Input
                    id="d-user"
                    value={draft.username}
                    onChange={(e) => setDraft({ ...draft, username: e.target.value })}
                    placeholder={draft.peer || 'same as peer name'}
                    className="font-mono"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="d-secret">Password</Label>
                  <Input
                    id="d-secret"
                    type="password"
                    autoComplete="new-password"
                    value={draft.secret}
                    onChange={(e) => setDraft({ ...draft, secret: e.target.value })}
                    placeholder={draft.hadSecret ? 'unchanged' : ''}
                    className="font-mono"
                  />
                </div>
              </div>

              <p className="text-2xs leading-relaxed text-muted-foreground">
                {draft.hadSecret
                  ? 'A password is stored. It is never sent back to this page — leave the field empty to keep it, or type a new one to replace it.'
                  : 'Stored so Asterisk can authenticate, which needs the password itself and not a hash of it. It is never shown again after saving, and it is included in database backups — treat a backup the way you would treat the password.'}
              </p>

              {/* Half-filled is the combination that fails at the far end and
                  reads like the dialler being down. */}
              {draft.host && !draft.secret && !draft.hadSecret ? (
                <p className="text-2xs leading-relaxed text-amber-600 dark:text-amber-500">
                  A host with no password builds a trunk that cannot log in. That
                  looks like the dialler being down, not like a missing field.
                </p>
              ) : null}

              {!draft.host && (
                <p className="text-2xs leading-relaxed text-muted-foreground">
                  Leave this blank if the peer is already written into{' '}
                  <span className="font-mono">iax.conf</span> on the server. The row
                  then just names it.
                </p>
              )}
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
