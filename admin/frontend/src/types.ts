export type Role = 'superadmin' | 'tenant_admin' | 'agent' | 'viewer'

export interface User {
  id: number
  email: string
  name: string | null
  role: Role
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
  usage: CallUsage
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

  // Soniox endpointing. null = the provider's defaults.
  stt_endpoint_level: number | null
  stt_endpoint_sensitivity: number | null

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

export interface AnalyticsSummary {
  calls: number
  transferred: number
  limit_hit: number
  errors: number
  total_duration_ms: number
  avg_duration_ms: number | null
  total_turns: number
  prompt_tokens: number
  cached_tokens: number
  completion_tokens: number
  tts_characters: number
  latency: Percentiles
  split: LatencySplit
  end_reasons: Record<string, number>
}

export interface TimeBucket {
  bucket: string
  calls: number
  transferred: number
  limit_hit: number
  prompt_tokens: number
  cached_tokens: number
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
