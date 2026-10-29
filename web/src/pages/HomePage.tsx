import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../lib/api'
import type { Paged, TagCount, VideoCard } from '../lib/types'
import {
  EmptyState,
  SectionTitle,
  Skeleton,
  VideoCardItem,
} from '../components/ui'

interface ShortsResp {
  items: VideoCard[]
  tags: TagCount[]
}

export function HomePage() {
  const shorts = useQuery({
    queryKey: ['shorts'],
    queryFn: () => api.get<ShortsResp>('/shorts'),
    retry: 1,
  })

  const latest = useQuery({
    queryKey: ['videos', 'new'],
    queryFn: () => api.get<Paged<VideoCard>>('/videos', { page: 1, size: 12 }),
    retry: 1,
  })

  const dbDown = shorts.isError || latest.isError

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      {/* Hero：原版首页的橙红主视觉 */}
      <section className="relative mb-8 overflow-hidden rounded-2xl bg-brand-500 px-7 py-10 text-white sm:px-12 sm:py-14">
        <div className="absolute -right-10 -top-16 h-56 w-56 rounded-full bg-white/10" />
        <div className="absolute -bottom-20 right-24 h-64 w-64 rounded-full bg-white/10" />
        <div className="relative max-w-xl">
          <span className="inline-flex items-center gap-1.5 rounded-pill bg-white/20 px-3 py-1 text-[11px] font-medium backdrop-blur">
            ✨ AI 内容共治 · 多模态审核 Agent
          </span>
          <h1 className="mt-4 text-2xl font-semibold leading-snug sm:text-3xl">
            校园里的好作品
            <br className="hidden sm:block" />
            值得一个更懂它的舞台
          </h1>
          <p className="mt-3 text-sm leading-relaxed text-white/85">
            上传你的校园创作，AI Agent 会逐帧理解画面与文字，
            结合校规与历史判例给出可解释的审核结论。
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <Link to="/submit" className="btn bg-white px-5 py-2.5 text-brand-600 hover:bg-white/90">
              立即投稿
            </Link>
            <Link to="/studio" className="btn border border-white/40 px-5 py-2.5 text-white hover:bg-white/10">
              试试 AI 工坊
            </Link>
          </div>
        </div>
      </section>

      {dbDown && (
        <div className="mb-6 rounded-card border border-amber-200 bg-amber-50 px-4 py-3 text-xs leading-relaxed text-amber-700">
          <strong className="font-medium">数据库暂不可用。</strong>
          页面骨架已正常渲染，作品数据需要连接 MySQL 后才会显示。 可访问{' '}
          <code className="rounded bg-amber-100 px-1">/api/health</code> 查看具体状态。
        </div>
      )}

      {/* 标签云 */}
      {!!shorts.data?.tags?.length && (
        <section className="mb-8">
          <SectionTitle>热门标签</SectionTitle>
          <div className="flex flex-wrap gap-2">
            {shorts.data.tags.map((t) => (
              <Link
                key={t.name}
                to={`/search?tag=${encodeURIComponent(t.name)}`}
                className="chip transition-colors hover:bg-brand-50 hover:text-brand-600"
              >
                #{t.name}
                <span className="ml-1 text-ink-mute">{t.count}</span>
              </Link>
            ))}
          </div>
        </section>
      )}

      {/* 热门 */}
      <section className="mb-8">
        <SectionTitle
          extra={
            <Link to="/search" className="text-xs text-ink-mute hover:text-brand-600">
              查看全部 →
            </Link>
          }
        >
          热门推荐
        </SectionTitle>
        {shorts.isLoading ? (
          <GridSkeleton />
        ) : shorts.data?.items?.length ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {shorts.data.items.map((v, i) => (
              <VideoCardItem key={v.id} video={v} index={i} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="还没有作品"
            hint="成为第一个投稿的人，AI 审核通过后会出现在这里。"
            action={
              <Link to="/submit" className="btn-primary">
                去投稿
              </Link>
            }
          />
        )}
      </section>

      {/* 最新 */}
      <section>
        <SectionTitle>最新上传</SectionTitle>
        {latest.isLoading ? (
          <GridSkeleton />
        ) : latest.data?.items?.length ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {latest.data.items.map((v, i) => (
              <VideoCardItem key={v.id} video={v} index={i} />
            ))}
          </div>
        ) : (
          <EmptyState title="暂无最新作品" hint="投稿后经审核通过即可在此展示。" />
        )}
      </section>
    </div>
  )
}

function GridSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
      {Array.from({ length: 8 }).map((_, i) => (
        <div key={i} className="overflow-hidden rounded-card border border-ink-line/60 bg-white">
          <Skeleton className="aspect-video rounded-none" />
          <div className="space-y-2 p-3">
            <Skeleton className="h-3 w-5/6" />
            <Skeleton className="h-3 w-1/3" />
          </div>
        </div>
      ))}
    </div>
  )
}
