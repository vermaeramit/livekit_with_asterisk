import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  BellOff,
  Check,
  Send,
  Settings2,
  TriangleAlert,
  Webhook,
} from 'lucide-react'
import { PageHeader } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { NumberField, Toggle } from '@/components/ui/field'
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, Input, Label, Select, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDateTime, formatRelative } from '@/lib/utils'
import type { Alert, AlertRule, Tenant } from '@/types'

const KIND_LABEL: Record<string, string> = {
  latency_p95: 'Response latency',
  error_rate: 'Errors',
  transfer_rate: 'Handed to a human',
  limit_hits: 'Guardrail stops',
  no_calls: 'No calls received',
  stale_calls: 'Stuck calls',
}

const KIND_HELP: Record<string, string> = {
  latency_p95: 'p95 turn latency over the window, in milliseconds.',
  error_rate: 'Share of calls that ended in an error.',
  transfer_rate: 'Share handed to a human — a high rate means the agent is not earning its keep.',
  limit_hits: 'Share cut short by a turn, duration or token limit.',
  no_calls: 'Nothing arrived at all — the dialer or the workers may be down.',
  stale_calls: 'Calls left open past their duration limit, which means a worker died holding one.',
}

const UNIT: Record<string, string> = {
  latency_p95: 'ms',
  error_rate: '%',
  transfer_rate: '%',
  limit_hits: '%',
  no_calls: 'calls',
  stale_calls: 'calls',
}

function DeliveryBadge({ alert }: { alert: Alert }) {
  if (alert.delivery === 'sent') return <Badge tone="success">delivered</Badge>
  if (alert.delivery === 'failed') {
    return (
      <Badge tone="danger" title={alert.delivery_error ?? ''}>
        delivery failed
      </Badge>
    )
  }
  if (alert.delivery === 'skipped') {
    return <Badge tone="muted" title="No webhook configured">not sent</Badge>
  }
  return <Badge tone="muted">{alert.delivery}</Badge>
}

function WebhookDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()
  const { user } = useAuth()
  const isSuper = user?.role === 'superadmin'

  const [url, setUrl] = useState('')
  const [tenantId, setTenantId] = useState('')
  const [error, setError] = useState<string | null>(null)

  // The webhook belongs to a client, and a superadmin is not in one - so it has
  // to say which. The API refuses an ambiguous request rather than guessing.
  const tenants = useQuery({
    queryKey: ['tenants'],
    queryFn: () => api<Tenant[]>('/tenants'),
    enabled: isSuper && open,
  })

  const scope = isSuper && tenantId ? `?tenant_id=${tenantId}` : ''
  const ready = !isSuper || Boolean(tenantId)

  const current = useQuery({
    queryKey: ['alert-webhook', tenantId],
    queryFn: () => api<{ configured: boolean; hint: string | null }>(`/alert-webhook${scope}`),
    enabled: open && ready,
  })

  const save = useMutation({
    mutationFn: () =>
      api<{ configured: boolean }>(`/alert-webhook${scope}`, {
        method: 'PUT',
        body: { webhook_url: url.trim() || null },
      }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['alert-webhook'] })
      setUrl('')
      setError(null)
      onClose()
      toast.success(r.configured ? 'Webhook saved' : 'Webhook cleared')
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save'),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Alert webhook"
      description="Alerts are POSTed here as JSON. Slack and Teams both render the text field, so an incoming-webhook URL works as-is."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={() => save.mutate()} loading={save.isPending} disabled={!ready}>
            Save
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        {isSuper && (
          <div className="space-y-1.5">
            <Label htmlFor="wh-tenant">Client</Label>
            <Select
              id="wh-tenant"
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
            >
              <option value="">Select a client…</option>
              {tenants.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </Select>
            <p className="text-2xs text-muted-foreground">
              Each client's alerts go to their own channel.
            </p>
          </div>
        )}

        <div className="space-y-1.5">
          <Label htmlFor="wh">Webhook URL</Label>
          <Input
            id="wh"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder={current.data?.hint ?? 'https://hooks.slack.com/services/…'}
            className="font-mono text-xs"
          />
          <p className="text-2xs text-muted-foreground">
            {current.data?.configured
              ? 'A webhook is already set — it is never shown in full, since anyone holding the URL can post into the channel. Enter a new one to replace it, or leave empty and save to clear it.'
              : 'Leave empty to keep alerts in the console only.'}
          </p>
        </div>

        {error && (
          <p className="rounded-md bg-danger/10 p-2.5 text-xs text-danger ring-1 ring-inset ring-danger/20">
            {error}
          </p>
        )}
      </div>
    </Dialog>
  )
}

function RulesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const toast = useToast()

  const rules = useQuery({
    queryKey: ['alert-rules'],
    queryFn: () => api<AlertRule[]>('/alert-rules'),
    enabled: open,
  })

  const update = useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: Partial<AlertRule> }) =>
      api<AlertRule>(`/alert-rules/${id}`, { method: 'PATCH', body: patch }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alert-rules'] }),
    onError: (e) => toast.error('Could not update the rule', (e as Error).message),
  })

  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title="Alert rules"
      description="Evaluated every minute. A rule fires once when it starts breaching and stays quiet until the condition clears, so a bad afternoon is one alert, not sixty."
      footer={
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="scrollbar-thin max-h-[60vh] space-y-4 overflow-y-auto">
        {rules.isLoading &&
          Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24 w-full" />)}

        {rules.data?.map((r) => (
          <div key={r.id} className="rounded-lg border border-border p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm font-medium">
                  {KIND_LABEL[r.kind] ?? r.kind}
                  {r.firing && <Badge tone="danger">firing</Badge>}
                  <Badge tone={r.severity === 'critical' ? 'danger' : 'warning'}>
                    {r.severity}
                  </Badge>
                </p>
                <p className="mt-0.5 text-2xs text-muted-foreground">{KIND_HELP[r.kind]}</p>
              </div>
              <Toggle
                label=""
                checked={r.enabled}
                onChange={(v) => update.mutate({ id: r.id, patch: { enabled: v } })}
              />
            </div>

            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <NumberField
                label="Threshold"
                value={r.threshold}
                onChange={(v) => update.mutate({ id: r.id, patch: { threshold: v } })}
                suffix={UNIT[r.kind]}
                min={0}
              />
              <NumberField
                label="Window"
                value={r.window_minutes}
                onChange={(v) => update.mutate({ id: r.id, patch: { window_minutes: v } })}
                suffix="min"
                min={5}
                max={1440}
              />
              <NumberField
                label="Minimum calls"
                value={r.min_calls}
                onChange={(v) => update.mutate({ id: r.id, patch: { min_calls: v } })}
                min={0}
                hint="Below this the sample is too small to judge"
              />
            </div>

            {r.last_checked_at && (
              <p className="mt-2 text-2xs text-muted-foreground">
                last checked {formatRelative(r.last_checked_at)}
                {r.last_fired_at && ` · last fired ${formatRelative(r.last_fired_at)}`}
              </p>
            )}
          </div>
        ))}
      </div>
    </Dialog>
  )
}

