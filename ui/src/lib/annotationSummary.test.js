import { describe, it, expect } from 'vitest'
import { annotationSummary, sortedText, summaryDuration } from './annotationSummary.js'

const base = {
  rule_matched: 3, learned_rule_annotated: 1,
  rag_direct_annotated: 2, rag_prompted_annotated: 1,
  llm_annotated: 4, llm_failed: 0, low_confidence: 1, ai_active: true, llm_error: null,
}

describe('annotationSummary', () => {
  it('reports a healthy run as sorted', () => {
    const s = annotationSummary(base)
    expect(s.type).toBe('success')
    expect(s.message).toBe('Sorted 11 payments: 4 by rules, 3 from history, 4 by AI')
    expect(summaryDuration(s)).toBe(5000)
  })

  it('says "1 payment" for one', () => {
    const s = annotationSummary({ ...base, rule_matched: 1, learned_rule_annotated: 0, rag_direct_annotated: 0, rag_prompted_annotated: 0, llm_annotated: 0 })
    expect(s.message).toBe('Sorted 1 payment: 1 by rules, 0 from history, 0 by AI')
  })

  it('surfaces a provider outage instead of a clean finish, without quoting the provider', () => {
    const s = annotationSummary({ ...base, llm_annotated: 2, llm_failed: 6, llm_error: 'HTTP 503' })
    expect(s.type).toBe('error')
    expect(s.message).toBe(
      "Your AI model isn't answering. 4 sorted by rules, 3 from history, 2 by AI, 6 left for the next run"
    )
    expect(s.message).not.toContain('503')
    expect(summaryDuration(s)).toBe(9000)
  })

  it('notes when matching from history dropped out mid-run, without the raw error', () => {
    const s = annotationSummary({ ...base, embed_error: 'ReadTimeout: hung' })
    expect(s.type).toBe('info')
    expect(s.message).toBe(
      'Sorted 11 payments: 4 by rules, 3 from history, 4 by AI. Matching from history was unavailable for part of this run.'
    )
    expect(s.message).not.toContain('ReadTimeout')
  })

  it('notes when the history-guided prompt was switched off mid-run, without the raw error', () => {
    const s = annotationSummary({ ...base, rag_prompted_error: 'the model timed out on 2 requests in a row' })
    expect(s.type).toBe('info')
    expect(s.message).toContain('The AI stopped using your past answers as examples part-way through.')
    expect(s.message).not.toContain('timed out')
  })

  it('treats no-AI as the normal state, not an error', () => {
    const s = annotationSummary({ ...base, ai_active: false, llm_failed: 5 })
    expect(s.type).toBe('success')
    expect(s.message).toBe('Sorted 7 payments with your rules and remembered payees, 6 waiting for you')
  })
})

describe('sortedText', () => {
  it('reads "1 needs you" in the singular and "0 need you" at zero', () => {
    expect(sortedText(41, 1)).toBe('41 sorted, 1 needs you')
    expect(sortedText(8, 0)).toBe('8 sorted, 0 need you')
  })
})
