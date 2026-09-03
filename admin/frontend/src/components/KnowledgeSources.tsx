import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Globe, Link2, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Card, Input, Label } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api, postStream } from '@/lib/api'
import { formatRelative } from '@/lib/utils'
import type { KbSource } from '@/types'

/**
 * Knowledge base pages pulled from a URL.
 *
 * One source is one address; importing it produces one document per page, so
 * a workbook of 47 sheets becomes 47 rows in the list above this one - each
 * with its own enable switch, because some of those sheets hold data no caller
 * should ever be answered from.
 */
export function KnowledgeSources({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const [adding, setAdding] = useState(false)
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState<string | null>(null)

  const sources = useQuery({
    queryKey: ['kb-sources', campaignId],
    queryFn: () => api<KbSource[]>(`/campaigns/${campaignId}/kb/sources`),
  })

  function done(label: string, r: any) {
    qc.invalidateQueries({ queryKey: ['kb-sources', campaignId] })
    qc.invalidateQueries({ queryKey: ['kb-docs', campaignId] })
    const skipped = (r?.skipped ?? []).length
    toast.success(
      `${label}: ${r?.pages ?? 0} page(s)`,
      [
        r?.removed ? `${r.removed} removed from the source` : '',
        skipped ? `${skipped} had no readable text — see the list` : '',
      ]
        .filter(Boolean)
        .join(' · ') || undefined,
    )
  }

  const add = useMutation({
    mutationFn: (u: string) => {
      setBusy('Importing…')
      return postStream(`/campaigns/${campaignId}/kb/sources`, { url: u }, (e) =>
        setBusy(describe(e)),
      )
    },
    onSettled: () => setBusy(null),
    onSuccess: (r) => {
      setAdding(false)
      setUrl('')
      done('Imported', r)
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Import failed'),
  })

  const refresh = useMutation({
    mutationFn: (id: number) => {
      setBusy('Fetching…')
      return postStream(`/kb/sources/${id}/refresh`, undefined, (e) => setBusy(describe(e)))
    },
    onSettled: () => setBusy(null),
    onSuccess: (r) => done('Refreshed', r),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Refresh failed'),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api(`/kb/sources/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['kb-sources', campaignId] })
      qc.invalidateQueries({ queryKey: ['kb-docs', campaignId] })
      toast.success('Source removed', 'Every document it produced went with it.')
    },
  })

  const list = sources.data ?? []
  const working = add.isPending || refresh.isPending

  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">From a link</p>
          <p className="mt-0.5 text-2xs leading-relaxed text-muted-foreground">
            A page, or a whole workbook published as one. Each page becomes its own
            document above, so you can switch off the ones a caller should never be
            answered from.
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={() => setAdding(true)} disabled={working}>
          <Link2 className="h-3.5 w-3.5" />
          Add a link
        </Button>
      </div>

      {busy && (
        <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
          {busy}
        </p>
      )}

      {list.length > 0 && (
        <div className="mt-4 space-y-3">
          {list.map((s) => (
            <div key={s.id} className="rounded-lg border border-border/70 p-3">
              <div className="flex flex-wrap items-start gap-2">
                <Globe className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <a
                    href={s.url}
                    target="_blank"
                    rel="noreferrer"
                    className="break-all text-xs font-medium hover:underline"
                  >
                    {s.title || s.url}
                  </a>
                  <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-2xs text-muted-foreground">
                    <span>
                      {s.enabled_count} of {s.document_count} pages in use
                    </span>
                    {/* Refresh is manual, so the age is the thing worth
                        reading. A date alone lets a source quietly rot. */}
                    <span>
                      {s.last_fetched_at
                        ? `fetched ${formatRelative(s.last_fetched_at)}`
                        : 'never fetched'}
                    </span>
                    {s.last_status === 'error' && <Badge tone="danger">last fetch failed</Badge>}
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={working}
                  onClick={() => refresh.mutate(s.id)}
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Refresh
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={working}
                  onClick={() => remove.mutate(s.id)}
                  aria-label="Remove source"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>

              {s.last_error && (
                <p className="mt-2 text-2xs text-danger">{s.last_error}</p>
              )}

              {s.skipped.length > 0 && (
                <div className="mt-2 rounded-md bg-amber-500/5 p-2 ring-1 ring-inset ring-amber-500/20">
                  <p className="flex items-center gap-1.5 text-2xs font-medium text-amber-700 dark:text-amber-500">
                    <AlertCircle className="h-3 w-3" />
                    {s.skipped.length} page(s) had no readable text
                  </p>
                  {/* Named, not counted. "New Prices Oil & Consummables is a
                      screenshot" is the sentence that explains why the agent
                      does not know oil prices; "3 pages skipped" is not. */}
                  <p className="mt-1 text-2xs leading-relaxed text-muted-foreground">
                    {s.skipped.map((k) => k.name).join(', ')} — these are pictures
                    rather than text, so the agent cannot read them. Reading them
                    would need OCR.
                  </p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <Dialog open={adding} onClose={() => setAdding(false)} title="Import from a link">
        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="kb-url">Address</Label>
            <Input
              id="kb-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://insights.example.com/knowledge/index.htm"
              className="font-mono text-xs"
            />
            <p className="text-2xs leading-relaxed text-muted-foreground">
              A page, a PDF, or an Excel or Word workbook published as HTML — those
              are imported one sheet per document. Internal addresses on your own
              network work; this server's own address does not.
            </p>
          </div>

          <p className="text-2xs leading-relaxed text-muted-foreground">
            Nothing refreshes on its own. When the page changes, come back and press
            Refresh — pages that have not changed are left alone, and pages that have
            gone are removed.
          </p>

          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => add.mutate(url.trim())}
              loading={add.isPending}
              disabled={!/^https?:\/\/\S+$/.test(url.trim())}
            >
              Import
            </Button>
          </div>
        </div>
      </Dialog>
    </Card>
  )
}

function describe(e: any): string {
  switch (e?.stage) {
    case 'fetching':
      return 'Fetching the page…'
    case 'page':
      return `Reading ${e.name ?? 'page'} (${(e.done ?? 0) + 1} of ${e.total ?? '?'})…`
    case 'chunking':
      return 'Splitting into chunks…'
    case 'embedding':
      return `Embedding ${e.done ?? 0} of ${e.total ?? '?'}…`
    case 'saving':
      // Reported in batches now: one insert of 500 chunks does not finish
      // inside the database's command timeout, so it is split - and a bar that
      // moves is the difference between "working" and "hung".
      return e.total
        ? `Saving ${e.done ?? 0} of ${e.total}…`
        : 'Saving…'
    default:
      return 'Working…'
  }
}
