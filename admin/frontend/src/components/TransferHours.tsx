import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Copy } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { TextField, Toggle } from '@/components/ui/field'
import { Select } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { DEFAULT_WEEK, Holidays, WeekGrid } from '@/components/HoursEditor'
import type { AgentConfig, Campaign, Holiday, WeekHours } from '@/types'

/**
 * When a human is there to take a handoff.
 *
 * The week grid and the holiday list are shared with the calling window — see
 * HoursEditor. What stays here is what is specific to transfers: the copy
 * button that pulls another campaign's schedule, and the sentence spoken to a
 * caller who asks for a person out of hours.
 */
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
          <WeekGrid
            title="Weekly hours"
            hours={hours}
            timezone={value.prompt_timezone}
            disabled={disabled}
            onChange={(day, window) =>
              onChange('transfer_hours', {
                ...hours,
                [day]: window,
              } as AgentConfig['transfer_hours'])
            }
            emptyWarning={
              <>
                No day is open, which would refuse every transfer. The agent
                allows them instead — open a day, or turn the hours off.
              </>
            }
            footer={
              others.length > 0 && (
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
              )
            }
          />

          <Holidays
            idPrefix="transfer-holiday"
            value={holidays}
            disabled={disabled}
            onChange={(next: Holiday[]) =>
              onChange('transfer_holidays', next as AgentConfig['transfer_holidays'])
            }
            hint={
              <>
                No transfers all day, whatever the weekly hours say. These are per
                campaign — use Copy above rather than typing Diwali three times.
              </>
            }
          />

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
