/** 与后端 API 一一对应的类型定义（严格模式下前端的第一道防线） */

export interface User {
  id: number
  username: string
  name: string
  school: string
  signature: string
  avatar: string
  bg: string
  is_auditor?: boolean
  total_likes?: number
  works?: number
  fans?: number
}

export interface VideoCard {
  id: number
  title: string
  desc?: string
  url: string
  cover: string
  views: number
  tags: string[]
  author: string
  author_id?: number
  author_avatar?: string
  created_at?: string
  duration?: string
  status?: string
}

export interface VideoDetail extends VideoCard {
  likes: number
  collects: number
  comment_count: number
  audit_result?: string
  violations?: string
}

export interface Comment {
  id: number
  content: string
  author: string
  user_id: number
  avatar: string
  created_at: string
  reply_to: string
  replies: Comment[]
}

/* ---------------- 切片审核 ---------------- */
export interface SegmentItem {
  index: number
  start: number
  end: number
  verdict: 'pass' | 'manual' | 'reject' | 'unknown'
  score: number
  confidence: number
  categories: string[]
  risks: { type: string; severity: string; frame?: number; detail: string }[]
  scene: string
  tags: string[]
  text: string
  kw_hits: Record<string, string[]>
  note: string
  frame_ts?: number
  motion?: number
  latency_ms: number
}

export interface SegmentPlan {
  duration: number
  count: number
  segment_seconds: number
  segments: { index: number; start: number; end: number; mid: number }[]
  meta: { fps?: number; frames?: number; width?: number; height?: number }
}

export interface SegmentAuditResult {
  mode: 'segment'
  verdict: 'pass' | 'manual' | 'reject'
  score: number
  confidence: number
  categories: string[]
  reasons: string[]
  evidence_strength: number
  narrative?: string
  segments: SegmentItem[]
  segment_seconds: number
  duration: number
  filename: string
  elapsed_ms: number
  ai_enabled: boolean
  model: string
}

export interface TagCount {
  name: string
  count: number
}

export interface RagHit {
  id: string
  title: string
  text: string
  cat: string
  score: number
  source: 'rulebook' | 'case' | string
}

export type Verdict = 'pass' | 'manual' | 'reject'

export interface AuditStep {
  id: string
  name: string
  status: 'pending' | 'running' | 'done' | 'skipped' | 'error'
  detail: string
  elapsed_ms: number
  data?: Record<string, unknown>
}

export interface AuditResult {
  verdict: Verdict
  score: number
  confidence: number
  categories: string[]
  reasons: string[]
  evidence: { type: string; ref: string; detail: string }[]
  suggestions: string[]
  steps: AuditStep[]
  elapsed_ms: number
  model: string
  ai_enabled: boolean
}

export interface AiStatus {
  ai_enabled: boolean
  model: string
  base_url: string
  rag_docs: number
  rag_sample: RagHit[]
  capabilities: string[]
}

export interface ApiEnvelope<T> {
  success: boolean
  error?: string
  message?: string
  data?: T
}

export interface Paged<T> {
  items: T[]
  total: number
  page: number
  size: number
  has_more?: boolean
}
