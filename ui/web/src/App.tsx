import { useCallback, useState } from 'react'
import { api } from './api'
import { ConnectBar } from './components'
import { LabView } from './LabView'
import { ResearchView } from './ResearchView'
import { useApiKey } from './session'

export default function App() {
  const [key, setKey] = useApiKey()
  const [tab, setTab] = useState<'research' | 'lab'>('research')
  const [notice, setNotice] = useState('')
  const onAuthError = useCallback(() => { setKey(''); setNotice('The API key was rejected. Please reconnect.') }, [setKey])

  return (
    <div className="app">
      <header>
        <h1>Sentiment Lab</h1>
        <ConnectBar apiKey={key} setKey={(k) => { setNotice(''); setKey(k) }} check={async (k) => { await api(k).check() }} />
      </header>
      {notice ? <div className="error" role="alert">{notice}</div> : null}
      {key ? (
        <>
          <nav role="tablist">
            <button role="tab" aria-selected={tab === 'research'} onClick={() => setTab('research')}>Research</button>
            <button role="tab" aria-selected={tab === 'lab'} onClick={() => setTab('lab')}>Injection lab</button>
          </nav>
          {tab === 'research' ? <ResearchView apiKey={key} onAuthError={onAuthError} /> : <LabView apiKey={key} onAuthError={onAuthError} />}
        </>
      ) : <p className="muted">Enter the agent API key to start. It is kept in this tab’s session storage only.</p>}
    </div>
  )
}
