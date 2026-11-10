// One wording for "auto-annotate finished", shared by every page that starts
// a run, so a run where the AI provider went down never reads as "All done!".

export function annotationSummary(result) {
  const fromRules = (result.rule_matched ?? 0) + (result.learned_rule_annotated ?? 0)
  const fromHistory = (result.rag_direct_annotated ?? 0) + (result.rag_prompted_annotated ?? 0)

  if (result.ai_active === false) {
    // No LLM configured: rules + history are the whole pipeline, and the
    // remainder is simply waiting for the user - the normal state, not an error.
    const waiting = (result.llm_failed ?? 0) + (result.low_confidence ?? 0)
    return {
      type: 'success',
      message: `Done! ${fromRules + fromHistory} categorized by your rules and history, ${waiting} waiting for you`,
    }
  }

  if (result.llm_error) {
    // The provider failed (unreachable, or refused the requests) and the
    // server stopped calling it; what's left stays unlabelled for the next run.
    // Labels the AI made before that still count.
    return {
      type: 'error',
      message:
        `The AI model isn't available (${result.llm_error}). ` +
        `${fromRules} matched by rules, ${fromHistory} from history, ` +
        `${result.llm_annotated ?? 0} by AI, ${result.llm_failed ?? 0} left for the next run`,
    }
  }

  return {
    type: 'success',
    message: `All done! ${fromRules} matched by rules, ${fromHistory} from history, ${result.llm_annotated ?? 0} by AI`,
  }
}

// Toast duration: errors stay up long enough to read the provider message.
export function summaryDuration(summary) {
  return summary.type === 'error' ? 9000 : 5000
}
