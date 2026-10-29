import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { streamForm, streamPost } from '../lib/stream'
import type { AiStatus, RagHit } from '../lib/types'
import { Badge, Spinner, ThinkingDots } from '../components/ui'

interface CopilotStep {
  id: string
  name: string
  status: string
  detail: string
  elapsed_ms: number
}

interface CopilotResult {
  titles: string[]
  intro: string
  tags: string[]
  risk_note: string
  scenes: string[]
  risks: { type: string; severity: string; frame: number; detail: string }[]
  elapsed_ms?: number
}

interface CopilotState {
  fileName: string
  steps: CopilotStep[]
  result?: CopilotResult
}

interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
  copilot?: CopilotState
}

const SUGGESTIONS = [
  '帮我想 3 个关于「图书馆熬夜复习」的标题，分别偏真诚、有梗、文艺',
  '我的作品叫《食堂的一天》，帮我写一段 80 字简介',
  '给「社团纳新」这个作品推荐 5 个校园标签',
  '哪些内容在校园社区里容易被判违规？我投稿前想自查一下',
]

export function StudioPage() {
  const [tab, setTab] = useState<'chat' | 'rag' | 'audit'>('chat')

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      <div className="mb-5">
        <h1 className="text-lg font-semibold text-ink">AI 工坊</h1>
        <p className="mt-1 text-sm text-ink-mute">
          创作助手 · 规范检索 · 审核报告 —— 三大 AI 能力的统一入口
        </p>
      </div>

      <div className="mb-5 flex gap-1 rounded-pill bg-surface-subtle p-1">
        {[
          { key: 'chat', label: '💬 创作助手' },
          { key: 'rag', label: '📚 规范检索' },
          { key: 'audit', label: '🛡️ 审核历史' },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key as typeof tab)}
            className={
              'flex-1 rounded-pill px-4 py-2 text-sm transition-colors ' +
              (tab === t.key
                ? 'bg-white font-medium text-brand-600 shadow-card'
                : 'text-ink-soft hover:text-ink')
            }
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'chat' && <ChatPanel />}
      {tab === 'rag' && <RagPanel />}
      {tab === 'audit' && <AuditHistoryPanel />}
    </div>
  )
}

