import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowLeft,
  BookOpen,
  History,
  Info,
  KeyRound,
  MessageSquare,
  MicOff,
  PhoneForwarded,
  PhoneIncoming,
  Plus,
  Send,
  ShieldCheck,
  TriangleAlert,
  Wrench,
  Undo2,
  Waves,
  X,
} from 'lucide-react'
import { CampaignRoutes } from '@/components/CampaignRoutes'
import { KnowledgeDocs } from '@/components/KnowledgeDocs'
import { CampaignPostback } from '@/components/CampaignPostback'
import { CampaignTools } from '@/components/CampaignTools'
import { ProviderKeys } from '@/components/ProviderKeys'
import { PAGE } from '@/components/Layout'
import { Button } from '@/components/ui/button'
import { ComboField, NumberField, SelectField, TextArea, TextField, Toggle } from '@/components/ui/field'
import { TransferHours } from '@/components/TransferHours'
import { Badge, Card, CardBody, CardHeader, CardTitle, EmptyState, Input, Label, Skeleton } from '@/components/ui/primitives'
import { useToast } from '@/components/ui/toast'
import { api, ApiError } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { cn, formatDateTime, formatRelative } from '@/lib/utils'
import type { AgentConfig, Dialler, AuditEntry, Campaign, TtsCatalog } from '@/types'

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

const PROVIDERS = [
  { value: 'sarvam', label: 'Sarvam' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'soniox', label: 'Soniox' },
]

// Only models and speakers known to exist are listed. Every one of these fails
// at call time rather than on save if it is wrong, so the list is the safe path
// and "Custom…" is the escape hatch for anything the provider adds later.
const STT_MODELS: Record<string, { value: string; label: string }[]> = {
  sarvam: [
    { value: 'saarika:v2.5', label: 'saarika:v2.5 — measured' },
    { value: 'saarika:v2', label: 'saarika:v2' },
    { value: 'saarika:v1', label: 'saarika:v1' },
  ],
  openai: [{ value: 'gpt-4o-mini-transcribe', label: 'gpt-4o-mini-transcribe' }],
  soniox: [{ value: 'stt-rt-v5', label: 'stt-rt-v5 — realtime' }],
}

const TTS_MODELS: Record<string, { value: string; label: string }[]> = {
  sarvam: [
    { value: 'bulbul:v3', label: 'bulbul:v3 — measured ~240ms TTFB' },
    { value: 'bulbul:v2', label: 'bulbul:v2' },
  ],
  openai: [{ value: 'gpt-4o-mini-tts', label: 'gpt-4o-mini-tts — ~889ms measured' }],
  // Only reached if the live catalogue cannot be read. tts-rt-v1 and its
  // -preview alias are deliberately absent: Soniox removes them on 31 Aug 2026,
  // and a fallback list is exactly where a dead model would go unnoticed.
  soniox: [{ value: 'tts-rt-v2', label: 'tts-rt-v2' }],
}

// bulbul:v3's speakers, taken from the plugin's own rejection message rather
// than from documentation. The first version of this list was written from
// memory and was bulbul:v2's - every one of those names makes TTS.__init__
// raise, so the job dies before the call is even answered.
//
// ⚠️ The Soniox list came from their docs, NOT from the provider. They expose
// GET /v1/voices; once there is a funded account, read it from there and delete
// this literal. Every hardcoded voice list in this project has been wrong once.
const VOICES: Record<string, { value: string; label: string }[]> = {
  sarvam: [
    'shubh', 'ritu', 'rahul', 'pooja', 'simran', 'kavya', 'amit', 'ratan',
    'rohan', 'dev', 'ishita', 'shreya', 'manan', 'sumit', 'priya', 'aditya',
    'kabir', 'neha', 'varun', 'roopa', 'aayan', 'ashutosh', 'advait', 'amelia',
    'sophia', 'suhani', 'rupali', 'tanya', 'shruti', 'kavitha',
  ].map((v) => ({ value: v, label: v })),
  // Fallback only - the real list is read from Soniox per model, because it
  // differs per model. This one used to be the union of v1 and v2 and offered
  // Meera, Maya, Noah, Jack, Claire, Sofia and Elise, none of which exist on
  // tts-rt-v2. Trimmed to the four with an Indian accent on v2, which is what
  // this deployment actually uses.
  soniox: [
    'Priya', 'Arjun', 'Rohan', 'Karan',
  ].map((v) => ({ value: v, label: v })),
  openai: [
    'alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer',
  ].map((v) => ({ value: v, label: v })),
}

