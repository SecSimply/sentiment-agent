import { useCallback, useState } from 'react'

const KEY = 'sentiment-lab.api-key'

// sessionStorage can throw (private mode, blocked storage): the app must still work without it.
function read(): string {
  try { return sessionStorage.getItem(KEY) ?? '' } catch { return '' }
}
function write(v: string) {
  try { v ? sessionStorage.setItem(KEY, v) : sessionStorage.removeItem(KEY) } catch { /* in-memory only */ }
}

/** API key held in sessionStorage only (cleared when the tab closes), never localStorage, never logged. */
export function useApiKey() {
  const [key, setKeyState] = useState<string>(read)
  const setKey = useCallback((v: string) => { write(v); setKeyState(v) }, [])
  return [key, setKey] as const
}
