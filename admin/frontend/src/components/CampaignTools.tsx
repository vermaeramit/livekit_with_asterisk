import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FlaskConical, Pencil, Plus, Trash2, TriangleAlert, Wrench } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog } from '@/components/ui/dialog'
import { Badge, EmptyState, Skeleton } from '@/components/ui/primitives'
import { NumberField, SelectField, TextArea, TextField, Toggle } from '@/components/ui/field'
import { useToast } from '@/components/ui/toast'
import { ApiError, api } from '@/lib/api'
import type { CampaignTool, ToolTestResult } from '@/types'

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => ({ value: m, label: m }))

const BLANK = {
  name: '',
  description: '',
  parameters: '{\n  "type": "object",\n  "properties": {},\n  "required": []\n}',
  method: 'GET',
  url: '',
  auth_header: '',
  auth_value: '',
  body_template: '',
  timeout_ms: 2500,
  max_response_bytes: 8192,
  response_path: '',
  enabled: true,
}

type Draft = typeof BLANK

/**
 * API field names in the order they appear in the dialog.
 *
 * Each input carries `id="f-<api field name>"` so a 422 can be traced straight
 * to the control that caused it. Deriving the id from the label instead — which
 * is what the field wrappers do by default — means "Parameters (JSON Schema)"
 * becomes `f-parameters-json-schema-`, and renaming a label silently breaks
 * the link.
 */
const FIELD_ORDER = [
  'name', 'description', 'method', 'url', 'parameters', 'body_template',
  'auth_header', 'auth_value', 'timeout_ms', 'max_response_bytes',
  'response_path',
]

function toDraft(t: CampaignTool): Draft {
  return {
    name: t.name,
    description: t.description,
    parameters: JSON.stringify(t.parameters, null, 2),
    method: t.method,
    url: t.url,
    auth_header: t.auth_header ?? '',
    // Never prefilled — the server does not return it. Left empty means "keep
    // what is stored"; that is what the hint beside the field explains.
    auth_value: '',
    body_template: t.body_template ?? '',
    timeout_ms: t.timeout_ms,
    max_response_bytes: t.max_response_bytes,
    response_path: t.response_path ?? '',
    enabled: t.enabled,
  }
}

