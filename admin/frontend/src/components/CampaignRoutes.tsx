import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PhoneIncoming, Plus, Trash2, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Card, EmptyState, Input, Label, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import type { CampaignRoute } from '@/types'

export function CampaignRoutes({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [did, setDid] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  const routes = useQuery({
    queryKey: ['campaign-routes', campaignId],
    queryFn: () => api<CampaignRoute[]>(`/campaigns/${campaignId}/routes`),
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['campaign-routes', campaignId] })
  }

  const add = useMutation({
    mutationFn: () =>
      api<CampaignRoute>(`/campaigns/${campaignId}/routes`, {
        method: 'POST',
        body: { did: did.trim(), description: description.trim() || null },
      }),
    onSuccess: (r) => {
      refresh()
      setDid('')
      setDescription('')
      setError(null)
      toast.success(`${r.did} routed here`, 'Applies from the next call to that number.')
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not add the number'),
  })

  const remove = useMutation({
    mutationFn: (r: CampaignRoute) =>
      api<void>(`/campaigns/${campaignId}/routes/${r.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      refresh()
      toast.success('Number removed', 'Calls to it now fall back to the default agent.')
    },
    onError: (e) => toast.error('Could not remove the number', (e as Error).message),
  })

  return (
    <div className="space-y-4">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (did.trim()) add.mutate()
        }}
      >
        <div className="w-40 space-y-1.5">
          <Label htmlFor="r-did">Number</Label>
          <Input
            id="r-did"
            value={did}
            onChange={(e) => setDid(e.target.value)}
            placeholder="700"
            className="font-mono"
          />
        </div>
        <div className="min-w-[12rem] flex-1 space-y-1.5">
          <Label htmlFor="r-desc">Description</Label>
          <Input
            id="r-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional — e.g. Mumbai collections line"
          />
        </div>
        <Button type="submit" loading={add.isPending} disabled={!did.trim()}>
          <Plus className="h-4 w-4" />
          Add number
        </Button>
      </form>

      {error && (
        <p className="rounded-md bg-danger/10 p-2.5 text-xs text-danger ring-1 ring-inset ring-danger/20">
          {error}
        </p>
      )}

      <Card className="overflow-hidden">
        {routes.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : routes.isError ? (
          <EmptyState
            icon={TriangleAlert}
            title="Could not load numbers"
            hint={(routes.error as Error).message}
          />
        ) : !routes.data?.length ? (
          <EmptyState
            icon={PhoneIncoming}
            title="No numbers routed here"
            hint="Calls only reach this campaign once a dialled number points at it. Until then they fall back to the default agent."
          />
        ) : (
          <div className="divide-y divide-border/70">
            {routes.data.map((r) => (
              <div key={r.id} className="flex items-center gap-3 px-4 py-3">
                <PhoneIncoming className="h-4 w-4 shrink-0 text-primary" />
                <div className="min-w-0 flex-1">
                  <p className="tnum font-mono text-sm font-medium">{r.did}</p>
                  {r.description && (
                    <p className="text-2xs text-muted-foreground">{r.description}</p>
                  )}
                </div>
                <Badge tone="muted">inbound</Badge>
                <Button variant="ghost" size="sm" onClick={() => remove.mutate(r)}>
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
