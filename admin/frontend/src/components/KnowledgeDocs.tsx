import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Eye,
  FileText,
  Power,
  RefreshCw,
  Trash2,
  TriangleAlert,
  Upload,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Card, EmptyState, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api, upload } from '@/lib/api'
import { cn, formatNumber, formatRelative } from '@/lib/utils'
import type { KbChunk2, KbDocument, KbIngestResult } from '@/types'

function ChunkViewer({ doc, onClose }: { doc: KbDocument | null; onClose: () => void }) {
  const chunks = useQuery({
    queryKey: ['kb-chunks', doc?.id],
    queryFn: () => api<KbChunk2[]>(`/kb/documents/${doc!.id}/chunks`),
    enabled: doc !== null,
  })

  return (
    <Dialog
      open={doc !== null}
      onClose={onClose}
      size="lg"
      title={doc?.title || doc?.filename || ''}
      description="Exactly what the agent can retrieve, in order. A PDF that extracted badly reads as nonsense here — far easier to spot than to diagnose from a bad answer on a live call."
      footer={
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="scrollbar-thin max-h-[60vh] space-y-2 overflow-y-auto">
        {chunks.isLoading &&
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}

        {chunks.data?.map((c) => (
          <div key={c.id} className="rounded-md border border-border bg-muted/30 p-3">
            <div className="flex items-center gap-2 text-2xs text-muted-foreground">
              <Badge tone="muted" className="tnum">
                #{c.seq}
              </Badge>
              {c.page != null && <span className="tnum">page {c.page}</span>}
              {c.heading && <span className="truncate font-medium">{c.heading}</span>}
              <span className="ml-auto tnum shrink-0">{c.n_tokens} tok</span>
            </div>
            <p className="mt-2 whitespace-pre-wrap text-xs leading-relaxed">{c.content}</p>
          </div>
        ))}

        {chunks.data?.length === 0 && (
          <p className="py-8 text-center text-sm text-muted-foreground">
            No chunks — the document produced nothing retrievable.
          </p>
        )}
      </div>
    </Dialog>
  )
}

export function KnowledgeDocs({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient()
  const toast = useToast()
  const fileInput = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState<{ name: string; percent: number } | null>(null)
  const [viewing, setViewing] = useState<KbDocument | null>(null)
  const [deleting, setDeleting] = useState<KbDocument | null>(null)

  const docs = useQuery({
    queryKey: ['kb-docs', campaignId],
    queryFn: () => api<KbDocument[]>(`/campaigns/${campaignId}/kb`),
  })

  function refresh() {
    qc.invalidateQueries({ queryKey: ['kb-docs', campaignId] })
  }

  const send = useMutation({
    mutationFn: (file: File) => {
      setProgress({ name: file.name, percent: 0 })
      return upload<KbIngestResult>(`/campaigns/${campaignId}/kb`, file, (percent) =>
        // 99, not 100: the bar completing while the server is still embedding
        // reads as a hang. It finishes when the response lands.
        setProgress({ name: file.name, percent: Math.min(99, percent) }),
      )
    },
    onSettled: () => setProgress(null),
    onSuccess: (r) => {
      refresh()
      if (r.status === 'empty') {
        toast.warning(
          `${r.filename} produced nothing`,
          r.error ?? 'No text could be extracted. A scanned PDF needs OCR first.',
        )
      } else if (r.status === 'unchanged') {
        toast.info(`${r.filename} is already up to date`, 'The file is byte-identical to the stored copy.')
      } else {
        toast.success(
          `${r.filename} ${r.status}`,
          `${r.pages} pages → ${r.chunks} chunks, ${formatNumber(r.tokens)} tokens. Live from the next call.`,
        )
      }
    },
    onError: (e) =>
      toast.error('Upload failed', e instanceof ApiError ? e.message : 'Unexpected error'),
  })

  const reingest = useMutation({
    mutationFn: (d: KbDocument) => api<KbIngestResult>(`/kb/documents/${d.id}/reingest`, { method: 'POST' }),
    onSuccess: (r) => {
      refresh()
      toast.success(`${r.filename} re-ingested`, `${r.chunks} chunks`)
    },
    onError: (e) => toast.error('Could not re-ingest', (e as Error).message),
  })

  const toggle = useMutation({
    mutationFn: (d: KbDocument) =>
      api<KbDocument>(`/kb/documents/${d.id}?enabled=${!d.enabled}`, { method: 'PATCH' }),
    onSuccess: (d) => {
      refresh()
      toast.success(d.enabled ? 'Document enabled' : 'Document disabled')
    },
    onError: (e) => toast.error('Could not update the document', (e as Error).message),
  })

  const remove = useMutation({
    mutationFn: (d: KbDocument) => api<void>(`/kb/documents/${d.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      refresh()
      setDeleting(null)
      toast.success('Document deleted')
    },
    onError: (e) => {
      setDeleting(null)
      toast.error('Could not delete', (e as Error).message)
    },
  })

  async function accept(files: FileList | null) {
    if (!files?.length) return
    // Sequential, not parallel: each ingest is CPU and API heavy, and firing
    // five at once turns a slow upload into a stalled console. A failure is
    // already reported by onError, so keep going with the rest.
    for (const f of Array.from(files)) {
      try {
        await send.mutateAsync(f)
      } catch {
        /* reported by the mutation's onError */
      }
    }
  }

  const busy = send.isPending

  return (
    <div className="space-y-4">
      {/* dropzone */}
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!busy) accept(e.dataTransfer.files)
        }}
        className={cn(
          'rounded-lg border-2 border-dashed p-6 text-center transition-colors',
          dragging ? 'border-primary bg-primary/5' : 'border-border bg-muted/20',
          busy && 'pointer-events-none opacity-60',
        )}
      >
        <input
          ref={fileInput}
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="hidden"
          onChange={(e) => {
            accept(e.target.files)
            e.target.value = ''
          }}
        />

        {progress ? (
          <div className="mx-auto max-w-sm">
            <p className="truncate text-sm font-medium">{progress.name}</p>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${progress.percent}%` }}
              />
            </div>
            <p className="mt-2 text-2xs text-muted-foreground">
              {progress.percent < 99
                ? `Uploading… ${Math.round(progress.percent)}%`
                : 'Extracting, chunking and embedding — this can take a while for a long document.'}
            </p>
          </div>
        ) : (
          <>
            <Upload className="mx-auto h-6 w-6 text-muted-foreground" />
            <p className="mt-2 text-sm font-medium">Drop PDFs here</p>
            <p className="mt-1 text-2xs text-muted-foreground">
              Text-based PDFs up to 30 MB. Scanned pages need OCR first — they extract as nothing.
            </p>
            <Button
              variant="outline"
              size="sm"
              className="mt-3"
              onClick={() => fileInput.current?.click()}
            >
              Choose files
            </Button>
          </>
        )}
      </div>

      {/* documents */}
      <Card className="overflow-hidden">
        {docs.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : docs.isError ? (
          <EmptyState
            icon={TriangleAlert}
            title="Could not load documents"
            hint={(docs.error as Error).message}
          />
        ) : !docs.data?.length ? (
          <EmptyState
            icon={FileText}
            title="No documents yet"
            hint="Upload a PDF and the agent can answer from it on the next call."
          />
        ) : (
          <div className="divide-y divide-border/70">
            {docs.data.map((d) => (
              <div key={d.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                <FileText
                  className={cn('h-4 w-4 shrink-0', d.enabled ? 'text-primary' : 'text-muted-foreground')}
                />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium">{d.title || d.filename}</p>
                  <p className="tnum text-2xs text-muted-foreground">
                    {d.page_count ?? '?'} pages · {d.chunk_count ?? 0} chunks ·{' '}
                    {formatNumber(d.token_count)} tokens · updated {formatRelative(d.updated_at)}
                  </p>
                </div>

                {!d.enabled && <Badge tone="muted">disabled</Badge>}

                <div className="flex items-center gap-1.5">
                  <Button variant="outline" size="sm" onClick={() => setViewing(d)}>
                    <Eye className="h-3.5 w-3.5" />
                    Chunks
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => reingest.mutate(d)}
                    loading={reingest.isPending && reingest.variables?.id === d.id}
                    title="Re-run extraction and embedding from the stored file"
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => toggle.mutate(d)}
                    loading={toggle.isPending && toggle.variables?.id === d.id}
                  >
                    <Power className="h-3.5 w-3.5" />
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setDeleting(d)}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <ChunkViewer doc={viewing} onClose={() => setViewing(null)} />

      <Dialog
        open={deleting !== null}
        onClose={() => setDeleting(null)}
        title={`Delete ${deleting?.filename ?? ''}?`}
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
              Delete document
            </Button>
          </>
        }
      >
        <p className="text-sm text-muted-foreground">
          Its {deleting?.chunk_count ?? 0} chunks and the stored file go with it, and the agent stops
          being able to answer from it on the next call. To keep it around without using it, disable
          it instead.
        </p>
      </Dialog>
    </div>
  )
}
