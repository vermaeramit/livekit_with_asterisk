import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, Copy, KeyRound, Trash2, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import type { Tenant, TenantApiKey } from '@/types'

/**
 * The key the dialler presents to ask how many calls a campaign can take.
 *
 * One per client. Shown exactly once, when it is generated — the database holds
 * only a sha256 of it, so there is no endpoint that could show it again even if
 * one were wanted. After that the console works from the last four characters,
 * which is enough to answer "is the dialler holding the key I think it is".
 *
 * See docs/DIALLER-API.md, which is the document handed to the dialler team.
 */
export function DiallerKey({ tenant }: { tenant: Tenant }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [issued, setIssued] = useState<TenantApiKey | null>(null)
  const [copied, setCopied] = useState(false)
  const [confirming, setConfirming] = useState(false)

  const generate = useMutation({
    mutationFn: () => api<TenantApiKey>(`/tenants/${tenant.id}/api-key`, { method: 'POST' }),
    onSuccess: (k) => {
      setIssued(k)
      setConfirming(false)
      qc.invalidateQueries({ queryKey: ['tenants'] })
    },
    onError: (e) => toast.error('Could not generate the key', (e as Error).message),
  })

  const revoke = useMutation({
    mutationFn: () => api(`/tenants/${tenant.id}/api-key`, { method: 'DELETE' }),
    onSuccess: () => {
      setIssued(null)
      qc.invalidateQueries({ queryKey: ['tenants'] })
      toast.success('Key revoked', 'The dialler will stop getting an answer immediately.')
    },
    onError: (e) => toast.error('Could not revoke the key', (e as Error).message),
  })

  return (
    <div className="space-y-3 rounded-lg border border-border/60 p-3">
      <div className="flex items-center gap-2">
        <KeyRound className="h-4 w-4 text-muted-foreground" />
        <p className="flex-1 text-xs font-medium">Dialler capacity API</p>
        {tenant.api_key_hint ? (
          <Badge tone="success">key set</Badge>
        ) : (
          <Badge tone="muted">no key</Badge>
        )}
      </div>

      <p className="text-2xs leading-relaxed text-muted-foreground">
        Lets the dialler ask how many more calls a campaign can take before placing
        them. Read-only — it cannot start, stop or change anything. It answers for
        the campaigns that have this client&rsquo;s dialler ids registered, and for
        nothing else.
      </p>

      {/* The key, the one time it exists on screen. Not in a toast: a toast can
          be dismissed by accident or time out while somebody is finding the
          right place to paste it, and there is no way to get it back. */}
      {issued && (
        <div className="space-y-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5">
          <div className="flex items-start gap-2">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
            <p className="text-2xs leading-relaxed">
              <strong className="font-medium">Copy it now.</strong> Only a hash of
              this is stored, so it cannot be shown again — if it is lost, the only
              option is to issue another one, which stops whatever is using this.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 overflow-x-auto whitespace-nowrap rounded bg-background/80 px-2 py-1.5 font-mono text-2xs">
              {issued.api_key}
            </code>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void navigator.clipboard.writeText(issued.api_key)
                setCopied(true)
                window.setTimeout(() => setCopied(false), 2000)
              }}
            >
              {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </div>
      )}

      {tenant.api_key_hint && !issued && (
        <p className="text-2xs text-muted-foreground">
          Ending <code className="font-mono text-foreground">{tenant.api_key_hint}</code>
          {tenant.api_key_set_at && <> · set {formatDateTime(tenant.api_key_set_at)}</>}
        </p>
      )}

      {/* Regenerating is a break, not an upgrade: the old key stops the instant
          the new one exists. Behind a confirm for that reason, and the wording
          says what happens rather than asking "are you sure". */}
      {confirming ? (
        <div className="space-y-2 rounded-md border border-destructive/40 bg-destructive/10 p-2.5">
          <p className="text-2xs leading-relaxed">
            The current key stops working immediately and the dialler gets a 401
            until the new one is in place. There is no overlap.
          </p>
          <div className="flex gap-2">
            <Button
              variant="danger"
              size="sm"
              loading={generate.isPending}
              onClick={() => generate.mutate()}
            >
              Replace the key
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirming(false)}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="flex gap-2">
          {tenant.api_key_hint ? (
            <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
              <KeyRound className="h-3.5 w-3.5" />
              Replace key
            </Button>
          ) : (
            <Button size="sm" loading={generate.isPending} onClick={() => generate.mutate()}>
              <KeyRound className="h-3.5 w-3.5" />
              Generate key
            </Button>
          )}
          {tenant.api_key_hint && (
            <Button
              variant="outline"
              size="sm"
              loading={revoke.isPending}
              onClick={() => revoke.mutate()}
            >
              <Trash2 className="h-3.5 w-3.5" />
              Revoke
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