export function Alerts() {
  const qc = useQueryClient()
  const toast = useToast()
  const [showRules, setShowRules] = useState(false)
  const [showWebhook, setShowWebhook] = useState(false)
  const [onlyUnread, setOnlyUnread] = useState(true)

  const alerts = useQuery({
    queryKey: ['alerts', onlyUnread],
    queryFn: () => api<Alert[]>(`/alerts?unacknowledged=${onlyUnread}`),
    refetchInterval: 30_000,
  })

  const ack = useMutation({
    mutationFn: (a: Alert) =>
      api<Alert>(`/alerts/${a.id}/acknowledge`, { method: 'POST' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] })
      qc.invalidateQueries({ queryKey: ['alerts-unread'] })
    },
    onError: (e) => toast.error('Could not acknowledge', (e as Error).message),
  })

  return (
    <div className="mx-auto max-w-[1100px] space-y-5 p-5 lg:p-7">
      <PageHeader
        title="Alerts"
        description="Raised automatically when a rule starts breaching."
        actions={
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowWebhook(true)}>
              <Webhook className="h-3.5 w-3.5" />
              Webhook
            </Button>
            <Button variant="outline" size="sm" onClick={() => setShowRules(true)}>
              <Settings2 className="h-3.5 w-3.5" />
              Rules
            </Button>
          </div>
        }
      />

      <div className="flex items-center gap-2">
        <Button
          variant={onlyUnread ? 'default' : 'outline'}
          size="sm"
          onClick={() => setOnlyUnread(true)}
        >
          Unacknowledged
        </Button>
        <Button
          variant={onlyUnread ? 'outline' : 'default'}
          size="sm"
          onClick={() => setOnlyUnread(false)}
        >
          All
        </Button>
      </div>

      <Card className="overflow-hidden">
        {alerts.isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-16 w-full" />
            ))}
          </div>
        ) : alerts.isError ? (
          <EmptyState
            icon={TriangleAlert}
            title="Could not load alerts"
            hint={(alerts.error as Error).message}
          />
        ) : !alerts.data?.length ? (
          <EmptyState
            icon={BellOff}
            title={onlyUnread ? 'Nothing needs attention' : 'No alerts recorded'}
            hint="Rules are evaluated every minute. Adjust the thresholds under Rules."
          />
        ) : (
          <div className="divide-y divide-border/70">
            {alerts.data.map((a) => (
              <div key={a.id} className="flex items-start gap-3 px-4 py-3">
                <span
                  className={cn(
                    'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                    a.severity === 'critical' ? 'bg-danger' : 'bg-warning',
                    a.acknowledged_at && 'opacity-30',
                  )}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">
                      {KIND_LABEL[a.kind] ?? a.kind}
                    </span>
                    <span className="text-2xs text-muted-foreground">
                      {a.tenant_name}
                      {a.campaign_name ? ` · ${a.campaign_name}` : ''}
                    </span>
                    <DeliveryBadge alert={a} />
                    <span
                      className="ml-auto tnum text-2xs text-muted-foreground"
                      title={formatDateTime(a.created_at)}
                    >
                      {formatRelative(a.created_at)}
                    </span>
                  </div>
                  <p className="mt-0.5 text-sm text-muted-foreground">{a.message}</p>
                  {a.acknowledged_at && (
                    <p className="mt-1 text-2xs text-muted-foreground">
                      acknowledged by {a.acknowledged_by_email ?? 'someone'}{' '}
                      {formatRelative(a.acknowledged_at)}
                    </p>
                  )}
                </div>

                {!a.acknowledged_at && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => ack.mutate(a)}
                    loading={ack.isPending && ack.variables?.id === a.id}
                  >
                    <Check className="h-3.5 w-3.5" />
                    Acknowledge
                  </Button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-4 w-4 text-muted-foreground" />
            How delivery works
          </CardTitle>
        </CardHeader>
        <CardBody className="space-y-2 text-xs leading-relaxed text-muted-foreground">
          <p>
            Every alert is written here first and the webhook is attempted second, so a chat
            tool being down loses nothing — the row stays, marked{' '}
            <strong>delivery failed</strong> with the reason.
          </p>
          <p>
            A rule fires on the <strong>edge</strong>: once when it starts breaching, then
            silence until the condition clears. Raising a threshold also re-arms the rule, so
            turning down the noise cannot accidentally mute the next real breach.
          </p>
        </CardBody>
      </Card>

      <RulesDialog open={showRules} onClose={() => setShowRules(false)} />
      <WebhookDialog open={showWebhook} onClose={() => setShowWebhook(false)} />
    </div>
  )
}
