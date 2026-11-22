import { useEffect, useReducer, useRef, useState } from 'react'
import { api, ApiError, streamRun } from './api'
import { Trace } from './components'
import { applyEvent, emptyTrace } from './trace'
import type { Page, RunInput, TraceEvent } from './types'

export function ResearchView({ apiKey, onAuthError }: { apiKey: string; onAuthError: () => void }) {
  const [subject, setSubject] = useState('')
  const [mode, setMode] = useState<'urls' | 'query'>('query')
  const [urls, setUrls] = useState('')
  const [query, setQuery] = useState('')
  const [lab, setLab] = useState<Page[]>([])
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [trace, dispatch] = useReducer((s: ReturnType<typeof emptyTrace>, e: TraceEvent | 'reset') => (e === 'reset' ? emptyTrace() : applyEvent(s, e)), emptyTrace())
  const [running, setRunning] = useState(false)
  const [err, setErr] = useState('')
  const abort = useRef<AbortController | null>(null)

  useEffect(() => { api(apiKey).pages().then(setLab).catch(() => setLab([])) }, [apiKey])
  useEffect(() => () => abort.current?.abort(), [])

  const run = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr(''); dispatch('reset')
    const list = urls.split('\n').map((u) => u.trim()).filter(Boolean)
    const labUrls = lab.filter((p) => picked.has(p.id)).map((p) => p.url)
    const input: RunInput = {
      urls: mode === 'urls' ? [...list, ...labUrls] : labUrls,
      query: mode === 'query' ? query.trim() || undefined : undefined,
      subject: subject.trim() || undefined,
    }
    abort.current = new AbortController()
    setRunning(true)
    try { await streamRun(apiKey, input, dispatch, abort.current.signal) }
    catch (x) {
      if (x instanceof ApiError && x.status === 401) onAuthError()
      else if (!(x instanceof DOMException && x.name === 'AbortError')) setErr(x instanceof Error ? x.message : 'run failed')
    } finally { setRunning(false) }
  }

  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const canRun = !running && (mode === 'query' ? query.trim() !== '' || picked.size > 0 : urls.trim() !== '' || picked.size > 0)

  return (
    <div className="research">
      <form onSubmit={run} className="card">
        <label>Research subject <span className="muted">(what the sentiment is about)</span>
          <input value={subject} maxLength={200} onChange={(e) => setSubject(e.target.value)} placeholder="e.g. ActionSA" />
        </label>
        <fieldset className="modes">
          <legend>Sources</legend>
          <label><input type="radio" checked={mode === 'query'} onChange={() => setMode('query')} /> Search the news (agent chooses sources)</label>
          <label><input type="radio" checked={mode === 'urls'} onChange={() => setMode('urls')} /> Specific URLs</label>
        </fieldset>
        {mode === 'query'
          ? <label>Search prompt<input value={query} maxLength={300} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. ActionSA coalition talks" /></label>
          : <label>URLs <span className="muted">(one per line)</span><textarea rows={4} value={urls} onChange={(e) => setUrls(e.target.value)} spellCheck={false} placeholder="https://example.com/article" /></label>}
        {lab.length > 0 && (
          <details>
            <summary>Add injection-lab pages as sources ({picked.size} selected)</summary>
            <p className="muted">These are hosted by this app and must be publicly reachable for the fetch service to read them.</p>
            <ul className="picklist">
              {lab.map((p) => (
                <li key={p.id}><label><input type="checkbox" checked={picked.has(p.id)} onChange={() => toggle(p.id)} /> {p.name} <span className="muted">· {p.technique}</span></label></li>
              ))}
            </ul>
          </details>
        )}
        <div className="row">
          <button disabled={!canRun}>{running ? 'Running…' : 'Run analysis'}</button>
          {running ? <button type="button" onClick={() => abort.current?.abort()}>Stop</button> : null}
          {err ? <span className="err" role="alert">{err}</span> : null}
        </div>
      </form>
      <Trace t={trace} running={running} />
    </div>
  )
}
