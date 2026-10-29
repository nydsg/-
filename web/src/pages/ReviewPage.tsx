import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import type {
  SegmentAuditResult,
  SegmentItem,
  SegmentPlan,
  VideoCard,
} from '../lib/types'
import { streamSSE } from '../lib/stream'
import { useAuth } from '../store/auth'
import { Badge, EmptyState, SectionTitle, Skeleton } from '../components/ui'
import { SegmentTimeline } from '../components/SegmentTimeline'
import { useState } from 'react'

interface QueueItem {
  id: number
  uploader: string
  url: string
  tags: string
  status: string
  reason: string
  created_at: string
}
interface AiRun {
  id: number
  filename: string
  verdict: string
  score: number
  confidence: number
  created_at: string
}

export function ReviewPage() {
  const { user } = useAuth()
  const qc = useQueryClient()
  const [busy, setBusy] = useState<number | null>(null)
  const [reason, setReason] = useState('')
  // 切片分析：一次只分析一条，避免同时打爆模型额度
  const [segFor, setSegFor] = useState<number | null>(null)
  const [segBusy, setSegBusy] = useState(false)
  const [segPlan, setSegPlan] = useState<SegmentPlan | null>(null)
  const [segments, setSegments] = useState<SegmentItem[]>([])
  const [segResult, setSegResult] = useState<SegmentAuditResult | null>(null)

  const queue = useQuery({
    queryKey: ['review-queue'],
    queryFn: () =>
      api.get<{ items: QueueItem[]; ai_runs: AiRun[] }>('/review/queue'),
    retry: 1,
    enabled: !!user?.is_auditor,
  })

  const mine = useQuery({
    queryKey: ['my-videos'],
    queryFn: () => api.get<{ items: VideoCard[] }>('/videos/mine'),
    retry: 1,
    enabled: !!user,
  })

  if (!user?.is_auditor) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16">
        <EmptyState
          title="审核台仅审核员可访问"
          hint="注册满 30 天后可在个人主页申请成为审核员，经管理员审批后开通。"
        />
        <div className="mt-4 text-center">
          <button
            className="btn-soft"
            onClick={async () => {
              try {
                const res = await api.post<{ message: string }>('/auth/auditor/apply')
                alert(res.message ?? '申请已提交')
              } catch (err) {
                alert(err instanceof Error ? err.message : '申请失败')
              }
            }}
          >
            申请成为审核员
          </button>
        </div>
      </div>
    )
  }

  const act = async (id: number, action: 'approve' | 'reject') => {
    setBusy(id)
    try {
      await api.post(`/review/${id}/${action}`, action === 'reject' ? { reason } : {})
      setReason('')
      if (segFor === id) setSegFor(null)
      void qc.invalidateQueries({ queryKey: ['review-queue'] })
    } catch (err) {
      alert(err instanceof Error ? err.message : '操作失败')
    } finally {
      setBusy(null)
    }
  }

  /** 对某条待审投稿跑切片审核：逐段判定，定位到秒 */
  const analyzeSegments = async (pendingId: number) => {
    if (segBusy) return
    setSegFor(pendingId)
    setSegBusy(true)
    setSegPlan(null)
    setSegments([])
    setSegResult(null)
    await streamSSE(
      '/api/ai/audit/segment/stream',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pending_id: pendingId }),
      },
      {
        onEvent: (ev: any) => {
          if (ev.type === 'plan') {
            setSegPlan(ev.plan as SegmentPlan)
          } else if (ev.type === 'segment') {
            setSegments((prev) =>
              [...prev, ev.segment as SegmentItem].sort((a, b) => a.index - b.index),
            )
          } else if (ev.type === 'result') {
            setSegResult(ev as SegmentAuditResult)
          } else if (ev.type === 'error') {
            alert(ev.message ?? '切片审核失败')
          }
        },
        onError: (err) => alert(err.message),
        onDone: () => setSegBusy(false),
      },
    )
    setSegBusy(false)
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <SectionTitle>人工审核台</SectionTitle>
      <p className="mb-5 text-sm text-ink-mute">
        AI 给出「转人工」的投稿会进入这里；你的结论会回流成判例，让后续检索更准。
      </p>

      {queue.isLoading ? (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : queue.data?.items?.length ? (
        <div className="space-y-3">
          {queue.data.items.map((item) => (
            <div key={item.id} className="card p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="warn">待审核</Badge>
                <span className="text-sm text-ink">投稿人：{item.uploader}</span>
                <span className="text-xs text-ink-mute">{item.created_at.slice(0, 16)}</span>
                {item.tags && <span className="chip-brand">#{item.tags}</span>}
              </div>
              <div className="mt-3 overflow-hidden rounded-lg bg-black">
                <video src={item.url} controls className="aspect-video w-full" />
              </div>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <input
                  className="input h-9 flex-1 text-xs"
                  placeholder="拒绝理由（会作为判例入库）"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
                <button
                  disabled={busy === item.id || (segBusy && segFor === item.id)}
                  onClick={() =>
                    segFor === item.id ? setSegFor(null) : void analyzeSegments(item.id)
                  }
                  className="btn-soft h-9 text-xs"
                >
                  {segBusy && segFor === item.id
                    ? '分析中…'
                    : segFor === item.id
                      ? '收起切片分析'
                      : '🔍 切片分析'}
                </button>
                <button
                  disabled={busy === item.id}
                  onClick={() => void act(item.id, 'approve')}
                  className="btn-primary h-9 text-xs"
                >
                  通过并发布
                </button>
                <button
                  disabled={busy === item.id}
                  onClick={() => void act(item.id, 'reject')}
                  className="btn-ghost h-9 text-xs text-rose-600"
                >
                  拒绝
                </button>
              </div>

              {/* 切片分析结果：定位到秒，直接决定该不该放行 */}
              {segFor === item.id && (
                <div className="mt-3">
                  <SegmentTimeline
                    plan={segPlan}
                    segments={segments}
                    result={segResult}
                    running={segBusy}
                    compact
                    title="切片分析（定位问题片段）"
                  />
                </div>
              )}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="队列已清空" hint="没有待人工审核的投稿。" />
      )}

      {!!queue.data?.ai_runs?.length && (
        <section className="mt-8">
          <SectionTitle>最近 AI 审核记录</SectionTitle>
          <div className="card divide-y divide-ink-line/70 overflow-hidden">
            {queue.data.ai_runs.map((r) => (
              <div key={r.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <Badge
                  tone={r.verdict === 'pass' ? 'success' : r.verdict === 'reject' ? 'danger' : 'warn'}
                >
                  {r.verdict}
                </Badge>
                <span className="min-w-0 flex-1 truncate text-ink-soft">{r.filename}</span>
                <span className="text-xs text-ink-mute">得分 {r.score}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {!!mine.data?.items?.length && (
        <section className="mt-8">
          <SectionTitle>我的作品</SectionTitle>
          <div className="card divide-y divide-ink-line/70 overflow-hidden">
            {mine.data.items.map((v) => (
              <div key={v.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span className="min-w-0 flex-1 truncate text-ink">{v.title}</span>
                <Badge
                  tone={
                    v.status === 'visible' ? 'success' : v.status === 'rejected' ? 'danger' : 'warn'
                  }
                >
                  {v.status === 'visible' ? '已发布' : v.status === 'rejected' ? '已拒绝' : '待审核'}
                </Badge>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}
