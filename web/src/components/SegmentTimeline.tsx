import { Badge, SectionTitle, ThinkingDots } from './ui'
import type { SegmentAuditResult, SegmentItem, SegmentPlan } from '../lib/types'

/** 切片颜色/文案映射：绿=通过 黄=待复核 红=拒绝 灰=未识别 */
const SEG_TONE: Record<
  string,
  { bar: string; badge: 'success' | 'warn' | 'danger' | 'default'; label: string }
> = {
  pass: { bar: 'bg-emerald-400', badge: 'success', label: '通过' },
  manual: { bar: 'bg-amber-400', badge: 'warn', label: '待复核' },
  reject: { bar: 'bg-rose-500', badge: 'danger', label: '拒绝' },
  unknown: { bar: 'bg-ink-line', badge: 'default', label: '未识别' },
}

/**
 * 切片审核时间轴：彩色时间条 + 逐段明细 + 汇总。
 *
 * 投稿页和审核台共用（compact 模式用于审核台，行高更紧凑）。
 */
export function SegmentTimeline({
  plan,
  segments,
  result,
  running,
  compact = false,
  title = '切片审核时间轴',
}: {
  plan: SegmentPlan | null
  segments: SegmentItem[]
  result: SegmentAuditResult | null
  running: boolean
  compact?: boolean
  title?: string
}) {
  const duration = plan?.duration || result?.duration || 0
  const total = plan?.count || 0
  const pad = compact ? 'px-4 py-3' : 'px-5 py-4'

  return (
    <section className={compact ? '' : 'mt-6'}>
      <SectionTitle
        extra={
          running ? (
            <span className="flex items-center gap-2 text-xs text-brand-600">
              <ThinkingDots /> 逐段判定中 {segments.length}/{total || '?'}
            </span>
          ) : result ? (
            <span className="text-xs text-ink-mute">
              共 {result.segments.length} 段 · {result.elapsed_ms}ms
            </span>
          ) : null
        }
      >
        {title}
      </SectionTitle>

      <div className="card overflow-hidden">
        {/* 时间轴条 */}
        {plan && (
          <div className={'border-b border-ink-line/70 ' + pad}>
            <div className="mb-2 flex items-center justify-between text-[11px] text-ink-mute">
              <span>0s</span>
              <span>
                整片 {duration}s，切成 {plan.count} 片（每片约 {plan.segment_seconds}s）
              </span>
              <span>{duration}s</span>
            </div>
            <div className="flex h-3 gap-0.5 overflow-hidden rounded-full bg-surface-subtle">
              {plan.segments.map((p) => {
                const seg = segments.find((s) => s.index === p.index)
                const tone = seg ? SEG_TONE[seg.verdict] ?? SEG_TONE.unknown : null
                return (
                  <div
                    key={p.index}
                    title={
                      seg
                        ? `${p.start}–${p.end}s：${tone?.label ?? ''}`
                        : `${p.start}–${p.end}s：待判定`
                    }
                    style={{ flexGrow: Math.max(p.end - p.start, 0.1) }}
                    className={
                      'h-full transition-colors ' +
                      (tone ? tone.bar : 'animate-pulse bg-brand-200')
                    }
                  />
                )
              })}
            </div>
            <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-ink-mute">
              {(['pass', 'manual', 'reject'] as const).map((k) => (
                <span key={k} className="flex items-center gap-1">
                  <i className={'inline-block h-2 w-2 rounded-full ' + SEG_TONE[k].bar} />
                  {SEG_TONE[k].label}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* 整体结论 */}
        {result && (
          <div
            className={
              'flex flex-wrap items-center gap-3 ' +
              pad +
              ' ' +
              (result.verdict === 'pass'
                ? 'bg-emerald-50'
                : result.verdict === 'reject'
                  ? 'bg-rose-50'
                  : 'bg-amber-50')
            }
          >
            <span className="text-xl">
              {result.verdict === 'pass' ? '✅' : result.verdict === 'reject' ? '⛔' : '👀'}
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-ink">
                {result.verdict === 'pass'
                  ? '全部切片通过'
                  : result.verdict === 'reject'
                    ? '存在高危切片，不予通过'
                    : '部分切片需要人工复核'}
              </p>
              <p className="text-xs text-ink-mute">
                综合得分 {result.score} / 100 · 有效切片证据 {result.evidence_strength} 段
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {result.categories.map((c) => (
                <Badge key={c} tone="danger">
                  {c}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* 逐段明细 */}
        <div className="divide-y divide-ink-line/70">
          {segments.map((s) => {
            const tone = SEG_TONE[s.verdict] ?? SEG_TONE.unknown
            return (
              <div
                key={s.index}
                className={
                  'flex flex-wrap items-start gap-3 ' + (compact ? 'px-4 py-2' : 'px-5 py-3')
                }
              >
                <div className="w-24 shrink-0 font-mono text-xs text-ink-mute">
                  {s.start}–{s.end}s
                </div>
                <Badge tone={tone.badge}>{tone.label}</Badge>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                    {s.scene && <span className="text-ink">场景：{s.scene}</span>}
                    {s.text && (
                      <span className="truncate text-ink-mute" title={s.text}>
                        文字：{s.text.slice(0, 30)}
                      </span>
                    )}
                    {s.tags.slice(0, 3).map((t) => (
                      <span
                        key={t}
                        className="rounded-pill bg-surface-subtle px-1.5 py-0.5 text-[10px] text-ink-mute"
                      >
                        #{t}
                      </span>
                    ))}
                  </div>
                  {s.risks.length > 0 && (
                    <div className="mt-1 text-[11px] text-rose-600">
                      {s.risks.map((r, i) => (
                        <div key={i}>
                          · {r.type}（{r.severity}）
                          {r.detail ? `：${r.detail.slice(0, 50)}` : ''}
                        </div>
                      ))}
                    </div>
                  )}
                  {!s.risks.length && s.note && (
                    <div className="mt-1 text-[11px] text-ink-mute">{s.note}</div>
                  )}
                </div>
                <div className="shrink-0 text-right text-[11px] text-ink-mute">
                  <div>{s.score} 分</div>
                  <div>{s.latency_ms}ms</div>
                </div>
              </div>
            )
          })}
          {running && segments.length < (total || 99) && (
            <div className={pad + ' text-xs text-ink-mute'}>
              <span className="flex items-center gap-2">
                <ThinkingDots /> 正在判定第 {segments.length + 1} 段…
              </span>
            </div>
          )}
        </div>

        {/* 总结 + 原因 */}
        {result && (
          <div className={'border-t border-ink-line/70 bg-surface-page/60 ' + pad}>
            {result.narrative && (
              <p className="mb-2 text-xs leading-relaxed text-ink-soft">{result.narrative}</p>
            )}
            <ul className="space-y-1">
              {result.reasons.map((r, i) => (
                <li key={i} className="flex gap-2 text-[11px] leading-relaxed text-ink-mute">
                  <span className="text-brand-500">·</span>
                  {r}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  )
}
