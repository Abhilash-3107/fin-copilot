import { describe, it, expect } from 'vitest'
import { annotationSummary, summaryDuration } from './annotationSummary.js'

const base = {
  rule_matched: 3, learned_rule_annotated: 1,
  rag_direct_annotated: 2, rag_prompted_annotated: 1,
  llm_annotated: 4, llm_failed: 0, low_confidence: 1, ai_active: true, llm_error: null,
}

describe('annotationSummary', () => {
  it('reports a healthy run as done', () => {
    const s = annotationSummary(base)
    expect(s.type).toBe('success')
    expect(s.message).toBe('All done! 4 matched by rules, 3 from history, 4 by AI')
    expect(summaryDuration(s)).toBe(5000)
  })

  it('surfaces a provider outage instead of "All done!"', () => {
    const s = annotationSummary({ ...base, llm_annotated: 2, llm_failed: 6, llm_error: 'HTTP 503' })
    expect(s.type).toBe('error')
    expect(s.message).toBe(
      "The AI model isn't available (HTTP 503). 4 matched by rules, 3 from history, 2 by AI, 6 left for the next run"
    )
    expect(summaryDuration(s)).toBe(9000)
  })

  it('notes when matching from history dropped out mid-run', () => {
    const s = annotationSummary({ ...base, embed_error: 'ReadTimeout: hung' })
    expect(s.type).toBe('info')
    expect(s.message).toBe(
      'All done! 4 matched by rules, 3 from history, 4 by AI. Matching from history was unavailable (ReadTimeout: hung)'
    )
  })

  it('notes when the history-guided prompt was switched off mid-run', () => {
    const s = annotationSummary({ ...base, rag_prompted_error: 'the model timed out on 2 requests in a row' })
    expect(s.type).toBe('info')
    expect(s.message).toContain(
      'The AI stopped using your past categories as examples (the model timed out on 2 requests in a row)'
    )
  })

  it('treats no-AI as the normal state, not an error', () => {
    const s = annotationSummary({ ...base, ai_active: false, llm_failed: 5 })
    expect(s.type).toBe('success')
    expect(s.message).toBe('Done! 7 categorized by your rules and history, 6 waiting for you')
  })
})
