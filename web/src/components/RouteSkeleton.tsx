/** 路由级懒加载的占位骨架（对应赛题「加载状态优雅」） */
export function RouteSkeleton() {
  return (
    <div className="mx-auto max-w-6xl animate-fade-up px-4 py-6">
      <div className="skeleton mb-4 h-8 w-40" />
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="overflow-hidden rounded-card border border-ink-line/60 bg-white">
            <div className="skeleton aspect-video rounded-none" />
            <div className="space-y-2 p-3">
              <div className="skeleton h-3 w-5/6" />
              <div className="skeleton h-3 w-1/3" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
