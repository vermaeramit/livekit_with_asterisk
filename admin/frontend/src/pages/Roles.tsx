import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Lock, Plus, ShieldCheck, Trash2, Users2 } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Toggle } from '@/components/ui/field'
import { Badge, Card, EmptyState, Input, Label, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import type { Permission, PermissionInfo, RoleDef } from '@/types'

type Draft = {
  id: number | null
  key: string
  name: string
  description: string
  all_tenants: boolean
  permissions: Permission[]
}

const BLANK: Draft = {
  id: null,
  key: '',
  name: '',
  description: '',
  all_tenants: false,
  permissions: [],
}

export function Roles() {
  const toast = useToast()
  const qc = useQueryClient()
  const { user } = useAuth()
  const [draft, setDraft] = useState<Draft | null>(null)

  // Only somebody who sees every client may change a role. Everyone else with
  // users.manage reads the list so they have something to assign from.
  const canEdit = Boolean(user?.all_tenants)

  const roles = useQuery({
    queryKey: ['roles'],
    queryFn: () => api<RoleDef[]>('/roles'),
  })

  const perms = useQuery({
    queryKey: ['permissions'],
    queryFn: () => api<PermissionInfo[]>('/permissions'),
  })

  // Grouped for the editor, in the order the backend returns them.
  const grouped = useMemo(() => {
    const out: Record<string, PermissionInfo[]> = {}
    for (const p of perms.data ?? []) (out[p.group] ??= []).push(p)
    return out
  }, [perms.data])

  const save = useMutation({
    mutationFn: (d: Draft) =>
      api(d.id ? `/roles/${d.id}` : '/roles', {
        method: d.id ? 'PUT' : 'POST',
        body: {
          key: d.key.trim(),
          name: d.name.trim(),
          description: d.description.trim() || null,
          all_tenants: d.all_tenants,
          permissions: d.permissions,
        },
      }),
    onSuccess: () => {
      setDraft(null)
      qc.invalidateQueries({ queryKey: ['roles'] })
      toast.success('Role saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save that'),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api(`/roles/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['roles'] })
      toast.success('Role removed')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not remove that'),
  })

  const label = (key: string) =>
    perms.data?.find((p) => p.key === key)?.label ?? key

  return (
    <div className={PAGE}>
      <PageHeader
        title="Roles"
        description="What each role may do. Roles belong to the platform — a client assigns its people to them and cannot change them."
        actions={
          canEdit ? (
            <Button size="sm" onClick={() => setDraft({ ...BLANK })}>
              <Plus className="h-3.5 w-3.5" />
              New role
            </Button>
          ) : undefined
        }
      />

      <Card className="overflow-hidden">
        {roles.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-14 w-full" />
            ))}
          </div>
        ) : !roles.data?.length ? (
          <EmptyState icon={ShieldCheck} title="No roles" hint="Migration 030 seeds four." />
        ) : (
          <div className="divide-y divide-border/70">
            {roles.data.map((r) => (
              <div key={r.id} className="flex items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{r.name}</span>
                    <span className="font-mono text-2xs text-muted-foreground">{r.key}</span>
                    {r.builtin && (
                      <Badge tone="muted">
                        <Lock className="mr-1 inline h-3 w-3" />
                        built in
                      </Badge>
                    )}
                    {r.all_tenants && <Badge tone="warning">every client</Badge>}
                    <span className="inline-flex items-center gap-1 text-2xs text-muted-foreground">
                      <Users2 className="h-3 w-3" />
                      {r.user_count}
                    </span>
                  </div>
                  {r.description && (
                    <p className="mt-0.5 text-2xs text-muted-foreground">{r.description}</p>
                  )}
                  <p className="mt-1 text-2xs text-muted-foreground">
                    {r.permissions.length
                      ? r.permissions.map(label).join(' · ')
                      : 'nothing — this role can sign in and do no more'}
                  </p>
                </div>

                {canEdit && !r.builtin && (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setDraft({
                          id: r.id,
                          key: r.key,
                          name: r.name,
                          description: r.description ?? '',
                          all_tenants: r.all_tenants,
                          permissions: [...r.permissions],
                        })
                      }
                    >
                      Edit
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => remove.mutate(r.id)}
                      aria-label={`Remove ${r.name}`}
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
        title={draft?.id ? 'Edit role' : 'New role'}
      >
        {draft && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="rn">Name</Label>
                <Input
                  id="rn"
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  placeholder="Quality"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rk">Key</Label>
                <Input
                  id="rk"
                  value={draft.key}
                  onChange={(e) => setDraft({ ...draft, key: e.target.value })}
                  placeholder="quality"
                  disabled={draft.id != null}
                />
                <p className="text-2xs text-muted-foreground">
                  {draft.id != null
                    ? 'Fixed once created — users refer to it.'
                    : 'Lowercase, no spaces.'}
                </p>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rd">Description</Label>
              <Input
                id="rd"
                value={draft.description}
                onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                placeholder="Listens to calls and checks quality"
              />
            </div>

            {Object.entries(grouped).map(([group, list]) => (
              <div key={group} className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">{group}</p>
                <div className="space-y-1.5 rounded-md border border-border p-3">
                  {list.map((p) => (
                    <Toggle
                      key={p.key}
                      label={p.label}
                      hint={p.description}
                      checked={draft.permissions.includes(p.key)}
                      onChange={(on) =>
                        setDraft({
                          ...draft,
                          permissions: on
                            ? [...draft.permissions, p.key]
                            : draft.permissions.filter((x) => x !== p.key),
                        })
                      }
                    />
                  ))}
                </div>
              </div>
            ))}

            <Toggle
              label="Sees every client"
              checked={draft.all_tenants}
              onChange={(v) => setDraft({ ...draft, all_tenants: v })}
              hint="Separate from the permissions above: this decides WHOSE data the role can reach, not what it may do with it. Very few roles should have it."
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
