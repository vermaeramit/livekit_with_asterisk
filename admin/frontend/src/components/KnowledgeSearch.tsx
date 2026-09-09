import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Loader2, Search, TriangleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Input } from '@/components/ui/primitives'
import { ApiError, api } from '@/lib/api'

/**
 * Ask the knowledge base a question and see what comes back.
 *
 * There was no way to do this. Whether retrieval worked could only be inferred
 * from asking the bot something and judging its answer - which is how an
 * embedder that had been dead for weeks went unnoticed. Chat kept answering,
 * fluently, out of the model's own training.
 *
 * It runs the agent's own search, so what is shown here is what a call gets.
 */

type Hit = {
  id: number
  document: string
  page: number | null
  heading: string | null
  content: string
  score: number
  src: string
  used: boolean
}

type Result = {
  query: string
  min_score: number
  degraded: string | null
  hits: Hit[]
}

export function KnowledgeSearch({ campaignId }: { campaignId: number }) {
  const [query, setQuery] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)

  const run = useMutation({
    mutationFn: () =>
      api<Result>(`/campaigns/${campaignId}/kb/search`, {
        method: 'POST',
        body: { query: query.trim() },
      }),
    onSuccess: (r) => {
      setResult(r)
      setError(null)
    },
    onError: (e) => {
      setResult(null)
      setError(e instanceof ApiError ? e.message : 'The search failed')
    },
  })

  // Everything matched on trigrams and nothing on the embedding. On a corpus
  // this size that does not happen when embeddings are working, and it is the
  // symptom that had no way of being seen before this panel existed.
  const noVectors =
    result !== null && result.hits.length > 0 && result.hits.every((h) => h.src === 'lex')

  return (
    <div className="space-y-4">
      <div>
        <p className="text-2xs leading-relaxed text-muted-foreground">
          The agent&rsquo;s own search, with its own thresholds — what you see here is
          what a call gets. <strong className="font-medium">Ask in English</strong>,
          the way the agent&rsquo;s tool does: on this corpus an English query scores
          0.44&ndash;0.48 where the same question in Devanagari scores 0.13&ndash;0.20
          and ranks the wrong passage.
        </p>
      </div>

      <div className="flex gap-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && query.trim()) run.mutate()
          }}
          placeholder="Splendor Plus i3s idle start stop"
          maxLength={400}
        />
        <Button onClick={() => run.mutate()} disabled={!query.trim() || run.isPending}>
          {run.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Search className="h-3.5 w-3.5" />
          )}
          Search
        </Button>
      </div>

      {error && (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs">
          {error}
        </div>
      )}

      {result?.degraded && (
        <div className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="text-xs leading-relaxed">
            <p className="font-medium">The query could not be embedded.</p>
            <p className="mt-1 text-muted-foreground">
              These results come from word matching alone, which finds passages that
              share the caller&rsquo;s words and misses everything phrased differently.
              Calls are answering this way too.
            </p>
            <p className="mt-1 font-mono text-2xs">{result.degraded}</p>
          </div>
        </div>
      )}

      {noVectors && !result?.degraded && (
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs leading-relaxed">
          Every result matched on words, none on meaning. That usually means the
          embeddings are not working even though the search did not report an error.
        </div>
      )}

      {result && !result.hits.length && (
        <div className="rounded-lg border border-border/70 bg-muted/30 p-3 text-xs text-muted-foreground">
          Nothing matched at all — not even below the threshold. The words in the
          question do not appear in any document, and no passage is close in meaning.
        </div>
      )}

      {result && result.hits.length > 0 && (
        <div className="space-y-2">
          {/* Said once, above the list, rather than repeated on every row that
              falls short of it. */}
          <p className="text-2xs text-muted-foreground">
            A call uses passages scoring {result.min_score} or above. The rest are shown
            so a near miss can be told apart from nothing at all.
          </p>

          {result.hits.map((h) => (
            <div
              key={h.id}
              className={`rounded-lg border p-3 ${
                h.used ? 'border-border/70' : 'border-dashed border-border/50 opacity-60'
              }`}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={h.used ? 'success' : 'muted'}>{h.score.toFixed(3)}</Badge>
                {/* Which leg matched. `vec` is the embedding, `lex` is trigram
                    matching inside Postgres - and all lex is the tell. */}
                <Badge tone="muted">{h.src === 'vec' ? 'meaning' : 'words'}</Badge>
                <span className="truncate text-2xs font-medium">{h.document}</span>
                {h.page != null && (
                  <span className="text-2xs text-muted-foreground">p{h.page}</span>
                )}
                {!h.used && (
                  <span className="text-2xs text-muted-foreground">below the threshold</span>
                )}
              </div>
              {h.heading && (
                <p className="mt-1.5 text-2xs font-medium text-muted-foreground">
                  {h.heading}
                </p>
              )}
              <p className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap text-2xs leading-relaxed text-muted-foreground">
                {h.content}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
