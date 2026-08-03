export type Role = 'superadmin' | 'tenant_admin' | 'agent' | 'viewer'

export interface User {
  id: number
  email: string
  name: string | null
  role: Role
  tenant_id: number | null
  tenant_name: string | null
  last_login_at: string | null
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
  slug: string
  name: string
  enabled: boolean
  tenant_name: string
}

export interface Tenant {
  id: number
  slug: string
  name: string
  status: 'active' | 'suspended'
  created_at: string
  campaign_count: number
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
