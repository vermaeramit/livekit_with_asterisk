import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound, ShieldAlert, Trash2, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Input, Label, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import type { ProviderKey, ProviderKeyWritten } from '@/types'

type Scope = 'client' | 'campaign'

const PROVIDERS: Record<string, { label: string; used: string }> = {
  openai: { label: 'OpenAI', used: 'Language model, and knowledge-base embeddings' },
  sarvam: { label: 'Sarvam', used: 'Speech to text, and the voice' },
}

function when(iso: string | null) {
  if (!iso) return null
  return new Date(iso).toLocaleDateString(undefined, {
    day: 'numeric', month: 'short', year: 'numeric',
  })
}

/**
 * Set the API keys a client's calls run on.
 *
 * Keys are write-only everywhere - here, in the API, and in the audit trail.
 * Once saved, the only thing anyone can see is the last four characters, which
 * is enough to answer "is this the key I pasted?" and nothing else. That is why
 * there is no "show key" affordance: there is nothing to show.
 */
export function ProviderKeys({ scope, id }: { scope: Scope; id: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const base = scope === 'client' ? `/clients/${id}` : `/campaigns/${id}`

  const [editing, setEditing] = useState<string | null>(null)
  const [value, setValue] = useState('')
  const [error, setError] = useState<string | null>(null)

  const keys = useQuery({
    queryKey: ['provider-keys', scope, id],
    queryFn: () => api<ProviderKey[]>(`${base}/keys`),
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['provider-keys', scope, id] })
    // The campaign list shows enabled/disabled, and setting the last missing
    // key is what makes a campaign eligible to be switched on.
    qc.invalidateQueries({ queryKey: ['campaigns'] })
  }

  function close() {
    setEditing(null)
    setValue('')
    setError(null)
  }

  const save = useMutation({
    mutationFn: (provider: string) =>
      api<ProviderKeyWritten>(`${base}/keys/${provider}`, {
        method: 'PUT',
        body: { key: value.trim() },
      }),
    onSuccess: (r) => {
      refresh()
      close()
      if (r.no_credits) {
        toast.error(
          `${PROVIDERS[r.provider]?.label ?? r.provider} key saved, but the account has no credits`,
          'The key is valid. Calls will fail until the provider account is topped up.',
        )
      } else {
        toast.success(`${PROVIDERS[r.provider]?.label ?? r.provider} key saved`,
          'Applies from the next call.')
      }
    },
    // The provider's own rejection is the useful message here - "OpenAI
    // rejected this key" tells the user what to fix.
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save the key'),
  })

  const remove = useMutation({
    mutationFn: (provider: string) =>
      api<void>(`${base}/keys/${provider}`, { method: 'DELETE' }),
    onSuccess: () => {
      refresh()
      toast.success('Key removed',
        scope === 'campaign'
          ? 'This campaign falls back to the client key.'
          : 'Campaigns using it cannot take calls until a key is set.')
    },
    onError: (e) => toast.error('Could not remove the key', (e as Error).message),
  })

  if (keys.isLoading) return <Skeleton className="h-32" />

  const rows = keys.data ?? []
  const missing = rows.filter((r) => r.source === 'none')

  return (
    <div className="space-y-4">
      {missing.length > 0 && (
        <div className="flex gap-3 rounded-lg border border-warning/30 bg-warning/10 p-3 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
          <div className="text-foreground">
            <p className="font-medium">
              No {missing.map((m) => PROVIDERS[m.provider]?.label ?? m.provider).join(' or ')}{' '}
              key set
            </p>
            <p className="text-muted-foreground">
              {scope === 'client'
                ? 'Campaigns for this client cannot be enabled until every key is set.'
                : 'This campaign cannot be enabled until every key is set, here or on the client.'}
            </p>
          </div>
        </div>
      )}

      <div className="divide-y divide-border rounded-lg border border-border">
        {rows.map((row) => {
          const meta = PROVIDERS[row.provider]
          const inherited = scope === 'campaign' && row.source === 'client'
          return (
            <div key={row.provider} className="flex flex-wrap items-center gap-3 p-4">
              <KeyRound className="size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-40 flex-1">
                <p className="font-medium text-foreground">
                  {meta?.label ?? row.provider}
                </p>
                <p className="text-xs text-muted-foreground">{meta?.used}</p>
              </div>

              <div className="min-w-44">
                {row.source === 'none' ? (
                  <Badge tone="danger">Not set</Badge>
                ) : (
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={inherited ? 'muted' : 'success'}>
                      {inherited ? 'From client' : 'Set'}
                    </Badge>
                    <span className="font-mono text-xs text-muted-foreground">
                      ····{row.hint}
                    </span>
                    {when(row.updated_at) && (
                      <span className="text-xs text-muted-foreground">
                        {when(row.updated_at)}
                      </span>
                    )}
                  </div>
                )}
              </div>

              <div className="flex gap-2">
                <Button variant="secondary" size="sm"
                        onClick={() => { setEditing(row.provider); setValue(''); setError(null) }}>
                  {row.source === 'none' ? 'Set key' : inherited ? 'Override' : 'Replace'}
                </Button>
                {/* Only offer removal of a key that lives at THIS level. A
                    campaign cannot delete the client's key from here. */}
                {((scope === 'client' && row.source === 'client') ||
                  (scope === 'campaign' && row.source === 'campaign')) && (
                  <Button variant="ghost" size="sm" aria-label={`Remove ${row.provider} key`}
                          onClick={() => remove.mutate(row.provider)}
                          disabled={remove.isPending}>
                    <Trash2 className="size-4" />
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <ShieldAlert className="mt-0.5 size-3.5 shrink-0" />
        Keys are encrypted before they are stored and are never shown again — only
        the last four characters. Save a new one to replace it.
      </p>

      <Dialog
        open={editing !== null}
        onClose={close}
        title={`${PROVIDERS[editing ?? '']?.label ?? editing} key`}
        description={
          scope === 'campaign'
            ? 'Used by this campaign only. It overrides the client key.'
            : 'Used by every campaign for this client, unless a campaign sets its own.'
        }
        footer={
          <>
            <Button variant="ghost" onClick={close}>Cancel</Button>
            <Button
              onClick={() => editing && save.mutate(editing)}
              disabled={value.trim().length < 8 || save.isPending}
            >
              {save.isPending ? 'Checking with the provider…' : 'Save key'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <div>
            <Label htmlFor="provider-key">Key</Label>
            <Input
              id="provider-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={value}
              placeholder="Paste the key"
              onChange={(e) => { setValue(e.target.value); setError(null) }}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            The key is checked against the provider before it is saved, so a typo
            is caught here rather than on a live call.
          </p>
          {error && (
            <p className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger/10 p-2 text-sm text-foreground">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" />
              {error}
            </p>
          )}
        </div>
      </Dialog>
    </div>
  )
}
