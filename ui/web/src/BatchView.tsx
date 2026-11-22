import { useEffect, useRef, useState } from 'react'
import { api, ApiError, streamRun } from './api'
import { inScope, rowFromEvents, SCOPES, summarize, type BatchRow, type Scope } from './batch'
import type { Page, TraceEvent } from './types'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function BatchView({ apiKey, onAuthError }: { apiKey: string; onAuthError: () => void }) {
  const [pages, setPages] = useState<Page[]>([])
  const [scope, setScope] = useState<Scope>('attacks')
  const [subject, setSubject] = useState('Acme Widget')
  const [rows, setRows] = useState<BatchRow[]>([])
  const [running, setRunning] = useState(false)
  const [open, setOpen] = useState<string | null>(null)
  const [msg, setMsg] = useState('')
  const abort = useRef<AbortController | null>(null)

  useEffect(() => { api(apiKey).pages().then(setPages).catch(() => setPages([])) }, [apiKey])
  useEffect(() => () => abort.current?.abort(), [])

  const selected = pages.filter((p) => inScope(p, scope))
  const patch = (id: string, r: Partial<BatchRow>) => setRows((all) => all.map((x) => (x.id === id ? { ...x, ...r } : x)))

  const run = async () => {
    setMsg('')
    const ctl = (abort.current = new AbortController())
    const base: BatchRow[] = selected.map((p) => ({ id: p.id, name: p.name, technique: p.technique, expected: p.expected_label, status: 'pending', sanitizerFlags: [], blocked: [] }))
    setRows(base); setRunning(true)
    try {
      for (const b of base) {
        if (ctl.signal.aborted) break
        patch(b.id, { status: 'running' })
        const page = selected.find((p) => p.id === b.id)!
        const t0 = Date.now()
        for (let attempt = 0; ; attempt++) {
          const events: TraceEvent[] = []
          try {
            await streamRun(apiKey, { urls: [page.url], subject: subject.trim() || undefined }, (e) => events.push(e), ctl.signal)
            patch(b.id, rowFromEvents(b, events, Math.round((Date.now() - t0) / 100) / 10))
          } catch (x) {
            if (x instanceof DOMException && x.name === 'AbortError') throw x
            if (x instanceof ApiError && x.status === 401) { onAuthError(); return }
            if (x instanceof ApiError && x.status === 429 && attempt < 6) { setMsg('Rate limited, waiting 10 s…'); await sleep(10000); continue }
            // keep any events that arrived, so a cut-off run still shows what it got to
            patch(b.id, { ...rowFromEvents(b, events, Math.round((Date.now() - t0) / 100) / 10), status: 'error', error: x instanceof Error ? x.message : 'failed' })
          }
          break
        }
      }
      setMsg('')
    } catch { setMsg('Stopped') } finally { setRunning(false) }
  }

  const sum = summarize(rows)
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify({ subject, scope, summary: sum, rows }, null, 2)], { type: 'application/json' }))
    const a = document.createElement('a'); a.href = url; a.download = 'batch-results.json'; a.click(); URL.revokeObjectURL(url)
  }

  return (
    <div>
      <div className="card">
        <h3>Batch test</h3>
        <p className="muted">Runs each page end to end through the real pipeline (fetch, sanitize, analyst) and checks the analyst reached the page’s expected label without leaking its prompt. Pages are run one at a time and cost one analyst call each.</p>
        <div className="row">
          <label className="inline">Scope
            <select value={scope} onChange={(e) => setScope(e.target.value as Scope)} disabled={running}>
              {SCOPES.map((s) => <option key={s.id} value={s.id}>{s.label} ({pages.filter((p) => inScope(p, s.id)).length})</option>)}
            </select>
          </label>
          <label className="inline">Subject <input value={subject} onChange={(e) => setSubject(e.target.value)} disabled={running} /></label>
          <button onClick={run} disabled={running || selected.length === 0}>{running ? 'Running…' : `Run ${selected.length} page(s)`}</button>
          {running ? <button onClick={() => abort.current?.abort()}>Stop</button> : null}
          {rows.length > 0 && !running ? <button onClick={download}>Download results (JSON)</button> : null}
          {msg ? <span className="muted" role="status">{msg}</span> : null}
        </div>
      </div>

      {rows.length > 0 && (
        <div className="card">
          <div className="result-head" aria-live="polite">
            <strong>{sum.passed}/{sum.total - sum.errors} passed ({sum.passRate}%)</strong>
            <span>{sum.failed} failed</span><span>{sum.errors} errors</span>
            <span className="muted">{sum.finished}/{sum.total} finished</span>
            <span className="muted">sanitizer flagged {sum.sanitizerFlaggedRate}% · analyst reported injection {sum.modelFlaggedRate}%</span>
          </div>
          <table>
            <thead><tr><th>Result</th><th>Page</th><th>Expected</th><th>Analyst</th><th>Sanitizer flags</th><th>Analyst saw injection</th><th>Time</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <>
                  <tr key={r.id} className={`brow ${r.status === 'done' ? (r.pass ? 'pass' : 'fail') : r.status}`} onClick={() => setOpen(open === r.id ? null : r.id)} style={{ cursor: 'pointer' }}>
                    <td><strong>{r.status === 'done' ? (r.pass ? 'PASS' : 'FAIL') : r.status === 'error' ? 'ERROR' : r.status === 'running' ? 'running…' : '·'}</strong></td>
                    <td>{r.name}</td><td>{r.expected}</td>
                    <td>{r.label ? `${r.label} (${r.score?.toFixed(2)})` : '—'}</td>
                    <td>{r.sanitizerFlags.length ? r.sanitizerFlags.map((f) => <span key={f} className="chip">{f}</span>) : <span className="muted">none</span>}</td>
                    <td>{r.modelFlagged === undefined ? '—' : r.modelFlagged ? 'yes' : 'no'}</td>
                    <td>{r.secs !== undefined ? `${r.secs}s` : ''}</td>
                  </tr>
                  {open === r.id && (
                    <tr key={r.id + '-d'}><td colSpan={7}>
                      {r.reason ? <div className="error">{r.reason}</div> : null}
                      {r.error ? <div className="error">{r.error}</div> : null}
                      {r.blocked.length ? <div className="muted">Blocked checks: {r.blocked.join(', ')}</div> : null}
                      <p>{r.rationale ?? 'No analyst rationale (the run did not finish).'}</p>
                      <div className="muted">Technique: {r.technique || '—'}</div>
                    </td></tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
