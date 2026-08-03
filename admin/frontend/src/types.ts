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
  recording_path: string | null
  usage: CallUsage
  turns: Turn[]
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