/* ---------------- 创作助手：流式对话 ---------------- */
function ChatPanel() {
  const navigate = useNavigate()
  const [turns, setTurns] = useState<ChatTurn[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [copilotBusy, setCopilotBusy] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [status, setStatus] = useState<AiStatus | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    void api.get<AiStatus>('/ai/status').then(setStatus).catch(() => undefined)
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [turns, streaming, copilotBusy])

  const send = async (text: string) => {
    const message = text.trim()
    if (!message || streaming) return
    setInput('')
    const history = turns.slice(-8)
    setTurns((prev) => [...prev, { role: 'user', content: message }])
    setStreaming(true)

    // 先占位一个空的 assistant 消息，边收边写
    setTurns((prev) => [...prev, { role: 'assistant', content: '' }])
    const controller = new AbortController()
    abortRef.current = controller

    await streamPost(
      '/api/ai/chat',
      { message, history },
      {
        onEvent: (ev: any) => {
          if (ev.type === 'delta') {
            setTurns((prev) => {
              const next = [...prev]
              const last = next[next.length - 1]
              if (last?.role === 'assistant') {
                next[next.length - 1] = { ...last, content: last.content + ev.text }
              }
              return next
            })
          }
        },
        onError: (err) => {
          setTurns((prev) => {
            const next = [...prev]
            const last = next[next.length - 1]
            if (last?.role === 'assistant' && !last.content) {
              next[next.length - 1] = {
                ...last,
                content: `⚠️ ${err.message}`,
              }
            }
            return next
          })
        },
        onDone: () => setStreaming(false),
      },
      controller.signal,
    )
    setStreaming(false)
  }

  /** 上传视频 → AI 看完 → 直接写文案（解决"用户说不清拍了什么"） */
  const analyzeVideo = async (file: File) => {
    if (copilotBusy) return
    if (!/\.(mp4|mov)$/i.test(file.name)) {
      setTurns((prev) => [
        ...prev,
        { role: 'user', content: `📹 ${file.name}` },
        { role: 'assistant', content: '⚠️ 只支持 mp4 / mov 格式的视频。' },
      ])
      return
    }
    setCopilotBusy(true)
    const form = new FormData()
    form.append('file', file)
    form.append('hint', '')

    setTurns((prev) => [
      ...prev,
      { role: 'user', content: `📹 我上传了「${file.name}」，帮我看看怎么写文案` },
      {
        role: 'assistant',
        content: '',
        copilot: { fileName: file.name, steps: [] },
      },
    ])

    const patchLast = (fn: (t: ChatTurn) => ChatTurn) => {
      setTurns((prev) => {
        const next = [...prev]
        const idx = next.length - 1
        if (idx >= 0 && next[idx].role === 'assistant') next[idx] = fn(next[idx])
        return next
      })
    }

    const controller = new AbortController()
    abortRef.current = controller

    await streamForm(
      '/api/ai/copilot/stream',
      form,
      {
        onEvent: (ev: any) => {
          if (ev.type === 'step') {
            patchLast((t) => {
              const steps = (t.copilot?.steps ?? []).filter(
                (s) => s.id !== ev.step.id,
              )
              return {
                ...t,
                copilot: {
                  ...(t.copilot ?? { fileName: file.name, steps: [] }),
                  steps: [...steps, ev.step],
                },
              }
            })
          } else if (ev.type === 'delta') {
            patchLast((t) => ({ ...t, content: t.content + (ev.text ?? '') }))
          } else if (ev.type === 'result') {
            patchLast((t) => ({
              ...t,
              copilot: {
                ...(t.copilot ?? { fileName: file.name, steps: [] }),
                result: ev as CopilotResult,
              },
            }))
          } else if (ev.type === 'error') {
            patchLast((t) => ({ ...t, content: `⚠️ ${ev.message ?? '读取视频失败'}` }))
          }
        },
        onError: (err) => {
          patchLast((t) => ({ ...t, content: `⚠️ ${err.message}` }))
        },
        onDone: () => setCopilotBusy(false),
      },
      controller.signal,
    )
    setCopilotBusy(false)
  }

  return (
    <div
      className={
        'card flex h-[calc(100vh-17rem)] min-h-[460px] flex-col overflow-hidden ' +
        (dragging ? 'ring-2 ring-brand-300' : '')
      }
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(e) => {
        // 只有真正离开容器时才取消高亮
        if (e.currentTarget === e.target) setDragging(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        const f = e.dataTransfer.files?.[0]
        if (f) void analyzeVideo(f)
      }}
    >
      <input
        ref={fileRef}
        type="file"
        accept="video/mp4,video/quicktime"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0]
          if (f) void analyzeVideo(f)
          e.target.value = ''
        }}
      />
      {/* 状态条 */}
      <div className="flex items-center justify-between border-b border-ink-line/70 px-4 py-2.5">
        <div className="flex items-center gap-2 text-xs">
          <span
            className={
              'h-1.5 w-1.5 rounded-full ' +
              (status?.ai_enabled ? 'bg-emerald-500' : 'bg-amber-400')
            }
          />
          <span className="text-ink-mute">
            {status?.ai_enabled ? `已连接 ${status.model}` : '演示模式（未配置 AI_API_KEY）'}
          </span>
        </div>
        {turns.length > 0 && (
          <button
            onClick={() => setTurns([])}
            className="text-xs text-ink-mute hover:text-brand-600"
          >
            清空对话
          </button>
        )}
      </div>

      {/* 消息区 */}
      <div className="flex-1 space-y-4 overflow-y-auto px-4 py-5">
        {turns.length === 0 && (
          <div className="mx-auto max-w-md pt-8 text-center">
            <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-50 text-2xl">
              ✨
            </div>
            <h3 className="text-sm font-semibold text-ink">我是你的 AI 创作搭子</h3>
            <p className="mt-1.5 text-xs leading-relaxed text-ink-mute">
              帮你起标题、写简介、配标签，也会提醒你哪些表达有审核风险。
            </p>

            {/* 核心入口：说不清内容？直接丢个视频给我 */}
            <button
              onClick={() => fileRef.current?.click()}
              disabled={copilotBusy}
              className="mt-4 flex w-full items-center gap-3 rounded-xl border border-dashed border-brand-300 bg-brand-50/50 px-3 py-3 text-left transition-colors hover:bg-brand-50 disabled:opacity-60"
            >
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-white text-lg">
                🎬
              </span>
              <span className="min-w-0">
                <span className="block text-xs font-medium text-brand-600">
                  说不清自己拍了什么？上传视频，我先看完再写
                </span>
                <span className="mt-0.5 block text-[11px] text-ink-mute">
                  支持 mp4 / mov · 也能直接把文件拖到这里
                </span>
              </span>
            </button>

            <div className="mt-3 space-y-2 text-left">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => void send(s)}
                  className="w-full rounded-lg border border-ink-line bg-white px-3 py-2 text-left text-xs text-ink-soft transition-colors hover:border-brand-300 hover:text-brand-600"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t, i) =>
          t.copilot ? (
            <CopilotCard
              key={i}
              copilot={t.copilot}
              text={t.content}
              streaming={copilotBusy && i === turns.length - 1}
              onGoSubmit={(title, tags, intro) => {
                const q = new URLSearchParams()
                if (title) q.set('title', title)
                if (tags) q.set('tags', tags)
                if (intro) q.set('intro', intro)
                navigate(`/submit?${q.toString()}`)
              }}
            />
          ) : (
            <div
              key={i}
              className={t.role === 'user' ? 'flex justify-end' : 'flex justify-start'}
            >
              <div
                className={
                  t.role === 'user'
                    ? 'max-w-[80%] rounded-2xl rounded-br-sm bg-brand-500 px-4 py-2.5 text-sm leading-relaxed text-white'
                    : 'max-w-[85%] rounded-2xl rounded-bl-sm bg-surface-subtle px-4 py-2.5 text-sm leading-relaxed text-ink'
                }
              >
                {t.content ? (
                  <span className="whitespace-pre-wrap break-words">{t.content}</span>
                ) : (
                  <span className="flex items-center gap-2 text-ink-mute">
                    <ThinkingDots /> 正在思考
                  </span>
                )}
                {streaming &&
                  i === turns.length - 1 &&
                  t.role === 'assistant' &&
                  t.content && <span className="typing-caret" />}
              </div>
            </div>
          ),
        )}
        <div ref={bottomRef} />
      </div>

      {/* 输入区 */}
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void send(input)
        }}
        className="border-t border-ink-line/70 p-3"
      >
        <div className="flex items-end gap-2">
          <button
            type="button"
            onClick={() => fileRef.current?.click()}
            disabled={copilotBusy}
            title="上传视频，让 AI 看完再帮你写文案"
            className="btn-ghost h-[42px] shrink-0 px-3 text-base"
          >
            📎
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                void send(input)
              }
            }}
            rows={1}
            placeholder="描述你的作品，或让我帮你改文案…（Enter 发送 / Shift+Enter 换行）"
            className="input max-h-32 min-h-[42px] resize-none py-2.5"
          />
          {streaming ? (
            <button
              type="button"
              onClick={() => abortRef.current?.abort()}
              className="btn-ghost h-[42px] shrink-0 px-4 text-xs"
            >
              停止
            </button>
          ) : (
            <button type="submit" disabled={!input.trim()} className="btn-primary h-[42px] shrink-0 px-5">
              发送
            </button>
          )}
        </div>
      </form>
    </div>
  )
}

