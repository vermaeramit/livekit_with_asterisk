import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Info, Megaphone, Pencil, Plus, Power, Settings2, Trash2 } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { DataTable, type Column } from '@/components/DataTable'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Input, Label, Select } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatNumber, slugify } from '@/lib/utils'
import type { Campaign, Tenant } from '@/types'

function CreateCampaignDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const isSuper = user?.role === 'superadmin'

  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [description, setDescription] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [error, setError] = useState<string | null>(null)

  const tenants = useQuery({
    queryKey: ['tenants'],
    queryFn: () => api<Tenant[]>('/tenants'),
    enabled: isSuper && open,
  })

  function reset() {
    setName('')
    setSlug('')
    setSlugTouched(false)
    setDescription('')
    setTenantId('')
    setError(null)
  }

  const create = useMutation({
    mutationFn: () =>
      api<Campaign>('/campaigns', {
        method: 'POST',
        body: {
          name,
          slug,
          description: description || null,
          ...(isSuper ? { tenant_id: Number(tenantId) } : {}),
        },
      }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ['campaigns'] })
      qc.invalidateQueries({ queryKey: ['tenants'] })
      toast.success(
        'Campaign created',
        `An agent config named ${c.config_name} was created with it — edit its prompt and voice next.`,
      )
      reset()
      onClose()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not create the campaign'),
  })

  const ready = name && slug && (!isSuper || tenantId)

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title="New campaign"
      description="A campaign carries its own prompt, knowledge base and voice — sales, support and collection can behave completely differently."
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => {
              reset()
              onClose()
            }}
          >
            Cancel
          </Button>
          <Button onClick={() => create.mutate()} loading={create.isPending} disabled={!ready}>
            Create campaign
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (ready) create.mutate()
        }}
      >
        {isSuper && (
          <div className="space-y-1.5">
            <Label htmlFor="c-tenant">Client</Label>
            <Select id="c-tenant" value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
              <option value="">Select a client…</option>
              {tenants.data
                ?.filter((t) => t.status === 'active')
                .map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
            </Select>
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="c-name">Name</Label>
          <Input
            id="c-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              if (!slugTouched) setSlug(slugify(e.target.value))
            }}
            placeholder="Collections — Hindi"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="c-slug">Slug</Label>
          <Input
            id="c-slug"
            value={slug}
            onChange={(e) => {
              setSlugTouched(true)
              setSlug(slugify(e.target.value))
            }}
            placeholder="collections-hindi"
            className="font-mono"
          />
          <p className="text-2xs text-muted-foreground">
            Becomes part of the agent config name the workers key on, so it cannot be changed later.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="c-desc">Description</Label>
          <Input
            id="c-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional"
          />
        </div>

        {error && (
          <p className="rounded-md bg-danger/10 p-2.5 text-xs text-danger ring-1 ring-inset ring-danger/20">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  )
}

function EditCampaignDialog({
  campaign,
  onClose,
}: {
  campaign: Campaign | null
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')

  useEffect(() => {
    if (!campaign) return
    setName(campaign.name)
    setDescription(campaign.description ?? '')
  }, [campaign])

  const save = useMutation({
    mutationFn: () =>
      api<Campaign>(`/campaigns/${campaign!.id}`, {
        method: 'PATCH',
        // Only what changed. PATCH treats an absent field as "leave it", so
        // sending the whole object would overwrite anything edited elsewhere
        // between this dialog opening and being saved.
        body: {
          ...(name.trim() !== campaign!.name ? { name: name.trim() } : {}),
          ...(description.trim() !== (campaign!.description ?? '')
            ? { description: description.trim() || null }
            : {}),
        },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['campaigns'] })
      onClose()
      toast.success('Campaign updated')
    },
    onError: (e) => toast.error('Could not save', (e as Error).message),
  })

  const dirty =
    campaign !== null &&
    (name.trim() !== campaign.name ||
      description.trim() !== (campaign.description ?? ''))

  return (
    <Dialog open={campaign !== null} onClose={onClose} title="Edit campaign">
      {campaign && (
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="e-name">Name</Label>
            <Input
              id="e-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="e-desc">Description</Label>
            <Input
              id="e-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this campaign is for"
            />
          </div>

          <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
            <p className="text-2xs text-muted-foreground">
              Slug{' '}
              <span className="font-mono text-foreground">{campaign.slug}</span>
              {campaign.config_name && (
                <>
                  {' · '}config{' '}
                  <span className="font-mono text-foreground">{campaign.config_name}</span>
                </>
              )}
            </p>
            {/* Said here rather than shown as a disabled field, which invites
                somebody to look for the way to enable it. */}
            <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">
              These cannot be changed. The agent loads its configuration by
              name, and routes, knowledge-base documents and call history all
              refer to it — renaming would leave a live campaign unable to find
              its own prompt.
            </p>
          </div>

          <p className="text-2xs leading-relaxed text-muted-foreground">
            The prompt, voice, knowledge base and routing are under{' '}
            <strong className="font-medium">Configure</strong>. Enabling and
            disabling is the button on the row.
          </p>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              onClick={() => save.mutate()}
              loading={save.isPending}
              disabled={!dirty || !name.trim()}
            >
              Save
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  )
}

export function Campaigns() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const [deleting, setDeleting] = useState<Campaign | null>(null)
  const [editing, setEditing] = useState<Campaign | null>(null)
  const isSuper = user?.role === 'superadmin'

  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Campaign[]>('/campaigns'),
  })

  const toggle = useMutation({
    mutationFn: (c: Campaign) =>
      api<Campaign>(`/campaigns/${c.id}`, { method: 'PATCH', body: { enabled: !c.enabled } }),
    onSuccess: (c) => {
      qc.invalidateQueries({ queryKey: ['campaigns'] })
      toast.success(c.enabled ? 'Campaign enabled' : 'Campaign disabled')
    },
    onError: (e) => toast.error('Could not update the campaign', (e as Error).message),
  })

  const remove = useMutation({
    mutationFn: (c: Campaign) => api<void>(`/campaigns/${c.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['campaigns'] })
      setDeleting(null)
      toast.success('Campaign deleted')
    },
    onError: (e) => {
      setDeleting(null)
      toast.error('Could not delete the campaign', (e as Error).message)
    },
  })

  const columns: Column<Campaign>[] = [
    {
      key: 'name',
      header: 'Campaign',
      render: (c) => (
        <div>
          <p className="font-medium">{c.name}</p>
          <p className="font-mono text-2xs text-muted-foreground">{c.config_name ?? c.slug}</p>
        </div>
      ),
    },
    ...(isSuper
      ? [
          {
            key: 'tenant',
            header: 'Client',
            render: (c: Campaign) => (
              <span className="text-muted-foreground">{c.tenant_name ?? '—'}</span>
            ),
          } as Column<Campaign>,
        ]
      : []),
    {
      key: 'description',
      header: 'Description',
      render: (c) => (
        <span className="text-muted-foreground">{c.description || '—'}</span>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (c) => (
        <Badge tone={c.enabled ? 'success' : 'muted'}>{c.enabled ? 'enabled' : 'disabled'}</Badge>
      ),
    },
    {
      key: 'calls',
      header: 'Calls',
      align: 'right',
      className: 'tnum',
      render: (c) => formatNumber(c.call_count),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (c) => (
        <div className="flex items-center justify-end gap-1.5">
          <Button variant="outline" size="sm" onClick={() => setEditing(c)}>
            <Pencil className="h-3.5 w-3.5" />
            Edit
          </Button>
          <Button variant="outline" size="sm" onClick={() => navigate(`/campaigns/${c.id}/config`)}>
            <Settings2 className="h-3.5 w-3.5" />
            Configure
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => toggle.mutate(c)}
            loading={toggle.isPending && toggle.variables?.id === c.id}
          >
            <Power className="h-3.5 w-3.5" />
            {c.enabled ? 'Disable' : 'Enable'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setDeleting(c)}
            title={
              c.call_count > 0
                ? 'Campaigns with call history cannot be deleted'
                : 'Delete this campaign'
            }
            disabled={c.call_count > 0}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div className={PAGE}>
      <PageHeader
        title="Campaigns"
        description="Prompt, knowledge base and voice live here — one client can run sales, support and collection side by side."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New campaign
          </Button>
        }
      />

      <div className="flex items-start gap-2 rounded-md bg-primary/5 p-3 text-xs text-muted-foreground ring-1 ring-inset ring-primary/15">
        <Info className="mt-px h-3.5 w-3.5 shrink-0 text-primary" />
        <span className="leading-relaxed">
          A campaign only receives calls once an inbound number points at it —
          set that under <strong>Configure → Routing</strong>. Disabling a campaign takes
          effect on the next call: it is dropped straight to your human extension rather
          than answered.
        </span>
      </div>

      <DataTable
        columns={columns}
        rows={campaigns.data}
        isLoading={campaigns.isLoading}
        error={campaigns.error as Error | null}
        onRetry={() => campaigns.refetch()}
        rowKey={(c) => c.id}
        empty={{
          icon: Megaphone,
          title: 'No campaigns yet',
          hint: 'Create a campaign to give an agent its own prompt, knowledge base and voice.',
          action: (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" />
              New campaign
            </Button>
          ),
        }}
      />

      <CreateCampaignDialog open={creating} onClose={() => setCreating(false)} />
      <EditCampaignDialog campaign={editing} onClose={() => setEditing(null)} />

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.name ?? ''}?`}
        description="This also removes its agent config and knowledge base."
        footer={
          <>
            <Button variant="ghost" onClick={() => setDeleting(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={remove.isPending}
              onClick={() => deleting && remove.mutate(deleting)}
            >
              Delete campaign
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted-foreground">
          This cannot be undone. Campaigns that already have calls on record cannot be deleted —
          disable them instead.
        </p>
      </Dialog>
    </div>
  )
}
