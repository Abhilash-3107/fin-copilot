// One wording for "sorting finished", shared by every page that starts a run,
// so a run where the AI provider went down never reads as a clean finish.
// Nothing from the server's error fields is quoted here: those are for the
// logs, and "Test connection" in Settings is where a provider problem is explained.
import { count } from './plural.js'

export function annotationSummary(result) {
  const fromRules = (result.rule_matched ?? 0) + (result.learned_rule_annotated ?? 0)
  const fromHistory = (result.rag_direct_annotated ?? 0) + (result.rag_prompted_annotated ?? 0)

  if (result.ai_active === false) {
    // No LLM configured: rules + history are the whole run, and the
    // remainder is simply waiting for the user - the normal state, not an error.
    const waiting = (result.llm_failed ?? 0) + (result.low_confidence ?? 0)
    return {
      type: 'success',
      message: `Sorted ${count(fromRules + fromHistory, 'payment')} with your rules and remembered payees, ${waiting} waiting for you`,
    }
  }

  if (result.llm_error) {
    // The provider failed (unreachable, or refused the requests) and the
    // server stopped calling it; what's left stays unsorted for the next run.
    // What the AI sorted before that still counts.
    return {
      type: 'error',
      message:
        "Your AI model isn't answering. " +
        `${fromRules} sorted by rules, ${fromHistory} from history, ` +
        `${result.llm_annotated ?? 0} by AI, ${result.llm_failed ?? 0} left for the next run`,
    }
  }

  const total = fromRules + fromHistory + (result.llm_annotated ?? 0)
  const done = `Sorted ${count(total, 'payment')}: ${fromRules} by rules, ${fromHistory} from history, ${result.llm_annotated ?? 0} by AI`
  // Retrieval or the history-guided prompt dropped out mid-run: still a
  // success, but part of the run got less help from your history (much less
  // accurate), so say so.
  if (result.embed_error) {
    return { type: 'info', message: `${done}. Matching similar payments was unavailable for part of this run.` }
  }
  if (result.rag_prompted_error) {
    return { type: 'info', message: `${done}. The AI stopped using your past answers as examples part-way through.` }
  }
  return { type: 'success', message: done }
}

// "41 sorted, 9 need you": the words the Add Statement page uses for a
// statement's sorting. The numbers come from the server (a statement's
// sorted_count), never from a run's counters, so this page, the statements
// table and Teach Me always agree.
export function sortedText(sorted, needYou) {
  return `${sorted} sorted, ${needYou} ${needYou === 1 ? 'needs' : 'need'} you`
}

// Toast duration: errors stay up long enough to read.
export function summaryDuration(summary) {
  return summary.type === 'error' ? 9000 : 5000
}
