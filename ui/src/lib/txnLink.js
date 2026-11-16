// One place that owns the /transactions deep-link convention. Every surface
// that links into the filtered transaction list (dashboard rows, Money Map
// tables and bars, learned rules, accuracy panel) builds its URL here so the
// param names never drift.
//
// Recognized params: category, merchant (the exact annotated merchant),
// payeeKeys (the grouping keys a Money Map row carries, repeated as payee_key),
// source, person (match-token), q (search), month (YYYY-MM).
export function txnFilterPath({ category, merchant, payeeKeys, source, person, q, month } = {}) {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  if (merchant) params.set('merchant', merchant)
  for (const key of payeeKeys ?? []) params.append('payee_key', key)
  if (source) params.set('source', source)
  if (person) params.set('person', person)
  if (q) params.set('q', q)
  if (month) params.set('month', month)
  const qs = params.toString()
  return qs ? `/transactions?${qs}` : '/transactions'
}
