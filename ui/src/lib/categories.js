// Categories that represent money staying with the user (moving between their
// own accounts), not real spend or income. Excluded from spend/income totals
// and category breakdowns so self-transfers don't inflate them.
export const NON_SPEND_CATEGORIES = new Set(['Self Transfers'])

export function isRealFlow(category) {
  return !NON_SPEND_CATEGORIES.has(category ?? 'Uncategorized')
}

// "Where you shop most" is about merchant spending. On top of self-transfers,
// exclude Investments: a SIP / brokerage transfer (e.g. INDmoney) is money you
// allocated, not a merchant you shopped at, and it otherwise dominates the list.
export const NON_SHOPPING_CATEGORIES = new Set([...NON_SPEND_CATEGORIES, 'Investments'])

export function isShopping(category) {
  return !NON_SHOPPING_CATEGORIES.has(category ?? 'Uncategorized')
}
