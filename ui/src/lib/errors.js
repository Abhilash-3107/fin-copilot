// What to tell a person about a failed request, beyond "it failed".
// Duck-typed on `status` so it also works for errors thrown by test doubles.
export function errorReason(err) {
  if (!err) return ''
  if (typeof err.status === 'number') {
    // A refused request has a reason worth showing; a server crash only has a stack-ish string.
    return err.status >= 500 ? '' : String(err.message ?? '')
  }
  return "Finance Copilot isn't answering."
}
