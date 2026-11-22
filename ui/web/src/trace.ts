import type { CheckEv, DocumentMeta, SentimentResult, Status, TraceEvent } from './types'

export interface DocTrace {
  url: string; domain?: string; title?: string | null; flags: string[]; chars?: number
  checks: CheckEv[]; blocked: boolean
}
export interface ToolCallTrace {
  id: number; tool: string; args: Record<string, unknown>
  status: 'running' | 'ok' | 'error'; ms?: number; nDocs?: number
}
export interface TraceState {
  count: number
  stages: Record<string, 'start' | 'end'>
  request: CheckEv[]
  toolCalls: ToolCallTrace[]
  docs: DocTrace[]
  analyst: { config?: { model: string; tools_bound: number; structured_output: string; temperature: number }; checks: CheckEv[]; prompt?: { system: string; user: string } }
  output: CheckEv[]
  notes: string[]
  step?: { step: number; limit: number }
  result?: { result: SentimentResult; documents: DocumentMeta[] }
  error?: { code: string; message: string }
  done: boolean
}

export const emptyTrace = (): TraceState => ({
  count: 0, stages: {}, request: [], toolCalls: [], docs: [], analyst: { checks: [] }, output: [], notes: [], done: false,
})

const worst = (checks: { status: Status }[]): Status =>
  checks.some((c) => c.status === 'block') ? 'block' : checks.some((c) => c.status === 'warn') ? 'warn' : 'pass'
export { worst }

function docFor(state: TraceState, url: string): DocTrace {
  let d = state.docs.find((x) => x.url === url)
  if (!d) { d = { url, flags: [], checks: [], blocked: false }; state.docs = [...state.docs, d] }
  return d
}

/** Pure reducer: folds one streamed event into the trace view-model. */
export function applyEvent(prev: TraceState, ev: TraceEvent): TraceState {
  const s: TraceState = {
    ...prev, count: prev.count + 1, stages: { ...prev.stages }, toolCalls: [...prev.toolCalls],
    docs: prev.docs.map((d) => ({ ...d, checks: [...d.checks] })), request: [...prev.request], output: [...prev.output],
    analyst: { ...prev.analyst, checks: [...prev.analyst.checks] }, notes: [...prev.notes],
  }
  switch (ev.type) {
    case 'stage': s.stages[ev.stage] = ev.status; break
    case 'tool_call': s.toolCalls.push({ id: s.toolCalls.length + 1, tool: ev.tool, args: ev.args, status: 'running' }); break
    case 'tool_result': {
      const i = s.toolCalls.findIndex((t) => t.tool === ev.tool && t.status === 'running')
      if (i >= 0) s.toolCalls[i] = { ...s.toolCalls[i], status: ev.ok ? 'ok' : 'error', ms: ev.ms, nDocs: ev.n_docs }
      break
    }
    case 'document': {
      const d = docFor(s, ev.url)
      d.domain = ev.domain; d.title = ev.title; d.flags = ev.flags; d.chars = ev.chars
      break
    }
    case 'check':
      if (ev.doc) {
        const d = docFor(s, ev.doc)
        d.checks.push(ev)
        if (ev.status === 'block') d.blocked = true
      } else if (ev.id.startsWith('request.')) s.request.push(ev)
      else if (ev.id.startsWith('analyst.')) s.analyst.checks.push(ev)
      else s.output.push(ev)
      break
    case 'analyst_config': s.analyst.config = { model: ev.model, tools_bound: ev.tools_bound, structured_output: ev.structured_output, temperature: ev.temperature }; break
    case 'analyst_prompt': s.analyst.prompt = { system: ev.system, user: ev.user }; break
    case 'agent_step': s.step = { step: ev.step, limit: ev.limit }; break
    case 'note': s.notes.push(ev.message); break
    case 'result': s.result = { result: ev.result, documents: ev.documents }; break
    case 'error': s.error = { code: ev.code, message: ev.message }; break
    case 'done': s.done = true; break
  }
  return s
}

export const foldEvents = (events: TraceEvent[]): TraceState => events.reduce(applyEvent, emptyTrace())