// Soniox does not list Odia among its 60+ languages; Sarvam does. A campaign on
// od-IN cannot use Soniox, and finding that out from a silent mis-synthesis
// would be miserable.
const SONIOX_UNSUPPORTED = ['od-IN']

type TabKey =
  | 'conversation' | 'voice' | 'knowledge' | 'tools' | 'routing' | 'keys'
  | 'limits' | 'postback' | 'history'

const TABS: { key: TabKey; label: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { key: 'conversation', label: 'Conversation', icon: MessageSquare },
  { key: 'voice', label: 'Voice & model', icon: Waves },
  { key: 'knowledge', label: 'Knowledge', icon: BookOpen },
  { key: 'tools', label: 'Tools', icon: Wrench },
  { key: 'routing', label: 'Routing', icon: PhoneIncoming },
  // Its own tab rather than a section under "Voice & model": these decide whose
  // account the calls are billed to, and that is not a voice setting.
  { key: 'keys', label: 'API keys', icon: KeyRound },
  { key: 'limits', label: 'Limits & handoff', icon: ShieldCheck },
  // Its own tab: what leaves this system afterwards is a different concern
  // from how the call is run, and it has a log of its own to show.
  { key: 'postback', label: 'Send to API', icon: Send },
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

/**
 * The lines the agent says into silence, one per attempt.
 *
 * There is no separate "number of attempts" field on purpose. Two fields that
 * have to agree eventually stop agreeing, and the failure is invisible: a count
 * of 3 with 2 lines means the third attempt says nothing, and the caller is hung
 * up on without warning. The list IS the count.
 */
function SilencePrompts({
  lines,
  onChange,
}: {
  lines: string[]
  onChange: (lines: string[]) => void
}) {
  const edit = (i: number, v: string) =>
    onChange(lines.map((l, j) => (j === i ? v : l)))

  return (
    <div className="space-y-1.5">
      <Label>What to say, in order</Label>

      {lines.length === 0 ? (
        <p className="text-2xs leading-relaxed text-muted-foreground">
          Nothing set — the agent waits indefinitely, and the call runs until the duration limit.
        </p>
      ) : (
        <div className="space-y-2">
          {lines.map((line, i) => (
            <div key={i} className="flex items-start gap-2">
              <span className="mt-2 w-14 shrink-0 text-2xs tabular-nums text-muted-foreground">
                {i === lines.length - 1 && lines.length > 1 ? 'last' : `try ${i + 1}`}
              </span>
              <Input
                value={line}
                onChange={(e) => edit(i, e.target.value)}
                placeholder={
                  i === 0
                    ? 'Kya aap mujhe sun paa rahe hain?'
                    : 'Aawaz nahi aa rahi, main call band kar rahi hoon. Dhanyavaad.'
                }
              />
              <Button
                variant="ghost"
                size="sm"
                className="mt-0.5"
                aria-label={`Remove line ${i + 1}`}
                onClick={() => onChange(lines.filter((_, j) => j !== i))}
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
          disabled={lines.length >= 5}
          onClick={() => onChange([...lines, ''])}
        >
          <Plus className="size-3.5" />
          Add a line
        </Button>
        <p className="text-2xs leading-relaxed text-muted-foreground">
          {lines.length > 0
            ? `${lines.length} attempt${lines.length === 1 ? '' : 's'}, then the call ends. The last line is spoken first — write it as a goodbye.`
            : 'Each line is one attempt. After the last one the call ends.'}
        </p>
      </div>
    </div>
  )
}

export function CampaignConfig() {
  const { id } = useParams<{ id: string }>()
  const campaignId = Number(id)
  const qc = useQueryClient()
  const toast = useToast()
  const { can } = useAuth()
  const canEdit = can('campaign.write')

  const [tab, setTab] = useState<TabKey>('conversation')
  const [draft, setDraft] = useState<Partial<AgentConfig>>({})
  // Counted server-side with the model's own tokeniser rather than guessed at
  // in the browser, and debounced so it is not recounted on every keystroke.
  const [countable, setCountable] = useState('')

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

  // Settles 500ms after typing stops. Counting on every keystroke would put a
  // request behind each character for no gain - nobody reads a number that is
  // moving.
  const instructions = value.instructions ?? ''
  useEffect(() => {
    const t = setTimeout(() => setCountable(instructions), 500)
    return () => clearTimeout(t)
  }, [instructions])

  const tokens = useQuery({
    queryKey: ['prompt-tokens', campaignId, countable],
    queryFn: () =>
      api<{ tokens: number; exact: boolean }>(
        `/campaigns/${campaignId}/prompt-tokens`,
        // Not stringified here: api() does that, and doing it twice sends a
        // JSON string rather than an object.
        { method: 'POST', body: { text: countable } },
      ),
    enabled: Boolean(campaignId) && countable.length > 0,
    // The same text always counts the same, so it never needs recounting.
    staleTime: Infinity,
  })

  // Models and voices read from the provider, not held as a list here.
  //
  // The static VOICES map below was the union of two Soniox models: it offered
  // Meera, which exists on tts-rt-v1 and not on tts-rt-v2, and a voice the
  // chosen model does not have raises inside TTS.__init__ - the job dies before
  // the call is answered, and nothing on this form hints at it.
  //
  // Failure is not surfaced: no key, no network, an unsupported provider, all
  // fall back to the static list, which is what this had before.
  // Everyone who can edit a campaign can read this list; only the platform can
  // change it. Failure is not surfaced beyond an empty list - the campaign then
  // shows the plain SIP target it has always had, which still works.
  const diallers = useQuery({
    queryKey: ['diallers'],
    queryFn: () => api<Dialler[]>('/diallers'),
    staleTime: 5 * 60 * 1000,
    retry: false,
  })

  const ttsCatalog = useQuery({
    queryKey: ['tts-catalog', campaignId, value.tts_provider],
    queryFn: () =>
      api<TtsCatalog>(`/campaigns/${campaignId}/tts-catalog/${value.tts_provider}`),
    enabled: Boolean(value.tts_provider),
    staleTime: 10 * 60 * 1000,
    retry: false,
  })

  const liveModels = ttsCatalog.data?.models ?? []
  const liveVoices =
    liveModels.find((m) => m.id === value.tts_model)?.voices ??
    // No model chosen yet: the agent's default is the first non-retiring one,
    // so show that model's voices rather than every voice the provider has.
    liveModels.find((m) => !m.retiring)?.voices ??
    []

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
      <div className={PAGE}>
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    )
  }

  if (config.isError) {
    return (
      <div className={PAGE}>
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
    <div className={cn(PAGE, "pb-24")}>
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

      {/* Above the tabs on purpose. A transfer marker the prompt never writes
          is a fault on two different tabs at once, and a notice tucked inside
          either one is a notice nobody opening the other will see. */}
      {value.warnings?.map((w) => (
        <Card key={w} className="border-warning/30 bg-warning/5 p-4">
          <div className="flex items-start gap-2 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <p className="leading-relaxed">{w}</p>
          </div>
        </Card>
      ))}

      {/* Nine tabs fit at the standard width; the scroll is the fallback for a
          narrow window, and `scrollbar-thin` keeps it from drawing a grey bar
          across the page when it is not needed. */}
      <div className="scrollbar-thin flex gap-1 overflow-x-auto border-b border-border">
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
            <Note>
              The dialler sends who is calling and what they own. Use{' '}
              <code>{'{{cus_name}}'}</code>, <code>{'{{modalname}}'}</code> or{' '}
              <code>{'{{calltype}}'}</code> instead of typing one caller's details
              in — a hardcoded name is correct for exactly one person.
              <br />
              <strong>Give each one a fallback</strong> after a pipe:{' '}
              <code>{'{{cus_name|आप}}'}</code>. The dialler does not always send
              every field, and without a fallback the sentence is spoken with a
              gap in it — “क्या मेरी बात जी से हो रही है?”
            </Note>

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
              expandable
              hint={
                <>
                  <span className="font-medium text-foreground/70 tnum">
                    {tokens.data
                      ? `${tokens.data.tokens.toLocaleString()} tokens`
                      : `${instructions.length.toLocaleString()} characters`}
                  </span>
                  {/* Said plainly, because this number is smaller than the one
                      on the bill and anyone comparing the two deserves to know
                      why rather than to wonder. */}
                  {' — the knowledge base and the rules are added on top, so the prompt that is '}
                  {'actually sent is larger.'}
                  <br />
                  The system prompt. Do not repeat the grounding or transfer rules here; they are
                  appended for you. Keep it stable: the first ~1024 tokens are what OpenAI caches,
                  and editing them throws that cache away.
                </>
              }
            />

            <Toggle
              label="Tell the agent the date and time"
              checked={value.prompt_datetime}
              onChange={(v) => set('prompt_datetime', v)}
              hint="Adds one line to the very end of the prompt at the start of each call. Without it the agent cannot answer “what's the time?”, and — the part that costs you — it cannot turn “कल सुबह 10 बजे” into an actual date for the postback."
            />

            {value.prompt_datetime && (
              <TextField
                label="Timezone"
                value={value.prompt_timezone}
                onChange={(v) => set('prompt_timezone', v.trim())}
                placeholder="Asia/Kolkata"
                hint="An IANA name. Read once when the call starts, not on every turn — a clock that changed mid-call would make every turn a fresh prompt and throw away the cache, so on a long call it can be a couple of minutes behind."
              />
            )}
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

            {[value.stt_provider, value.tts_provider].includes('soniox') &&
              SONIOX_UNSUPPORTED.includes(value.language) && (
                <Note tone="warn">
                  Soniox does not support {value.language}. Pick another provider
                  for this language, or the call will be synthesised in the wrong
                  one — the API does not reject it, it just speaks something else.
                </Note>
              )}

            <div className="grid gap-5 sm:grid-cols-2">
              <SelectField
                label="Speech-to-text provider"
                value={value.stt_provider}
                onChange={(v) => {
                  // Clear the model: it names one belonging to the old provider,
                  // and a stale value fails on the first utterance, not on save.
                  set('stt_provider', v)
                  set('stt_model', null)
                  if (value.stt_fallback_provider === v) set('stt_fallback_provider', null)
                }}
                options={PROVIDERS}
                hint="Whose speech recognition this campaign runs on. Billed to that provider's key."
              />
              <SelectField
                label="…falls back to"
                value={value.stt_fallback_provider ?? ''}
                onChange={(v) => set('stt_fallback_provider', v || null)}
                options={[
                  { value: '', label: 'No fallback' },
                  ...PROVIDERS.filter((p) => p.value !== value.stt_provider),
                ]}
                hint="Used only if the primary fails mid-call. Needs its own key, or it is skipped."
              />
            </div>

            {/* Soniox is the only provider exposing these. Sarvam's endpointing
                is server-side and not tunable from here; OpenAI's STT is not
                streaming, so it has no endpoint to detect. */}
            {value.stt_provider === 'soniox' && (
              <div className="grid gap-5 sm:grid-cols-2">
                <NumberField
                  label="Endpoint latency level"
                  value={value.stt_endpoint_level ?? -1}
                  onChange={(v) => set('stt_endpoint_level', v < 0 ? null : v)}
                  min={-1}
                  max={3}
                  hint="0–3. Higher returns a result sooner but breaks long speech into more pieces — on Hinglish sales calls, being cut off mid-sentence is worse than waiting. −1 leaves Soniox's own default (0). Measured at the default: 1067ms against Sarvam's 238ms."
                />
                <NumberField
                  label="Endpoint sensitivity"
                  value={value.stt_endpoint_sensitivity ?? 0}
                  onChange={(v) => set('stt_endpoint_sensitivity', v)}
                  min={-1}
                  max={1}
                  step={0.1}
                  hint="−1.0 to 1.0. Positive ends turns sooner, negative waits longer for people who pause. Set the level first, then tune this — and never pair a high level with a negative value, they cancel out."
                />
              </div>
            )}

            <ComboField
              label="Speech-to-text model"
              value={value.stt_model ?? ''}
              onChange={(v) => set('stt_model', v.trim() || null)}
              options={STT_MODELS[value.stt_provider] ?? []}
              placeholder="model name"
              allowEmpty
              emptyLabel="Provider default"
              hint="Applies to the primary only — a fallback always uses its own provider's default."
            />

            <div className="grid gap-5 sm:grid-cols-2">
              <SelectField
                label="Text-to-speech provider"
                value={value.tts_provider}
                onChange={(v) => {
                  // The voice list is per provider too: Sarvam's "shubh" means
                  // nothing to Soniox, and TTS.__init__ raises rather than the
                  // call merely sounding wrong.
                  set('tts_provider', v)
                  set('tts_model', null)
                  set('tts_voice', null)
                  if (value.tts_fallback_provider === v) set('tts_fallback_provider', null)
                }}
                options={PROVIDERS}
                hint="Whose voice this campaign speaks with."
              />
              <SelectField
                label="…falls back to"
                value={value.tts_fallback_provider ?? ''}
                onChange={(v) => set('tts_fallback_provider', v || null)}
                options={[
                  { value: '', label: 'No fallback' },
                  ...PROVIDERS.filter((p) => p.value !== value.tts_provider),
                ]}
                hint="This chain carried every call the day Sarvam ran out of credits."
              />
            </div>

            <div className="grid gap-5 sm:grid-cols-2">
              <ComboField
                label="Text-to-speech model"
                value={value.tts_model ?? ''}
                onChange={(v) => {
                  set('tts_model', v.trim() || null)
                  // The voice list is per MODEL, not per provider. Keeping a
                  // voice across a model change is how you select one that does
                  // not exist there — tts-rt-v2 has no Meera.
                  set('tts_voice', null)
                }}
                options={
                  liveModels.length
                    ? liveModels.map((m) => ({
                        value: m.id,
                        label: m.retiring ? `${m.id} — ${m.retiring}` : (m.name ?? m.id),
                      }))
                    : TTS_MODELS[value.tts_provider] ?? []
                }
                placeholder="model name"
                allowEmpty
                emptyLabel="Provider default"
                hint="Primary only."
              />
              <ComboField
                label="Voice"
                value={value.tts_voice ?? ''}
                onChange={(v) => set('tts_voice', v.trim() || null)}
                options={
                  liveVoices.length
                    ? liveVoices.map((v) => ({
                        value: v.id,
                        label: [v.id, v.gender, v.description?.split(/[.,]/)[0]]
                          .filter(Boolean)
                          .join(' · '),
                      }))
                    : VOICES[value.tts_provider] ?? []
                }
                placeholder="voice name"
                allowEmpty
                emptyLabel="Provider default"
                hint={
                  liveVoices.length
                    ? `${liveVoices.length} voices, read from ${value.tts_provider} for this model.`
                    : 'A voice the chosen model does not have fails before the call is answered, not on save.'
                }
              />
            </div>

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

            <TextField
              label="Say this while searching"
              value={value.kb_filler_message ?? ''}
              onChange={(v) => set('kb_filler_message', v.trim() || null)}
              placeholder="एक मिनट, देखती हूँ…"
              hint="Spoken only if the search is still running after ~600ms, and cut off the moment it answers. A search costs 810–1860ms and now runs on nearly every question, so this is a second of silence each time — and silence is what makes a caller say “hello?”. Keep it short: it has to finish before the answer arrives. Leave empty for silence."
            />

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

      {tab === 'tools' && (
        <Card>
          <CardHeader>
            <CardTitle>Tools</CardTitle>
          </CardHeader>
          <CardBody className="space-y-5">
            <Note>
              A tool lets the agent call your API in the middle of a conversation
              — look up a service, check a warranty, book a slot. The agent
              decides when to use one from the description you write, the same
              way it decides to search the knowledge base or hand over to a
              human.
            </Note>
            <Note tone="warn">
              A tool call happens <em>while the caller is listening</em>. Anything
              past a second or so is heard as silence, which is why the timeout
              is capped — and why the test button reports how long it took, not
              just whether it worked.
            </Note>
            <CampaignTools campaignId={campaignId} />
          </CardBody>
        </Card>
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
            {/* Only the providers this campaign is configured to use. Warning
                about an unset Soniox key on a campaign that runs on Sarvam
                would be noise, and noise in a blocking warning is how real
                warnings stop being read. */}
            <ProviderKeys
              scope="campaign"
              id={campaignId}
              inUse={[...new Set([value.stt_provider, value.tts_provider, 'openai'])]}
            />
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

              <SelectField
                label="Dialler"
                value={value.transfer_dialler_id ? String(value.transfer_dialler_id) : ''}
                onChange={(v) => set('transfer_dialler_id', v ? Number(v) : null)}
                options={[
                  { value: '', label: 'Use the SIP target below' },
                  ...(diallers.data ?? [])
                    // An inactive dialler is hidden unless this campaign is
                    // already on it - otherwise the field would look unset and
                    // the next save would silently move the campaign off it.
                    .filter((d) => d.active || d.id === value.transfer_dialler_id)
                    .map((d) => ({
                      value: String(d.id),
                      label: d.active ? d.name : `${d.name} (inactive)`,
                    })),
                ]}
                hint="Which dialler takes this campaign's transfers. Diallers are set up on the Diallers page."
              />

              {value.transfer_dialler_id ? (
                <TextField
                  label="Extension"
                  value={value.transfer_extension ?? ''}
                  onChange={(v) => set('transfer_extension', v.trim() || null)}
                  placeholder="5000"
                  className="font-mono"
                  hint="The extension to ring on that dialler. Two campaigns may use the same number on different diallers — which dialler is what this campaign's setting decides."
                />
              ) : (
                <TextField
                  label="Transfer target"
                  value={value.transfer_to}
                  onChange={(v) => set('transfer_to', v)}
                  placeholder="sip:800@10.130.9.243"
                  className="font-mono"
                  hint="A SIP URI. A wrong target only surfaces when a real caller asks for a human."
                />
              )}

              {/* Chosen but not filled in is the one combination that fails at
                  the moment a caller is waiting: the route exists, the lookup
                  returns nothing, and they hear the failure message. */}
              {value.transfer_dialler_id && !value.transfer_extension?.trim() ? (
                <Note tone="warn">
                  No extension set. Transfers on this campaign will not connect
                  until there is one.
                </Note>
              ) : null}

              <TextField
                label="Message before transferring"
                value={value.transfer_message ?? ''}
                onChange={(v) => set('transfer_message', v || null)}
                placeholder="Main aapko ek executive se connect kar raha hoon."
                hint="Spoken and finished before the REFER is sent — otherwise the caller hears it cut off."
              />

              <Toggle
                label="Ask the caller before transferring"
                checked={value.transfer_confirm}
                onChange={(v) => set('transfer_confirm', v)}
                hint="The agent asks first and waits for an answer, so a caller who says “no, wait” stops it. Enforced in the agent, not left to the model to remember."
              />

              {value.transfer_confirm && (
                <TextField
                  label="Confirmation question"
                  value={value.transfer_confirm_message ?? ''}
                  onChange={(v) => set('transfer_confirm_message', v || null)}
                  placeholder="Main aapko ek sathi se jod rahi hoon. Theek hai?"
                  hint="Asked, then the caller's reply decides. Only after they agree does the transfer happen."
                />
              )}

              {/* Between the transfer settings and the marker: it is part of
                  whether a handoff happens at all, not part of how it is
                  triggered. */}
              <div className="border-t border-border/70 pt-5">
                <TransferHours
                  value={value}
                  campaignId={campaignId}
                  onChange={set}
                  disabled={!canEdit}
                />
              </div>

              <TextField
                label="Transfer marker"
                value={value.transfer_marker ?? ''}
                onChange={(v) => set('transfer_marker', v.trim() || null)}
                placeholder="[TRANSFER]"
                className="font-mono"
                hint="Optional. Works like the end-of-call marker: the model writes it, it is never spoken, and the handoff happens once the sentence finishes. Empty means the tool is the only route. Confirmation applies either way."
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <MicOff className="h-4 w-4 text-muted-foreground" />
                When the caller says nothing
              </CardTitle>
            </CardHeader>
            <CardBody className="space-y-5">
              <NumberField
                label="Wait this long before speaking again"
                value={value.silence_timeout_sec ?? 0}
                onChange={(v) => set('silence_timeout_sec', v > 0 ? v : null)}
                min={0}
                max={60}
                suffix="sec"
                hint="Counted from the moment the AGENT stops speaking, not from the caller's last word. 0 turns the whole thing off. Under 3s fires while someone is drawing breath."
              />

              <SilencePrompts
                lines={value.silence_prompts ?? []}
                onChange={(lines) => set('silence_prompts', lines.length ? lines : null)}
              />

              <TextField
                label="End-of-call marker"
                value={value.end_call_marker ?? ''}
                // NOT NULL in the schema: clearing the box means "back to the
                // default", not "no marker" — an empty marker would match every
                // chunk and silence the agent entirely.
                onChange={(v) => set('end_call_marker', v.trim() || '[EOC]')}
                placeholder="[EOC]"
                className="font-mono"
                hint="When the model writes this, it is removed before speaking and the call ends once the sentence finishes. Tell the model to use it in the instructions, or it never will."
              />
            </CardBody>
          </Card>
        </div>
      )}

      {tab === 'postback' && (
        <CampaignPostback campaignId={campaignId} value={value} set={set} />
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
