import { Component, useEffect, useState, type ErrorInfo, type ReactNode } from 'react'
import { worst, type DocTrace, type TraceState } from './trace'
import type { SentimentResult, Status } from './types'

// NOTE: everything below renders data as React text nodes. Titles, URLs, rationales and prompts contain
// attacker-controlled strings, so nothing in this app uses dangerouslySetInnerHTML or innerHTML.

const ICON: Record<Status, string> = { pass: '✓', warn: '!', block: '✕' }
const WORD: Record<Status, string> = { pass: 'PASS', warn: 'WARN', block: 'BLOCKED' }

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge ${status}`} title={WORD[status]}><span aria-hidden>{ICON[status]}</span><span className="sr"> {WORD[status]}</span></span>
}

export interface CheckLike { id: string; status: Status; detail: string; title?: string; defends?: string }

export function CheckRow({ c }: { c: CheckLike }) {
  return (
    <li className={`check ${c.status}`}>
      <StatusBadge status={c.status} />
      <div className="check-body">
        <div className="check-title">{c.title ?? c.id} <code className="id">{c.id}</code></div>
        <div className="detail">{c.detail}</div>
        {c.defends ? <details><summary>What this defends against</summary><p>{c.defends}</p></details> : null}
      </div>
    </li>
  )
}

export function CheckList({ checks }: { checks: CheckLike[] }) {
  return checks.length ? <ul className="checks">{checks.map((c, i) => <CheckRow key={c.id + i} c={c} />)}</ul> : null
}

/** Seconds since mount; ticks once a second while `active`. Shows that a silent step is still alive. */
function Elapsed({ active }: { active: boolean }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!active) return
    const t = setInterval(() => setN((v) => v + 1), 1000)
    return () => clearInterval(t)
  }, [active])
  return <>{n}s</>
}

export class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state: { error: Error | null } = { error: null }
  static getDerivedStateFromError(error: Error) { return { error } }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error('UI render error', error, info.componentStack) }
  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="error" role="alert">
        <strong>The page hit an error while rendering.</strong>
        <pre>{this.state.error.message}{'\n'}{this.state.error.stack?.split('\n').slice(0, 6).join('\n')}</pre>
        <button onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    )
  }
}

function Section({ title, state, running = false, children }: { title: string; state?: 'start' | 'end'; running?: boolean; children: React.ReactNode }) {
  return (
    <section className="stage">
      <h3>{title} {state === 'start' && running ? <span className="spin" aria-label="running">running… <Elapsed active /></span> : state === 'start' ? <span className="muted">interrupted</span> : null}</h3>
      {children}
    </section>
  )
}

function DocCard({ d, notAnalysed, limit }: { d: DocTrace; notAnalysed: boolean; limit?: number }) {
  const status = worst(d.checks)
  return (
    <details className={`doc ${status}`} open={d.blocked || status === 'warn'}>
      <summary>
        <StatusBadge status={status} />
        <span className="doc-title">{d.title || d.domain || d.url}</span>
        <span className="muted"> {d.domain ?? ''}</span>
        {d.flags.map((f) => <span key={f} className="chip">{f}</span>)}
        {notAnalysed ? <span className="chip dim" title="Fetched and checked, but over the per-run source limit, so the analyst never saw it">not analysed (limit {limit})</span> : null}
      </summary>
      <div className="muted mono">{d.url}</div>
      <CheckList checks={d.checks} />
    </details>
  )
}

export function Trace({ t, running }: { t: TraceState; running: boolean }) {
  if (t.count === 0 && !running) return null
  return (
    <div className="trace">
      {t.count === 0 ? <div className="muted" role="status">Connecting to the agent…</div> : null}
      {t.request.length > 0 && <Section title="1 · Request"><CheckList checks={t.request} /></Section>}

      <Section title="2 · Gather" state={t.stages.gather} running={running}>
        {t.step ? <div className="muted">Agent step {t.step.step} of {t.step.limit} allowed</div> : null}
        <ul className="calls">
          {t.toolCalls.map((c) => (
            <li key={c.id} className={`call ${c.status}`}>
              <code>{c.tool}</code>
              <code className="args">{JSON.stringify(c.args)}</code>
              <span className="muted">
                {c.status === 'running' ? (running ? <>running… <Elapsed active /> (searching and fetching pages can take 10-20 s)</> : 'interrupted') : `${c.status === 'ok' ? 'ok' : 'failed'} · ${c.ms} ms${c.status === 'ok' ? ` · ${c.nDocs} doc(s)` : ''}`}
              </span>
            </li>
          ))}
        </ul>
        {t.docs.map((d) => <DocCard key={d.url} d={d} notAnalysed={!!t.selection?.dropped.includes(d.url)} limit={t.selection?.limit} />)}
        {t.selection && t.selection.dropped.length > 0 ? <div className="note">Collected {t.selection.kept.length + t.selection.dropped.length} sources; the analyst read the first {t.selection.kept.length} (limit {t.selection.limit}).</div> : null}
        {t.notes.map((n, i) => <div key={i} className="note">{n}</div>)}
      </Section>

      {(t.stages.analyze || t.analyst.checks.length > 0) && (
        <Section title="3 · Analyse" state={t.stages.analyze} running={running}>
          {t.analyst.config ? (
            <div className="muted mono">
              model {t.analyst.config.model} · tools bound: {t.analyst.config.tools_bound} · output: {t.analyst.config.structured_output} · temperature {t.analyst.config.temperature}
            </div>
          ) : null}
          <CheckList checks={t.analyst.checks} />
          {t.analyst.prompt ? (
            <details className="prompt">
              <summary>Exact prompt sent to the analyst model</summary>
              <h4>System</h4><pre>{t.analyst.prompt.system}</pre>
              <h4>User (documents as inert JSON)</h4><pre>{t.analyst.prompt.user}</pre>
            </details>
          ) : null}
          <CheckList checks={t.output} />
        </Section>
      )}

      {t.error ? <div className="error" role="alert"><strong>{t.error.code}</strong>: {t.error.message}</div> : null}
      {t.result ? <ResultCard r={t.result.result} /> : null}
    </div>
  )
}

export function ResultCard({ r }: { r: SentimentResult }) {
  const pct = Math.round(((r.score + 1) / 2) * 100)
  return (
    <section className="result">
      <h3>Result</h3>
      <div className="result-head">
        <span className={`label ${r.label}`}>{r.label}</span>
        <span>score <strong>{r.score.toFixed(2)}</strong></span>
        <span>confidence <strong>{Math.round(r.confidence * 100)}%</strong></span>
        {r.injection_suspected ? <span className="chip warn">injection suspected</span> : <span className="chip ok">no injection suspected</span>}
      </div>
      <div className="meter" aria-hidden><div className="meter-fill" style={{ width: `${pct}%` }} /></div>
      <p>{r.rationale}</p>
      {r.per_source.length > 0 && (
        <table>
          <thead><tr><th>Source</th><th>Label</th><th>Score</th></tr></thead>
          <tbody>{r.per_source.map((s) => <tr key={s.url}><td className="mono wrap">{s.url}</td><td>{s.label}</td><td>{s.score.toFixed(2)}</td></tr>)}</tbody>
        </table>
      )}
    </section>
  )
}

export function ConnectBar({ apiKey, setKey, check }: { apiKey: string; setKey: (k: string) => void; check: (k: string) => Promise<void> }) {
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  if (apiKey) {
    return (
      <div className="connect ok">
        <span><StatusBadge status="pass" /> Connected (key held for this tab only)</span>
        <button onClick={() => setKey('')}>Disconnect</button>
      </div>
    )
  }
  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setErr('')
    try { await check(input.trim()); setKey(input.trim()); setInput('') }
    catch (x) { setErr(x instanceof Error ? x.message : 'connection failed') }
    finally { setBusy(false) }
  }
  return (
    <form className="connect" onSubmit={submit}>
      <label>API key
        <input type="password" autoComplete="off" spellCheck={false} value={input} onChange={(e) => setInput(e.target.value)} placeholder="agent API key" />
      </label>
      <button disabled={busy || !input.trim()}>{busy ? 'Checking…' : 'Connect'}</button>
      {err ? <span className="err" role="alert">{err}</span> : null}
    </form>
  )
}
