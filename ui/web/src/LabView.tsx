import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api, ApiError, streamRun } from './api'
import { CheckList, Trace } from './components'
import { BLANK_PAGE, INSERT_KINDS, insertFragment, wrapPayload, type InsertKind } from './payload'
import { applyEvent, emptyTrace } from './trace'
import { LABELS, type Label, type Page, type PageInput, type Payload, type PreviewResult, type TraceEvent } from './types'

const blank = (): PageInput => ({ name: 'New test page', html: BLANK_PAGE, technique: '', expected_label: 'negative', notes: '' })
const fromPage = (p: Page): PageInput => ({ name: p.name, html: p.html ?? '', technique: p.technique, expected_label: p.expected_label, notes: p.notes })

export function LabView({ apiKey, onAuthError }: { apiKey: string; onAuthError: () => void }) {
  const A = api(apiKey)
  const [pages, setPages] = useState<Page[]>([])
  const [payloads, setPayloads] = useState<Payload[]>([])
  const [sel, setSel] = useState<Page | null>(null)       // saved page (null = unsaved new)
  const [draft, setDraft] = useState<PageInput>(blank())
  const [dirty, setDirty] = useState(true)
  const [filter, setFilter] = useState('')
  const [preview, setPreview] = useState<PreviewResult | null>(null)
  const [subject, setSubject] = useState('Acme Widget')
  const [payloadId, setPayloadId] = useState('')
  const [kind, setKind] = useState<InsertKind>('visible')
  const [msg, setMsg] = useState('')
  const [running, setRunning] = useState(false)
  const [trace, dispatch] = useReducer((s: ReturnType<typeof emptyTrace>, e: TraceEvent | 'reset') => (e === 'reset' ? emptyTrace() : applyEvent(s, e)), emptyTrace())
  const abort = useRef<AbortController | null>(null)

  const fail = useCallback((x: unknown) => {
    if (x instanceof ApiError && x.status === 401) onAuthError()
    else setMsg(x instanceof Error ? x.message : 'request failed')
  }, [onAuthError])

  const reload = useCallback(() => api(apiKey).pages().then(setPages).catch(fail), [apiKey, fail])
  useEffect(() => { reload(); api(apiKey).payloads().then(setPayloads).catch(() => setPayloads([])) }, [apiKey, reload])
  useEffect(() => () => abort.current?.abort(), [])

  const edit = (patch: Partial<PageInput>) => { setDraft((d) => ({ ...d, ...patch })); setDirty(true) }
  const open = async (id: string) => {
    try { const p = await A.page(id); setSel(p); setDraft(fromPage(p)); setDirty(false); setPreview(null); dispatch('reset'); setMsg('') } catch (x) { fail(x) }
  }
  const startNew = () => { setSel(null); setDraft(blank()); setDirty(true); setPreview(null); dispatch('reset'); setMsg('') }

  const save = async (): Promise<Page | null> => {
    try {
      const p = sel ? await A.update(sel.id, draft) : await A.create(draft)
      setSel(p); setDirty(false); setMsg('Saved'); reload(); return p
    } catch (x) { fail(x); return null }
  }
  const remove = async () => {
    if (!sel || !confirm(`Delete "${sel.name}"?`)) return
    try { await A.remove(sel.id); startNew(); reload() } catch (x) { fail(x) }
  }
  const duplicate = () => { setSel(null); setDraft((d) => ({ ...d, name: d.name + ' (copy)' })); setDirty(true); setMsg('Duplicated: save to create a copy') }
  const rotate = async () => { if (sel) try { const p = await A.rotate(sel.id); setSel({ ...sel, url: p.url }); setMsg('Old URL revoked; new URL issued') } catch (x) { fail(x) } }
  const runPreview = async () => { try { setPreview(await A.preview(draft.html)); setMsg('') } catch (x) { fail(x) } }

  const insert = () => {
    const p = payloads.find((x) => x.id === payloadId)
    if (p) edit({ html: insertFragment(draft.html, wrapPayload(kind, p.text)) })
  }

  const runE2E = async () => {
    const saved = dirty || !sel ? await save() : sel
    if (!saved) return
    dispatch('reset'); setMsg(''); abort.current = new AbortController(); setRunning(true)
    try { await streamRun(apiKey, { urls: [saved.url], subject: subject.trim() || undefined }, dispatch, abort.current.signal) }
    catch (x) { if (!(x instanceof DOMException && x.name === 'AbortError')) fail(x) } finally { setRunning(false) }
  }

  const download = async () => {
    try {
      const data = await A.exportPages()
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const a = document.createElement('a'); a.href = url; a.download = 'lab-pages.json'; a.click(); URL.revokeObjectURL(url)
    } catch (x) { fail(x) }
  }
  const upload = async (f: File | undefined) => {
    if (!f) return
    try {
      const j = JSON.parse(await f.text()) as { pages: PageInput[] }
      const r = await A.importPages(j.pages); setMsg(`Imported ${r.imported} page(s)`); reload()
    } catch (x) { fail(x) }
  }

  const shown = pages.filter((p) => (p.name + p.technique).toLowerCase().includes(filter.toLowerCase()))
  const verdict = trace.result && sel
    ? (trace.result.result.label === draft.expected_label ? 'pass' : 'fail') : null
  const humanSrc = sel ? new URL(sel.url, window.location.origin).pathname : ''

  return (
    <div className="lab">
      <aside className="card list">
        <div className="row"><button onClick={startNew}>+ New page</button><button onClick={() => A.restoreSeeds().then((r) => { setMsg(`Restored ${r.added} seed page(s)`); reload() }).catch(fail)}>Restore seeds</button></div>
        <input placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
        <ul>
          {shown.map((p) => (
            <li key={p.id}><button className={`item ${sel?.id === p.id ? 'active' : ''}`} onClick={() => open(p.id)}>
              <span>{p.name}</span><span className="muted">{p.seeded ? 'seed' : 'mine'} · expects {p.expected_label}</span>
            </button></li>
          ))}
        </ul>
        <div className="row"><button onClick={download}>Export mine</button><label className="btn">Import<input type="file" accept="application/json" hidden onChange={(e) => upload(e.target.files?.[0])} /></label></div>
      </aside>

      <section className="editor">
        <div className="card">
          <div className="grid2">
            <label>Name<input value={draft.name} maxLength={100} onChange={(e) => edit({ name: e.target.value })} /></label>
            <label>Technique<input value={draft.technique} maxLength={60} onChange={(e) => edit({ technique: e.target.value })} /></label>
            <label>Expected label <span className="muted">(what an honest analyst should conclude)</span>
              <select value={draft.expected_label} onChange={(e) => edit({ expected_label: e.target.value as Label })}>{LABELS.map((l) => <option key={l}>{l}</option>)}</select>
            </label>
            <label>Notes<input value={draft.notes} maxLength={500} onChange={(e) => edit({ notes: e.target.value })} /></label>
          </div>
          <div className="row inserter">
            <select aria-label="Payload" value={payloadId} onChange={(e) => setPayloadId(e.target.value)}>
              <option value="">Payload…</option>{payloads.map((p) => <option key={p.id} value={p.id}>{p.technique.replace(/_/g, ' ')}</option>)}
            </select>
            <select aria-label="Concealment" value={kind} onChange={(e) => setKind(e.target.value as InsertKind)} title={INSERT_KINDS.find((k) => k.id === kind)?.hint}>
              {INSERT_KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
            </select>
            <button onClick={insert} disabled={!payloadId}>Insert into page</button>
          </div>
          <label>HTML<textarea className="code" rows={16} spellCheck={false} value={draft.html} maxLength={200000} onChange={(e) => edit({ html: e.target.value })} /></label>
          <div className="row">
            <button onClick={save} disabled={!dirty}>{sel ? 'Save' : 'Create'}</button>
            <button onClick={runPreview}>Run sanitizer (no LLM)</button>
            <label className="inline">Subject <input value={subject} onChange={(e) => setSubject(e.target.value)} /></label>
            <button onClick={runE2E} disabled={running}>{running ? 'Running…' : 'Run end-to-end'}</button>
            {running ? <button onClick={() => abort.current?.abort()}>Stop</button> : null}
            <button onClick={duplicate}>Duplicate</button>
            {sel ? <><button onClick={rotate}>Rotate URL</button><button onClick={remove} className="danger">Delete</button></> : null}
            {msg ? <span className="muted" role="status">{msg}</span> : null}
          </div>
          {sel ? <div className="muted mono wrap">Public URL (read by the fetch service): {sel.url}</div> : null}
        </div>

        <div className="panes">
          <div className="card">
            <h3>What a human sees</h3>
            {sel && !dirty ? <iframe title="Human view" sandbox="" src={humanSrc} className="human" /> : <p className="muted">Save the page to render the human view.</p>}
          </div>
          <div className="card">
            <h3>What the model gets (sanitizer output)</h3>
            {preview ? (
              <>
                <div>{preview.sanitization.flags.map((f) => <span key={f} className="chip">{f}</span>)}</div>
                <pre className="out">{preview.text}</pre>
                <CheckList checks={preview.checks} />
              </>
            ) : <p className="muted">Press “Run sanitizer” for an instant result with no LLM cost.</p>}
          </div>
        </div>

        {verdict ? (
          <div className={`verdict ${verdict}`} role="status">
            {verdict === 'pass' ? 'PASS' : 'FAIL'}: analyst said <strong>{trace.result?.result.label}</strong>, expected <strong>{draft.expected_label}</strong>
            {trace.result?.result.injection_suspected ? ' · injection was suspected' : ' · injection NOT suspected'}
          </div>
        ) : null}
        <Trace t={trace} running={running} />
      </section>
    </div>
  )
}
