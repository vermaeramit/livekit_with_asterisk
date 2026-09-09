import { useEffect, useRef, useState } from 'react'
import { DayPicker, type DateRange } from 'react-day-picker'
import {
  endOfMonth,
  format,
  isSameDay,
  isValid,
  parseISO,
  startOfMonth,
  subDays,
  subMonths,
} from 'date-fns'
import { CalendarDays, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import 'react-day-picker/style.css'

/**
 * One control for "which days", instead of two boxes wanting yyyy-mm-dd typed
 * into them twice.
 *
 * The presets are the point. Nobody filtering a call list wants to think about
 * dates - they want yesterday, or this month, and typing two ISO dates to get
 * there is the part that made the filter not worth using.
 *
 * DATES ARE LOCAL, everywhere in here. The list renders timestamps in the
 * browser's own zone (`toLocaleString('en-IN')` with no timeZone), so a filter
 * working in UTC would disagree with what is on the screen beside it - and did:
 * "to 9 Sept" was sent as 9 Sept 00:00 UTC, which is 05:30 IST, so most of the
 * 9th was quietly missing from its own results.
 *
 * The value in and out is `yyyy-MM-dd`, which is what the URL carries and what
 * makes a filtered list shareable as a link.
 */

type Preset = { label: string; range: () => DateRange }

// Only backwards. These filter calls that have happened, and an empty result
// for next Tuesday teaches nobody anything.
const PRESETS: Preset[] = [
  { label: 'Today', range: () => ({ from: new Date(), to: new Date() }) },
  {
    label: 'Yesterday',
    range: () => ({ from: subDays(new Date(), 1), to: subDays(new Date(), 1) }),
  },
  { label: 'Last 7 days', range: () => ({ from: subDays(new Date(), 6), to: new Date() }) },
  { label: 'Last 30 days', range: () => ({ from: subDays(new Date(), 29), to: new Date() }) },
  {
    label: 'This month',
    range: () => ({ from: startOfMonth(new Date()), to: new Date() }),
  },
  {
    label: 'Last month',
    range: () => {
      const m = subMonths(new Date(), 1)
      return { from: startOfMonth(m), to: endOfMonth(m) }
    },
  },
]

function parse(s: string): Date | undefined {
  if (!s) return undefined
  const d = parseISO(s)
  // parseISO reads a bare yyyy-MM-dd as LOCAL midnight, which is the whole
  // reason it is used here rather than new Date(s) - that one reads UTC.
  return isValid(d) ? d : undefined
}

function label(from: string, to: string): string {
  const f = parse(from)
  const t = parse(to)
  if (!f && !t) return 'Any date'
  if (f && t) {
    return isSameDay(f, t)
      ? format(f, 'd MMM yyyy')
      : `${format(f, 'd MMM')} – ${format(t, 'd MMM yyyy')}`
  }
  if (f) return `From ${format(f, 'd MMM yyyy')}`
  return `Until ${format(t!, 'd MMM yyyy')}`
}

export function DateRangeField({
  from,
  to,
  onChange,
}: {
  from: string
  to: string
  onChange: (range: { from: string; to: string }) => void
}) {
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<DateRange | undefined>()
  const box = useRef<HTMLDivElement>(null)

  // Opening starts from what is applied. Editing then discarding must not
  // leave the next open showing the abandoned edit.
  useEffect(() => {
    if (!open) return
    const f = parse(from)
    setDraft(f ? { from: f, to: parse(to) } : undefined)
  }, [open, from, to])

  useEffect(() => {
    if (!open) return
    function away(e: MouseEvent) {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    function esc(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('mousedown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  function apply(r: DateRange | undefined) {
    onChange({
      from: r?.from ? format(r.from, 'yyyy-MM-dd') : '',
      // A range with only a start is a real thing to ask for - "everything
      // since Monday" - so the end is allowed to stay empty.
      to: r?.to ? format(r.to, 'yyyy-MM-dd') : '',
    })
    setOpen(false)
  }

  const set = Boolean(from || to)

  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'flex h-9 w-full items-center gap-2 rounded-md border border-input bg-background px-3 text-sm',
          'hover:bg-muted/50 focus:outline-none focus:ring-2 focus:ring-ring',
          !set && 'text-muted-foreground',
        )}
      >
        <CalendarDays className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate">{label(from, to)}</span>
        {set && (
          <span
            role="button"
            tabIndex={-1}
            aria-label="Clear the dates"
            className="ml-auto rounded p-0.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={(e) => {
              // Clearing from the closed control, without opening a calendar
              // to press a button in.
              e.stopPropagation()
              onChange({ from: '', to: '' })
            }}
          >
            <X className="h-3 w-3" />
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 z-50 mt-1 flex max-w-[calc(100vw-2rem)] gap-0 overflow-auto rounded-lg border border-border bg-card shadow-lg"
          // The library themes itself from these. Pointed at the console's own
          // tokens so it follows light and dark without a second palette.
          style={
            {
              '--rdp-accent-color': 'hsl(var(--primary))',
              '--rdp-accent-background-color': 'hsl(var(--primary) / 0.12)',
              '--rdp-range_middle-background-color': 'hsl(var(--primary) / 0.12)',
              '--rdp-today-color': 'hsl(var(--primary))',
              '--rdp-day-width': '2.25rem',
              '--rdp-day-height': '2.25rem',
              '--rdp-day_button-width': '2.25rem',
              '--rdp-day_button-height': '2.25rem',
              '--rdp-day_button-border-radius': '0.375rem',
              '--rdp-months-gap': '1.25rem',
            } as React.CSSProperties
          }
        >
          <div className="flex w-32 shrink-0 flex-col gap-0.5 border-r border-border/70 bg-muted/30 p-2">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                type="button"
                onClick={() => apply(p.range())}
                className="rounded px-2 py-1.5 text-left text-xs hover:bg-muted"
              >
                {p.label}
              </button>
            ))}
            <button
              type="button"
              onClick={() => apply(undefined)}
              className="mt-1 rounded border-t border-border/70 px-2 py-1.5 pt-2 text-left text-xs text-muted-foreground hover:bg-muted"
            >
              Any date
            </button>
          </div>

          <div className="p-2">
            <DayPicker
              mode="range"
              numberOfMonths={2}
              defaultMonth={parse(from) ?? subMonths(new Date(), 1)}
              selected={draft}
              onSelect={setDraft}
              disabled={{ after: new Date() }}
              weekStartsOn={1}
              className="text-sm"
            />
            <div className="flex items-center justify-between gap-3 border-t border-border/70 px-1 pt-2">
              <span className="text-2xs text-muted-foreground">
                {draft?.from
                  ? draft.to
                    ? `${format(draft.from, 'd MMM')} – ${format(draft.to, 'd MMM yyyy')}`
                    : 'Pick the last day, or apply for everything since this one'
                  : 'Pick the first day'}
              </span>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button size="sm" disabled={!draft?.from} onClick={() => apply(draft)}>
                  Apply
                </Button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
