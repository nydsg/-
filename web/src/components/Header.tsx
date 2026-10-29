import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { clsx } from 'clsx'
import { useAuth } from '../store/auth'

const NAV = [
  { to: '/', label: '首页', icon: '🏠' },
  { to: '/search', label: '发现', icon: '🔍' },
  { to: '/submit', label: '投稿', icon: '⬆️' },
  { to: '/studio', label: 'AI 工坊', icon: '✨' },
  { to: '/review', label: '审核台', icon: '🛡️', auditorOnly: true },
]

export function Header() {
  const { user, fetchMe, logout, initialized } = useAuth()
  const navigate = useNavigate()
  const [keyword, setKeyword] = useState('')
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => {
    if (!initialized) void fetchMe()
  }, [initialized, fetchMe])

  const onSearch = (e: React.FormEvent) => {
    e.preventDefault()
    const q = keyword.trim()
    navigate(q ? `/search?q=${encodeURIComponent(q)}` : '/search')
  }

  return (
    <header className="sticky top-0 z-40 border-b border-ink-line/70 bg-white/85 backdrop-blur-md">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4">
        <NavLink to="/" className="flex shrink-0 items-center gap-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-500 text-sm font-bold text-white">
            校
          </span>
          <span className="hidden text-sm font-semibold tracking-tight text-ink sm:block">
            校园创作
            <span className="ml-1 text-[11px] font-normal text-ink-mute">AI 共治</span>
          </span>
        </NavLink>

        <nav className="hidden items-center gap-1 md:flex">
          {NAV.filter((n) => !n.auditorOnly || user?.is_auditor).map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                clsx(
                  'rounded-pill px-3 py-1.5 text-sm transition-colors',
                  isActive
                    ? 'bg-brand-50 font-medium text-brand-600'
                    : 'text-ink-soft hover:bg-surface-subtle hover:text-ink',
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <form onSubmit={onSearch} className="ml-auto flex max-w-xs flex-1 items-center">
          <div className="relative w-full">
            <input
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              placeholder="搜索作品 / 标签"
              className="input h-9 rounded-pill pl-9 text-[13px]"
            />
            <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-xs text-ink-mute">
              🔍
            </span>
          </div>
        </form>

        {user ? (
          <div className="relative shrink-0">
            <button
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center gap-2 rounded-pill py-1 pl-1 pr-2 transition-colors hover:bg-surface-subtle"
            >
              {user.avatar ? (
                <img src={user.avatar} alt="" className="h-8 w-8 rounded-full object-cover" />
              ) : (
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-100 text-xs font-semibold text-brand-600">
                  {user.name.slice(0, 1)}
                </span>
              )}
              <span className="hidden max-w-[6rem] truncate text-sm text-ink-soft sm:block">
                {user.name}
              </span>
            </button>
            {menuOpen && (
              <div
                className="absolute right-0 mt-2 w-44 overflow-hidden rounded-xl border border-ink-line bg-white py-1 shadow-panel"
                onMouseLeave={() => setMenuOpen(false)}
              >
                <MenuItem onClick={() => navigate(`/u/${user.id}`)}>我的主页</MenuItem>
                <MenuItem onClick={() => navigate('/studio')}>AI 工坊</MenuItem>
                {user.is_auditor && (
                  <MenuItem onClick={() => navigate('/review')}>审核台</MenuItem>
                )}
                <div className="my-1 h-px bg-ink-line/70" />
                <MenuItem
                  onClick={async () => {
                    await logout()
                    navigate('/login')
                  }}
                >
                  退出登录
                </MenuItem>
              </div>
            )}
          </div>
        ) : (
          <div className="flex shrink-0 items-center gap-2">
            <NavLink to="/login" className="btn-ghost h-9 px-3 text-[13px]">
              登录
            </NavLink>
            <NavLink to="/register" className="btn-primary h-9 px-3 text-[13px]">
              注册
            </NavLink>
          </div>
        )}
      </div>

      {/* 移动端导航 */}
      <nav className="flex items-center gap-1 overflow-x-auto border-t border-ink-line/60 px-3 py-1.5 md:hidden">
        {NAV.filter((n) => !n.auditorOnly || user?.is_auditor).map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              clsx(
                'whitespace-nowrap rounded-pill px-3 py-1 text-xs',
                isActive ? 'bg-brand-50 text-brand-600' : 'text-ink-soft',
              )
            }
          >
            {item.icon} {item.label}
          </NavLink>
        ))}
      </nav>
    </header>
  )
}

function MenuItem({ children, onClick }: { children: React.ReactNode; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="block w-full px-4 py-2 text-left text-sm text-ink-soft transition-colors hover:bg-surface-subtle hover:text-brand-600"
    >
      {children}
    </button>
  )
}
