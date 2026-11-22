import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ConnectBar, ErrorBoundary, ResultCard, Trace } from '../components'
import { streamRun } from '../api'
import { useApiKey } from '../session'
import { renderHook, act } from '@testing-library/react'
import { foldEvents } from '../trace'
import type { TraceEvent } from '../types'

const ev = (e: object, seq = 1) => ({ seq, ts: 0, ...e }) as TraceEvent
const HOSTILE = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>'

describe('XSS safety: attacker-controlled strings render as text', () => {
  it('trace, prompt, doc title and rationale', () => {
    const t = foldEvents([
      ev({ type: 'document', url: 'https://e.test/?q=' + HOSTILE, domain: HOSTILE, title: HOSTILE, flags: [HOSTILE], chars: 1 }),
      ev({ type: 'check', doc: 'https://e.test/?q=' + HOSTILE, id: 'sanitize.x', status: 'warn', detail: HOSTILE, title: HOSTILE, defends: HOSTILE }),
      ev({ type: 'analyst_prompt', system: HOSTILE, user: HOSTILE }),
      ev({ type: 'tool_call', tool: 'fetch_page', args: { url: HOSTILE } }),
      ev({ type: 'error', code: 'x', message: HOSTILE }),
      ev({ type: 'result', result: { label: 'negative', score: -1, confidence: 1, rationale: HOSTILE, per_source: [{ url: HOSTILE, label: 'negative', score: -1 }], injection_suspected: false }, documents: [] }),
    ])
    const { container } = render(<Trace t={t} running={false} />)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('script')).toBeNull()
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined()
    expect(container.textContent).toContain('<img src=x onerror=')
  })

  it('result card', () => {
    const { container } = render(<ResultCard r={{ label: 'mixed', score: 0, confidence: 0.5, rationale: HOSTILE, per_source: [], injection_suspected: true }} />)
    expect(container.querySelector('img,script')).toBeNull()
    expect(screen.getByText(/injection suspected/)).toBeInTheDocument()
  })
})

describe('ConnectBar', () => {
  it('stores the key only after a successful check, and shows failure otherwise', async () => {
    const setKey = vi.fn()
    const check = vi.fn().mockRejectedValueOnce(new Error('invalid API key')).mockResolvedValueOnce(undefined)
    render(<ConnectBar apiKey="" setKey={setKey} check={check} />)
    const input = screen.getByLabelText('API key')
    expect(input).toHaveAttribute('type', 'password')
    await userEvent.type(input, 'bad')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid API key')
    expect(setKey).not.toHaveBeenCalled()
    await userEvent.clear(input); await userEvent.type(input, 'good')
    await userEvent.click(screen.getByRole('button', { name: 'Connect' }))
    await waitFor(() => expect(setKey).toHaveBeenCalledWith('good'))
  })
  it('does not render the key once connected', () => {
    render(<ConnectBar apiKey="super-secret" setKey={() => {}} check={async () => {}} />)
    expect(document.body.textContent).not.toContain('super-secret')
    expect(screen.getByText(/Connected/)).toBeInTheDocument()
  })
})

describe('session key storage', () => {
  beforeEach(() => { sessionStorage.clear(); localStorage.clear() })
  it('uses sessionStorage, never localStorage, and clears on empty', () => {
    const { result } = renderHook(() => useApiKey())
    act(() => result.current[1]('k1'))
    expect(result.current[0]).toBe('k1')
    expect(Object.values(sessionStorage)).toContain('k1')
    expect(localStorage.length).toBe(0)
    act(() => result.current[1](''))
    expect(sessionStorage.length).toBe(0)
  })
  it('still works if storage throws', () => {
    const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    const { result } = renderHook(() => useApiKey())
    act(() => result.current[1]('mem'))
    expect(result.current[0]).toBe('mem')
    spy.mockRestore()
  })
})

describe('streamRun', () => {
  const sseBody = (chunks: string[]) => new ReadableStream<Uint8Array>({
    start(c) { const enc = new TextEncoder(); chunks.forEach((x) => c.enqueue(enc.encode(x))); c.close() },
  })

  it('sends the key header and delivers events across chunk boundaries', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(sseBody(['data: {"seq":1,"type":"stage","stage":"gather","status":"start"}\n', '\ndata: {"seq":2,"type":"done"}\n\n']), { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)
    const got: TraceEvent[] = []
    await streamRun('K', { urls: ['u'], subject: 's' }, (e) => got.push(e), new AbortController().signal)
    expect(got.map((e) => e.type)).toEqual(['stage', 'done'])
    const init = fetchMock.mock.calls[0][1] as RequestInit
    expect((init.headers as Record<string, string>)['X-API-Key']).toBe('K')
    expect(JSON.parse(init.body as string)).toEqual({ urls: ['u'], subject: 's' })
  })

  it('reports a stream that ends without a done event instead of stopping silently', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(sseBody(['data: {"seq":1,"type":"stage","stage":"gather","status":"start"}\n\n']), { status: 200 })))
    const got: TraceEvent[] = []
    await expect(streamRun('K', { urls: ['u'] }, (e) => got.push(e), new AbortController().signal)).rejects.toThrow(/ended before the analysis finished/)
    expect(got).toHaveLength(1)   // events received before the cut were still delivered
  })

  it('reports an interrupted connection', async () => {
    const broken = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new TextEncoder().encode('data: {"seq":1,"type":"stage","stage":"gather","status":"start"}\n\n')); c.error(new TypeError('network error')) } })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(broken, { status: 200 })))
    await expect(streamRun('K', { urls: ['u'] }, () => {}, new AbortController().signal)).rejects.toThrow(/interrupted/)
  })

  it('surfaces HTTP errors with status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'invalid API key' }), { status: 401 })))
    await expect(streamRun('K', { urls: [] }, () => {}, new AbortController().signal)).rejects.toMatchObject({ status: 401, message: 'invalid API key' })
  })
})


describe('ErrorBoundary', () => {
  it('shows the error instead of a blank page', () => {
    const Boom = () => { throw new Error('kaboom ' + HOSTILE) }
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const { container } = render(<ErrorBoundary><Boom /></ErrorBoundary>)
    spy.mockRestore()
    expect(screen.getByRole('alert')).toHaveTextContent('kaboom')
    expect(container.querySelector('img,script')).toBeNull()
  })
})
