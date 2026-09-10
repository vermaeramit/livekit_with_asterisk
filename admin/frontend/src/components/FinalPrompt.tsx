import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ChevronDown, Copy, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Card, Skeleton } from '@/components/ui/primitives'
import { api } from '@/lib/api'
import { formatNumber } from '@/lib/utils'
import type { FinalPrompt as FinalPromptType } from '@/types'

/**
 * The prompt as the model actually receives it.
 *
 * Six tabs contribute to one string and nothing showed them together. That is
 * how a rule ends up referring to a section that is not in the prompt — which
 * happened, and cost thirteen turns of a call answered out of the model's own
 * training rather than the customer's documents. One look at this would have
 * shown it.
 *
 * Every token here is sent on EVERY turn of EVERY call, so the per-section
 * counts are not trivia: they are the bill.
 */
export function FinalPromptPanel({ campaignId }: { campaignId: number }) {
  const [copied, setCopied] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const q = useQuery({
    queryKey: ['final-prompt', campaignId],
    queryFn: () => api<FinalPromptType>(`/campaigns/${campaignId}/config/final-prompt`),
    // Rebuilt from the saved config, so a stale copy would be showing the
    // previous save's prompt next to the current editor.
    staleTime: 0,
  })

  if (q.isLoading) return <Skeleton className="h-96 w-full" />
  if (q.isError) {
    return (
      <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs">
        {(q.error as Error).message}
      </div>
    )
  }

  const p = q.data!
  const toolTokens = p.tools.reduce((n, t) => n + t.tokens, 0)

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="muted">{formatNumber(p.total_tokens)} tokens</Badge>
        <Badge tone={p.kb_mode === 'off' ? 'muted' : 'success'}>
          knowledge: {p.kb_mode}
        </Badge>
        {p.tools.length > 0 && (
          <Badge tone="muted">
            {p.tools.length} tool{p.tools.length === 1 ? '' : 's'} ·{' '}
            {formatNumber(toolTokens)} tokens
          </Badge>
        )}
        <div className="ml-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              void navigator.clipboard.writeText(p.text)
              setCopied(true)
              setTimeout(() => setCopied(false), 1500)
            }}
          >
            {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
            Copy
          </Button>
        </div>
      </div>

      <p className="max-w-3xl text-2xs leading-relaxed text-muted-foreground">
        Built by the agent&rsquo;s own code, not reassembled here — so what is below is
        what goes down the wire. Every token is sent on <strong>every turn of every
        call</strong>, which is what makes the per-section counts worth reading.
        {p.total_tokens !== p.cached_tokens && (
          <>
            {' '}
            {formatNumber(p.cached_tokens)} of them are identical on every call and
            are cached by the provider; the rest is rebuilt each time.
          </>
        )}
      </p>

      {/* Where it came from, before what it says. "Which tab do I go and change"
          is most of what somebody wants from this page. */}
      <Card className="overflow-hidden">
        <table className="w-full text-xs">
          <thead className="border-b border-border/70 bg-muted/40 text-2xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-3 py-2 text-left font-medium">Section</th>
              <th className="px-3 py-2 text-left font-medium">Comes from</th>
              <th className="px-3 py-2 text-right font-medium">Tokens</th>
              <th className="w-8" />
            </tr>
          </thead>
          <tbody>
            {p.sections.map((s) => {
              const isOpen = open === s.name
              return (
                <tr key={s.name} className="border-b border-border/40 last:border-0">
                  <td colSpan={4} className="p-0">
                    <button
                      type="button"
                      onClick={() => setOpen(isOpen ? null : s.name)}
                      className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted/30"
                    >
                      <span className="min-w-0 flex-1 truncate font-medium">{s.name}</span>
                      <span className="min-w-0 flex-1 truncate text-2xs text-muted-foreground">
                        {s.source}
                      </span>
                      <span className="tnum w-20 text-right text-muted-foreground">
                        {formatNumber(s.tokens)}
                      </span>
                      <ChevronDown
                        className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${
                          isOpen ? 'rotate-180' : ''
                        }`}
                      />
                    </button>
                    {isOpen && (
                      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words border-t border-border/40 bg-muted/20 px-3 py-2 text-2xs leading-relaxed">
                        {s.text}
                      </pre>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>

      <div>
        <p className="mb-1.5 text-xs font-medium">The whole thing, in order</p>
        <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words rounded-lg border border-border/70 bg-muted/20 px-3 py-3 text-2xs leading-relaxed">
          {p.text}
        </pre>
      </div>

      {p.tools.length > 0 && (
        <div className="space-y-2">
          <p className="text-xs font-medium">Tools sent with it</p>
          <p className="max-w-3xl text-2xs leading-relaxed text-muted-foreground">
            Sent beside the prompt rather than inside it, and billed on every turn just
            the same. The agent adds two of its own — the knowledge search and the
            handover — which are not listed here.
          </p>
          {p.tools.map((t) => {
            const isOpen = open === `tool:${t.name}`
            return (
              <Card key={t.name} className="overflow-hidden">
                <button
                  type="button"
                  onClick={() => setOpen(isOpen ? null : `tool:${t.name}`)}
                  className="flex w-full items-center gap-3 px-3 py-2 text-left hover:bg-muted/30"
                >
                  <Wrench className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate font-mono text-xs">{t.name}</span>
                  <span className="tnum text-2xs text-muted-foreground">
                    {formatNumber(t.tokens)} tokens
                  </span>
                  <ChevronDown
                    className={`h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform ${
                      isOpen ? 'rotate-180' : ''
                    }`}
                  />
                </button>
                {isOpen && (
                  <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words border-t border-border/40 bg-muted/20 px-3 py-2 text-2xs leading-relaxed">
                    {t.json_schema}
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
