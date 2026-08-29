import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { IndianRupee, Plus, Trash2, Wallet } from 'lucide-react'
import { PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, Input, Label, Select, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { formatRelative } from '@/lib/utils'
import type { ProviderRate } from '@/types'

/**
 * What each price is FOR, in the words a provider's price list uses.
 *
 * `llm_input` is the part that is easiest to get wrong, so it says outright
 * that cached tokens are excluded. Charging both at the input rate is a
 * twenty-fold error on a call with a warm cache, and it looks entirely
 * plausible on the way past.
 */
const KINDS: { value: ProviderRate['kind']; label: string; hint: string }[] = [
  { value: 'llm_input', label: 'LLM input tokens', hint: 'Uncached prompt tokens only — the cached ones are priced separately below.' },
  { value: 'llm_cached', label: 'LLM cached input', hint: 'Usually a fraction of the input price. Most of a prompt is cached after the first turn.' },
  { value: 'llm_output', label: 'LLM output tokens', hint: 'What the model generated.' },
  { value: 'tts_characters', label: 'TTS characters', hint: 'Set this OR TTS audio, whichever your provider bills — never both.' },
  { value: 'tts_seconds', label: 'TTS audio', hint: 'Set this OR TTS characters, whichever your provider bills — never both.' },
  { value: 'stt_seconds', label: 'STT audio', hint: 'Audio streamed to the transcriber, which includes the quiet parts of a call.' },
]

const UNITS: { value: ProviderRate['unit']; label: string; for: 'counted' | 'timed' }[] = [
  { value: 'per_million', label: 'per 1M', for: 'counted' },
  { value: 'per_hour', label: 'per hour', for: 'timed' },
  { value: 'per_minute', label: 'per minute', for: 'timed' },
  { value: 'per_unit', label: 'per unit', for: 'counted' },
]

const BLANK = {
  provider: 'openai',
  model: '',
  kind: 'llm_input' as ProviderRate['kind'],
  unit: 'per_million' as ProviderRate['unit'],
  usd_price: '',
  note: '',
}

function isTimed(kind: ProviderRate['kind']) {
  return kind === 'tts_seconds' || kind === 'stt_seconds'
}

export function Rates() {
  const toast = useToast()
  const qc = useQueryClient()
  const [editing, setEditing] = useState<typeof BLANK | null>(null)
  const [fx, setFx] = useState('')

  const rates = useQuery({
    queryKey: ['rates'],
    queryFn: () => api<ProviderRate[]>('/rates'),
  })

  const exchange = useQuery({
    queryKey: ['rates-exchange'],
    queryFn: () => api<{ usd_to_inr: number | null }>('/rates/exchange'),
  })

  const save = useMutation({
    mutationFn: (r: typeof BLANK) =>
      api('/rates', {
        method: 'PUT',
        body: {
          provider: r.provider.trim(),
          model: r.model.trim() || null,
          kind: r.kind,
          unit: r.unit,
          usd_price: r.usd_price,
          note: r.note.trim() || null,
        },
      }),
    onSuccess: () => {
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['rates'] })
      toast.success('Rate saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save that'),
  })

  const remove = useMutation({
    mutationFn: (id: number) => api(`/rates/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rates'] })
      toast.success('Rate removed')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not remove that'),
  })

  const saveFx = useMutation({
    mutationFn: (v: string) => api('/rates/exchange', { method: 'PUT', body: { value: v } }),
    onSuccess: () => {
      setFx('')
      qc.invalidateQueries({ queryKey: ['rates-exchange'] })
      qc.invalidateQueries({ queryKey: ['call'] })
      toast.success('Exchange rate saved')
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : 'Could not save that'),
  })

  return (
    <div className="mx-auto max-w-[1100px] space-y-5 p-5 lg:p-7">
      <PageHeader
        title="Provider rates"
        description="What each provider charges, in their own units. Nothing is filled in for you — a price copied from a page months ago is worse than a blank, because a blank asks to be checked."
        actions={
          <Button size="sm" onClick={() => setEditing({ ...BLANK })}>
            <Plus className="h-3.5 w-3.5" />
            Add a rate
          </Button>
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <IndianRupee className="h-4 w-4" />
            Exchange rate
          </CardTitle>
        </CardHeader>
        <CardBody className="space-y-2">
          <div className="flex flex-wrap items-end gap-3">
            <div className="w-48 space-y-1.5">
              <Label htmlFor="fx">1 USD in rupees</Label>
              <Input
                id="fx"
                value={fx}
                onChange={(e) => setFx(e.target.value)}
                placeholder={
                  exchange.data?.usd_to_inr != null
                    ? String(exchange.data.usd_to_inr)
                    : 'not set'
                }
                inputMode="decimal"
              />
            </div>
            <Button
              variant="outline"
              size="sm"
              disabled={!fx.trim() || saveFx.isPending}
              onClick={() => saveFx.mutate(fx.trim())}
            >
              Save
            </Button>
          </div>
          <p className="text-2xs leading-relaxed text-muted-foreground">
            {/* Held rather than fetched live, on purpose. */}
            Held, not looked up. A live rate would make the same call cost a
            different amount every time it was opened, and nobody reconciling a
            month's spend wants a figure that moves while they read it. Leave it
            unset to show costs in dollars only.
          </p>
        </CardBody>
      </Card>

      <Card className="overflow-hidden">
        {rates.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : !rates.data?.length ? (
          <EmptyState
            icon={Wallet}
            title="No prices set"
            hint="Until a provider has prices here, calls using it show as uncosted rather than as free."
          />
        ) : (
          <div className="divide-y divide-border/70">
            {rates.data.map((r) => {
              const kind = KINDS.find((k) => k.value === r.kind)
              const unit = UNITS.find((u) => u.value === r.unit)
              return (
                <div key={r.id} className="flex items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">{r.provider}</span>
                      {/* "any model" is a real choice, not a blank field. */}
                      <Badge tone={r.model ? 'default' : 'muted'}>
                        {r.model || 'any model'}
                      </Badge>
                      <span className="text-2xs text-muted-foreground">
                        {kind?.label ?? r.kind}
                      </span>
                    </div>
                    {r.note && (
                      <p className="mt-0.5 text-2xs text-muted-foreground">{r.note}</p>
                    )}
                    <p className="mt-0.5 text-2xs text-muted-foreground">
                      set {formatRelative(r.updated_at)}
                      {r.updated_by_email ? ` by ${r.updated_by_email}` : ''}
                    </p>
                  </div>
                  <span className="tnum shrink-0 text-sm font-medium">
                    ${Number(r.usd_price).toFixed(4)}
                    <span className="ml-1 text-2xs font-normal text-muted-foreground">
                      {unit?.label ?? r.unit}
                    </span>
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setEditing({
                        provider: r.provider,
                        model: r.model ?? '',
                        kind: r.kind,
                        unit: r.unit,
                        usd_price: String(r.usd_price),
                        note: r.note ?? '',
                      })
                    }
                  >
                    Edit
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => remove.mutate(r.id)}
                    aria-label="Remove this rate"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      <Dialog open={editing !== null} onClose={() => setEditing(null)} title="Provider rate">
        {editing && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="rp">Provider</Label>
                <Input
                  id="rp"
                  value={editing.provider}
                  onChange={(e) => setEditing({ ...editing, provider: e.target.value })}
                  placeholder="openai"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rm">Model</Label>
                <Input
                  id="rm"
                  value={editing.model}
                  onChange={(e) => setEditing({ ...editing, model: e.target.value })}
                  placeholder="gpt-4.1-mini — leave empty for any"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rk">What is being priced</Label>
              <Select
                id="rk"
                value={editing.kind}
                onChange={(e) => {
                  const kind = e.target.value as ProviderRate['kind']
                  // The unit follows the kind. Pricing tokens per hour is not a
                  // mistake anybody catches later — it just produces a figure
                  // three thousand times out.
                  setEditing({
                    ...editing,
                    kind,
                    unit: isTimed(kind) ? 'per_hour' : 'per_million',
                  })
                }}
              >
                {KINDS.map((k) => (
                  <option key={k.value} value={k.value}>{k.label}</option>
                ))}
              </Select>
              <p className="text-2xs leading-relaxed text-muted-foreground">
                {KINDS.find((k) => k.value === editing.kind)?.hint}
              </p>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="ru">Unit</Label>
                <Select
                  id="ru"
                  value={editing.unit}
                  onChange={(e) =>
                    setEditing({ ...editing, unit: e.target.value as ProviderRate['unit'] })
                  }
                >
                  {UNITS.filter((u) =>
                    isTimed(editing.kind) ? u.for === 'timed' || u.value === 'per_unit' : u.for === 'counted',
                  ).map((u) => (
                    <option key={u.value} value={u.value}>{u.label}</option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="rv">Price in USD</Label>
                <Input
                  id="rv"
                  value={editing.usd_price}
                  onChange={(e) => setEditing({ ...editing, usd_price: e.target.value })}
                  placeholder="0.40"
                  inputMode="decimal"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="rn">Note</Label>
              <Input
                id="rn"
                value={editing.note}
                onChange={(e) => setEditing({ ...editing, note: e.target.value })}
                placeholder="From the price list, 29 Aug 2026"
              />
              <p className="text-2xs leading-relaxed text-muted-foreground">
                Worth dating. The next person to look will want to know how old
                this is before they trust it.
              </p>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setEditing(null)}>Cancel</Button>
              <Button onClick={() => save.mutate(editing)} loading={save.isPending}>
                Save
              </Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  )
}
