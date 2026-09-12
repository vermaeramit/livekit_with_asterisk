import { Copy, PhoneOff } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toggle } from '@/components/ui/field'
import { DEFAULT_WEEK, Holidays, WeekGrid, openDayCount } from '@/components/HoursEditor'
import type { AgentConfig, Holiday, WeekHours } from '@/types'

/**
 * When the dialler may call this campaign at all.
 *
 * Read by the capacity endpoint — outside the window it reports zero slots and
 * the dialler stops. It does NOT refuse calls: one that arrives anyway is
 * answered, because refusing it puts a caller on the line who has to hear
 * something, and choosing what they hear is a different decision.
 *
 * Same grid and holiday list as the transfer window, from HoursEditor, and the
 * agent evaluates both through one function. What differs is the consequence,
 * which is why the wording lives here rather than in the shared piece.
 */
export function CallingHours({
  value,
  onChange,
  disabled,
}: {
  value: AgentConfig
  onChange: <K extends keyof AgentConfig>(key: K, v: AgentConfig[K]) => void
  disabled?: boolean
}) {
  const hours = (value.calling_hours ?? {}) as WeekHours
  const holidays = value.calling_holidays ?? []
  const on = value.calling_hours_enabled

  // Copy from the TRANSFER window on this campaign, not from another campaign.
  // Usually the two schedules are the same, and this is a local edit with no
  // endpoint behind it - the page's own save button commits it, unlike the
  // cross-campaign copy on the transfer tab which writes immediately.
  const transferWeek = value.transfer_hours as WeekHours | null
  const canCopy = !!transferWeek && openDayCount(transferWeek) > 0

  return (
    <div className="space-y-4">
      <Toggle
        label="Only let the dialer call during set hours"
        checked={on}
        onChange={(v) => {
          onChange('calling_hours_enabled', v)
          if (v && !Object.keys(hours).length) {
            onChange('calling_hours', DEFAULT_WEEK as AgentConfig['calling_hours'])
          }
        }}
        hint="Outside these hours the capacity API answers zero, so the dialer stops sending calls. Off means it may call at any hour, including three in the morning."
      />

      {on && (
        <>
          <WeekGrid
            title="When the dialer may call"
            hours={hours}
            timezone={value.prompt_timezone}
            disabled={disabled}
            onChange={(day, window) =>
              onChange('calling_hours', {
                ...hours,
                [day]: window,
              } as AgentConfig['calling_hours'])
            }
            emptyWarning={
              <>
                No day is open, which would stop the dialer permanently. The
                capacity API treats that as a mistake and answers normally
                instead — open a day, or turn this off.
              </>
            }
            footer={
              canCopy && (
                <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-border/70 pt-3">
                  <Copy className="h-3.5 w-3.5 text-muted-foreground" />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={disabled}
                    onClick={() => {
                      onChange('calling_hours', transferWeek as AgentConfig['calling_hours'])
                      onChange(
                        'calling_holidays',
                        (value.transfer_holidays ?? []) as AgentConfig['calling_holidays'],
                      )
                    }}
                  >
                    Copy the transfer hours
                  </Button>
                  <span className="text-2xs text-muted-foreground">
                    Usually the same schedule. Save to keep it.
                  </span>
                </div>
              )
            }
          />

          <Holidays
            idPrefix="calling-holiday"
            value={holidays}
            disabled={disabled}
            onChange={(next: Holiday[]) =>
              onChange('calling_holidays', next as AgentConfig['calling_holidays'])
            }
            hint={
              <>
                No calling all day, whatever the weekly hours say. A separate list
                from the transfer holidays on purpose — not handing a caller to a
                person on Diwali and not phoning them on Diwali are two different
                decisions.
              </>
            }
          />

          {/* Not the Note component from CampaignConfig - that one is local to
              that file, and importing it here would make the two modules import
              each other. The classes are the same. */}
          <div className="flex items-start gap-2 rounded-md bg-primary/5 p-3 text-xs text-muted-foreground ring-1 ring-inset ring-primary/15">
            <PhoneOff className="mt-px h-3.5 w-3.5 shrink-0 text-primary" />
            <span className="leading-relaxed">
              This tells the dialer; it does not block calls.{' '}
              <strong className="font-medium text-foreground">
                A call that arrives outside these hours is still answered
              </strong>{' '}
              — refusing one means a caller is on the line waiting to hear
              something, which is a separate decision from this one. So if the
              dialer ignores the capacity answer, nothing here stops it.
            </span>
          </div>
        </>
      )}
    </div>
  )
}
