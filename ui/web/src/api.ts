import { createSseParser } from './sse'
import type { Page, PageInput, Payload, PreviewResult, RunInput, TraceEvent } from './types'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function call<T>(key: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { 'X-API-Key': key, ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) {
    let msg = res.statusText
    try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j) } catch { /* keep statusText */ }
    throw new ApiError(res.status, msg)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

export const api = (key: string) => ({
  check: () => call<{ ok: boolean }>(key, 'GET', '/api/auth/check'),
  pages: () => call<Page[]>(key, 'GET', '/api/pages'),
  page: (id: string) => call<Page>(key, 'GET', `/api/pages/${encodeURIComponent(id)}`),
  create: (p: PageInput) => call<Page>(key, 'POST', '/api/pages', p),
  update: (id: string, p: PageInput) => call<Page>(key, 'PUT', `/api/pages/${encodeURIComponent(id)}`, p),
  remove: (id: string) => call<void>(key, 'DELETE', `/api/pages/${encodeURIComponent(id)}`),
  rotate: (id: string) => call<Page>(key, 'POST', `/api/pages/${encodeURIComponent(id)}/rotate`),
  restoreSeeds: () => call<{ added: number }>(key, 'POST', '/api/pages-restore-seeds'),
  payloads: () => call<Payload[]>(key, 'GET', '/api/lab/payloads'),
  preview: (html: string) => call<PreviewResult>(key, 'POST', '/api/sanitize-preview', { html }),
  exportPages: () => call<{ version: number; pages: PageInput[] }>(key, 'GET', '/api/export'),
  importPages: (pages: PageInput[]) => call<{ imported: number }>(key, 'POST', '/api/import', { pages }),
})

/** POST /api/run and feed each streamed trace event to `onEvent`. Resolves when the stream ends. */
export async function streamRun(key: string, input: RunInput, onEvent: (e: TraceEvent) => void, signal: AbortSignal): Promise<void> {
  const res = await fetch('/api/run', {
    method: 'POST', signal,
    headers: { 'X-API-Key': key, 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  })
  if (!res.ok || !res.body) {
    let msg = res.statusText
    try { const j = await res.json(); msg = typeof j.detail === 'string' ? j.detail : msg } catch { /* ignore */ }
    throw new ApiError(res.status, msg)
  }
  const parser = createSseParser((data) => {
    try { onEvent(JSON.parse(data) as TraceEvent) } catch { /* ignore malformed frame */ }
  })
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    parser.push(value)
  }
}