export function CampaignTools({ campaignId }: { campaignId: number }) {
  const qc = useQueryClient()
  const toast = useToast()

  const [editing, setEditing] = useState<CampaignTool | 'new' | null>(null)
  const [draft, setDraft] = useState<Draft>(BLANK)
  // `error` is only what belongs to no single field; anything the server tied
  // to a field goes in `fieldErrors` and is rendered against that input.
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [testing, setTesting] = useState<CampaignTool | null>(null)
  const [testArgs, setTestArgs] = useState('{}')
  const [testResult, setTestResult] = useState<ToolTestResult | null>(null)

  const tools = useQuery({
    queryKey: ['campaign-tools', campaignId],
    queryFn: () => api<CampaignTool[]>(`/campaigns/${campaignId}/tools`),
  })

  const refresh = () => qc.invalidateQueries({ queryKey: ['campaign-tools', campaignId] })
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => {
    setDraft((d) => ({ ...d, [k]: v }))
    // Clear this field's rejection as it is edited. Red left on a field you
    // have just corrected reads as "still wrong" and sends people hunting.
    // Only this field: the banner holds whole-object rules that editing one
    // input does not necessarily resolve.
    setFieldErrors((e) => (k in e ? { ...e, [k]: '' } : e))
  }

  function open(t: CampaignTool | 'new') {
    setEditing(t)
    setDraft(t === 'new' ? BLANK : toDraft(t))
    setError(null)
    setFieldErrors({})
  }

  /**
   * Bring the first rejected field into view.
   *
   * Without this the dialog scrolls wherever it was left — usually at the Save
   * button, fourteen fields below a Name input that is the thing being
   * complained about.
   */
  function revealFirstError(fields: Record<string, string>) {
    const first = FIELD_ORDER.find((f) => fields[f])
    if (!first) return
    // After paint, so the message is already rendered and the field has its
    // final height and position.
    requestAnimationFrame(() => {
      const el = document.getElementById(`f-${first}`)
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) el.focus()
    })
  }

  const save = useMutation({
    mutationFn: () => {
      let parameters: unknown
      try {
        parameters = JSON.parse(draft.parameters)
      } catch {
        throw new Error('Parameters is not valid JSON')
      }
      const payload: Record<string, unknown> = {
        ...draft,
        parameters,
        auth_header: draft.auth_header.trim() || null,
        body_template: draft.body_template.trim() || null,
        response_path: draft.response_path.trim() || null,
      }
      // Omitted, not empty: an empty string means "clear the stored secret",
      // and editing a description should not wipe a credential.
      if (!draft.auth_value) delete payload.auth_value
      const isNew = editing === 'new'
      return api<CampaignTool>(
        isNew ? `/campaigns/${campaignId}/tools`
              : `/campaigns/${campaignId}/tools/${(editing as CampaignTool).id}`,
        { method: isNew ? 'POST' : 'PATCH', body: payload },
      )
    },
    onSuccess: () => {
      refresh()
      setEditing(null)
      toast.success('Tool saved', 'Applies from the next call.')
    },
    onError: (e) => {
      // Anything that names a field is shown at that field. What is left over —
      // "parameters is not valid JSON", a 409, a whole-object rule — has no
      // field to sit against, so it keeps the banner.
      const fields = e instanceof ApiError ? e.fields : {}
      setFieldErrors(fields)
      setError(e instanceof ApiError ? e.general || null : (e as Error).message)
      revealFirstError(fields)
    },
  })

  const remove = useMutation({
    mutationFn: (t: CampaignTool) =>
      api<void>(`/campaigns/${campaignId}/tools/${t.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      refresh()
      toast.success('Tool removed', 'The agent can no longer call it.')
    },
    onError: (e) => toast.error('Could not remove the tool', (e as Error).message),
  })

  const runTest = useMutation({
    mutationFn: () => {
      let args: unknown
      try {
        args = JSON.parse(testArgs)
      } catch {
        throw new Error('Arguments is not valid JSON')
      }
      return api<ToolTestResult>(
        `/campaigns/${campaignId}/tools/${testing!.id}/test`,
        { method: 'POST', body: args as Record<string, unknown> },
      )
    },
    onSuccess: (r) => setTestResult(r),
    onError: (e) =>
      setTestResult({
        ok: false, status_code: null, duration_ms: 0, body: null,
        error: e instanceof ApiError ? e.message : (e as Error).message, url: '',
      }),
  })

  if (tools.isLoading) return <Skeleton className="h-32" />

  const rows = tools.data ?? []

  return (
    <div className="space-y-4">
      <div className="flex justify-end">
        <Button size="sm" onClick={() => open('new')}>
          <Plus className="size-3.5" />
          New tool
        </Button>
      </div>

      {rows.length === 0 ? (
        <EmptyState
          icon={Wrench}
          title="No tools yet"
          hint="A tool lets the agent call your API during a call — look up a service, check a warranty, book a slot."
        />
      ) : (
        <div className="divide-y divide-border rounded-lg border border-border">
          {rows.map((t) => (
            <div key={t.id} className="flex flex-wrap items-start gap-3 p-4">
              <Wrench className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div className="min-w-56 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono text-sm font-medium">{t.name}</span>
                  <Badge tone={t.method === 'GET' ? 'muted' : 'warning'}>{t.method}</Badge>
                  {!t.enabled && <Badge tone="danger">Disabled</Badge>}
                  {t.auth_value_hint && (
                    <span className="font-mono text-2xs text-muted-foreground">
                      {t.auth_header}: ····{t.auth_value_hint}
                    </span>
                  )}
                </div>
                <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">
                  {t.url}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{t.description}</p>
              </div>
              <div className="flex gap-2">
                <Button variant="ghost" size="sm" aria-label={`Test ${t.name}`}
                        onClick={() => { setTesting(t); setTestArgs('{}'); setTestResult(null) }}>
                  <FlaskConical className="size-4" />
                </Button>
                <Button variant="ghost" size="sm" aria-label={`Edit ${t.name}`}
                        onClick={() => open(t)}>
                  <Pencil className="size-4" />
                </Button>
                <Button variant="ghost" size="sm" aria-label={`Delete ${t.name}`}
                        onClick={() => remove.mutate(t)} disabled={remove.isPending}>
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── edit ───────────────────────────────────────────────────────── */}
      <Dialog
        open={editing !== null}
        onClose={() => setEditing(null)}
        size="lg"
        title={editing === 'new' ? 'New tool' : `Edit ${(editing as CampaignTool)?.name ?? ''}`}
        description="The agent decides on its own when to call this, based on the description."
        footer={
          <>
            <Button variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
            <Button onClick={() => save.mutate()} loading={save.isPending}>Save tool</Button>
          </>
        }
      >
        <div className="space-y-4">
          <TextField
            id="f-name"
            label="Name"
            value={draft.name}
            onChange={(v) => set('name', v)}
            error={fieldErrors.name}
            placeholder="check_service_status"
            hint="Lowercase, letters, digits and underscores. This is what the model calls."
          />
          <TextArea
            id="f-description"
            label="When to use it"
            value={draft.description}
            onChange={(v) => set('description', v)}
            error={fieldErrors.description}
            rows={3}
            placeholder="Look up the service status for a vehicle by its registration number. Use this when the caller asks about a service, repair or job card."
            hint="The model reads ONLY this when deciding whether to call the tool. Vague wording is why a tool fires at the wrong moment, or never."
          />

          <div className="grid gap-4 sm:grid-cols-[8rem_1fr]">
            <SelectField label="Method" value={draft.method}
                         onChange={(v) => set('method', v)} options={METHODS} />
            <TextField
              id="f-url"
              label="URL"
              value={draft.url}
              onChange={(v) => set('url', v)}
              error={fieldErrors.url}
              placeholder="https://api.example.com/service?reg={{registration}}"
              hint="{{arg}} is replaced with the model's argument of that name."
            />
          </div>

          <TextArea
            id="f-parameters"
            label="Parameters (JSON Schema)"
            value={draft.parameters}
            onChange={(v) => set('parameters', v)}
            error={fieldErrors.parameters}
            rows={8}
            hint="What arguments the model may send. Describe each one — the model uses those descriptions to fill them in, and a vague one produces a reformatted value your API may not match."
          />

          {draft.method !== 'GET' && (
            <TextArea
              id="f-body_template"
              label="Request body"
              value={draft.body_template}
              onChange={(v) => set('body_template', v)}
              error={fieldErrors.body_template}
              rows={4}
              placeholder={'{"registration": "{{registration}}", "slot": "{{slot}}"}'}
              hint="Same {{arg}} substitution. Sent as application/json unless you set a Content-Type header."
            />
          )}

          <div className="grid gap-4 sm:grid-cols-2">
            <TextField
              id="f-auth_header"
              label="Auth header name"
              value={draft.auth_header}
              onChange={(v) => set('auth_header', v)}
              error={fieldErrors.auth_header}
              placeholder="Authorization"
            />
            <TextField
              id="f-auth_value"
              label="Auth header value"
              value={draft.auth_value}
              onChange={(v) => set('auth_value', v)}
              error={fieldErrors.auth_value}
              type="password"
              placeholder={
                editing !== 'new' && (editing as CampaignTool)?.auth_value_hint
                  ? 'Leave empty to keep the stored value'
                  : 'Bearer …'
              }
              hint="Encrypted before storage and never shown again. Leave empty to keep what is stored."
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-3">
            <NumberField id="f-timeout_ms" label="Timeout (ms)" value={draft.timeout_ms}
                         onChange={(v) => set('timeout_ms', v)} min={200} max={8000}
                         error={fieldErrors.timeout_ms} />
            <NumberField id="f-max_response_bytes" label="Max response (bytes)"
                         value={draft.max_response_bytes}
                         onChange={(v) => set('max_response_bytes', v)} min={256} max={65536}
                         error={fieldErrors.max_response_bytes} />
            <TextField id="f-response_path" label="Response path" value={draft.response_path}
                       onChange={(v) => set('response_path', v)} placeholder="data.customer"
                       error={fieldErrors.response_path} />
          </div>

          <Toggle
            label="Enabled"
            checked={draft.enabled}
            onChange={(v) => set('enabled', v)}
            hint="Disabled tools stay configured but the agent is not told about them."
          />

          {error && (
            <p className="flex items-start gap-2 rounded-md border border-danger/30 bg-danger/10 p-2 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" />
              {error}
            </p>
          )}
        </div>
      </Dialog>

      {/* ── test ───────────────────────────────────────────────────────── */}
      <Dialog
        open={testing !== null}
        onClose={() => setTesting(null)}
        size="lg"
        title={`Test ${testing?.name ?? ''}`}
        description="Runs the request for real, exactly as the agent would."
        footer={
          <>
            <Button variant="ghost" onClick={() => setTesting(null)}>Close</Button>
            <Button onClick={() => runTest.mutate()} loading={runTest.isPending}>Run</Button>
          </>
        }
      >
        <div className="space-y-3">
          {testing && testing.method !== 'GET' && (
            <p className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 p-2 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
              This is a <strong>{testing.method}</strong>. It will do whatever it
              normally does — if it books something, it books it. There is no dry
              run, because a request that is not sent proves nothing.
            </p>
          )}
          <TextArea
            label="Arguments"
            value={testArgs}
            onChange={setTestArgs}
            rows={5}
            hint="What the model would send. These fill the {{…}} placeholders."
          />
          {testResult && (
            <div className="space-y-2 rounded-lg border border-border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={testResult.ok ? 'success' : 'danger'}>
                  {testResult.status_code ?? 'no response'}
                </Badge>
                <span className="tnum text-xs text-muted-foreground">
                  {testResult.duration_ms} ms
                </span>
                {testResult.url && (
                  <span className="truncate font-mono text-2xs text-muted-foreground">
                    {testResult.url}
                  </span>
                )}
              </div>
              {testResult.error && (
                <p className="text-xs text-warning">{testResult.error}</p>
              )}
              {testResult.body && (
                <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-2xs">
                  {testResult.body}
                </pre>
              )}
              <p className="text-2xs text-muted-foreground">
                This is exactly what the model would receive — same truncation,
                same response path.
              </p>
            </div>
          )}
        </div>
      </Dialog>
    </div>
  )
}
