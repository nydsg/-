/** 统一 API 客户端：错误归一化 + 类型安全 */

const JSON_HEADERS = { 'Content-Type': 'application/json' }

export class ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

async function parse<T>(res: Response): Promise<T> {
  let payload: any = null
  try {
    payload = await res.json()
  } catch {
    payload = null
  }
  if (!res.ok) {
    const msg = payload?.error || payload?.message || `请求失败（${res.status}）`
    throw new ApiError(msg, res.status)
  }
  return payload as T
}

export const api = {
  async get<T>(path: string, params?: Record<string, string | number | undefined>) {
    const qs = params
      ? '?' +
        new URLSearchParams(
          Object.entries(params)
            .filter(([, v]) => v !== undefined && v !== null && v !== '')
            .map(([k, v]) => [k, String(v)]),
        ).toString()
      : ''
    const res = await fetch(`/api${path}${qs}`, { credentials: 'same-origin' })
    return parse<T>(res)
  },

  async post<T>(path: string, body?: unknown) {
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: JSON_HEADERS,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return parse<T>(res)
  },

  async patch<T>(path: string, body?: unknown) {
    const res = await fetch(`/api${path}`, {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: JSON_HEADERS,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    return parse<T>(res)
  },

  async del<T>(path: string) {
    const res = await fetch(`/api${path}`, {
      method: 'DELETE',
      credentials: 'same-origin',
    })
    return parse<T>(res)
  },

  async upload<T>(path: string, form: FormData) {
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      credentials: 'same-origin',
      body: form,
    })
    return parse<T>(res)
  },
}

/** 统一的图片兜底：加载失败时换占位图 */
export const FALLBACK_COVER = '/static/jpg/home.jpg'

export function coverOf(url?: string) {
  return url && url.trim() ? url : FALLBACK_COVER
}

export function formatViews(n?: number) {
  const v = n ?? 0
  if (v >= 10000) return `${(v / 10000).toFixed(1)}w`
  return String(v)
}

export function formatTime(iso?: string) {
  if (!iso) return ''
  const d = new Date(iso.replace(' ', 'T'))
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10)
  const diff = (Date.now() - d.getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(
    d.getDate(),
  ).padStart(2, '0')}`
}
