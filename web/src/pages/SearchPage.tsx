import { useSearchParams, Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import type { Paged, VideoCard } from '../lib/types'
import { EmptyState, Skeleton, VideoCardItem } from '../components/ui'

interface SearchResp extends Paged<VideoCard> {
  keyword: string
  users: { id: number; name: string; school: string; avatar: string }[]
}

export function SearchPage() {
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const tag = params.get('tag') ?? ''

  const result = useQuery({
    queryKey: ['search', q, tag],
    queryFn: () =>
      api.get<SearchResp>('/search', { q: q || tag || '', page: 1, size: 24 }),
    retry: 1,
  })

  const setTag = (t: string) => {
    const next = new URLSearchParams(params)
    if (t) next.set('tag', t)
    else next.delete('tag')
    next.delete('q')
    setParams(next)
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <div className="mb-5">
        <h1 className="text-lg font-semibold text-ink">
          {q ? `搜索「${q}」` : tag ? `标签 #${tag}` : '发现作品'}
        </h1>
        <p className="mt-1 text-sm text-ink-mute">
          {result.data ? `共 ${result.data.total} 个结果` : '按标题与标签检索'}
        </p>
      </div>

      {(q || tag) && (
        <button onClick={() => setTag('')} className="chip mb-4 hover:bg-brand-50 hover:text-brand-600">
          ✕ 清除筛选
        </button>
      )}

      {result.isLoading ? (
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
      ) : result.data?.items?.length ? (
        <>
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {result.data.items.map((v, i) => (
              <VideoCardItem key={v.id} video={v} index={i} />
            ))}
          </div>
          {!!result.data.users?.length && (
            <section className="mt-8">
              <h2 className="mb-3 text-sm font-semibold text-ink">相关用户</h2>
              <div className="flex flex-wrap gap-2">
                {result.data.users.map((u) => (
                  <Link
                    key={u.id}
                    to={`/u/${u.id}`}
                    className="card flex items-center gap-2 px-3 py-2 text-sm hover:border-brand-200"
                  >
                    <span className="flex h-7 w-7 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-600">
                      {u.name.slice(0, 1)}
                    </span>
                    <span className="text-ink-soft">{u.name}</span>
                  </Link>
                ))}
              </div>
            </section>
          )}
        </>
      ) : (
        <EmptyState
          title="没有找到相关作品"
          hint="换个关键词，或者去首页看看热门内容。"
          action={
            <Link to="/" className="btn-primary">
              回首页
            </Link>
          }
        />
      )}
    </div>
  )
}
