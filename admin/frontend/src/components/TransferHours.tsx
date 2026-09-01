import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarOff, Copy, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { TextField, Toggle } from '@/components/ui/field'
import { Input, Label, Select } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import type { AgentConfig, Campaign, Holiday, WeekHours } from '@/types'

const DAYS: { key: keyof WeekHours; label: string }[] = [
  { key: 'mon', label: 'Monday' },
  { key: 'tue', label: 'Tuesday' },
  { key: 'wed', label: 'Wednesday' },
  { key: 'thu', label: 'Thursday' },
  { key: 'fri', label: 'Friday' },
  { key: 'sat', label: 'Saturday' },
  { key: 'sun', label: 'Sunday' },
]

// What a campaign gets when hours are switched on for the first time. A blank
// week would mean "closed always", which the agent treats as a mistake and
// ignores - so the first thing anyone saw would be a warning about the state
// the toggle had just put them in.
const DEFAULT_WEEK: WeekHours = {
  mon: ['09:30', '18:30'],
  tue: ['09:30', '18:30'],
  wed: ['09:30', '18:30'],
  thu: ['09:30', '18:30'],
  fri: ['09:30', '18:30'],
  sat: ['10:00', '14:00'],
  sun: null,
}

export function TransferHours({
  value,
  campaignId,
  onChange,
  disabled,
}: {
  value: AgentConfig
  campaignId: number
  onChange: <K extends keyof AgentConfig>(key: K, v: AgentConfig[K]) => void
  /** Read-only view. The page hides its save button; the copy button below
      writes on its own and so has to check for itself. */
  disabled?: boolean
}) {
  const toast = useToast()
  const qc = useQueryClient()
  const [copyFrom, setCopyFrom] = useState('')

  const hours = (value.transfer_hours ?? {}) as WeekHours
  const holidays = value.transfer_holidays ?? []
  const on = value.transfer_hours_enabled

  // Only offered when there is somewhere to copy from.
  const campaigns = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api<Campaign[]>('/campaigns'),
    enabled: on,
    staleTime: 5 * 60 * 1000,
  })
  const others = (campaigns.data ?? []).filter((c) => c.id !== campaignId)

  const copy = useMutation({
    mutationFn: (from: number) =>
      api<AgentConfig>(`/campaigns/${campaignId}/config/copy-hours`, {
        method: 'POST',
        body: { from_campaign_id: from },
      }),
    onSuccess: (fresh) => {
      // Straight into the cache rather than into the draft: it is already
      // saved, so leaving it as an unsaved edit would invite someone to
      // discard changes that are on the server.
      qc.setQueryData(['campaign-config', campaignId], fresh)
      setCopyFrom('')
      toast.success('Hours and holidays copied')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not copy'),
  })

  function setDay(day: keyof WeekHours, window: [string, string] | null) {
    onChange('transfer_hours', { ...hours, [day]: window } as AgentConfig['transfer_hours'])
  }

  function setHolidays(next: Holiday[]) {
    onChange('transfer_holidays', next as AgentConfig['transfer_holidays'])
  }

  const openDays = DAYS.filter((d) => hours[d.key]).length

  return (
    <div className="space-y-4">
      <Toggle
        label="Only transfer during set hours"
        checked={on}
        onChange={(v) => {
          onChange('transfer_hours_enabled', v)
          if (v && !Object.keys(hours).length) {
            onChange('transfer_hours', DEFAULT_WEEK as AgentConfig['transfer_hours'])
          }
        }}
        hint="The agent answers around the clock; the people it hands calls to do not. Outside these hours it says the message below instead of transferring, and carries on with the caller."
      />

      {on && (
        <>
          <div className="rounded-lg border border-border/70 bg-muted/30 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium">Weekly hours</p>
              {/* Which clock these are on. Nobody should have to guess, and
                  the answer is not the server's. */}
              <p className="text-2xs text-muted-foreground">
                Times are {value.prompt_timezone || 'Asia/Kolkata'}
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
                          onChange={(e) => setDay(key, [e.target.value, window[1]])}
                          className="w-28 font-mono"
                          aria-label={`${label} opens`}
                        />
                        <span className="text-2xs text-muted-foreground">to</span>
                        <Input
                          type="time"
                          value={window[1]}
                          disabled={disabled}
                          onChange={(e) => setDay(key, [window[0], e.target.value])}
                          className="w-28 font-mono"
                          aria-label={`${label} closes`}
                        />
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={disabled}
                          onClick={() => setDay(key, null)}
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
                          onClick={() => setDay(key, ['09:30', '18:30'])}
                        >
                          Open this day
                        </Button>
                      </>
                    )}
                  </div>
                )
              })}
            </div>

            {/* Every day closed refuses every handoff, so the agent overrides
                it and allows them. Saying so here beats discovering it from a
                call. */}
            {openDays === 0 && (
              <p className="mt-3 text-2xs leading-relaxed text-amber-600 dark:text-amber-500">
                No day is open, which would refuse every transfer. The agent
                allows them instead — open a day, or turn the hours off.
              </p>
            )}

            {others.length > 0 && (
              <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
                <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                <span className="text-2xs text-muted-foreground">Copy from</span>
                <Select
                  value={copyFrom}
                  disabled={disabled || copy.isPending}
                  onChange={(e) => setCopyFrom(e.target.value)}
                  className="h-8 w-56 text-xs"
                  aria-label="Copy hours from campaign"
                >
                  <option value="">another campaign…</option>
                  {others.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={!copyFrom || disabled}
                  loading={copy.isPending}
                  onClick={() => copy.mutate(Number(copyFrom))}
                >
                  Copy
                </Button>
                <span className="text-2xs text-muted-foreground">
                  Hours, holidays and the message together — saves immediately.
                </span>
              </div>
            )}
          </div>

          <Holidays value={holidays} onChange={setHolidays} disabled={disabled} />

          <TextField
            label="What to say outside those hours"
            value={value.transfer_closed_message ?? ''}
            onChange={(v) => onChange('transfer_closed_message', v || null)}
            placeholder="Abhi hamari team available nahi hai. {next_open} se koi aapse baat kar sakega."
            disabled={disabled}
            hint={
              <>
                Spoken instead of transferring, after which the agent carries on
                helping the caller. Write <span className="font-mono">{'{next_open}'}</span>{' '}
                where the next available time should go — it becomes “kal 9:30 baje”
                or “somvar 9:30 baje” in the caller's language. Left empty, a
                generic sentence is used.
              </>
            }
          />
        </>
      )}
    </div>
  )
}

function Holidays({
  value,
  onChange,
  disabled,
}: {
  value: Holiday[]
  onChange: (v: Holiday[]) => void
  disabled?: boolean
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
    onChange(
      [...value, { date, label }].sort((a, b) => a.date.localeCompare(b.date)),
    )
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
      <p className="mt-0.5 text-2xs leading-relaxed text-muted-foreground">
        Closed all day, whatever the weekly hours say. These are per campaign —
        use Copy above rather than typing Diwali three times.
      </p>

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
          <Label htmlFor="h-date" className="text-2xs">
            Date
          </Label>
          <Input
            id="h-date"
            type="date"
            value={date}
            disabled={disabled}
            onChange={(e) => setDate(e.target.value)}
            className="w-40 font-mono"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="h-label" className="text-2xs">
            Name
          </Label>
          <Input
            id="h-label"
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
