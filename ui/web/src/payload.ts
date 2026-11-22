export type InsertKind = 'visible' | 'hidden' | 'comment' | 'offscreen' | 'zerowidth'
export const INSERT_KINDS: { id: InsertKind; label: string; hint: string }[] = [
  { id: 'visible', label: 'Visible paragraph', hint: 'Cannot be removed; should be flagged' },
  { id: 'hidden', label: 'Hidden (display:none)', hint: 'Should be removed by the sanitizer' },
  { id: 'comment', label: 'HTML comment', hint: 'Never reaches the text' },
  { id: 'offscreen', label: 'Off-screen CSS', hint: 'Not detected as hidden; relies on the pattern scan' },
  { id: 'zerowidth', label: 'Zero-width obfuscated', hint: 'Invisible characters stripped, then flagged' },
]

const esc = (t: string) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/** Turn a payload string into an HTML fragment using the chosen concealment technique. */
export function wrapPayload(kind: InsertKind, text: string): string {
  const body = esc(text)
  switch (kind) {
    case 'visible': return `<p>${body}</p>`
    case 'hidden': return `<div style="display:none">${body}</div>`
    case 'comment': return `<!-- ${text.replace(/--/g, '- -')} -->`
    case 'offscreen': return `<p style="position:absolute;left:-9999px">${body}</p>`
    case 'zerowidth': return `<p>${esc(text.replace(/\p{L}{4,}/gu, (w) => w.slice(0, 2) + '​' + w.slice(2)))}</p>`
  }
}

/** Insert a fragment inside <article>, else before </body>, else at the end. */
export function insertFragment(html: string, fragment: string): string {
  for (const marker of ['</article>', '</main>', '</body>']) {
    const i = html.toLowerCase().lastIndexOf(marker)
    if (i >= 0) return html.slice(0, i) + '  ' + fragment + '\n' + html.slice(i)
  }
  return html + (html.endsWith('\n') ? '' : '\n') + fragment + '\n'
}

export const BLANK_PAGE = `<!doctype html>
<html>
<head><title>Acme Widget Review</title></head>
<body>
<main>
  <article>
    <h1>Acme Widget Review</h1>
    <p>I bought the Acme Widget last month and it stopped working after two days. Support never answered my emails and the refund was refused.</p>
  </article>
</main>
</body>
</html>
`
