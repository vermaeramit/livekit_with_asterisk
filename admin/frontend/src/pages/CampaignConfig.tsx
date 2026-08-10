import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BookOpen,
  History,
  Info,
  KeyRound,
  MessageSquare,
  PhoneForwarded,
  PhoneIncoming,
  ShieldCheck,
  TriangleAlert,
  Undo2,
  Waves,
} from 'lucide-react'
import { CampaignRoutes } from '@/components/CampaignRoutes'
import { KnowledgeDocs } from '@/components/KnowledgeDocs'
import { ProviderKeys } from '@/components/ProviderKeys'
import { Button } from '@/components/ui/button'
import { ComboField, NumberField, SelectField, TextArea, TextField, Toggle } from '@/components/ui/field'
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDateTime, formatRelative } from '@/lib/utils'
import type { AgentConfig, AuditEntry, Campaign } from '@/types'

// Sarvam's saarika/bulbul language codes. Anything outside this set is accepted
// by the API but will fail at call time, so the editor does not offer it.
const LANGUAGES = [
  { value: 'hi-IN', label: 'Hindi (hi-IN)' },
  { value: 'en-IN', label: 'English — India (en-IN)' },
  { value: 'bn-IN', label: 'Bengali (bn-IN)' },
  { value: 'gu-IN', label: 'Gujarati (gu-IN)' },
  { value: 'kn-IN', label: 'Kannada (kn-IN)' },
  { value: 'ml-IN', label: 'Malayalam (ml-IN)' },
  { value: 'mr-IN', label: 'Marathi (mr-IN)' },
  { value: 'od-IN', label: 'Odia (od-IN)' },
  { value: 'pa-IN', label: 'Punjabi (pa-IN)' },
  { value: 'ta-IN', label: 'Tamil (ta-IN)' },
  { value: 'te-IN', label: 'Telugu (te-IN)' },
]

const LLM_MODELS = [
  { value: 'gpt-4.1-mini', label: 'gpt-4.1-mini — measured p50 ~2.0s, low variance' },
  { value: 'gpt-4.1-nano', label: 'gpt-4.1-nano — faster, weaker reasoning' },
  { value: 'gpt-4.1', label: 'gpt-4.1 — strongest, noticeably slower' },
  { value: 'gpt-4o-mini', label: 'gpt-4o-mini' },
]

// Only models and speakers known to exist are listed. Every one of these fails
// at call time rather than on save if it is wrong, so the list is the safe path
// and "Custom…" is the escape hatch for anything the provider adds later.
const STT_MODELS = [
  { value: 'saarika:v2.5', label: 'saarika:v2.5 — in use' },
  { value: 'saarika:v2', label: 'saarika:v2' },
  { value: 'saarika:v1', label: 'saarika:v1' },
]

const TTS_MODELS = [
  { value: 'bulbul:v3', label: 'bulbul:v3 — in use' },
  { value: 'bulbul:v2', label: 'bulbul:v2' },
]

// bulbul:v3's speakers, taken from the plugin's own rejection message rather
// than from documentation. The first version of this list was written from
// memory and was bulbul:v2's - every one of those names makes TTS.__init__
// raise, so the job dies before the call is even answered.
const VOICES = [
  'shubh', 'ritu', 'rahul', 'pooja', 'simran', 'kavya', 'amit', 'ratan',
  'rohan', 'dev', 'ishita', 'shreya', 'manan', 'sumit', 'priya', 'aditya',
  'kabir', 'neha', 'varun', 'roopa', 'aayan', 'ashutosh', 'advait', 'amelia',
  'sophia', 'suhani', 'rupali', 'tanya', 'shruti', 'kavitha',
].map((v) => ({ value: v, label: v }))

type TabKey =
  | 'conversation' | 'voice' | 'knowledge' | 'routing' | 'keys' | 'limits' | 'history'

const TABS: { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'conversation', label: 'Conversation', icon: MessageSquare },
  { key: 'voice', label: 'Voice & model', icon: Waves },
  { key: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { key: 'routing', label: 'Routing', icon: PhoneIncoming },
  // Its own tab rather than a section under "Voice & model": these decide whose
  // account the calls are billed to, and that is not a voice setting.
  { key: 'keys', label: 'API keys', icon: KeyRound },
  { key: 'limits', label: 'Limits & handoff', icon: ShieldCheck },
  { key: 'history', label: 'History', icon: History },
]

