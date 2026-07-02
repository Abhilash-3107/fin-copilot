// Categories that represent money staying with the user (moving between their
// own accounts), not real spend or income. Excluded from spend/income totals
// and category breakdowns so self-transfers don't inflate them.
export const NON_SPEND_CATEGORIES = new Set(['Self Transfers'])

export function isRealFlow(category) {
  return !NON_SPEND_CATEGORIES.has(category ?? 'Uncategorized')
}
