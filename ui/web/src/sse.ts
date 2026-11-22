/** Incremental parser for Server-Sent Events: feed text chunks, get one callback per `data:` frame. */
export function createSseParser(onData: (json: string) => void) {
  let buf = ''
  return {
    push(chunk: string) {
      buf += chunk.replace(/\r\n/g, '\n')
      let i: number
      while ((i = buf.indexOf('\n\n')) !== -1) {
        const frame = buf.slice(0, i)
        buf = buf.slice(i + 2)
        const data = frame
          .split('\n')
          .filter((l) => l.startsWith('data:'))   // comment lines (": keep-alive") are ignored
          .map((l) => l.slice(5).replace(/^ /, ''))
          .join('\n')
        if (data) onData(data)
      }
    },
  }
}
