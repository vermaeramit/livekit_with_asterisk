import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, Pencil, Plus, Trash2, UserCog, Users2 } from 'lucide-react'
import { PAGE, PageHeader } from '@/components/Layout'
import { DataTable, type Column } from '@/components/DataTable'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Input, Label, Select } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { formatRelative } from '@/lib/utils'
import type { Role, Tenant, User, RoleDef } from '@/types'

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

  // The backend returns only the roles this user could actually hand out, so
  // an option that appears here is one the save will accept.
  const roles = useQuery({
    queryKey: ['roles'],
    queryFn: () => api<RoleDef[]>('/roles'),
    enabled: open,
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
          {/* From the API, not a list in here. Roles are rows now, and a
              hardcoded list means a role somebody creates can never be handed
              to anyone. The backend already returns only what this user is
              allowed to assign, so there is nothing here to filter. */}
          <Select id="u-role" value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {roles.data?.map((r) => (
              <option key={r.id} value={r.key}>
                {r.name}
              </option>
            ))}
          </Select>
          <p className="text-2xs text-muted-foreground">
            {roles.data?.find((r) => r.key === role)?.description ?? ROLE_HELP[role] ?? ''}
          </p>
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

/**
 * Change a user's name or role.
 *
 * The API has always accepted this; the console only ever sent `active`, so a
 * user's role was fixed at the moment they were created. That was survivable
 * while there were four roles nobody could change. It is not now: a role you
 * create is worth nothing if there is no way to move anybody into it.
 *
 * Only what changed is sent. A PATCH carrying fields nobody touched is a PATCH
 * that overwrites an edit somebody else made while this dialog was open.
 */
function EditUserDialog({
  target,
  onClose,
}: {
  target: User | null
  onClose: () => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const { user: me } = useAuth()
  const [name, setName] = useState('')
  const [role, setRole] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setName(target?.name ?? '')
    setRole(target?.role ?? '')
    setError(null)
  }, [target])

  const roles = useQuery({
    queryKey: ['roles'],
    queryFn: () => api<RoleDef[]>('/roles'),
    enabled: target !== null,
  })

  const save = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {}
      if (name.trim() !== (target?.name ?? '')) body.name = name.trim() || null
      if (role !== target?.role) body.role = role
      return api<User>(`/users/${target!.id}`, { method: 'PATCH', body })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      // Your own role can change what the console is allowed to show you.
      qc.invalidateQueries({ queryKey: ['roles'] })
      toast.success('User updated')
      onClose()
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save that'),
  })

  const dirty =
    target != null && (name.trim() !== (target.name ?? '') || role !== target.role)
  const ownRole = target?.id === me?.id && role !== target?.role

  return (
    <Dialog open={target !== null} onClose={onClose} title="Edit user">
      {target && (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{target.email}</p>

          <div className="space-y-1.5">
            <Label htmlFor="e-name">Name</Label>
            <Input
              id="e-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Optional"
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="e-role">Role</Label>
            <Select id="e-role" value={role} onChange={(e) => setRole(e.target.value)}>
              {/* The role they currently hold may be one this admin cannot
                  assign. Kept in the list so the field is not silently blank -
                  the save would refuse it, which is the correct answer. */}
              {!roles.data?.some((r) => r.key === target.role) && (
                <option value={target.role}>{target.role}</option>
              )}
              {roles.data?.map((r) => (
                <option key={r.id} value={r.key}>
                  {r.name}
                </option>
              ))}
            </Select>
            <p className="text-2xs text-muted-foreground">
              {roles.data?.find((r) => r.key === role)?.description ?? ''}
            </p>
          </div>

          {ownRole && (
            <p className="text-2xs leading-relaxed text-warning">
              This is your own account. Changing your role changes what you can
              reach, immediately — including, possibly, this page.
            </p>
          )}

          {error && <p className="text-2xs text-danger">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!dirty}>
              Save
            </Button>
          </div>
        </div>
      )}
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
  const [editing, setEditing] = useState<User | null>(null)
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
          <Button variant="outline" size="sm" onClick={() => setEditing(u)}>
            <Pencil className="h-3.5 w-3.5" />
            Edit
          </Button>
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
    <div className={PAGE}>
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
      <EditUserDialog target={editing} onClose={() => setEditing(null)} />
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
