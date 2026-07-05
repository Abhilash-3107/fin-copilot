// One place that owns the /transactions deep-link convention. Every surface
// that links into the filtered transaction list (dashboard rows, Money Map
// tables and bars, learned rules, accuracy panel) builds its URL here so the
// param names never drift.
//
// Recognized params: category, merchant, source, q (search), month (YYYY-MM).
export function txnFilterPath({ category, merchant, source, q, month } = {}) {
  const params = new URLSearchParams()
  if (category) params.set('category', category)
  if (merchant) params.set('merchant', merchant)
  if (source) params.set('source', source)
  if (q) params.set('q', q)
  if (month) params.set('month', month)
  const qs = params.toString()
  return qs ? `/transactions?${qs}` : '/transactions'
}
