import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { coverOf, formatViews } from '../lib/api'
import type { VideoCard as VideoCardType } from '../lib/types'

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx('skeleton', className)} />
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center rounded-card border border-dashed border-ink-line bg-white/60 px-6 py-16 text-center">
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-brand-50 text-2xl">
        🎬
      </div>
      <p className="text-sm font-medium text-ink">{title}</p>
      {hint && <p className="mt-1 max-w-sm text-xs leading-relaxed text-ink-mute">{hint}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function Badge({
  children,
  tone = 'default',
}: {
  children: ReactNode
  tone?: 'default' | 'brand' | 'success' | 'warn' | 'danger'
}) {
  const tones: Record<string, string> = {
    default: 'bg-surface-subtle text-ink-soft',
    brand: 'bg-brand-50 text-brand-600',
    success: 'bg-emerald-50 text-emerald-600',
    warn: 'bg-amber-50 text-amber-600',
    danger: 'bg-rose-50 text-rose-600',
  }
  return (
    <span
      className={clsx(
        'inline-flex items-center rounded-pill px-2 py-0.5 text-[11px] font-medium',
        tones[tone],
      )}
    >
      {children}
    </span>
  )
}

/** 作品卡片：沿用原版「封面 + 标题 + 作者 + 播放量」信息层级 */
export function VideoCardItem({ video, index = 0 }: { video: VideoCardType; index?: number }) {
  return (
    <Link
      to={`/video/${video.id}`}
      className="group animate-fade-up"
      style={{ animationDelay: `${Math.min(index * 40, 320)}ms` }}
    >
      <div className="overflow-hidden rounded-card border border-ink-line/60 bg-white shadow-card transition-all duration-200 group-hover:-translate-y-0.5 group-hover:border-brand-200 group-hover:shadow-hover">
        <div className="relative aspect-video overflow-hidden bg-surface-subtle">
          <img
            src={coverOf(video.cover)}
            alt={video.title}
            loading="lazy"
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
          />
          <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/55 to-transparent px-2.5 pb-1.5 pt-6">
            <span className="text-[11px] font-medium text-white/90">
              ▶ {formatViews(video.views)} 次播放
            </span>
          </div>
        </div>
        <div className="space-y-1.5 p-3">
          <h3 className="line-clamp-2 min-h-[2.4rem] text-sm font-medium leading-snug text-ink group-hover:text-brand-600">
            {video.title}
          </h3>
          <div className="flex items-center justify-between text-xs text-ink-mute">
            <span className="truncate">{video.author}</span>
            {video.tags.length > 0 && (
              <span className="ml-2 shrink-0 text-brand-500">#{video.tags[0]}</span>
            )}
          </div>
        </div>
      </div>
    </Link>
  )
}

export function SectionTitle({
  children,
  extra,
}: {
  children: ReactNode
  extra?: ReactNode
}) {
  return (
    <div className="mb-3 flex items-end justify-between">
      <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
        <span className="h-4 w-1 rounded-full bg-brand-500" />
        {children}
      </h2>
      {extra}
    </div>
  )
}

export function Spinner({ className }: { className?: string }) {
  return (
    <span
      className={clsx(
        'inline-block h-4 w-4 animate-spin rounded-full border-2 border-brand-200 border-t-brand-500',
        className,
      )}
    />
  )
}

/** AI 思考态：三个跳动的点（对应赛题「AI 思考状态可视化」） */
export function ThinkingDots() {
  return (
    <span className="inline-flex items-end gap-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-pulse-dot rounded-full bg-brand-400"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </span>
  )
}
