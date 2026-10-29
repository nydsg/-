import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  message: string
}

/**
 * 全局错误边界。
 *
 * 为什么需要：React 18 里任何渲染期异常都会卸载整棵树，用户看到的是纯白屏。
 * 对应赛题「错误边界处理优雅」——这里给出可读错误 + 一键恢复，而不是白屏。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message || '未知错误' }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 生产环境可在这里上报到监控平台
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  private reset = () => {
    this.setState({ hasError: false, message: '' })
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <div className="mx-auto max-w-md px-4 py-24">
        <div className="card p-8 text-center">
          <div className="mb-3 text-3xl">😵</div>
          <h2 className="text-lg font-semibold text-ink">页面出了点问题</h2>
          <p className="mt-2 break-words text-xs leading-relaxed text-ink-mute">
            {this.state.message}
          </p>
          <div className="mt-6 flex justify-center gap-2">
            <button className="btn-primary" onClick={() => location.reload()}>
              重新加载
            </button>
            <button className="btn-ghost" onClick={this.reset}>
              返回尝试恢复
            </button>
          </div>
        </div>
      </div>
    )
  }
}
