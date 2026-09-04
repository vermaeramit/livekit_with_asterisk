export type Role = 'superadmin' | 'tenant_admin' | 'agent' | 'viewer'

/**
 * Everything a role may be allowed to do.
 *
 * Mirrors admin/backend/app/permissions.py, which is the file that decides.
 * The backend also serves the list at /permissions with labels, so the roles
 * page never offers one this build does not enforce.
 */
export type Permission =
  | 'calls.read'
  | 'calls.recording'
  | 'analytics.read'
  | 'usage.read'
  | 'cost.read'
  | 'alerts.read'
  | 'live.read'
  | 'gaps.read'
  | 'campaign.write'
  | 'provider_keys.write'
  | 'users.manage'
  | 'tenants.manage'
  | 'rates.manage'
  | 'system.manage'

export interface PermissionInfo {
  key: Permission
  group: string
  label: string
  description: string
}

export interface RoleDef {
  id: number
  key: string
  name: string
  description: string | null
  // Sees every client. Held apart from permissions on purpose.
  all_tenants: boolean
  // Cannot be edited or deleted.
  builtin: boolean
  permissions: Permission[]
  user_count: number
  updated_at: string
}

export interface User {
  id: number
  email: string
  name: string | null
  role: Role
  // Only present on the signed-in user.
  permissions?: Permission[]
  all_tenants?: boolean
  tenant_id: number | null
  tenant_name: string | null
  last_login_at: string | null
  must_change_password: boolean
  active: boolean
  created_at: string | null
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface Campaign {
  id: number
  tenant_id: number
  tenant_name: string | null
  slug: string
  name: string
  description: string | null
  enabled: boolean
  created_at: string | null
  call_count: number
  config_name: string | null
}

export interface Tenant {
  id: number
  slug: string
  name: string
  status: 'active' | 'suspended'
  created_at: string
  campaign_count: number
  user_count: number
  call_count: number
}

export interface CallListItem {
  id: number
  started_at: string
  ended_at: string | null
  duration_ms: number | null
  caller: string | null
  callee: string | null
  language: string | null
  end_reason: string | null
  limit_hit: string | null
  transferred_to: string | null
  // null = not refused. Otherwise why: 'closed' or 'holiday'. The caller
  // asked for a person and there was nobody to hand them to.
  transfer_refused: string | null
  // Characters of transcript per language the STT identified. This is what was
  // SPOKEN; `language` above is what the campaign is configured for.
  detected_languages: Record<string, number> | null
  turn_count: number | null
  campaign_id: number | null
  campaign_name: string | null
  tenant_id: number | null
}

export interface CallListResponse {
  items: CallListItem[]
  total: number
  page: number
  page_size: number
}

export interface Turn {
  seq: number
  role: string
  text: string | null
  ts: string
  eou_ms: number | null
  stt_ms: number | null
  llm_ttft_ms: number | null
  tts_ttfb_ms: number | null
  total_ms: number | null
  interrupted: boolean
  kb_chunk_ids: number[] | null
  kb_scores: number[] | null
}

export interface CallUsage {
  llm_prompt_tokens: number | null
  llm_prompt_cached_tokens: number | null
  llm_completion_tokens: number | null
  tts_characters: number | null
  tts_audio_seconds: number | null
  stt_audio_seconds: number | null
}

export interface CallDetail extends CallListItem {
  room_name: string | null
  sip_call_id: string | null
  outcome: string | null
  transfer_reason: string | null
  // What served the call. A comma means a fallback fired: "sarvam,openai".
  stt_provider_used: string | null
  llm_provider_used: string | null
  tts_provider_used: string | null
  recording_path: string | null
  recording_available: boolean
  recording_bytes: number | null
  // What the dialler sent with the call: name, product, their lead/SR ids.
  // Free-form on purpose — the key set is theirs to change.
  dialer_context: Record<string, string> | null
  // Absent when the viewer may not see usage.
  usage?: CallUsage | null
  cost?: CallCost | null
  turns: Turn[]
  tools: ToolInvocation[]
}

export interface ToolInvocation {
  id: number
  name: string
  // What the MODEL chose to send. A tool that "did not work" is usually a tool
  // called with the wrong argument, and the transcript never shows that.
  arguments: Record<string, unknown> | null
  // The resolved URL. Arguments alone were not enough — a placeholder written
  // with single braces leaves them looking correct and sends `{pin}` verbatim.
  url: string | null
  // The resolved body actually sent. Arguments can be faultless while the
  // template that carries them is malformed — two 400s on call 365 were that.
  request: string | null
  // The endpoint's own words. Always present on a 4xx/5xx.
  response: string | null
  status_code: number | null
  duration_ms: number | null
  error: string | null
  created_at: string
}

/** An invocation seen from the tool's side: which call it belonged to. */
export interface ToolActivityItem extends ToolInvocation {
  call_id: number | null
}

export interface ToolActivityResponse {
  items: ToolActivityItem[]
  total: number
  page: number
  page_size: number
}

/** Read from the provider with the campaign's key — never a list held here. */
export interface TtsCatalog {
  provider: string
  models: {
    id: string
    name: string | null
    // Set when the provider is retiring it, with the date.
    retiring: string | null
    voices: { id: string; gender: string | null; description: string | null }[]
  }[]
}

export interface KbChunk {
  id: number
  seq: number
  page: number | null
  heading: string | null
  content: string
  filename: string
  title: string | null
}

export interface AgentConfig {
  campaign_id: number
  name: string
  language: string
  greeting: string | null
  instructions: string

