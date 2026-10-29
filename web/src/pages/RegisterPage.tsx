import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '../store/auth'
import { Spinner } from '../components/ui'

export function RegisterPage() {
  const { register, login } = useAuth()
  const navigate = useNavigate()
  const [form, setForm] = useState({
    username: '',
    email: '',
    password: '',
    confirm: '',
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (form.password !== form.confirm) {
      setError('两次输入的密码不一致')
      return
    }
    setLoading(true)
    try {
      await register(form)
      await login(form.username.trim(), form.password)
      navigate('/')
    } catch (err) {
      setError(err instanceof Error ? err.message : '注册失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-surface-page px-5 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 text-center">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-brand-500 text-base font-bold text-white">
            校
          </div>
          <h2 className="mt-4 text-2xl font-semibold text-ink">创建账号</h2>
          <p className="mt-1.5 text-sm text-ink-mute">加入校园创作社区，让作品被看见</p>
        </div>

        <form onSubmit={submit} className="card space-y-4 p-7">
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-soft">用户名</label>
            <input className="input h-11" value={form.username} onChange={set('username')}
              placeholder="2~40 个字符" required />
          </div>
          <div>
            <label className="mb-1.5 block text-xs font-medium text-ink-soft">邮箱</label>
            <input className="input h-11" type="email" value={form.email} onChange={set('email')}
              placeholder="用于找回账号" required />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-soft">密码</label>
              <input className="input h-11" type="password" value={form.password}
                onChange={set('password')} placeholder="至少 6 位" required />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-medium text-ink-soft">确认密码</label>
              <input className="input h-11" type="password" value={form.confirm}
                onChange={set('confirm')} placeholder="再输一次" required />
            </div>
          </div>

          {error && (
            <div className="rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">{error}</div>
          )}

          <button type="submit" disabled={loading} className="btn-primary h-11 w-full">
            {loading ? <Spinner className="border-white/40 border-t-white" /> : '注册并登录'}
          </button>

          <p className="pt-1 text-center text-sm text-ink-mute">
            已有账号？
            <Link to="/login" className="ml-1 font-medium text-brand-600 hover:underline">
              直接登录
            </Link>
          </p>
        </form>
      </div>
    </div>
  )
}
