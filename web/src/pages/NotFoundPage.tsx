import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center px-4 py-24 text-center">
      <div className="text-6xl font-bold tracking-widest text-brand-500">404</div>
      <h1 className="mt-4 text-lg font-semibold text-ink">页面不存在</h1>
      <p className="mt-2 text-sm text-ink-mute">链接可能已失效，或者地址写错了。</p>
      <Link to="/" className="btn-primary mt-6">
        返回首页
      </Link>
    </div>
  )
}