  stt_model: string | null
  llm_model: string
  llm_temperature: number
  tts_model: string | null
  tts_voice: string | null
  allow_interrupt: boolean

  kb_enabled: boolean
  kb_top_k: number
  kb_min_score: number
  kb_inline_max_tokens: number
  kb_summary: string | null
  // Spoken while a search runs. null = silence.
  // Off = stay silent while searching, keeping the wording below for later.
  kb_filler_enabled: boolean
  kb_filler_message: string | null
  // Words the speech recogniser would otherwise get wrong. Soniox only.
  stt_context_terms: string[]

  // Wrong but not invalid — a save is never blocked on these. Computed on read
  // too, so a mismatch already in the database shows on opening the page.
  warnings: string[]

  max_turns: number
  max_duration_sec: number
  max_prompt_tokens: number
  limit_message: string | null

  transfer_enabled: boolean
  transfer_to: string
  transfer_message: string | null
  transfer_confirm: boolean
  transfer_confirm_message: string | null

  // null = no silence handling. The array's LENGTH is the attempt count — the
  // last line is spoken and then the call ends.
  silence_timeout_sec: number | null
  silence_prompts: string[] | null
  end_call_marker: string
  // null = no marker-driven handoff; the tool still works.
  transfer_marker: string | null
  // When a human is there to take the handoff. Off = transfer whenever, which
  // is what every campaign did before this existed.
  transfer_hours_enabled: boolean
  transfer_hours: WeekHours | null
  transfer_holidays: Holiday[]
  transfer_closed_message: string | null
  // null = use transfer_to as written. Set = the target is built from the
  // campaign and Asterisk looks the dialler up when the transfer happens.
  transfer_dialler_id: number | null
  transfer_extension: string | null

  // Soniox endpointing. null = the provider's defaults.
  stt_endpoint_level: number | null
  stt_endpoint_sensitivity: number | null

  // One line at the very end of the prompt, stamped once per call. Kept at the
  // end on purpose: everything above it stays a cacheable prefix.
  prompt_datetime: boolean
  prompt_timezone: string

