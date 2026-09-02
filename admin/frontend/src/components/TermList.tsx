import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Label } from '@/components/ui/primitives'

const MAX_TERMS = 150
const MAX_CHARS = 60

/**
 * Words the speech recogniser has no reason to know.
 *
 * "Splendor Plus Flex" reached the knowledge base as "Lender Plus Flex" and
 * matched a different motorcycle at 0.57. No prompt fixes that - by the time
 * the model sees the transcript the wrong words are already in it.
 */
export function TermList({
  terms,
  onChange,
  provider,
}: {
  terms: string[]
  onChange: (terms: string[]) => void
  provider: string
}) {
  const [draft, setDraft] = useState('')

  // Only Soniox takes a term list. Saying so beats letting somebody fill this
  // in for a Sarvam campaign and wonder why nothing changed.
  const supported = provider === 'soniox'

  function add() {
    // A pasted list is the common case - a column out of a spreadsheet, or a
    // model range separated by commas. Splitting on both saves the tedium of
    // one-at-a-time.
    const incoming = draft
      .split(/[\n,]/)
      .map((t) => t.replace(/\s+/g, ' ').trim().slice(0, MAX_CHARS))
      .filter(Boolean)
    if (!incoming.length) return

    const seen = new Set(terms.map((t) => t.toLowerCase()))
    const next = [...terms]
    for (const t of incoming) {
      if (!seen.has(t.toLowerCase()) && next.length < MAX_TERMS) {
        seen.add(t.toLowerCase())
        next.push(t)
      }
    }
    onChange(next)
    setDraft('')
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor="term-add">Words the recogniser should know</Label>
        <span className="tnum text-2xs text-muted-foreground">
          {terms.length}/{MAX_TERMS}
        </span>
      </div>

      <p className="text-2xs leading-relaxed text-muted-foreground">
        Product names, model names, dealer names — anything a caller says that a
        general speech model has no reason to have heard.{' '}
        {supported ? (
          <>
            Fixes the transcript itself, which is the only place it can be fixed:
            once a word is wrong, the knowledge base searches for the wrong word
            and the model answers about the wrong thing.
          </>
        ) : (
          <span className="text-amber-600 dark:text-amber-500">
            Only Soniox uses this. This campaign is on {provider}, so anything
            here is stored and ignored.
          </span>
        )}
      </p>

      {terms.length > 0 && (
        <div className="flex flex-wrap gap-1.5 rounded-lg border border-border/70 bg-muted/30 p-2.5">
          {terms.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded-md bg-card px-2 py-1 text-xs ring-1 ring-inset ring-border"
            >
              {t}
              <button
                type="button"
                onClick={() => onChange(terms.filter((x) => x !== t))}
                className="text-muted-foreground transition-colors hover:text-danger"
                aria-label={`Remove ${t}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <Input
          id="term-add"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
          placeholder="Splendor Plus, Passion Pro, HF Deluxe"
          maxLength={2000}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={add}
          disabled={!draft.trim() || terms.length >= MAX_TERMS}
        >
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>

      <p className="text-2xs text-muted-foreground">
        Paste a comma-separated or line-separated list to add several at once.
        {terms.length >= MAX_TERMS &&
          ' The list is full — a longer one is rejected by the recogniser, which would fail the call rather than the field.'}
      </p>
    </div>
  )
}
