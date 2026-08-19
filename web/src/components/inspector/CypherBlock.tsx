const KEYWORDS =
  /\b(MATCH|RETURN|CALL|WHERE|DISTINCT|YIELD|UNWIND|MERGE|SET|ORDER BY|LIMIT|AS|AND|OR|NOT|STARTS WITH)\b/g

// A 20-line highlighter rather than a syntax-highlighting dependency: the
// grammar we use is tiny and the whole point is to show the statement, not to
// be clever about it.
export function CypherBlock({ cypher }: { cypher: string }) {
  const html = cypher
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(KEYWORDS, '<span class="text-ember">$1</span>')
    .replace(/(\$\w+)/g, '<span class="text-verdict-clean">$1</span>')
    .replace(/('[^']*')/g, '<span class="text-chalk-dim">$1</span>')

  return (
    <pre
      className="overflow-x-auto whitespace-pre-wrap break-words rounded bg-ink-900 p-3 font-mono text-[11px] leading-relaxed text-chalk"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  )
}
