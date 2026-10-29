/**
 * SSE 流式客户端。
 *
 * 为什么不用 EventSource：它只支持 GET，而我们的对话/审核需要 POST 带 body。
 * 这里用 fetch + ReadableStream 手写一个 SSE 解析器，行为与 EventSource 一致，
 * 但支持 AbortController 取消（用户点"停止生成"）。
 */

export interface StreamHandlers {
  onEvent: (event: any) => void
  onError?: (err: Error) => void
  onDone?: () => void
}

export async function streamSSE(
  url: string,
  init: RequestInit,
  handlers: StreamHandlers,
): Promise<void> {
  let finished = false
  try {
    const res = await fetch(url, {
      credentials: 'same-origin',
      ...init,
    })
    if (!res.ok || !res.body) {
      let message = `流式请求失败（${res.status}）`
      try {
        const j = await res.json()
        message = j?.error || j?.message || message
      } catch {
        /* ignore */
      }
      throw new Error(message)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 以空行分隔事件
      let idx: number
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const raw = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)
        for (const line of raw.split('\n')) {
          if (!line.startsWith('data:')) continue
          const data = line.slice(5).trim()
          if (!data) continue
          try {
            const event = JSON.parse(data)
            handlers.onEvent(event)
            if (event.type === 'done') finished = true
          } catch {
            /* 忽略心跳等非 JSON 帧 */
          }
        }
      }
    }
    if (!finished) handlers.onDone?.()
  } catch (err) {
    const e = err instanceof Error ? err : new Error(String(err))
    if (e.name === 'AbortError') {
      handlers.onDone?.()
      return
    }
    handlers.onError?.(e)
  }
}

/** 便捷封装：POST JSON 并流式接收 */
export function streamPost(
  url: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal,
) {
  return streamSSE(
    url,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body ?? {}),
      signal,
    },
    handlers,
  )
}

/** 便捷封装：POST FormData（文件上传式审核）并流式接收 */
export function streamForm(
  url: string,
  form: FormData,
  handlers: StreamHandlers,
  signal?: AbortSignal,
) {
  return streamSSE(url, { method: 'POST', body: form, signal }, handlers)
}