/* ---------------- 创作副驾结果卡：AI 看完视频后给出的文案 ---------------- */
function CopilotCard({
  copilot,
  text,
  streaming,
  onGoSubmit,
}: {
  copilot: CopilotState
  text: string
  streaming: boolean
  onGoSubmit: (title: string, tags: string, intro: string) => void
}) {
  const [copied, setCopied] = useState('')
  const r = copilot.result

  const copy = async (label: string, value: string) => {
    if (!value) return
    try {
      await navigator.clipboard.writeText(value)
      setCopied(label)
      setTimeout(() => setCopied(''), 1600)
    } catch {
      /* 剪贴板受限时静默失败，用户仍可手动选中 */
    }
  }

  const allText = r
    ? [
        ...r.titles.map((t, i) => `标题${i + 1}：${t}`),
        r.intro ? `简介：${r.intro}` : '',
        r.tags.length ? `标签：${r.tags.join(', ')}` : '',
        r.risk_note ? `注意：${r.risk_note}` : '',
      ]
        .filter(Boolean)
        .join('\n')
    : text

  return (
    <div className="w-full max-w-[92%] rounded-2xl rounded-bl-sm border border-ink-line/70 bg-white p-3.5 shadow-sm">
      {/* 头部：文件 + 推理步骤 */}
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-line/60 pb-2.5">
        <span className="text-base">🎬</span>
        <span className="min-w-0 flex-1 truncate text-xs font-medium text-ink">
          {copilot.fileName}
        </span>
        {r?.elapsed_ms ? (
          <span className="text-[11px] text-ink-mute">{(r.elapsed_ms / 1000).toFixed(1)}s</span>
        ) : null}
      </div>

      {copilot.steps.length > 0 && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          {copilot.steps.map((s) => (
            <span
              key={s.id}
              className={
                'inline-flex items-center gap-1 rounded-pill px-2 py-0.5 text-[11px] ' +
                (s.status === 'error'
                  ? 'bg-rose-50 text-rose-600'
                  : s.status === 'done'
                    ? 'bg-emerald-50 text-emerald-700'
                    : 'bg-brand-50 text-brand-600')
              }
              title={s.detail}
            >
              {s.status === 'done' ? '✓' : s.status === 'error' ? '!' : '·'} {s.name}
              {s.elapsed_ms > 0 && <span className="opacity-60">{s.elapsed_ms}ms</span>}
            </span>
          ))}
        </div>
      )}

      {/* 流式解说 */}
      {text && (
        <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink">
          {text}
          {streaming && <span className="typing-caret" />}
        </p>
      )}

      {!text && !r && (
        <p className="mt-3 flex items-center gap-2 text-xs text-ink-mute">
          <ThinkingDots /> 正在看你的视频…
        </p>
      )}

      {/* 结构化文案 */}
      {r && (r.titles.length > 0 || r.intro || r.tags.length > 0) && (
        <div className="mt-3 space-y-3">
          {r.titles.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <h4 className="text-[11px] font-semibold text-ink-soft">备选标题</h4>
                <button
                  onClick={() => void copy('titles', r.titles.join('\n'))}
                  className="text-[11px] text-ink-mute hover:text-brand-600"
                >
                  {copied === 'titles' ? '已复制' : '复制全部'}
                </button>
              </div>
              <div className="space-y-1.5">
                {r.titles.map((t) => (
                  <button
                    key={t}
                    onClick={() => void copy(t, t)}
                    className="flex w-full items-center gap-2 rounded-lg border border-ink-line bg-surface-page/60 px-2.5 py-1.5 text-left text-xs text-ink transition-colors hover:border-brand-300 hover:bg-brand-50/50"
                  >
                    <span className="min-w-0 flex-1 truncate">{t}</span>
                    <span className="shrink-0 text-[10px] text-ink-mute">
                      {copied === t ? '已复制' : '点击复制'}
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {r.intro && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <h4 className="text-[11px] font-semibold text-ink-soft">作品简介</h4>
                <button
                  onClick={() => void copy('intro', r.intro)}
                  className="text-[11px] text-ink-mute hover:text-brand-600"
                >
                  {copied === 'intro' ? '已复制' : '复制'}
                </button>
              </div>
              <p className="rounded-lg bg-surface-subtle px-2.5 py-2 text-xs leading-relaxed text-ink-soft">
                {r.intro}
              </p>
            </div>
          )}

          {r.tags.length > 0 && (
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <h4 className="text-[11px] font-semibold text-ink-soft">建议标签</h4>
                <button
                  onClick={() => void copy('tags', r.tags.join(', '))}
                  className="text-[11px] text-ink-mute hover:text-brand-600"
                >
                  {copied === 'tags' ? '已复制' : '复制'}
                </button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {r.tags.map((t) => (
                  <span
                    key={t}
                    className="rounded-pill bg-brand-50 px-2 py-0.5 text-[11px] text-brand-600"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </div>
          )}

          {r.risk_note && (
            <div className="rounded-lg bg-amber-50 px-2.5 py-2 text-[11px] leading-relaxed text-amber-700">
              ⚠️ {r.risk_note}
            </div>
          )}

          <div className="flex flex-wrap gap-2 pt-0.5">
            <button
              onClick={() => void copy('all', allText)}
              className="btn-ghost h-8 px-3 text-[11px]"
            >
              {copied === 'all' ? '✓ 已复制全部' : '复制全部文案'}
            </button>
            <button
              onClick={() => onGoSubmit(r.titles[0] ?? '', r.tags.join(','), r.intro)}
              className="btn-primary h-8 px-3 text-[11px]"
            >
              带着这些去投稿 →
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/* ---------------- 规范检索：RAG 可视化 ---------------- */
function RagPanel() {
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<RagHit[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [corpus, setCorpus] = useState(0)

  const status = useQuery({
    queryKey: ['ai-status'],
    queryFn: () => api.get<AiStatus>('/ai/status'),
    retry: 1,
  })

  useEffect(() => {
    if (status.data) {
      setCorpus(status.data.rag_docs)
      setHits(status.data.rag_sample ?? [])
    }
  }, [status.data])

  const search = async (q: string) => {
    if (!q.trim()) return
    setLoading(true)
    try {
      const res = await api.get<{ items: RagHit[]; corpus_size: number }>('/ai/rag/search', {
        q,
        k: 5,
      })
      setHits(res.items)
      setCorpus(res.corpus_size)
    } catch {
      setHits([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="card p-4">
        <div className="flex gap-2">
          <input
            className="input"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void search(query)}
            placeholder="输入一个内容场景，例如：宿舍 明火 实验 / 成人礼 / 兼职日结"
          />
          <button onClick={() => void search(query)} disabled={loading} className="btn-primary shrink-0">
            {loading ? <Spinner className="border-white/40 border-t-white" /> : '检索'}
          </button>
        </div>
        <p className="mt-2 text-xs text-ink-mute">
          语料库：{corpus} 条（规范条款 + 人工判例）。检索采用 TF‑IDF 字符 n‑gram 混合匹配，
          零外部依赖、可离线、可解释。
        </p>
      </div>

      {hits && (
        <div className="space-y-3">
          {hits.length === 0 && (
            <p className="card px-4 py-6 text-center text-sm text-ink-mute">
              没有命中条款，试试更具体的关键词。
            </p>
          )}
          {hits.map((h) => (
            <div key={h.id} className="card p-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone="brand">{h.id}</Badge>
                <span className="text-sm font-medium text-ink">{h.title}</span>
                <Badge>{h.cat}</Badge>
                {h.source === 'case' && <Badge tone="success">历史判例</Badge>}
                <span className="ml-auto text-xs text-ink-mute">
                  相关度 {(h.score * 100).toFixed(0)}%
                </span>
              </div>
              <p className="mt-2 text-xs leading-relaxed text-ink-soft">{h.text}</p>
              <div className="mt-2 h-1 overflow-hidden rounded-full bg-surface-subtle">
                <div
                  className="h-full rounded-full bg-brand-400"
                  style={{ width: `${Math.max(h.score * 100, 3)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ---------------- 审核历史 ---------------- */
function AuditHistoryPanel() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['audit-history'],
    queryFn: () =>
      api.get<{
        items: {
          id: number
          filename: string
          verdict: string
          score: number
          confidence: number
          model: string
          latency_ms: number
          created_at: string
        }[]
      }>('/ai/audit/history'),
    retry: 1,
  })

  if (isLoading) {
    return <div className="card px-4 py-10 text-center text-sm text-ink-mute">加载中…</div>
  }
  if (isError || !data?.items?.length) {
    return (
      <div className="card px-4 py-12 text-center">
        <p className="text-sm text-ink-soft">还没有审核记录</p>
        <p className="mt-1 text-xs text-ink-mute">去「投稿」上传一个视频，就能看到完整推理链。</p>
      </div>
    )
  }

  return (
    <div className="card divide-y divide-ink-line/70 overflow-hidden">
      {data.items.map((r) => (
        <div key={r.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
          <Badge
            tone={r.verdict === 'pass' ? 'success' : r.verdict === 'reject' ? 'danger' : 'warn'}
          >
            {r.verdict === 'pass' ? '通过' : r.verdict === 'reject' ? '拒绝' : '人工'}
          </Badge>
          <span className="min-w-0 flex-1 truncate text-sm text-ink">{r.filename}</span>
          <span className="text-xs text-ink-mute">得分 {r.score}</span>
          <span className="text-xs text-ink-mute">{r.latency_ms}ms</span>
          <span className="text-xs text-ink-mute">{r.created_at.slice(5, 16)}</span>
        </div>
      ))}
    </div>
  )
}
