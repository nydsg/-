import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { api } from '../lib/api'
import type { VideoCard } from '../lib/types'
import { EmptyState, Skeleton, VideoCardItem } from '../components/ui'

interface ProfileResp {
  user: {
    id: number
    username: string
    name: string
    school: string
    signature: string
    avatar: string
    bg: string
    joined_at: string
  }
  works: VideoCard[]
  stats: { fans: number; following: number; likes: number }
}

export function ProfilePage() {
  const { id } = useParams()
  const uid = Number(id)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['profile', uid],
    queryFn: () => api.get<ProfileResp>(`/users/${uid}`),
    enabled: Number.isFinite(uid),
    retry: 1,
  })

  if (isLoading) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-6">
        <Skeleton className="h-40 w-full rounded-xl" />
        <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="aspect-video" />
          ))}
        </div>
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16">
        <EmptyState title="用户不存在" hint="该主页可能已被删除。" />
      </div>
    )
  }

  const { user, works, stats } = data

  return (
    <div className="mx-auto max-w-5xl px-4 py-6">
      {/* 主页头部：沿用原版的背景图 + 头像叠加布局 */}
      <div className="overflow-hidden rounded-2xl border border-ink-line/70 bg-white shadow-card">
        <div
          className="h-36 bg-brand-500 bg-cover bg-center sm:h-44"
          style={
            user.bg
              ? { backgroundImage: `url(${user.bg})` }
              : {
                  backgroundImage:
                    'linear-gradient(135deg,#ff6b4a 0%,#ff8558 55%,#ffa283 100%)',
                }
          }
        />
        <div className="relative px-5 pb-5">
          <div className="-mt-10 flex flex-wrap items-end gap-4">
            {user.avatar ? (
              <img
                src={user.avatar}
                alt=""
                className="h-20 w-20 rounded-2xl border-4 border-white object-cover shadow-card"
              />
            ) : (
              <span className="flex h-20 w-20 items-center justify-center rounded-2xl border-4 border-white bg-brand-100 text-2xl font-semibold text-brand-600 shadow-card">
                {user.name.slice(0, 1)}
              </span>
            )}
            <div className="min-w-0 flex-1 pb-1">
              <h1 className="truncate text-lg font-semibold text-ink">{user.name}</h1>
              <p className="mt-0.5 text-xs text-ink-mute">
                @{user.username} · {user.school}
                {user.joined_at && ` · 加入于 ${user.joined_at.slice(0, 10)}`}
              </p>
            </div>
          </div>

          {user.signature && (
            <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-ink-soft">
              {user.signature}
            </p>
          )}

          <div className="mt-4 flex gap-6 border-t border-ink-line/70 pt-3">
            {[
              { label: '作品', value: works.length },
              { label: '获赞', value: stats.likes },
              { label: '粉丝', value: stats.fans },
              { label: '关注', value: stats.following },
            ].map((s) => (
              <div key={s.label}>
                <p className="text-base font-semibold text-ink">{s.value}</p>
                <p className="text-[11px] text-ink-mute">{s.label}</p>
              </div>
            ))}
          </div>
        </div>
      </div>

      <section className="mt-6">
        <h2 className="mb-3 flex items-center gap-2 text-base font-semibold text-ink">
          <span className="h-4 w-1 rounded-full bg-brand-500" />
          作品
          <span className="text-xs font-normal text-ink-mute">{works.length}</span>
        </h2>
        {works.length ? (
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
            {works.map((v, i) => (
              <VideoCardItem key={v.id} video={v} index={i} />
            ))}
          </div>
        ) : (
          <EmptyState
            title="还没有公开作品"
            hint="作品通过审核后会展示在这里。"
            action={
              <Link to="/submit" className="btn-primary">
                去投稿
              </Link>
            }
          />
        )}
      </section>
    </div>
  )
}