  // Where the call's result is sent afterwards. The auth value never comes
  // back — only the last four characters.
  postback_enabled: boolean
  postback_url: string | null
  postback_auth_header: string | null
  postback_auth_value_hint: string | null
  postback_fields: { key: string; type: string; description: string }[] | null
  postback_include_transcript: boolean
  // false = just the extracted fields, flat. true = the full envelope.
  postback_full_payload: boolean
  postback_max_attempts: number
  postback_retry_after_sec: number
  // Write-only: sent on save, never returned. Present on the type so the
  // editor can hold it in the same draft as everything else.
  postback_auth_value?: string

  recording_disclosure: string
  stt_provider: string
  tts_provider: string
  // null = no fallback for that layer
  stt_fallback_provider: string | null
  tts_fallback_provider: string | null

  updated_at: string
}

export interface KbDocument {
  id: number
  campaign_id: number | null
  config_name: string
  filename: string
  title: string | null
  page_count: number | null
  chunk_count: number | null
  token_count: number
  language: string | null
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface ChatWidget {
  id: number
  campaign_id: number
  // Public: it sits in the page source of a site anyone can view. The origin
  // list and the cap are what protect the campaign, not this.
  public_key: string
  allowed_origins: string[]
  // Ignores the list above. The cap is then the only limit.
  allow_any_origin: boolean
  enabled: boolean
  daily_token_cap: number
  welcome: string | null
  title: string | null
  tokens_today: number
  conversations_today: number
  created_at: string
  updated_at: string
}

export interface ChatStep {
  kind: 'kb' | 'tool'
  name: string
  args: Record<string, unknown>
  result: string
  ms: number
  // KB only: what was retrieved and how well it matched. The score is the
  // number that explains a wrong answer.
  hits: { document: string; score: number; heading: string | null; matched: string }[]
}

export interface ChatTurn {
  text: string
  steps: ChatStep[]
  // Time to the first word, which is the number that matters on a call.
  first_token_ms: number
  prompt_tokens: number
  completion_tokens: number
  cached_tokens: number
  ms: number
}

export interface KbSource {
  id: number
  campaign_id: number | null
  url: string
  title: string | null
  last_fetched_at: string | null
  last_status: string | null
  last_error: string | null
  page_count: number
  // Pages that held no readable text, by name. A count would not tell anyone
  // WHICH part of their knowledge base the agent cannot see.
  skipped: { name: string; why: string }[]
  document_count: number
  enabled_count: number
  created_at: string
  updated_at: string
}

export interface KbIngestResult {
  filename: string
  status: 'created' | 'updated' | 'unchanged' | 'empty'
  pages: number | null
  chunks: number | null
  tokens: number | null
  error: string | null
}

export interface KbChunk2 {
  id: number
  seq: number
  page: number | null
  heading: string | null
  content: string
  n_tokens: number | null
}

export interface Percentiles {
  p50: number | null
  p90: number | null
  p95: number | null
  worst: number | null
  turns: number
}

export interface LatencySplit {
  eou_ms: number | null
  llm_ttft_ms: number | null
  tts_ttfb_ms: number | null
}

/** What the calls in a window cost, blended. */
export interface AnalyticsCost {
  currency: string
  total: number
  per_call: number
  // Total cost over total minutes, not the average of per-call rates.
  per_minute_avg: number
  per_minute_max: number | null
  per_minute_max_call_id: number | null
  per_minute_max_floor_sec: number
  // If these disagree, the total is short by whatever the unpriced ones cost.
  priced_calls: number
  unpriced_calls: number
}

export interface AnalyticsSummary {
  calls: number
  transferred: number
  limit_hit: number
  errors: number
  total_duration_ms: number
  // Average handle time, over calls that have a duration.
  avg_duration_ms: number | null
  max_duration_ms: number | null
  longest_call_id: number | null
  cost: AnalyticsCost | null
  total_turns: number
  prompt_tokens: number | null
  cached_tokens: number | null
  completion_tokens: number | null
  tts_characters: number | null
  latency: Percentiles
  split: LatencySplit
  end_reasons: Record<string, number>
}

export interface TimeBucket {
  bucket: string
  calls: number
  transferred: number
  limit_hit: number
  prompt_tokens: number | null
  cached_tokens: number | null
  p50: number | null
  p95: number | null
  eou_ms: number | null
  llm_ttft_ms: number | null
  tts_ttfb_ms: number | null
}

export interface AlertRule {
  id: number
  tenant_id: number
  tenant_name: string | null
  campaign_id: number | null
  campaign_name: string | null
  kind: string
  threshold: number
  window_minutes: number
  min_calls: number
  severity: 'warning' | 'critical'
  enabled: boolean
  firing: boolean
  last_fired_at: string | null
  last_checked_at: string | null
}

export interface Alert {
  id: number
  tenant_id: number
  tenant_name: string | null
  campaign_id: number | null
  campaign_name: string | null
  kind: string
  severity: 'warning' | 'critical'
  message: string
  value: number | null
  threshold: number | null
  delivery: 'pending' | 'sent' | 'failed' | 'skipped'
  delivery_error: string | null
  created_at: string
  acknowledged_at: string | null
  acknowledged_by_email: string | null
}

export interface LiveCall {
  id: number
  started_at: string
  caller: string | null
  callee: string | null
  language: string | null
  campaign_id: number | null
  campaign_name: string | null
  tenant_id: number | null
  elapsed_sec: number
  max_duration_sec: number
  turn_count: number
  last_latency_ms: number | null
  last_text: string | null
  stale: boolean
}

export interface LiveSummary {
  calls: LiveCall[]
  active: number
  stale: number
  verified_capacity: number
}

export interface CampaignRoute {
  id: number
  campaign_id: number
  did: string
  description: string | null
  created_at: string
}

export interface AuditEntry {
  id: number
  entity: string
  entity_id: string | null
  action: string
  changes: Record<string, { from: unknown; to: unknown }> | null
  created_at: string
  user_email: string | null
}

export interface CallFilters {
  search?: string
  campaign_id?: number
  tenant_id?: number
  end_reason?: string
  transferred?: boolean
  date_from?: string
  date_to?: string
  min_duration_ms?: number
}

export interface ProviderKey {
  provider: string
  // Which key the next call would actually use. 'client' on a campaign row
  // means it is inheriting, not that it has one of its own.
  source: 'campaign' | 'client' | 'none'
  hint: string | null
  updated_at: string | null
}

export interface ProviderKeyWritten {
  provider: string
  hint: string
  message: string
  // Key is genuine, account is empty. Saved, but the client will not get a
  // call answered until they top up.
  no_credits: boolean
}

export interface CampaignTool {
  id: number
  name: string
  description: string
  parameters: Record<string, unknown>
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  url: string
  headers: Record<string, string> | null
  auth_header: string | null
  // Last four characters. The value itself never leaves the server.
  auth_value_hint: string | null
  body_template: string | null
  timeout_ms: number
  max_response_bytes: number
  response_path: string | null
  // Spoken only if the tool is still running after ~600ms.
  filler_message: string | null
  filler_enabled: boolean
  // Status code (or "timeout"/"default") -> what to tell the model.
  error_messages: Record<string, string> | null
  // Keep the response so extraction can read values never spoken aloud.
  keep_response: boolean
  enabled: boolean
  updated_at: string
}

export interface ToolTestResult {
  ok: boolean
  status_code: number | null
  duration_ms: number
  body: string | null
  error: string | null
  url: string
}

export interface Postback {
  id: number
  call_id: number
  // pending | sent | failed | skipped
  status: string
  attempts: number
  last_status_code: number | null
  last_error: string | null
  next_attempt_at: string | null
  created_at: string
  sent_at: string | null
  payload: Record<string, unknown>
}

export interface BackupFile {
  name: string
  bytes: number
  at: string
}

export interface BackupStatus {
  configured: boolean
  // The whole point of the page. A list of files leaves the reader to work out
  // whether that list is healthy; this says so outright.
  problem: string | null
  last_run: string | null
  last_result: string | null
  last_detail: string | null
  newest_at: string | null
  age_hours: number | null
  total_bytes: number
  disk_free_bytes: number | null
  disk_total_bytes: number | null
  files: BackupFile[]
  secrets_key_ack: SystemAck | null
}

export interface SystemAck {
  key: string
  acked_by: string | null
  acked_at: string | null
  // The key has been rotated since this was given, so the confirmation no
  // longer describes anything real.
  stale: boolean
}

/**
 * One QUESTION the bot could not answer, however many times it was asked.
 *
 * The rows behind this are one per occurrence, each tied to a call so it can be
 * listened to. They arrive grouped because the unit of work is the question:
 * twenty callers asking the same thing is one document to write.
 */
export interface KnowledgeGap {
  tenant_id: number
  tenant_name: string | null
  campaign_id: number | null
  campaign_name: string | null
  // kb_miss — nothing came back at all
  // kb_weak — something did, but only just
  // tool_failed — a lookup the caller was waiting on did not answer
  kind: 'kb_miss' | 'kb_weak' | 'tool_failed' | string
  query: string
  query_key: string
  detail: string | null
  occurrences: number
  open_occurrences: number
  first_seen: string
  last_seen: string
  worst_score: number | null
  call_ids: number[]
  acknowledged_at: string | null
  acknowledged_by_email: string | null
  note: string | null
}

/** What a call cost, at the rates set when it was looked at. */
export interface CallCost {
  usd: { llm: number; tts: number; stt: number }
  usd_total: number
  // Per minute of call. null when there is no duration to divide by.
  usd_per_minute?: number | null
  inr?: { llm: number; tts: number; stt: number } | null
  inr_total?: number | null
  inr_per_minute?: number | null
  usd_to_inr?: number | null
  // Named, so the console can say which row to go and add.
  missing_rates: string[]
  // false = no leg had a rate at all. Must not be shown as a zero: a confident
  // 0.00 reads as free.
  priced: boolean
  // Everything that makes the figure less than exact, in words.
  caveats: string[]
}

// ["09:30", "18:30"], or null for a day the team does not work. Times are in
// the campaign's prompt_timezone, never the server's.
export type DayWindow = [string, string] | null

export interface WeekHours {
  mon?: DayWindow
  tue?: DayWindow
  wed?: DayWindow
  thu?: DayWindow
  fri?: DayWindow
  sat?: DayWindow
  sun?: DayWindow
}

export interface Holiday {
  date: string
  label: string
}

export interface Dialler {
  id: number
  name: string
  // The section name in iax.conf. Asterisk dials IAX2/<peer>/<extension>.
  peer: string
  description: string | null
  active: boolean
  // How many campaigns transfer here - shown so nobody deletes a live one.
  campaign_count: number
  updated_at: string
  // Empty = the peer is a hand-written section in iax.conf and this row only
  // names it. Filled in = Asterisk reads it from the database.
  host: string | null
  port: number | null
  username: string | null
  // Whether a password is stored. Never the password itself.
  has_secret: boolean
}

export interface ProviderRate {
  id: number
  provider: string
  // null = any model from this provider. A row naming the model wins.
  model: string | null
  kind: 'llm_input' | 'llm_cached' | 'llm_output' | 'tts_characters' | 'tts_seconds' | 'stt_seconds'
  unit: 'per_million' | 'per_hour' | 'per_minute' | 'per_unit'
  price: string
  // What the provider bills in. Sarvam charges rupees and always will, so its
  // price is held in rupees rather than converted on the way in.
  currency: 'USD' | 'INR'
  note: string | null
  updated_at: string
  updated_by_email: string | null
}
