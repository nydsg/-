import { useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../lib/api'
import type {
  AuditResult,
  AuditStep,
  SegmentAuditResult,
  SegmentItem,
  SegmentPlan,
} from '../lib/types'
import { Badge, SectionTitle, Spinner, ThinkingDots } from '../components/ui'
import { SegmentTimeline } from '../components/SegmentTimeline'

type Phase = 'idle' | 'uploading' | 'auditing' | 'done' | 'error'
type AuditMode = 'whole' | 'segment'

export function SubmitPage() {
  const navigate = useNavigate()
  // 支持从 AI 工坊「带着这些去投稿」带参跳转，预填标题与标签
  const [searchParams] = useSearchParams()
  const fileRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState(searchParams.get('title') ?? '')
  const [tags, setTags] = useState(searchParams.get('tags') ?? '')
  const [desc, setDesc] = useState(searchParams.get('intro') ?? '')
  const [phase, setPhase] = useState<Phase>('idle')
  const [progress, setProgress] = useState(0)
  const [steps, setSteps] = useState<AuditStep[]>([])
  const [result, setResult] = useState<AuditResult | null>(null)
  const [error, setError] = useState('')
  const [aiTags, setAiTags] = useState<string[]>([])
  const [tagLoading, setTagLoading] = useState(false)
  // 切片审核
  const [mode, setMode] = useState<AuditMode>('segment')
  const [segPlan, setSegPlan] = useState<SegmentPlan | null>(null)
  const [segments, setSegments] = useState<SegmentItem[]>([])
  const [segResult, setSegResult] = useState<SegmentAuditResult | null>(null)

  const onPick = (f: File | null) => {
    setFile(f)
    // 已有预填标题（来自 AI 文案）时不要被文件名覆盖
    if (f && !title.trim()) setTitle(f.name.replace(/\.[^.]+$/, ''))
  }

  const genTags = async () => {
    if (!title.trim()) return
    setTagLoading(true)
    try {
      const res = await api.post<{ tags: string[] }>('/ai/tags', {
        title,
        desc: tags,
      })
      setAiTags(res.tags ?? [])
    } catch {
      /* 忽略 */
    } finally {
      setTagLoading(false)
    }
  }

  const startAudit = (videoId?: number) => {
    setPhase('auditing')
    setSteps([])
    setResult(null)
    const controller = new AbortController()
    abortRef.current = controller

    const handlers = {
      onEvent: (ev: any) => {
        if (ev.type === 'step') {
          setSteps((prev) => {
            const next = prev.filter((s) => s.id !== ev.step.id)
            return [...next, ev.step].sort((a, b) => order(a.id) - order(b.id))
          })
        } else if (ev.type === 'result') {
          setResult(ev as AuditResult)
          setPhase('done')
        } else if (ev.type === 'error') {
          setError(ev.message ?? 'AI 审核失败')
          setPhase('error')
        }
      },
      onError: (err: Error) => {
        setError(err.message)
        setPhase('error')
      },
    }

    if (videoId) {
      void fetch('/api/ai/audit/stream', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ video_id: videoId }),
        signal: controller.signal,
      }).then(async (res) => {
        if (!res.ok || !res.body) {
          const j = await res.json().catch(() => ({}))
          handlers.onError(new Error(j.error ?? '审核流启动失败'))
          return
        }
        await consume(res, handlers)
      })
    }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!file) {
      setError('请选择视频文件')
      return
    }
    setPhase('uploading')
    setProgress(0)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('title', title || file.name)
      form.append('tags', tags)
      if (desc.trim()) form.append('desc', desc.trim())
      const res = await api.upload<{ video_id: number; pending_id: number }>(
        '/videos/upload',
        form,
      )
      setProgress(100)
      // 上传成功后立刻跑 AI 审核，让用户看到完整时间线
      if (mode === 'segment') {
        startSegmentAudit(res.video_id)
      } else {
        startAudit(res.video_id)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
      setPhase('error')
    }
  }

  /** 切片审核：逐段判定，结论能定位到具体秒数 */
  const startSegmentAudit = (videoId?: number) => {
    setPhase('auditing')
    setSegPlan(null)
    setSegments([])
    setSegResult(null)
    setResult(null)
    const controller = new AbortController()
    abortRef.current = controller

    void fetch('/api/ai/audit/segment/stream', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_id: videoId ?? 0 }),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          const j = await res.json().catch(() => ({}))
          setError(j.error ?? '切片审核启动失败')
          setPhase('error')
          return
        }
        await consume(res, {
          onEvent: (ev: any) => {
            if (ev.type === 'plan') {
              setSegPlan(ev.plan as SegmentPlan)
            } else if (ev.type === 'segment') {
              setSegments((prev) =>
                [...prev, ev.segment as SegmentItem].sort((a, b) => a.index - b.index),
              )
            } else if (ev.type === 'result') {
              setSegResult(ev as SegmentAuditResult)
              setPhase('done')
            } else if (ev.type === 'error') {
              setError(ev.message ?? '切片审核失败')
              setPhase('error')
            }
          },
          onError: (err: Error) => {
            setError(err.message)
            setPhase('error')
          },
        })
      })
      .catch((err: Error) => {
        if (err.name !== 'AbortError') {
          setError(err.message)
          setPhase('error')
        }
      })
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-6">
      <SectionTitle>投稿作品</SectionTitle>
      <p className="mb-5 text-sm text-ink-mute">
        上传后 AI 审核 Agent 会逐帧理解画面与文字，结合校规与历史判例给出可解释结论。
      </p>

      <form onSubmit={submit} className="card p-6">
        {/* 文件选择 */}
        <div
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            onPick(e.dataTransfer.files?.[0] ?? null)
          }}
          className="flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed border-ink-line bg-surface-page/60 px-6 py-10 text-center transition-colors hover:border-brand-300 hover:bg-brand-50/40"
        >
          <input
            ref={fileRef}
            type="file"
            accept="video/mp4,video/quicktime"
            className="hidden"
            onChange={(e) => onPick(e.target.files?.[0] ?? null)}
          />
          <div className="mb-2 text-2xl">{file ? '🎞️' : '⬆️'}</div>
          {file ? (
            <>
              <p className="text-sm font-medium text-ink">{file.name}</p>
              <p className="mt-1 text-xs text-ink-mute">
                {(file.size / 1024 / 1024).toFixed(1)} MB · 点击重新选择
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-medium text-ink">点击或拖拽上传视频</p>
              <p className="mt-1 text-xs text-ink-mute">支持 mp4 / mov，最大 500MB</p>
            </>
          )}
        </div>

        {/* 元信息 */}
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-soft">作品标题</label>
            <input
              className="input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="给作品起个名字"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-soft">
              标签（逗号分隔）
            </label>
            <input
              className="input"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="如：校园日常,社团活动"
            />
          </div>
        </div>

        {/* 作品简介：AI 创作副驾生成的内容可直接预填到这里 */}
        <div className="mt-4">
          <div className="mb-1.5 flex items-center justify-between">
            <label className="text-xs font-medium text-ink-soft">作品简介（可选）</label>
            <span className="text-[11px] text-ink-mute">{desc.length}/1000</span>
          </div>
          <textarea
            className="input min-h-[84px] resize-y py-2.5 text-sm"
            value={desc}
            maxLength={1000}
            onChange={(e) => setDesc(e.target.value)}
            placeholder="用一两句话说清你拍了什么。不知道怎么写？去「AI 工坊」上传视频，让 AI 看完帮你写。"
          />
          <div className="mt-1.5 flex items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/studio')}
              className="text-[11px] text-brand-600 hover:underline"
            >
              🎬 让 AI 看完视频帮我写 →
            </button>
            {desc && (
              <button
                type="button"
                onClick={() => setDesc('')}
                className="text-[11px] text-ink-mute hover:text-rose-500"
              >
                清空
              </button>
            )}
          </div>
        </div>

        {/* AI 标签建议 */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={genTags}
            disabled={tagLoading || !title.trim()}
            className="btn-soft h-8 px-3 text-xs"
          >
            {tagLoading ? <Spinner className="h-3 w-3" /> : '✨ AI 推荐标签'}
          </button>
          {aiTags.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() =>
                setTags((prev) => {
                  const list = prev.split(',').map((x) => x.trim()).filter(Boolean)
                  return list.includes(t) ? prev : [...list, t].join(',')
                })
              }
              className="chip hover:bg-brand-50 hover:text-brand-600"
            >
              + {t}
            </button>
          ))}
        </div>

        {error && (
          <div className="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</div>
        )}

        {/* 审核模式：整片 vs 切片 */}
        <div className="mt-5 rounded-xl border border-ink-line/70 bg-surface-page/50 p-3">
          <div className="mb-2 text-xs font-medium text-ink-soft">审核方式</div>
          <div className="flex flex-col gap-2 sm:flex-row">
            {[
              {
                key: 'segment' as AuditMode,
                title: '切片审核（推荐）',
                desc: '按时间切成若干段逐段判定，能定位到第几秒有问题',
              },
              {
                key: 'whole' as AuditMode,
                title: '整片审核',
                desc: '整条视频给一个结论，速度快、消耗少',
              },
            ].map((m) => (
              <button
                key={m.key}
                type="button"
                onClick={() => setMode(m.key)}
                className={
                  'flex-1 rounded-lg border px-3 py-2 text-left transition-colors ' +
                  (mode === m.key
                    ? 'border-brand-300 bg-brand-50'
                    : 'border-ink-line bg-white hover:border-brand-200')
                }
              >
                <div
                  className={
                    'text-xs font-medium ' +
                    (mode === m.key ? 'text-brand-600' : 'text-ink')
                  }
                >
                  {m.title}
                </div>
                <div className="mt-0.5 text-[11px] leading-relaxed text-ink-mute">
                  {m.desc}
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="mt-5 flex items-center gap-3">
          <button
            type="submit"
            disabled={phase === 'uploading' || phase === 'auditing'}
            className="btn-primary h-10 px-6"
          >
            {phase === 'uploading' ? <Spinner className="border-white/40 border-t-white" /> : null}
            {phase === 'uploading'
              ? '上传中…'
              : phase === 'auditing'
                ? mode === 'segment'
                  ? '切片审核中…'
                  : 'AI 审核中…'
                : mode === 'segment'
                  ? '提交并切片审核'
                  : '提交并审核'}
          </button>
          {phase === 'uploading' && (
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-subtle">
              <div
                className="h-full rounded-full bg-brand-500 transition-all"
                style={{ width: `${Math.max(progress, 12)}%` }}
              />
            </div>
          )}
        </div>
      </form>

      {/* ★ 切片审核：时间轴 + 逐段结论 */}
      {(mode === 'segment' && (segPlan || segResult || phase === 'auditing')) && (
        <SegmentTimeline
          plan={segPlan}
          segments={segments}
          result={segResult}
          running={phase === 'auditing'}
        />
      )}

      {/* ★ 审核时间线：AI 思考过程可视化 */}
      {mode === 'whole' && (steps.length > 0 || phase === 'auditing') && (
        <section className="mt-6">
          <SectionTitle
            extra={
              phase === 'auditing' ? (
                <span className="flex items-center gap-2 text-xs text-brand-600">
                  <ThinkingDots /> Agent 正在推理
                </span>
              ) : result ? (
                <span className="text-xs text-ink-mute">
                  {result.ai_enabled ? `模型 ${result.model}` : '规则引擎（未配置 API Key）'} ·{' '}
                  {result.elapsed_ms}ms
                </span>
              ) : null
            }
          >
            审核推理时间线
          </SectionTitle>

          <div className="card p-5">
            <ol className="relative space-y-4 border-l border-ink-line pl-5">
              {steps.map((s) => (
                <li key={s.id} className="relative">
                  <span
                    className={
                      'absolute -left-[26px] flex h-4 w-4 items-center justify-center rounded-full text-[9px] text-white ' +
                      (s.status === 'done'
                        ? 'bg-emerald-500'
                        : s.status === 'running'
                          ? 'bg-brand-500'
                          : s.status === 'error'
                            ? 'bg-rose-500'
                            : 'bg-ink-line')
                    }
                  >
                    {s.status === 'done' ? '✓' : s.status === 'error' ? '!' : ''}
                  </span>
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-ink">{s.name}</span>
                    {s.status === 'running' && <ThinkingDots />}
                    {s.elapsed_ms > 0 && (
                      <span className="text-[11px] text-ink-mute">{s.elapsed_ms}ms</span>
                    )}
                  </div>
                  {s.detail && (
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-mute">{s.detail}</p>
                  )}
                </li>
              ))}
              {phase === 'auditing' && steps.length < 6 && (
                <li className="relative">
                  <span className="absolute -left-[26px] flex h-4 w-4 items-center justify-center rounded-full bg-brand-200" />
                  <span className="flex items-center gap-2 text-sm text-ink-mute">
                    <ThinkingDots /> 等待下一步…
                  </span>
                </li>
              )}
            </ol>
          </div>
        </section>
      )}

      {/* 审核结论（整片模式） */}
      {mode === 'whole' && result && (
        <section className="mt-6">
          <SectionTitle>审核结论</SectionTitle>
          <div className="card overflow-hidden">
            <div
              className={
                'flex items-center justify-between px-5 py-4 ' +
                (result.verdict === 'pass'
                  ? 'bg-emerald-50'
                  : result.verdict === 'reject'
                    ? 'bg-rose-50'
                    : 'bg-amber-50')
              }
            >
              <div className="flex items-center gap-3">
                <span className="text-2xl">
                  {result.verdict === 'pass' ? '✅' : result.verdict === 'reject' ? '⛔' : '👀'}
                </span>
                <div>
                  <p className="text-sm font-semibold text-ink">
                    {result.verdict === 'pass'
                      ? '审核通过'
                      : result.verdict === 'reject'
                        ? '不予通过'
                        : '转人工复核'}
                  </p>
                  <p className="text-xs text-ink-mute">
                    综合得分 {result.score} / 100 · 置信度{' '}
                    {(result.confidence * 100).toFixed(0)}%
                  </p>
                </div>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {result.categories.map((c) => (
                  <Badge key={c} tone="danger">
                    {c}
                  </Badge>
                ))}
              </div>
            </div>

            <div className="grid gap-5 p-5 sm:grid-cols-2">
              <div>
                <h4 className="mb-2 text-xs font-semibold text-ink">判定理由</h4>
                <ul className="space-y-1.5">
                  {result.reasons.map((r, i) => (
                    <li key={i} className="flex gap-2 text-xs leading-relaxed text-ink-soft">
                      <span className="text-brand-500">·</span>
                      {r}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h4 className="mb-2 text-xs font-semibold text-ink">证据链</h4>
                {result.evidence.length ? (
                  <ul className="space-y-1.5">
                    {result.evidence.map((e, i) => (
                      <li key={i} className="rounded-lg bg-surface-subtle px-2.5 py-1.5 text-xs">
                        <span className="font-medium text-brand-600">{e.ref}</span>
                        <span className="ml-1.5 text-ink-mute">{e.detail}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-ink-mute">未命中具体条款</p>
                )}
              </div>
            </div>

            {result.suggestions.length > 0 && (
              <div className="border-t border-ink-line/70 bg-surface-page/60 px-5 py-3">
                <h4 className="mb-1 text-xs font-semibold text-ink">整改建议</h4>
                <ul className="list-inside list-disc space-y-0.5 text-xs text-ink-soft">
                  {result.suggestions.map((s, i) => (
                    <li key={i}>{s}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className="flex flex-wrap gap-2 border-t border-ink-line/70 px-5 py-3">
              <button className="btn-ghost h-9 text-xs" onClick={() => navigate('/')}>
                回首页
              </button>
              <button
                className="btn-ghost h-9 text-xs"
                onClick={() => {
                  setPhase('idle')
                  setFile(null)
                  setSteps([])
                  setResult(null)
                }}
              >
                再投一个
              </button>
              <button className="btn-soft h-9 text-xs" onClick={() => navigate('/studio')}>
                去 AI 工坊看完整报告
              </button>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}

const ORDER = ['meta', 'frames', 'vision', 'text', 'rules', 'decide']

function order(id: string) {
  const i = ORDER.indexOf(id)
  return i < 0 ? 99 : i
}

/** 复用流解析：把 fetch Response 交给统一的事件消费逻辑 */
async function consume(
  res: Response,
  handlers: { onEvent: (e: any) => void; onError: (e: Error) => void },
) {
  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const raw = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 2)
      for (const line of raw.split('\n')) {
        if (!line.startsWith('data:')) continue
        try {
          handlers.onEvent(JSON.parse(line.slice(5).trim()))
        } catch {
          /* ignore */
        }
      }
    }
  }
}
