import { useAuth } from '../store/auth'
import { useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, initialized } = useAuth()
  const navigate = useNavigate()

  if (!initialized) {
    return (
      <div className="mx-auto max-w-6xl px-4 py-20 text-center text-sm text-ink-mute">
        正在确认登录状态…
      </div>
    )
  }
  if (!user) {
    return (
      <div className="mx-auto max-w-md px-4 py-24 text-center">
        <div className="card p-10">
          <div className="mb-3 text-3xl">🔒</div>
          <h2 className="text-lg font-semibold text-ink">这个页面需要登录</h2>
          <p className="mt-2 text-sm text-ink-mute">
            登录后即可投稿、评论、收藏，并使用 AI 创作工坊。
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <button className="btn-primary" onClick={() => navigate('/login')}>
              去登录
            </button>
            <button className="btn-ghost" onClick={() => navigate('/register')}>
              注册账号
            </button>
          </div>
        </div>
      </div>
    )
  }
  return <>{children}</>
}
