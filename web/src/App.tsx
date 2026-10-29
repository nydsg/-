import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes, useLocation } from 'react-router-dom'
import { lazy, Suspense, useEffect } from 'react'
import { Header } from './components/Header'
import { RequireAuth } from './components/RequireAuth'
import { ErrorBoundary } from './components/ErrorBoundary'
import { RouteSkeleton } from './components/RouteSkeleton'
import { HomePage } from './pages/HomePage'

/**
 * 路由级代码分割。
 *
 * 首页保持静态导入（首屏需要），其余页面按需加载 —— 构建后会被拆成多个 chunk，
 * 用户第一次进入时只需要下载首屏那部分。对应赛题「懒加载、代码分割」。
 *
 * 注意：页面文件用的是具名导出（export function XxxPage），
 * 所以 lazy 里需要做一次 default 映射。
 */
const LoginPage = lazy(() =>
  import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })),
)
const RegisterPage = lazy(() =>
  import('./pages/RegisterPage').then((m) => ({ default: m.RegisterPage })),
)
const VideoPage = lazy(() =>
  import('./pages/VideoPage').then((m) => ({ default: m.VideoPage })),
)
const SearchPage = lazy(() =>
  import('./pages/SearchPage').then((m) => ({ default: m.SearchPage })),
)
const SubmitPage = lazy(() =>
  import('./pages/SubmitPage').then((m) => ({ default: m.SubmitPage })),
)
const StudioPage = lazy(() =>
  import('./pages/StudioPage').then((m) => ({ default: m.StudioPage })),
)
const ReviewPage = lazy(() =>
  import('./pages/ReviewPage').then((m) => ({ default: m.ReviewPage })),
)
const ProfilePage = lazy(() =>
  import('./pages/ProfilePage').then((m) => ({ default: m.ProfilePage })),
)
const NotFoundPage = lazy(() =>
  import('./pages/NotFoundPage').then((m) => ({ default: m.NotFoundPage })),
)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 0,
      refetchOnWindowFocus: false,
      staleTime: 30_000,
    },
  },
})

/** 路由切换回到顶部（对应赛题「路由切换流畅」） */
function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo({ top: 0, behavior: 'instant' as ScrollBehavior })
  }, [pathname])
  return null
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen">
      <Header />
      <main>{children}</main>
      <footer className="mt-10 border-t border-ink-line/70 py-6 text-center text-xs text-ink-mute">
        校园创作 · AI 内容共治社区 · 多模态审核 Agent / RAG 规范检索 / 人工复核闭环
      </footer>
    </div>
  )
}

export default function App() {
  return (
    <ErrorBoundary>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ScrollToTop />
          <Suspense fallback={<RouteSkeleton />}>
            <Routes>
              {/* 登录/注册使用独立全屏布局 */}
              <Route path="/login" element={<LoginPage />} />
              <Route path="/register" element={<RegisterPage />} />

              <Route
                path="/*"
                element={
                  <Shell>
                    <Routes>
                      <Route path="/" element={<HomePage />} />
                      <Route path="/search" element={<SearchPage />} />
                      <Route path="/video/:id" element={<VideoPage />} />
                      <Route path="/u/:id" element={<ProfilePage />} />
                      <Route
                        path="/submit"
                        element={
                          <RequireAuth>
                            <SubmitPage />
                          </RequireAuth>
                        }
                      />
                      <Route
                        path="/studio"
                        element={
                          <RequireAuth>
                            <StudioPage />
                          </RequireAuth>
                        }
                      />
                      <Route path="/review" element={<ReviewPage />} />
                      <Route path="*" element={<NotFoundPage />} />
                    </Routes>
                  </Shell>
                }
              />
            </Routes>
          </Suspense>
        </BrowserRouter>
      </QueryClientProvider>
    </ErrorBoundary>
  )
}
