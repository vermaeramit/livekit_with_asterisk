import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, Copy, History, RotateCcw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Card, EmptyState, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { formatDateTime, formatNumber, formatRelative } from '@/lib/utils'
import type { PromptVersion } from '@/types'

/**
 * What the prompt used to say.
 *
 * A version is kept whenever the instructions change, and it holds the text as
 * it was BEFORE that change - the current prompt is in the editor, and nobody
 * needs to restore what they are looking at.
 *
 * This is a working list, not a record. config_audit still holds every change
 * with both sides of the text and nothing here can touch it, which is what
 * makes deleting from this list safe to offer.
 */
export function PromptVersions({
  campaignId,
  current,
  canEdit,
}: {
  campaignId: number
  current: string
  canEdit: boolean
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [open, setOpen] = useState<number | null>(null)
  const [copied, setCopied] = useState<number | null>(null)

  const versions = useQuery({
    queryKey: ['prompt-versions', campaignId],
    queryFn: () => api<PromptVersion[]>(`/campaigns/${campaignId}/prompt-versions`),
  })

  const restore = useMutation({
    mutationFn: (id: number) =>
      api(`/campaigns/${campaignId}/prompt-versions/${id}/restore`, { method: 'POST' }),
    onSuccess: () => {
      // Both: the config the editor reads, and the list this restore just
      // added the outgoing prompt to.
      qc.invalidateQueries({ queryKey: ['campaign-config', campaignId] })
      qc.invalidateQueries({ queryKey: ['prompt-versions', campaignId] })
      toast.success('Prompt restored', 'The one it replaced is now the newest version here.')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not restore'),
  })

  const remove = useMutation({
    mutationFn: (id: number) =>
      api(`/campaigns/${campaignId}/prompt-versions/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['prompt-versions', campaignId] }),
  })

  if (versions.isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-16 w-full" />
        ))}
      </div>
    )
  }

  const list = versions.data ?? []

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <p className="text-sm font-medium">Previous prompts</p>
        <p className="mt-1 max-w-3xl text-2xs leading-relaxed text-muted-foreground">
          A copy is kept every time the instructions change, holding the text as it
          was <em>before</em> that save. Restoring one keeps the prompt it replaces,
          so a restore can always be undone. The newest {50} are kept.
        </p>
      </Card>

      {!list.length ? (
        <EmptyState
          icon={History}
          title="Nothing kept yet"
          hint="The first version appears the next time the prompt is saved with a change in it."
        />
      ) : (
        <div className="space-y-2">
          {list.map((v) => {
            const isOpen = open === v.id
            const same = v.instructions === current
            return (
              <Card key={v.id} className="overflow-hidden">
                <div className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <button
                    type="button"
                    onClick={() => setOpen(isOpen ? null : v.id)}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <ChevronDown
                      className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${
                        isOpen ? 'rotate-180' : ''
                      }`}
                    />
                    <div className="min-w-0">
                      <p className="text-xs font-medium">
                        {formatRelative(v.created_at)}
                        {/* The exact time next to the relative one: "3 days
                            ago" is how you find it, the timestamp is how you
                            match it against a call that went wrong. */}
                        <span className="ml-2 font-normal text-muted-foreground">
                          {formatDateTime(v.created_at)}
                        </span>
                      </p>
                      <p className="mt-0.5 truncate text-2xs text-muted-foreground">
                        {v.instructions.slice(0, 120).replace(/\s+/g, ' ')}…
                      </p>
                    </div>
                  </button>

                  {/* Prompt size is what decides how many calls run at once, so
                      it belongs next to the version rather than in a tooltip. */}
                  {v.n_tokens != null && (
                    <Badge tone="muted">{formatNumber(v.n_tokens)} tokens</Badge>
                  )}
                  {same && <Badge tone="success">same as now</Badge>}
                  {v.created_by && (
                    <span className="text-2xs text-muted-foreground">{v.created_by}</span>
                  )}

                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        void navigator.clipboard.writeText(v.instructions)
                        setCopied(v.id)
                        setTimeout(() => setCopied(null), 1500)
                      }}
                    >
                      {copied === v.id ? (
                        <Check className="h-3.5 w-3.5" />
                      ) : (
                        <Copy className="h-3.5 w-3.5" />
                      )}
                      Copy
                    </Button>
                    {canEdit && (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={same || restore.isPending}
                          onClick={() => restore.mutate(v.id)}
                          title={same ? 'This is already the current prompt' : 'Make this the prompt'}
                        >
                          <RotateCcw className="h-3.5 w-3.5" />
                          Restore
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => remove.mutate(v.id)}
                          aria-label="Delete this version"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                  </div>
                </div>

                {isOpen && (
                  <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words border-t border-border/70 bg-muted/30 px-4 py-3 text-2xs leading-relaxed">
                    {v.instructions}
                  </pre>
                )}
              </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}
