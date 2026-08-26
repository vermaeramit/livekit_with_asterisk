import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw, Send, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge, Card, CardBody, CardHeader, CardTitle, Input, Label, Skeleton } from '@/components/ui/primitives'
import { NumberField, TextField, Toggle } from '@/components/ui/field'
import { useToast } from '@/components/ui/toast'
import { api } from '@/lib/api'
import { formatDateTime } from '@/lib/utils'
import type { AgentConfig, Postback } from '@/types'

const TYPES = [
  { value: 'string', label: 'text' },
  { value: 'number', label: 'number' },
  { value: 'boolean', label: 'yes / no' },
]

type Field = { key: string; type: string; description: string }

/**
 * The fields to pull out of the conversation.
 *
 * This one list does two jobs: it is the schema the model is given, and it is
 * the shape of the payload. Defining them separately is how a field gets
 * extracted and never sent, or sent and never filled — and neither failure is
 * visible from either end.
 *
 * The description is the part that matters. The model reads it and nothing
 * else, so "cash or finance, whichever the caller chose" produces a usable
 * value where "payment" produces whatever the model feels like.
 */
function FieldList({
  fields,
  onChange,
}: {
  fields: Field[]
  onChange: (f: Field[]) => void
}) {
  const edit = (i: number, patch: Partial<Field>) =>
    onChange(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)))

  return (
    <div className="space-y-1.5">
      <Label>Fields to extract</Label>

      {fields.length === 0 ? (
        <p className="text-2xs leading-relaxed text-muted-foreground">
          Nothing set — only the facts are sent (identifiers, duration, outcome, what the dialler
          gave us). Nothing is read out of the conversation.
        </p>
      ) : (
        <div className="space-y-2">
          {fields.map((f, i) => (
            <div key={i} className="flex items-start gap-2">
              <Input
                value={f.key}
                onChange={(e) => edit(i, { key: e.target.value.trim() })}
                placeholder="payment_mode"
                className="w-44 shrink-0 font-mono"
              />
              <select
                value={f.type}
                onChange={(e) => edit(i, { type: e.target.value })}
                className="h-9 w-28 shrink-0 rounded-md border border-input bg-card px-2 text-sm"
              >
                {TYPES.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
              <Input
                value={f.description}
                onChange={(e) => edit(i, { description: e.target.value })}
                placeholder="cash or finance, whichever the caller chose"
              />
              <Button
                variant="ghost"
                size="sm"
                className="mt-0.5"
                aria-label={`Remove ${f.key || 'field'}`}
                onClick={() => onChange(fields.filter((_, j) => j !== i))}
              >
                <X className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3 pt-1">
        <Button
          variant="outline"
          size="sm"
          disabled={fields.length >= 25}
          onClick={() => onChange([...fields, { key: '', type: 'string', description: '' }])}
        >
          <Plus className="size-3.5" />
          Add a field
        </Button>
        <p className="text-2xs leading-relaxed text-muted-foreground">
          The description is all the model gets. A field the conversation never established comes
          back empty rather than guessed — that is deliberate, an invented pincode is worse than a
          missing one.
        </p>
      </div>
    </div>
  )
}

function StatusBadge({ s }: { s: string }) {
  const tone =
    s === 'sent' ? 'success' : s === 'failed' ? 'danger' : s === 'skipped' ? 'muted' : 'warning'
  return <Badge tone={tone as never}>{s}</Badge>
}

export function CampaignPostback({
  campaignId,
  value,
  set,
}: {
  campaignId: number
  value: AgentConfig
  set: <K extends keyof AgentConfig>(k: K, v: AgentConfig[K]) => void
}) {
  const qc = useQueryClient()
  const toast = useToast()
  const [failedOnly, setFailedOnly] = useState(false)
  // Held locally because the server never returns it. Binding the input to
  // the config would leave it permanently blank — you could not see what you
  // typed. It is pushed into the draft on every keystroke so Save picks it up.
  const [secret, setSecret] = useState('')
  const [open, setOpen] = useState<number | null>(null)

  const log = useQuery({
    queryKey: ['postbacks', campaignId, failedOnly],
    queryFn: () =>
      api<Postback[]>(
        `/campaigns/${campaignId}/postbacks?limit=25${failedOnly ? '&failed_only=true' : ''}`,
      ),
    refetchInterval: 15000,
  })

  const retry = useMutation({
    mutationFn: (id: number) =>
      api<Postback>(`/campaigns/${campaignId}/postbacks/${id}/retry`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['postbacks', campaignId] })
      toast.success('Queued again', 'The sweeper picks it up within a few seconds.')
    },
    onError: (e) => toast.error('Could not queue it', (e as Error).message),
  })

  const fields = (value.postback_fields ?? []) as Field[]

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-4 w-4 text-muted-foreground" />
            Send each call to your API
          </CardTitle>
        </CardHeader>
        <CardBody className="space-y-5">
          <Toggle
            label="Send finished calls"
            checked={value.postback_enabled}
            onChange={(v) => set('postback_enabled', v)}
            hint="Queued the moment the call ends, then delivered by the console — not by the call. A call is never delayed or affected by this."
          />

          <TextField
            label="URL"
            value={value.postback_url ?? ''}
            onChange={(v) => set('postback_url', v.trim() || null)}
            placeholder="https://api.example.com/calls"
            className="font-mono"
            hint="POST, JSON body. Read at send time, so fixing a wrong URL also fixes the calls already waiting."
          />

          <div className="grid gap-5 sm:grid-cols-2">
            <TextField
              label="Auth header name"
              value={value.postback_auth_header ?? ''}
              onChange={(v) => set('postback_auth_header', v.trim() || null)}
              placeholder="x-api-key"
            />
            <TextField
              label="Auth header value"
              value={secret}
              onChange={(v) => {
                setSecret(v)
                set('postback_auth_value', v)
              }}
              type="password"
              placeholder={
                value.postback_auth_value_hint
                  ? `Stored ····${value.postback_auth_value_hint} — leave empty to keep it`
                  : 'Leave empty for no auth'
              }
              hint="Encrypted before storage and never shown again."
            />
          </div>

          <FieldList fields={fields} onChange={(f) => set('postback_fields', f as never)} />

          <Toggle
            label="Send the call details too"
            checked={value.postback_full_payload}
            onChange={(v) => set('postback_full_payload', v)}
            hint={
              value.postback_full_payload
                ? 'On: the fields arrive under "extracted", alongside "call" (duration, outcome, recording id), "dialer" (whatever your dialler sent) and "tools" (what your own APIs answered).'
                : 'Off: the payload is just the fields above, flat, and nothing else — { "payment_mode": "cash", "pincode": "122015" }. Note there is then no id in it, so the receiving end cannot tie the record back to a call.'
            }
          />

          {value.postback_full_payload && (
            <Toggle
              label="Include the full transcript"
              checked={value.postback_include_transcript}
              onChange={(v) => set('postback_include_transcript', v)}
              hint="Off by default — it is by far the largest part of the payload, and most APIs do not want it."
            />
          )}

          <div className="grid gap-5 sm:grid-cols-2">
            <NumberField
              label="Attempts"
              value={value.postback_max_attempts}
              onChange={(v) => set('postback_max_attempts', v)}
              min={1}
              max={20}
              hint="After this many failures the call is marked failed and left alone. It is never deleted — you can retry it by hand below."
            />
            <NumberField
              label="Wait between attempts"
              value={value.postback_retry_after_sec}
              onChange={(v) => set('postback_retry_after_sec', v)}
              min={10}
              max={3600}
              suffix="sec"
              hint="A fixed wait, not a backoff. Predictable beats clever when someone is watching a queue drain."
            />
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>Delivery log</CardTitle>
            <p className="mt-0.5 text-2xs text-muted-foreground">
              One row per call. A postback that never arrived is invisible everywhere else — the
              call itself looks perfectly normal.
            </p>
          </div>
          <Button
            size="sm"
            variant={failedOnly ? 'default' : 'outline'}
            onClick={() => setFailedOnly((f) => !f)}
          >
            {failedOnly ? 'Showing failures' : 'Failures only'}
          </Button>
        </CardHeader>

        {log.isLoading ? (
          <Skeleton className="m-4 h-24" />
        ) : !log.data?.length ? (
          <p className="px-4 py-6 text-center text-xs text-muted-foreground">
            {failedOnly ? 'Nothing has failed.' : 'No calls have been sent yet.'}
          </p>
        ) : (
          <div className="divide-y divide-border/60">
            {log.data.map((p) => (
              <div key={p.id} className="px-4 py-2.5">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-2xs text-muted-foreground">
                  <StatusBadge s={p.status} />
                  <Link to={`/calls/${p.call_id}`} className="text-primary hover:underline">
                    call #{p.call_id}
                  </Link>
                  <span className="tnum">{formatDateTime(p.created_at)}</span>
                  {p.attempts > 0 && (
                    <span className="tnum">
                      {p.attempts} attempt{p.attempts === 1 ? '' : 's'}
                    </span>
                  )}
                  {p.last_status_code != null && (
                    <span className="tnum">HTTP {p.last_status_code}</span>
                  )}
                  <span className="ml-auto flex items-center gap-2">
                    <button
                      onClick={() => setOpen(open === p.id ? null : p.id)}
                      className="hover:text-foreground hover:underline"
                    >
                      {open === p.id ? 'hide payload' : 'show payload'}
                    </button>
                    {p.status !== 'sent' && (
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={retry.isPending}
                        onClick={() => retry.mutate(p.id)}
                      >
                        <RefreshCw className="size-3.5" />
                        Retry
                      </Button>
                    )}
                  </span>
                </div>

                {p.last_error && (
                  <p className="mt-1 break-words text-2xs text-danger">{p.last_error}</p>
                )}

                {open === p.id && (
                  <pre className="scrollbar-thin mt-2 max-h-72 overflow-auto rounded-md bg-muted/50 p-2.5 text-2xs leading-relaxed">
                    {JSON.stringify(p.payload, null, 2)}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
