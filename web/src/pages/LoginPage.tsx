import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../store/auth'
import { Spinner } from '../components/ui'

export function LoginPage() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      await login(username.trim(), password)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen">
      {/* 左侧品牌区：沿用原版橙红大色块 */}
      <div className="relative hidden w-[46%] flex-col justify-between overflow-hidden bg-brand-500 p-12 text-white lg:flex">
        <div className="absolute -right-16 -top-16 h-64 w-64 rounded-full bg-white/10" />
        <div className="absolute -bottom-24 -left-10 h-72 w-72 rounded-full bg-white/10" />
        <div className="relative">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-white/20 text-lg font-bold backdrop-blur">
            校
          </div>
          <h1 className="mt-10 text-3xl font-semibold leading-snug">
            让每一次创作
            <br />
            都被认真对待
          </h1>
          <p className="mt-4 max-w-sm text-sm leading-relaxed text-white/85">
            校园作品展示社区 · 多模态 AI 审核 Agent · 可解释的内容共治
          </p>
        </div>
        <ul className="relative space-y-3 text-sm text-white/90">
          {[
            'AI Agent 逐帧审核，判定有据可查',
            'RAG 检索校规与判例，越用越准',
            '人工复核闭环，误判可申诉',
          ].map((t) => (
            <li key={t} className="flex items-center gap-2">
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-white/20 text-[11px]">
                ✓
              </span>
              {t}
            </li>
          ))}
        </ul>
      </div>

      {/* 右侧表单 */}
      <div className="flex flex-1 items-center justify-center bg-surface-page px-5 py-12">
        <div className="w-full max-w-sm">
          <div className="mb-8 lg:hidden">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-500 text-sm font-bold text-white">
              校
            </div>
          </div>
          <h2 className="text-2xl font-semibold text-ink">欢迎回来</h2>
          <p className="mt-1.5 text-sm text-ink-mute">登录后投稿、评论，并使用 AI 创作工坊</p>

          <form onSubmit={submit} className="mt-8 space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-soft">用户名</label>
              <input
                className="input h-11"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="请输入用户名"
                autoComplete="username"
                required
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-soft">密码</label>
              <input
                className="input h-11"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="请输入密码"
                autoComplete="current-password"
                required
              />
            </div>

            {error && (
              <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</div>
            )}

            <button type="submit" disabled={loading} className="btn-primary h-11 w-full">
              {loading ? <Spinner className="border-white/40 border-t-white" /> : '登录'}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-ink-mute">
            还没有账号？
            <Link to="/register" className="ml-1 font-medium text-brand-600 hover:underline">
              立即注册
            </Link>
          </p>
        </div>
      </div>
    </div>
  )
}
