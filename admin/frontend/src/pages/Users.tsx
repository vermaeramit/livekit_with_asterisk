import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Plus, Trash2, UserCog, Users2 } from 'lucide-react'
import { PageHeader } from '@/components/Layout'
import { DataTable, type Column } from '@/components/DataTable'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Input, Label, Select } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatRelative } from '@/lib/utils'
import type { Role, Tenant, User } from '@/types'

const ROLE_LABEL: Record<Role, string> = {
  superadmin: 'Super Admin',
  tenant_admin: 'Administrator',
  agent: 'Agent',
  viewer: 'Viewer',
}

const ROLE_HELP: Record<Role, string> = {
  superadmin: 'Every client, every setting. Belongs to no client.',
  tenant_admin: 'Manages this client’s campaigns, knowledge base and users.',
  agent: 'Reads calls and transcripts. Cannot change configuration.',
  viewer: 'Read-only access to calls and reports.',
}

/** A password field with a generator — an admin typing one by hand picks a weak
 *  one, and the user has to replace it on first sign-in anyway. */
function PasswordField({
  id,
  value,
  onChange,
}: {
  id: string
  value: string
  onChange: (v: string) => void
}) {
  function generate() {
    const alphabet = 'abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    const bytes = new Uint32Array(18)
    crypto.getRandomValues(bytes)
    onChange(Array.from(bytes, (b) => alphabet[b % alphabet.length]).join(''))
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>Initial password</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="At least 12 characters"
          className="font-mono"
        />
        <Button type="button" variant="outline" onClick={generate} className="shrink-0">
          Generate
        </Button>
      </div>
      <p className="text-2xs text-muted-foreground">
        Share it once, over a channel you trust. They must change it before the console will let
        them in.
      </p>
    </div>
  )
}

function CreateUserDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const isSuper = user?.role === 'superadmin'

  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [tenantId, setTenantId] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const tenants = useQuery({
    queryKey: ['tenants'],
    queryFn: () => api<Tenant[]>('/tenants'),
    enabled: isSuper && open,
  })

  function reset() {
    setEmail('')
    setName('')
    setRole('viewer')
    setTenantId('')
    setPassword('')
    setError(null)
  }

  const create = useMutation({
    mutationFn: () =>
      api<User>('/users', {
        method: 'POST',
        body: {
          email,
          name: name || null,
          role,
          password,
          ...(isSuper && role !== 'superadmin' ? { tenant_id: Number(tenantId) } : {}),
        },
      }),
    onSuccess: (u) => {
      qc.invalidateQueries({ queryKey: ['users'] })
      qc.invalidateQueries({ queryKey: ['tenants'] })
      toast.success('User created', `${u.email} must change the password at first sign-in.`)
      reset()
      onClose()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not create the user'),
  })

  const needsTenant = isSuper && role !== 'superadmin'
  const ready = email && password.length >= 12 && (!needsTenant || tenantId)

  return (
    <Dialog
      open={open}
      onClose={() => {
        reset()
        onClose()
      }}
      title="New user"
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
            Create user
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
        <div className="space-y-1.5">
          <Label htmlFor="u-email">Email</Label>
          <Input
            id="u-email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="person@client.com"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="u-name">Name</Label>
          <Input
            id="u-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Optional"
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="u-role">Role</Label>
          <Select id="u-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(isSuper
              ? (['superadmin', 'tenant_admin', 'agent', 'viewer'] as Role[])
              : (['tenant_admin', 'agent', 'viewer'] as Role[])
            ).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </Select>
          <p className="text-2xs text-muted-foreground">{ROLE_HELP[role]}</p>
        </div>

        {needsTenant && (
          <div className="space-y-1.5">
            <Label htmlFor="u-tenant">Client</Label>
            <Select id="u-tenant" value={tenantId} onChange={(e) => setTenantId(e.target.value)}>
              <option value="">Select a client…</option>
              {tenants.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
          </div>
        )}

        <PasswordField id="u-password" value={password} onChange={setPassword} />

        {error && (
          <p className="rounded-md bg-danger/10 p-2.5 text-xs text-danger ring-1 ring-inset ring-danger/20">
            {error}
          </p>
        )}
      </form>
    </Dialog>
  )
}

