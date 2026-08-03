import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Pause, Play, Plus } from 'lucide-react'
import { PageHeader } from '@/components/Layout'
import { DataTable, type Column } from '@/components/DataTable'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Input, Label } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api, ApiError } from '@/lib/api'
import { formatNumber, slugify } from '@/lib/utils'
import type { Tenant } from '@/types'

function CreateTenantDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [slugTouched, setSlugTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function reset() {
    setName('')
    setSlug('')
    setSlugTouched(false)
    setError(null)
  }

  const create = useMutation({
    mutationFn: () => api<Tenant>('/tenants', { method: 'POST', body: { name, slug } }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ['tenants'] })
      toast.success('Client created', `${t.name} can now have campaigns and users.`)
      reset()
      onClose()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not create the client'),
  })

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title="New client"
      description="A client owns its campaigns, users, knowledge base and calls. Nothing is shared between clients."
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
          <Button onClick={() => create.mutate()} loading={create.isPending} disabled={!name || !slug}>
            Create client
          </Button>
        </>
      }
    >
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <div className="space-y-1.5">
          <Label htmlFor="t-name">Name</Label>
          <Input
            id="t-name"
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              // The slug follows the name until it is edited by hand, then it
              // stops moving - retyping the name should not silently rewrite a
              // slug someone deliberately chose.
              if (!slugTouched) setSlug(slugify(e.target.value))
            }}
            placeholder="Acme Financial Services"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="t-slug">Slug</Label>
          <Input
            id="t-slug"
            value={slug}
            onChange={(e) => {
              setSlugTouched(true)
              setSlug(slugify(e.target.value))
            }}
            placeholder="acme-financial"
            className="font-mono"
          />
          <p className="text-2xs text-muted-foreground">
            Lowercase letters, digits and hyphens. Used in agent config names, so it cannot be
            changed later.
          </p>
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

export function Tenants() {
  const qc = useQueryClient()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const [confirm, setConfirm] = useState<Tenant | null>(null)

  const tenants = useQuery({ queryKey: ['tenants'], queryFn: () => api<Tenant[]>('/tenants') })

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: number; status: Tenant['status'] }) =>
      api<Tenant>(`/tenants/${id}`, { method: 'PATCH', body: { status } }),
    onSuccess: (t) => {
      qc.invalidateQueries({ queryKey: ['tenants'] })
      setConfirm(null)
      toast.success(
        t.status === 'suspended' ? 'Client suspended' : 'Client reactivated',
        t.status === 'suspended'
          ? `${t.name}'s users can no longer sign in. Existing calls are untouched.`
          : `${t.name}'s users can sign in again.`,
      )
    },
    onError: (e) => toast.error('Could not update the client', (e as Error).message),
  })

  const columns: Column<Tenant>[] = [
    {
      key: 'name',
      header: 'Client',
      render: (t) => (
        <div>
          <p className="font-medium">{t.name}</p>
          <p className="font-mono text-2xs text-muted-foreground">{t.slug}</p>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      render: (t) => (
        <Badge tone={t.status === 'active' ? 'success' : 'warning'}>{t.status}</Badge>
      ),
    },
    {
      key: 'campaigns',
      header: 'Campaigns',
      align: 'right',
      className: 'tnum',
      render: (t) => formatNumber(t.campaign_count),
    },
    {
      key: 'users',
      header: 'Users',
      align: 'right',
      className: 'tnum',
      render: (t) => formatNumber(t.user_count),
    },
    {
      key: 'calls',
      header: 'Calls',
      align: 'right',
      className: 'tnum',
      render: (t) => formatNumber(t.call_count),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (t) =>
        t.status === 'active' ? (
          <Button variant="outline" size="sm" onClick={() => setConfirm(t)}>
            <Pause className="h-3.5 w-3.5" />
            Suspend
          </Button>
        ) : (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setStatus.mutate({ id: t.id, status: 'active' })}
            loading={setStatus.isPending}
          >
            <Play className="h-3.5 w-3.5" />
            Reactivate
          </Button>
        ),
    },
  ]

  return (
    <div className="mx-auto max-w-[1200px] space-y-5 p-5 lg:p-7">
      <PageHeader
        title="Clients"
        description="Each client is a hard isolation boundary — campaigns, users, knowledge base and call history."
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New client
          </Button>
        }
      />

      <DataTable
        columns={columns}
        rows={tenants.data}
        isLoading={tenants.isLoading}
        error={tenants.error as Error | null}
        onRetry={() => tenants.refetch()}
        rowKey={(t) => t.id}
        empty={{
          icon: Building2,
          title: 'No clients yet',
          hint: 'Create a client, then give it campaigns and users.',
          action: (
            <Button size="sm" onClick={() => setCreating(true)}>
              <Plus className="h-3.5 w-3.5" />
              New client
            </Button>
          ),
        }}
      />

      <CreateTenantDialog open={creating} onClose={() => setCreating(false)} />

      <Dialog
        open={confirm !== null}
        onClose={() => setConfirm(null)}
        title={`Suspend ${confirm?.name ?? ''}?`}
        description="Their users will be signed out and refused at login. Campaigns, recordings and call history are kept."
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            <Button
              variant="danger"
              loading={setStatus.isPending}
              onClick={() => confirm && setStatus.mutate({ id: confirm.id, status: 'suspended' })}
            >
              Suspend client
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted-foreground">
          Live calls already in progress are not cut off — only console access is blocked. Reactivate
          at any time.
        </p>
      </Dialog>
    </div>
  )
}