function Note({ children, tone = 'info' }: { children: React.ReactNode; tone?: 'info' | 'warn' }) {
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-md p-3 text-xs ring-1 ring-inset',
        // --warning-foreground is the colour for text ON a solid warning fill;
        // on a 10% tint it is near-white and effectively invisible.
        tone === 'warn'
          ? 'bg-warning/10 text-foreground/90 ring-warning/30'
          : 'bg-primary/5 text-muted-foreground ring-primary/15',
      )}
    >
      {tone === 'warn' ? (
        <TriangleAlert className="mt-px h-3.5 w-3.5 shrink-0 text-warning" />
      ) : (
        <Info className="mt-px h-3.5 w-3.5 shrink-0 text-primary" />
      )}
      <div className="leading-relaxed">{children}</div>
    </div>
  )
}

function HistoryTab({ campaignId }: { campaignId: number }) {
  const audit = useQuery({
    queryKey: ['campaign-audit', campaignId],
    queryFn: () => api<AuditEntry[]>(`/campaigns/${campaignId}/audit`),
  })

  if (audit.isLoading) {
    return (
      <div className="space-y-2 p-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    )
  }

  if (!audit.data?.length) {
    return (
      <EmptyState
        icon={History}
        title="No changes recorded"
        hint="Every save from the console is logged here with who made it."
      />
    )
  }

  return (
    <div className="divide-y divide-border/60">
      {audit.data.map((e) => (
        <div key={e.id} className="px-4 py-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <Badge tone={e.action === 'delete' ? 'danger' : 'default'}>{e.action}</Badge>
            <span className="font-medium">{e.entity}</span>
            <span className="text-muted-foreground">{e.user_email ?? 'system'}</span>
            <span className="ml-auto tnum text-2xs text-muted-foreground" title={formatDateTime(e.created_at)}>
              {formatRelative(e.created_at)}
            </span>
          </div>

          {e.changes && Object.keys(e.changes).length > 0 && (
            <div className="mt-2 space-y-1">
              {Object.entries(e.changes).map(([field, { from, to }]) => (
                <div key={field} className="flex flex-wrap items-baseline gap-x-2 text-2xs">
                  <span className="font-mono font-medium">{field}</span>
                  <span className="max-w-[16rem] truncate text-danger line-through">
                    {String(from ?? '—').slice(0, 120)}
                  </span>
                  <span className="text-muted-foreground">→</span>
                  <span className="max-w-[16rem] truncate text-success">
                    {String(to ?? '—').slice(0, 120)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export function CampaignConfig() {
  const { id } = useParams<{ id: string }>()
  const campaignId = Number(id)
  const qc = useQueryClient()
  const toast = useToast()
  const { can } = useAuth()
  const canEdit = can('tenant_admin')

  const [tab, setTab] = useState<TabKey>('conversation')
  const [draft, setDraft] = useState<Partial<AgentConfig>>({})

  const campaign = useQuery({
    queryKey: ['campaign', campaignId],
    queryFn: () => api<Campaign>(`/campaigns/${campaignId}`),
  })

  const config = useQuery({
    queryKey: ['campaign-config', campaignId],
    queryFn: () => api<AgentConfig>(`/campaigns/${campaignId}/config`),
  })

  // The draft holds only edited fields, so a save sends exactly what changed and
  // a concurrent edit to a different field is not silently overwritten.
  const value = useMemo(
    () => ({ ...(config.data ?? {}), ...draft }) as AgentConfig,
    [config.data, draft],
  )
  const dirty = Object.keys(draft).length > 0

  function set<K extends keyof AgentConfig>(key: K, v: AgentConfig[K]) {
    setDraft((d) => {
      const next = { ...d, [key]: v }
      // Editing back to the original value should clear the dirty flag, not
      // leave a no-op change queued.
      if (config.data && config.data[key] === v) delete next[key]
      return next
    })
  }

  const save = useMutation({
    mutationFn: () =>
      api<AgentConfig>(`/campaigns/${campaignId}/config`, { method: 'PATCH', body: draft }),
    onSuccess: (fresh) => {
      qc.setQueryData(['campaign-config', campaignId], fresh)
      qc.invalidateQueries({ queryKey: ['campaign-audit', campaignId] })
      setDraft({})
      toast.success('Configuration saved', 'It applies from the next call — nothing in progress is affected.')
    },
    onError: (e) =>
      toast.error('Could not save', e instanceof ApiError ? e.message : 'Unexpected error'),
  })

  if (config.isLoading || campaign.isLoading) {
    return (
      <div className="mx-auto max-w-4xl space-y-5 p-5 lg:p-7">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (config.isError) {
    return (
      <div className="mx-auto max-w-4xl p-6">
        <EmptyState
          icon={TriangleAlert}
          title="No agent configuration"
          hint={(config.error as Error).message}
          action={
            <Link to="/campaigns">
              <Button size="sm" variant="outline">
                Back to campaigns
              </Button>
            </Link>
          }
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl space-y-5 p-5 lg:p-7 pb-24">
      <div>
        <Link
          to="/campaigns"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          All campaigns
        </Link>
        <h1 className="mt-1.5 text-xl font-semibold tracking-tight">
          {campaign.data?.name ?? 'Campaign'}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          <span className="font-mono text-xs">{value.name}</span> · last changed{' '}
          {formatRelative(value.updated_at)}
        </p>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-border">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={cn(
              '-mb-px flex items-center gap-1.5 whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors',
              tab === key
                ? 'border-primary font-medium text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            <Icon className="h-3.5 w-3.5" />
            {label}
          </button>
        ))}
      </div>

      {tab === 'conversation' && (
        <Card>
          <CardHeader>
            <CardTitle>What the agent says</CardTitle>
          </CardHeader>
          <CardBody className="space-y-5">
            <Note>
              Saved changes take effect on the <strong>next call</strong>. The worker loads this
              configuration when a job starts, so nothing in progress is disturbed and no restart is
              needed.
            </Note>

            <SelectField
              label="Language"
              value={value.language}
              onChange={(v) => set('language', v)}
              options={LANGUAGES}
              hint="Drives speech recognition and the voice. Callers can still speak mixed Hindi-English."
            />

            <TextField
              label="Greeting"
              value={value.greeting ?? ''}
              onChange={(v) => set('greeting', v || null)}
              placeholder="Namaste! Main aapki kaise madad kar sakta hoon?"
              hint="Spoken the moment the call connects. Leave empty to let the caller speak first."
            />

            <TextField
              label="Recording notice"
              value={value.recording_disclosure ?? ''}
              onChange={(v) => set('recording_disclosure', v)}
              placeholder="Yeh call quality aur training ke liye record ki ja rahi hai."
              hint="Spoken immediately after the greeting, as one sentence with it."
            />
            <Note tone="warn">
              Every call on this platform is recorded — the dialplan does it
              unconditionally, whatever a campaign is set to. This line is what
              tells the caller, so it cannot be left empty. It is kept out of the
              greeting on purpose: the greeting gets rewritten often, and a notice
              living inside it would eventually be deleted by accident with
              nothing to warn you.
            </Note>

            <TextArea
              label="Instructions"
              value={value.instructions}
              onChange={(v) => set('instructions', v)}
              rows={16}
              mono
              hint={
                <>
                  The system prompt. Grounding and transfer rules are appended automatically — do not
                  repeat them here. Keep it stable: the first ~1024 tokens are what OpenAI caches, and
                  editing them throws that cache away.
                </>
              }
            />
          </CardBody>
        </Card>
      )}

      {tab === 'voice' && (
        <Card>
          <CardHeader>
            <CardTitle>Voice and model</CardTitle>
          </CardHeader>
          <CardBody className="space-y-5">
            <Note tone="warn">
              <code>SARVAM_STT_MODEL</code> and <code>SARVAM_TTS_VOICE</code> in the server's{' '}
              <code>.env</code> override the two fields below. If a change here has no effect on the
              next call, that is why — check with{' '}
              <code>grep -c 'SARVAM_TTS_VOICE\|SARVAM_STT_MODEL' /opt/aivoice/.env</code>.
            </Note>

            <div className="grid gap-5 sm:grid-cols-2">
              <ComboField
                label="Speech-to-text model"
                value={value.stt_model ?? ''}
                onChange={(v) => set('stt_model', v.trim() || null)}
                options={STT_MODELS}
                placeholder="saarika:…"
                allowEmpty
                emptyLabel="Default (saarika:v2.5)"
                hint="Sarvam."
              />
              <ComboField
                label="Text-to-speech model"
                value={value.tts_model ?? ''}
                onChange={(v) => set('tts_model', v.trim() || null)}
                options={TTS_MODELS}
                placeholder="bulbul:…"
                allowEmpty
                emptyLabel="Default (bulbul:v3)"
                hint="Sarvam."
              />
            </div>

            <ComboField
              label="Voice"
              value={value.tts_voice ?? ''}
              onChange={(v) => set('tts_voice', v.trim() || null)}
              options={VOICES}
              placeholder="speaker name"
              allowEmpty
              emptyLabel="Model default"
              hint="These are bulbul:v3 speakers. A speaker the chosen model does not have makes the call fail before it is answered, not on save."
            />

            <div className="grid gap-5 sm:grid-cols-2">
              <SelectField
                label="Language model"
                value={value.llm_model}
                onChange={(v) => set('llm_model', v)}
                options={LLM_MODELS}
                hint="gpt-4.1-mini was chosen for variance, not average — it cut spread from 800ms to 85ms."
              />
              <NumberField
                label="Temperature"
                value={value.llm_temperature}
                onChange={(v) => set('llm_temperature', v)}
                min={0}
                max={2}
                step={0.1}
                hint="Higher is more varied and less predictable. 0.6 is the default."
              />
            </div>

            <Toggle
              label="Allow barge-in"
              checked={value.allow_interrupt}
              onChange={(v) => set('allow_interrupt', v)}
              hint="The caller can cut the agent off mid-sentence. Turning this off makes calls feel robotic."
            />
          </CardBody>
        </Card>
      )}

      {tab === 'knowledge' && (
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>Documents</CardTitle>
            </CardHeader>
            <CardBody>
              <KnowledgeDocs campaignId={campaignId} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Retrieval tuning</CardTitle>
            </CardHeader>
            <CardBody className="space-y-5">
            <Note>
              Two layers. A small knowledge base is injected into the prompt whole and costs nothing
              per turn; a large one contributes only its headings, and the agent calls a search tool
              when that is not enough. Retrieval on every turn was measured at 390–1244 ms and
              rejected.
            </Note>

            <Toggle
              label="Enable the knowledge base"
              checked={value.kb_enabled}
              onChange={(v) => set('kb_enabled', v)}
              hint="Off means the agent answers from its instructions alone."
            />

            <div className="grid gap-5 sm:grid-cols-3">
              <NumberField
                label="Results per search"
                value={value.kb_top_k}
                onChange={(v) => set('kb_top_k', v)}
                min={1}
                max={10}
                hint="More context, slower turn."
              />
              <NumberField
                label="Minimum score"
                value={value.kb_min_score}
                onChange={(v) => set('kb_min_score', v)}
                min={0}
                max={1}
                step={0.05}
                hint="Below this a chunk is dropped. 0.25 default."
              />
              <NumberField
                label="Inline budget"
                value={value.kb_inline_max_tokens}
                onChange={(v) => set('kb_inline_max_tokens', v)}
                min={0}
                max={16000}
                suffix="tok"
                hint="Under this, the whole document goes in the prompt."
              />
            </div>

            <TextArea
              label="Knowledge summary"
              value={value.kb_summary ?? ''}
              onChange={(v) => set('kb_summary', v || null)}
              rows={4}
              hint="Optional one-paragraph description of what the documents cover. Helps the agent decide when to search."
            />
            </CardBody>
          </Card>
        </div>
      )}

      {tab === 'routing' && (
        <Card>
          <CardHeader>
            <CardTitle>Inbound numbers</CardTitle>
          </CardHeader>
          <CardBody className="space-y-5">
            <Note>
              A call reaches this campaign when the number it dialled is listed here.
              That is also what makes the enable/disable switch real: a call to a
              disabled campaign is dropped immediately and falls through to your human
              extension, rather than being answered by the wrong agent.
            </Note>
            <Note tone="warn">
              This list decides <em>which campaign</em> serves a call — it does not make
              the number reachable. The number must also be pointed at the platform by
              your telephony side. On the lab PBX anything in <code>700–799</code> is
              already forwarded (except <code>702</code>, the latency test); anything
              outside that range answers <code>404 Not Found</code> before it ever gets
              here.
            </Note>
            <CampaignRoutes campaignId={campaignId} />
          </CardBody>
        </Card>
      )}

      {tab === 'keys' && (
        <Card>
          <CardHeader>
            <CardTitle>API keys</CardTitle>
          </CardHeader>
          <CardBody className="space-y-5">
            <Note>
              These decide whose provider account this campaign's calls are billed
              to. By default it uses the client's keys; set one here only if this
              campaign needs its own.
            </Note>
            <Note tone="warn">
              A campaign cannot be enabled unless every key resolves — its own or
              the client's. That check is here on purpose: without a key the call
              still connects, but the agent cannot answer and the caller is handed
              to a human, which looks like a fault rather than a missing setting.
            </Note>
            <ProviderKeys scope="campaign" id={campaignId} />
          </CardBody>
        </Card>
      )}

      {tab === 'limits' && (
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>Cost guardrails</CardTitle>
            </CardHeader>
            <CardBody className="space-y-5">
              <Note>
                A stuck or abusive call is capped rather than left to run. When a limit is reached the
                agent says the message below and hangs up, and the call is tagged with which limit
                fired.
              </Note>

              <div className="grid gap-5 sm:grid-cols-3">
                <NumberField
                  label="Max turns"
                  value={value.max_turns}
                  onChange={(v) => set('max_turns', v)}
                  min={1}
                  max={300}
                />
                <NumberField
                  label="Max duration"
                  value={value.max_duration_sec}
                  onChange={(v) => set('max_duration_sec', v)}
                  min={30}
                  max={7200}
                  suffix="sec"
                />
                <NumberField
                  label="Max prompt tokens"
                  value={value.max_prompt_tokens}
                  onChange={(v) => set('max_prompt_tokens', v)}
                  min={1000}
                  max={1000000}
                  suffix="tok"
                />
              </div>

              <TextField
                label="Message when a limit is hit"
                value={value.limit_message ?? ''}
                onChange={(v) => set('limit_message', v || null)}
                placeholder="Is call ka samay poora ho gaya hai. Dhanyavaad."
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <PhoneForwarded className="h-4 w-4 text-muted-foreground" />
                Human handoff
              </CardTitle>
            </CardHeader>
            <CardBody className="space-y-5">
              <Toggle
                label="Allow transfer to a human"
                checked={value.transfer_enabled}
                onChange={(v) => set('transfer_enabled', v)}
                hint="The agent transfers by SIP REFER when the caller asks for a person, or when it cannot help."
              />

              <TextField
                label="Transfer target"
                value={value.transfer_to}
                onChange={(v) => set('transfer_to', v)}
                placeholder="sip:800@10.130.9.243"
                className="font-mono"
                hint="A SIP URI. A wrong target only surfaces when a real caller asks for a human."
              />

              <TextField
                label="Message before transferring"
                value={value.transfer_message ?? ''}
                onChange={(v) => set('transfer_message', v || null)}
                placeholder="Main aapko ek executive se connect kar raha hoon."
                hint="Spoken and finished before the REFER is sent — otherwise the caller hears it cut off."
              />
            </CardBody>
          </Card>
        </div>
      )}

      {tab === 'history' && (
        <Card className="overflow-hidden">
          <CardHeader>
            <CardTitle>Change history</CardTitle>
          </CardHeader>
          <HistoryTab campaignId={campaignId} />
        </Card>
      )}

      {/* save bar — only while there is something to save */}
      {dirty && canEdit && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-card/95 backdrop-blur md:left-60">
          <div className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-5 py-3">
            <p className="text-xs text-muted-foreground">
              {Object.keys(draft).length} unsaved{' '}
              {Object.keys(draft).length === 1 ? 'change' : 'changes'}
              <span className="ml-2 hidden font-mono text-2xs sm:inline">
                {Object.keys(draft).join(', ')}
              </span>
            </p>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" onClick={() => setDraft({})}>
                <Undo2 className="h-3.5 w-3.5" />
                Discard
              </Button>
              <Button size="sm" onClick={() => save.mutate()} loading={save.isPending}>
                Save changes
              </Button>
            </div>
          </div>
        </div>
      )}

      {!canEdit && (
        <Note>You have read-only access to this configuration.</Note>
      )}
    </div>
  )
}
