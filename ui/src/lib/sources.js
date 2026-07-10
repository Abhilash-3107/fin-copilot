// Single vocabulary for annotation sources: pill colors and display labels.
// Every surface (transactions table, filter dropdown, annotation panel, review
// queue) renders sources through this module so the same source never shows
// up under two different names.

export const SOURCE_PILL = {
  manual:       'bg-[#14532d] text-[#86efac]',
  rule:         'bg-[#1e3a5f] text-[#7dd3fc]',
  learned_rule: 'bg-[#1e3a5f] text-[#7dd3fc]',
  rag_direct:   'bg-[#164e63] text-[#67e8f9]',
  rag_prompted: 'bg-[#164e63] text-[#67e8f9]',
  llm:          'bg-[#3b1f5e] text-[#c4b5fd]',
  model:        'bg-[#3b1f5e] text-[#c4b5fd]',
  imported:     'bg-[#292524] text-[#d6d3d1]',
  pending:      'bg-[#292524] text-[#a8a29e]',
}

const SOURCE_LABEL = {
  rag_direct:   'from history',
  rag_prompted: 'from history',
  llm:          'AI guess',
  model:        'AI guess',
  learned_rule: 'learned merchant',
  rule:         'rule',
  manual:       'manual',
  imported:     'imported',
}

export function sourceLabel(src) {
  return SOURCE_LABEL[src] ?? src ?? 'pending'
}

// Group raw source values that share a display label (rag_direct and
// rag_prompted are both "from history") into single filter options, so a
// dropdown never lists the same label twice. Each option carries every raw
// value it stands for.
export function sourceFilterOptions(sources) {
  const byLabel = new Map()
  for (const s of sources) {
    const label = sourceLabel(s)
    if (!byLabel.has(label)) byLabel.set(label, [])
    byLabel.get(label).push(s)
  }
  return [...byLabel.entries()].map(([label, values]) => ({ label, values }))
}
