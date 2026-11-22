export type Label = 'positive' | 'negative' | 'neutral' | 'mixed'
export const LABELS: Label[] = ['positive', 'negative', 'neutral', 'mixed']
export type Status = 'pass' | 'warn' | 'block'

interface Base { seq: number; ts: number }
export interface CheckEv extends Base { type: 'check'; doc: string | null; id: string; status: Status; detail: string; title: string; defends: string }
export interface StageEv extends Base { type: 'stage'; stage: string; status: 'start' | 'end'; n_docs?: number }
export interface ToolCallEv extends Base { type: 'tool_call'; tool: string; args: Record<string, unknown> }
export interface ToolResultEv extends Base { type: 'tool_result'; tool: string; ok: boolean; ms: number; n_docs: number }
export interface DocumentEv extends Base { type: 'document'; url: string; domain: string; title: string | null; flags: string[]; chars: number }
export interface AnalystConfigEv extends Base { type: 'analyst_config'; model: string; tools_bound: number; structured_output: string; temperature: number }
export interface AnalystPromptEv extends Base { type: 'analyst_prompt'; system: string; user: string }
export interface AgentStepEv extends Base { type: 'agent_step'; step: number; limit: number }
export interface NoteEv extends Base { type: 'note'; message: string }
export interface ErrorEv extends Base { type: 'error'; code: string; message: string }
export interface DoneEv extends Base { type: 'done' }

export interface SourceSentiment { url: string; label: Label; score: number }
export interface SentimentResult {
  label: Label; score: number; confidence: number; rationale: string
  per_source: SourceSentiment[]; injection_suspected: boolean
}
export interface DocumentMeta { source_url: string; domain: string; title: string | null; flags: string[] }
export interface ResultEv extends Base { type: 'result'; result: SentimentResult; documents: DocumentMeta[] }

export type TraceEvent =
  | CheckEv | StageEv | ToolCallEv | ToolResultEv | DocumentEv | AnalystConfigEv
  | AnalystPromptEv | AgentStepEv | NoteEv | ErrorEv | DoneEv | ResultEv

export interface Page {
  id: string; name: string; technique: string; expected_label: Label; notes: string
  seeded: boolean; updated_at: string; url: string; html?: string
}
export interface PageInput { name: string; html: string; technique: string; expected_label: Label; notes: string }
export interface Payload { id: string; technique: string; target_label: Label; text: string }

export interface PreviewResult {
  title: string | null; text: string
  sanitization: { truncated: boolean; flags: string[] }
  checks: { id: string; status: Status; detail: string; title?: string; defends?: string }[]
}

export interface RunInput { urls: string[]; query?: string; subject?: string }
