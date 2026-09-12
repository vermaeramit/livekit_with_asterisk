import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Label, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import type { DiallerId } from '@/types'

/**
 * The dialler's own campaign ids, mapped to this campaign.
 *
 * Several per campaign, because the dialler splits one campaign of ours across
 * several of theirs and that split is theirs to make. Each id asks about this
 * campaign; the answer is the same whichever one they send.
 *
 * Lives on the Limits tab on purpose: what the dialler is told is the concurrent
 * call limit set directly above, and one is useless without the other. Adding an
 * id to a campaign with no limit is refused by the API, and so is clearing the
 * limit while an id exists.
 *
 * See docs/DIALLER-API.md, which is what the dialler team is given.
 */
export function DiallerIds({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [draft, setDraft] = useState('')

  const key = ['dialler-ids', campaignId]
  const ids = useQuery({
    queryKey: key,
    queryFn: () => api<DiallerId[]>(`/campaigns/${campaignId}/dialler-ids`),
  })

  const add = useMutation({
    mutationFn: () =>
      api<DiallerId>(`/campaigns/${campaignId}/dialler-ids`, {
        method: 'POST',
        body: { dialler_campaign_id: draft.trim() },
      }),
    onSuccess: () => {
      setDraft('')
      qc.invalidateQueries({ queryKey: key })
    },
    // The message carries the reason - already registered elsewhere, or no
    // concurrent call limit set - and both are things to act on rather than
    // retry, so they go in front of the person instead of into a console.
    onError: (e) => toast.error('Could not add that id', (e as Error).message),
  })

  const remove = useMutation({
    mutationFn: (id: number) =>
      api(`/campaigns/${campaignId}/dialler-ids/${id}`, { method: 'DELETE' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
    onError: (e) => toast.error('Could not remove that id', (e as Error).message),
  })

  return (
    <div className="space-y-3">
      <div>
        <Label>Dialler campaign ids</Label>
        <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">
          What the dialler sends when it asks how many more calls this campaign can
          take. Add one for each of their campaigns that feeds this one. An id can
          point at only one campaign, across every client.
        </p>
      </div>

      {ids.isLoading ? (
        <Skeleton className="h-8 w-full" />
      ) : ids.data?.length ? (
        <div className="space-y-1.5">
          {ids.data.map((d) => (
            <div
              key={d.id}
              className="flex items-center gap-2 rounded-md border border-border/60 px-2.5 py-1.5"
            >
              <code className="flex-1 font-mono text-xs">{d.dialler_campaign_id}</code>
              <Button
                variant="ghost"
                size="sm"
                loading={remove.isPending && remove.variables === d.id}
                onClick={() => remove.mutate(d.id)}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-2xs text-muted-foreground">
          None yet — the dialler cannot ask about this campaign until one is added.
        </p>
      )}

      <div className="flex gap-2">
        <Input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="their campaign id"
          className="font-mono text-xs"
          onKeyDown={(e) => {
            // Enter submits. Without preventDefault it also submits the config
            // form this sits inside, which would save the whole campaign.
            if (e.key === 'Enter') {
              e.preventDefault()
              if (draft.trim()) add.mutate()
            }
          }}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={!draft.trim()}
          loading={add.isPending}
          onClick={() => add.mutate()}
        >
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>
    </div>
  )
}
