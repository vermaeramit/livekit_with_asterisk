import { useState } from 'react'
import { CalendarOff, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input, Label } from '@/components/ui/primitives'
import type { Holiday, WeekHours } from '@/types'

/**
 * The week grid and the holiday list, shared by both windows on a campaign.
 *
 * There are two: when a human is there to take a transfer, and when the dialler
 * may call at all. They are different decisions with the same shape, and the
 * agent evaluates both through one function (hours.open_now) for exactly the
 * reason these live here — a second copy of a day-and-timezone editor would
 * drift, and the drift would show up at whichever midnight nobody was watching.
 *
 * What is NOT here is anything about what either window means. That belongs to
 * whichever component is using it, because the consequences are not the same:
 * outside transfer hours a caller is told something; outside calling hours a
 * dialler is told zero.
 */

export const DAYS: { key: keyof WeekHours; label: string }[] = [
  { key: 'mon', label: 'Monday' },
  { key: 'tue', label: 'Tuesday' },
  { key: 'wed', label: 'Wednesday' },
  { key: 'thu', label: 'Thursday' },
  { key: 'fri', label: 'Friday' },
  { key: 'sat', label: 'Saturday' },
  { key: 'sun', label: 'Sunday' },
]

// What a campaign gets when a window is switched on for the first time. A blank
// week would mean "closed always", which the agent treats as a mistake and
// ignores - so the first thing anyone saw would be a warning about the state
// the toggle had just put them in.
export const DEFAULT_WEEK: WeekHours = {
  mon: ['09:30', '18:30'],
  tue: ['09:30', '18:30'],
  wed: ['09:30', '18:30'],
  thu: ['09:30', '18:30'],
  fri: ['09:30', '18:30'],
  sat: ['10:00', '14:00'],
  sun: null,
}

export function openDayCount(hours: WeekHours): number {
  return DAYS.filter((d) => hours[d.key]).length
}

export function WeekGrid({
  title,
  hours,
  timezone,
  onChange,
  disabled,
  emptyWarning,
  footer,
}: {
  title: string
  hours: WeekHours
  timezone: string | null
  onChange: (day: keyof WeekHours, window: [string, string] | null) => void
  disabled?: boolean
  /** Shown when no day is open. Both windows treat that as a mistake and
      override it, but they override it differently, so the sentence belongs to
      the caller rather than here. */
  emptyWarning: React.ReactNode
  /** Whatever copy control this window offers. Transfer hours copy from another
      campaign; calling hours copy from the transfer window on this one. */
  footer?: React.ReactNode
}) {
  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-medium">{title}</p>
        {/* Which clock these are on. Nobody should have to guess, and the
            answer is not the server's — it runs UTC. */}
        <p className="text-2xs text-muted-foreground">
          Times are {timezone || 'Asia/Kolkata'}
        </p>
      </div>

      <div className="mt-3 space-y-1.5">
        {DAYS.map(({ key, label }) => {
          const window = hours[key]
          return (
            <div key={key} className="flex items-center gap-2">
              <span className="w-24 shrink-0 text-xs">{label}</span>
              {window ? (
                <>
                  <Input
                    type="time"
                    value={window[0]}
                    disabled={disabled}
                    onChange={(e) => onChange(key, [e.target.value, window[1]])}
                    className="w-28 font-mono"
                    aria-label={`${label} opens`}
                  />
                  <span className="text-2xs text-muted-foreground">to</span>
                  <Input
                    type="time"
                    value={window[1]}
                    disabled={disabled}
                    onChange={(e) => onChange(key, [window[0], e.target.value])}
                    className="w-28 font-mono"
                    aria-label={`${label} closes`}
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={disabled}
                    onClick={() => onChange(key, null)}
                  >
                    Closed
                  </Button>
                </>
              ) : (
                <>
                  <span className="text-xs text-muted-foreground">Closed</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={disabled}
                    onClick={() => onChange(key, ['09:30', '18:30'])}
                  >
                    Open this day
                  </Button>
                </>
              )}
            </div>
          )
        })}
      </div>

      {openDayCount(hours) === 0 && (
        <p className="mt-3 text-2xs leading-relaxed text-amber-600 dark:text-amber-500">
          {emptyWarning}
        </p>
      )}

      {footer}
    </div>
  )
}

export function Holidays({
  idPrefix,
  value,
  onChange,
  disabled,
  hint,
}: {
  /** Unique per instance. Two of these now render on the same page - one per
      window - and the ids were hardcoded as "h-date" and "h-label", so both
      labels would have pointed at the first pair of inputs. */
  idPrefix: string
  value: Holiday[]
  onChange: (v: Holiday[]) => void
  disabled?: boolean
  hint: React.ReactNode
}) {
  const [date, setDate] = useState('')
  const [label, setLabel] = useState('')

  function add() {
    if (!date) return
    if (value.some((h) => h.date === date)) {
      setDate('')
      setLabel('')
      return
    }
    onChange([...value, { date, label }].sort((a, b) => a.date.localeCompare(b.date)))
    setDate('')
    setLabel('')
  }

  // Past holidays are kept rather than pruned - deleting last year's Diwali on
  // the user's behalf would be an edit nobody asked for - but they are dimmed,
  // because a list where half the rows no longer matter is a list nobody reads.
  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
      <p className="text-xs font-medium">Holidays</p>
      <p className="mt-0.5 text-2xs leading-relaxed text-muted-foreground">{hint}</p>

      {value.length > 0 && (
        <div className="mt-3 space-y-1">
          {value.map((h) => (
            <div
              key={h.date}
              className={`flex items-center gap-2 text-xs ${
                h.date < today ? 'text-muted-foreground/60' : ''
              }`}
            >
              <span className="w-28 shrink-0 font-mono">{h.date}</span>
              <span className="min-w-0 flex-1 truncate">{h.label || '—'}</span>
              <Button
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={() => onChange(value.filter((x) => x.date !== h.date))}
                aria-label={`Remove ${h.label || h.date}`}
              >
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      {value.length === 0 && (
        <p className="mt-3 flex items-center gap-1.5 text-2xs text-muted-foreground">
          <CalendarOff className="h-3.5 w-3.5" />
          None set — only the weekly hours apply.
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-end gap-2">
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}-date`} className="text-2xs">
            Date
          </Label>
          <Input
            id={`${idPrefix}-date`}
            type="date"
            value={date}
            disabled={disabled}
            onChange={(e) => setDate(e.target.value)}
            className="w-40 font-mono"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}-label`} className="text-2xs">
            Name
          </Label>
          <Input
            id={`${idPrefix}-label`}
            value={label}
            disabled={disabled}
            onChange={(e) => setLabel(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && add()}
            placeholder="Diwali"
            className="w-48"
          />
        </div>
        <Button variant="outline" size="sm" disabled={!date || disabled} onClick={add}>
          <Plus className="h-3.5 w-3.5" />
          Add
        </Button>
      </div>
    </div>
  )
}