function ResetPasswordDialog({ target, onClose }: { target: User | null; onClose: () => void }) {
  const toast = useToast()
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)

  const reset = useMutation({
    mutationFn: () =>
      api<User>(`/users/${target!.id}/password`, { method: 'POST', body: { password } }),
    onSuccess: () => {
      toast.success(
        'Password reset',
        `${target!.email} has been signed out everywhere and must set a new password.`,
      )
      setPassword('')
      setError(null)
      onClose()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not reset the password'),
  })

  return (
    <Dialog
      open={target !== null}
      onClose={() => {
        setPassword('')
        setError(null)
        onClose()
      }}
      title={`Reset password for ${target?.email ?? ''}`}
      description="Every existing session for this user is revoked immediately."
      footer={
        <>
          <Button
            variant="ghost"
            onClick={() => {
              setPassword('')
              onClose()
            }}
          >
            Cancel
          </Button>
          <Button
            onClick={() => reset.mutate()}
            loading={reset.isPending}
            disabled={password.length < 12}
          >
            Reset password
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <PasswordField id="r-password" value={password} onChange={setPassword} />
        {error && (
          <p className="rounded-md bg-danger/10 p-2.5 text-xs text-danger ring-1 ring-inset ring-danger/20">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  )
}

export function Users() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const toast = useToast()
  const isSuper = user?.role === 'superadmin'

  const [creating, setCreating] = useState(false)
  const [resetting, setResetting] = useState<User | null>(null)
  const [deleting, setDeleting] = useState<User | null>(null)

  const users = useQuery({ queryKey: ['users'], queryFn: () => api<User[]>('/users') })

  const toggle = useMutation({
    mutationFn: (u: User) =>
      api<User>(`/users/${u.id}`, { method: 'PATCH', body: { active: !u.active } }),
    onSuccess: (u) => {
      qc.invalidateQueries({ queryKey: ['users'] })
      toast.success(u.active ? 'User enabled' : 'User disabled')
    },
    onError: (e) => toast.error('Could not update the user', (e as Error).message),
  })

  const remove = useMutation({
    mutationFn: (u: User) => api<void>(`/users/${u.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      setDeleting(null)
      toast.success('User deleted')
    },
    onError: (e) => {
      setDeleting(null)
      toast.error('Could not delete the user', (e as Error).message)
    },
  })

  const columns: Column<User>[] = [
    {
      key: 'email',
      header: 'User',
      render: (u) => (
        <div>
          <p className="font-medium">
            {u.name || u.email}
            {u.id === user?.id && (
              <span className="ml-1.5 text-2xs font-normal text-muted-foreground">(you)</span>
            )}
          </p>
          {u.name && <p className="text-2xs text-muted-foreground">{u.email}</p>}
        </div>
      ),
    },
    {
      key: 'role',
      header: 'Role',
      render: (u) => (
        <Badge tone={u.role === 'superadmin' ? 'info' : 'default'}>{ROLE_LABEL[u.role]}</Badge>
      ),
    },
    ...(isSuper
      ? [
          {
            key: 'tenant',
            header: 'Client',
            render: (u: User) => (
              <span className="text-muted-foreground">{u.tenant_name ?? 'All clients'}</span>
            ),
          } as Column<User>,
        ]
      : []),
    {
      key: 'status',
      header: 'Status',
      render: (u) =>
        !u.active ? (
          <Badge tone="danger">disabled</Badge>
        ) : u.must_change_password ? (
          <Badge tone="warning">password pending</Badge>
        ) : (
          <Badge tone="success">active</Badge>
        ),
    },
    {
      key: 'last_login',
      header: 'Last sign-in',
      render: (u) => (
        <span className="text-muted-foreground">
          {u.last_login_at ? formatRelative(u.last_login_at) : 'never'}
        </span>
      ),
    },
    {
      key: 'actions',
      header: '',
      align: 'right',
      render: (u) => (
        <div className="flex items-center justify-end gap-1.5">
          <Button variant="outline" size="sm" onClick={() => setResetting(u)}>
            <KeyRound className="h-3.5 w-3.5" />
            Reset
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={u.id === user?.id}
            title={u.id === user?.id ? 'You cannot disable your own account' : undefined}
            onClick={() => toggle.mutate(u)}
            loading={toggle.isPending && toggle.variables?.id === u.id}
          >
            <UserCog className="h-3.5 w-3.5" />
            {u.active ? 'Disable' : 'Enable'}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={u.id === user?.id}
            onClick={() => setDeleting(u)}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      ),
    },
  ]

  return (
    <div className="mx-auto max-w-[1300px] space-y-5 p-5 lg:p-7">
      <PageHeader
        title="Users"
        description={
          isSuper
            ? 'Everyone with console access, across every client.'
            : 'Everyone in your organisation with console access.'
        }
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus className="h-4 w-4" />
            New user
          </Button>
        }
      />

      <DataTable
        columns={columns}
        rows={users.data}
        isLoading={users.isLoading}
        error={users.error as Error | null}
        onRetry={() => users.refetch()}
        rowKey={(u) => u.id}
        empty={{
          icon: Users2,
          title: 'No users yet',
          hint: 'Create an account and share the initial password over a channel you trust.',
        }}
      />

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} />
      <ResetPasswordDialog target={resetting} onClose={() => setResetting(null)} />

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.email ?? ''}?`}
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
              Delete user
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted-foreground">
          Their sessions end immediately and this cannot be undone. Changes they made stay in the
          audit log. If you only want to block access for now, disable the account instead.
        </p>
      </Dialog>
    </div>
  )
}
