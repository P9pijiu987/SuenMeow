export type Data = Record<string, any>
let csrf = ''
export const setCsrf = (value: string) => { csrf = value }
export async function api<T = any>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch('/api' + path, {
    method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (response.status === 401 && path !== '/auth/login') window.dispatchEvent(new Event('session-expired'))
    throw Object.assign(new Error(typeof data.detail === 'string' ? data.detail : '请求暂时失败，请稍后重试'), { status: response.status })
  }
  return data
}
