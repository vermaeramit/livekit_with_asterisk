import { useState } from 'react'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Label } from '@/components/ui/primitives'

/**
 * A short list of phrases, added one at a time.
 *
 * Deliberately not a textarea split on newlines. A controlled textarea whose
 * value is `phrases.join('\n')` deletes the newline the moment it is pressed:
 * the round trip out to an array and back drops the empty last line, which is
 * exactly the line the person has just started. The add-button pattern has no
 * such round trip - the draft is its own state until it is committed.
 *
 * TermList does the same thing for the speech recogniser, but carries copy and
 * a provider warning that belong only there.
 */
export function PhraseList({
  label,
  help,
  phrases,
  onChange,
  placeholder,
  max = 6,
  maxChars = 40,
}: {
  label: string
  help?: React.ReactNode
  phrases: string[]
  onChange: (phrases: string[]) => void
  placeholder?: string
  max?: number
  maxChars?: number
}) {
  const [draft, setDraft] = useState('')
  const id = `phrase-add-${label.replace(/\W+/g, '-').toLowerCase()}`

  function add() {
    const next = draft.replace(/\s+/g, ' ').trim().slice(0, maxChars)
    if (!next || phrases.length >= max) return
    if (!phrases.includes(next)) onChange([...phrases, next])
    setDraft('')
  }

  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={id}>{label}</Label>
        <span className="tnum text-2xs text-muted-foreground">
          {phrases.length}/{max}
        </span>
      </div>

      {help && (
        <p className="text-2xs leading-relaxed text-muted-foreground">{help}</p>
      )}

      {phrases.length > 0 && (
        <div className="flex flex-wrap gap-1.5 rounded-lg border border-border/70 bg-muted/30 p-2.5">
          {phrases.map((p) => (
            <span
              key={p}
              className="inline-flex items-center gap-1 rounded-md bg-card px-2 py-1 text-xs ring-1 ring-inset ring-border"
            >
              {p}
              <button
                type="button"
                onClick={() => onChange(phrases.filter((x) => x !== p))}
                className="text-muted-foreground transition-colors hover:text-danger"
                aria-label={`Remove ${p}`}
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <Input
          id={id}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add()
            }
          }}
          placeholder={placeholder}
          maxLength={maxChars}
        />
        <Button
          variant="outline"
          size="sm"
          onClick={add}
          disabled={!draft.trim() || phrases.length >= max}
        >
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>
    </div>
  )
}
