import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, coverOf, formatViews } from '../lib/api'
import type { Comment, VideoDetail } from '../lib/types'
import { useAuth } from '../store/auth'
import { Badge, EmptyState, Skeleton, Spinner } from '../components/ui'

export function VideoPage() {
  const { id } = useParams()
  const videoId = Number(id)
  const { user } = useAuth()
  const qc = useQueryClient()
  const [comment, setComment] = useState('')
  const [posting, setPosting] = useState(false)

  const detail = useQuery({
    queryKey: ['video', videoId],
    queryFn: () => api.get<{ video: VideoDetail }>(`/videos/${videoId}`),
    enabled: Number.isFinite(videoId),
    retry: 1,
  })

  const comments = useQuery({
    queryKey: ['comments', videoId],
    queryFn: () => api.get<{ items: Comment[]; total: number }>(`/videos/${videoId}/comments`),
    enabled: Number.isFinite(videoId),
    retry: 1,
  })

  const likeState = useQuery({
    queryKey: ['like', videoId],
    queryFn: () => api.get<{ liked: boolean; count: number }>(`/videos/${videoId}/like`),
    enabled: Number.isFinite(videoId),
    retry: 1,
  })

  const collectState = useQuery({
    queryKey: ['collect', videoId],
    queryFn: () => api.get<{ collected: boolean }>(`/videos/${videoId}/collect`),
    enabled: Number.isFinite(videoId),
    retry: 1,
  })

  // 播放量：进入页面计一次，并写入浏览历史
  useEffect(() => {
    if (!Number.isFinite(videoId)) return
    void api.post(`/videos/${videoId}/view`).catch(() => undefined)
    if (user) void api.post('/history', { video_id: videoId }).catch(() => undefined)
  }, [videoId, user])

  const video = detail.data?.video

  const toggleLike = async () => {
    if (!user) return
    try {
      await api.post(`/videos/${videoId}/like`)
      void qc.invalidateQueries({ queryKey: ['like', videoId] })
    } catch {
      /* 忽略 */
    }
  }

  const toggleCollect = async () => {
    if (!user) return
    try {
      await api.post(`/videos/${videoId}/collect`, { folder: '默认收藏夹' })
      void qc.invalidateQueries({ queryKey: ['collect', videoId] })
    } catch {
      /* 忽略 */
    }
  }

  const submitComment = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!comment.trim() || !user) return
    setPosting(true)
    try {
      await api.post(`/videos/${videoId}/comments`, { content: comment.trim() })
      setComment('')
      void qc.invalidateQueries({ queryKey: ['comments', videoId] })
    } catch (err) {
      alert(err instanceof Error ? err.message : '评论失败')
    } finally {
      setPosting(false)
    }
  }

  if (detail.isLoading) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-6">
        <Skeleton className="aspect-video w-full rounded-xl" />
        <div className="mt-4 space-y-2">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-4 w-1/3" />
        </div>
      </div>
    )
  }

  if (detail.isError || !video) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-16">
        <EmptyState
          title="作品不存在或暂不可访问"
          hint="可能已下架，或数据库尚未连接。"
          action={
            <Link to="/" className="btn-primary">
              返回首页
            </Link>
          }
        />
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-6xl px-4 py-6">
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div>
          {/* 播放器 */}
          <div className="overflow-hidden rounded-xl bg-black">
            <video
              key={video.url}
              src={video.url}
              poster={coverOf(video.cover)}
              controls
              playsInline
              className="aspect-video w-full"
            />
          </div>

          <h1 className="mt-4 text-xl font-semibold leading-snug text-ink">{video.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-ink-mute">
            <span>{formatViews(video.views)} 次播放</span>
            <span>·</span>
            <span>{video.author}</span>
            {video.created_at && (
              <>
                <span>·</span>
                <span>{video.created_at.slice(0, 10)}</span>
              </>
            )}
            {video.tags.map((t) => (
              <Link key={t} to={`/search?tag=${encodeURIComponent(t)}`} className="chip-brand">
                #{t}
              </Link>
            ))}
          </div>

          {/* 作品简介 */}
          {video.desc && (
            <p className="mt-3 whitespace-pre-wrap break-words rounded-xl bg-surface-subtle px-4 py-3 text-sm leading-relaxed text-ink-soft">
              {video.desc}
            </p>
          )}

          {/* 操作条 */}
          <div className="mt-4 flex flex-wrap items-center gap-2 border-y border-ink-line/70 py-3">
            <button
              onClick={toggleLike}
              disabled={!user}
              className={
                likeState.data?.liked
                  ? 'btn bg-brand-50 text-brand-600'
                  : 'btn-ghost'
              }
            >
              {likeState.data?.liked ? '❤️ 已点赞' : '🤍 点赞'}
              <span className="text-ink-mute">{likeState.data?.count ?? 0}</span>
            </button>
            <button
              onClick={toggleCollect}
              disabled={!user}
              className={collectState.data?.collected ? 'btn bg-brand-50 text-brand-600' : 'btn-ghost'}
            >
              {collectState.data?.collected ? '⭐ 已收藏' : '☆ 收藏'}
            </button>
            <span className="btn-ghost cursor-default">
              💬 评论 <span className="text-ink-mute">{video.comment_count}</span>
            </span>
          </div>

          {/* 评论 */}
          <section className="mt-6">
            <h2 className="mb-3 text-sm font-semibold text-ink">
              评论 <span className="text-ink-mute">{comments.data?.total ?? 0}</span>
            </h2>

            {user ? (
              <form onSubmit={submitComment} className="mb-5">
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="友善评论，让作者感受到支持…"
                  rows={3}
                  maxLength={600}
                  className="input resize-none"
                />
                <div className="mt-2 flex items-center justify-between">
                  <span className="text-xs text-ink-mute">{comment.length}/600</span>
                  <button
                    type="submit"
                    disabled={posting || !comment.trim()}
                    className="btn-primary"
                  >
                    {posting ? <Spinner className="border-white/40 border-t-white" /> : '发布评论'}
                  </button>
                </div>
              </form>
            ) : (
              <div className="mb-5 rounded-lg bg-surface-subtle px-4 py-3 text-xs text-ink-mute">
                <Link to="/login" className="font-medium text-brand-600 hover:underline">
                  登录
                </Link>{' '}
                后参与评论
              </div>
            )}

            {comments.isLoading ? (
              <div className="space-y-3">
                {[0, 1].map((i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : comments.data?.items?.length ? (
              <ul className="space-y-4">
                {comments.data.items.map((c) => (
                  <CommentItem key={c.id} comment={c} videoId={videoId} />
                ))}
              </ul>
            ) : (
              <p className="py-8 text-center text-sm text-ink-mute">还没有评论，来说两句？</p>
            )}
          </section>
        </div>

        {/* 侧栏：AI 审核结论（可解释性展示） */}
        <aside className="space-y-4">
          <div className="card p-4">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-ink">
              🛡️ 内容审核
            </h3>
            <p className="mt-2 text-xs leading-relaxed text-ink-mute">
              本作品经多模态审核 Agent 检查：画面、文字、元信息三路证据综合判定。
            </p>
            <div className="mt-3 flex items-center gap-2">
              <Badge
                tone={
                  video.audit_result === 'visible'
                    ? 'success'
                    : video.audit_result === 'rejected'
                      ? 'danger'
                      : 'warn'
                }
              >
                {video.audit_result === 'visible'
                  ? '已通过'
                  : video.audit_result === 'rejected'
                    ? '未通过'
                    : '待审核'}
              </Badge>
              <Link to="/studio" className="text-xs text-brand-600 hover:underline">
                查看 AI 工坊 →
              </Link>
            </div>
          </div>

          <div className="card p-4">
            <h3 className="text-sm font-semibold text-ink">作者</h3>
            <div className="mt-3 flex items-center gap-3">
              {video.author_avatar ? (
                <img src={video.author_avatar} alt="" className="h-10 w-10 rounded-full object-cover" />
              ) : (
                <span className="flex h-10 w-10 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-600">
                  {video.author.slice(0, 1)}
                </span>
              )}
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">{video.author}</p>
                <Link
                  to={`/u/${video.author_id ?? ''}`}
                  className="text-xs text-ink-mute hover:text-brand-600"
                >
                  查看主页
                </Link>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}

function CommentItem({ comment, videoId }: { comment: Comment; videoId: number }) {
  return (
    <li className="flex gap-3">
      {comment.avatar ? (
        <img src={comment.avatar} alt="" className="h-8 w-8 shrink-0 rounded-full object-cover" />
      ) : (
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-subtle text-xs font-semibold text-ink-soft">
          {comment.author.slice(0, 1)}
        </span>
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium text-ink">{comment.author}</span>
          <span className="text-[11px] text-ink-mute">{comment.created_at.slice(0, 16)}</span>
        </div>
        <p className="mt-1 whitespace-pre-wrap break-words text-sm leading-relaxed text-ink-soft">
          {comment.reply_to && (
            <span className="text-brand-600">回复 @{comment.reply_to}：</span>
          )}
          {comment.content}
        </p>
        {comment.replies.length > 0 && (
          <ul className="mt-3 space-y-3 border-l-2 border-ink-line/70 pl-3">
            {comment.replies.map((r) => (
              <li key={r.id} className="text-sm">
                <span className="text-xs font-medium text-ink">{r.author}</span>
                <span className="ml-2 text-[11px] text-ink-mute">
                  {r.created_at.slice(0, 16)}
                </span>
                <p className="mt-0.5 break-words text-ink-soft">
                  {r.reply_to && <span className="text-brand-600">@{r.reply_to} </span>}
                  {r.content}
                </p>
              </li>
            ))}
          </ul>
        )}
        <input type="hidden" value={videoId} />
      </div>
    </li>
  )
}
