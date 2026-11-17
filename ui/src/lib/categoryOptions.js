// The choices a person can sort a payment into: every category, and every
// subcategory under it. Uncategorized is the absence of an answer, so it is
// never one of them.
const NOT_AN_ANSWER = 'Uncategorized'
const MAX_MATCHES = 8

export const labelOf = option => (option.subcategory ? `${option.category} > ${option.subcategory}` : option.category)

/** Flatten the category tree ([{id, name, parent_id}]) into pickable options, parents first. */
export function categoryOptions(tree) {
  const roots = tree.filter(c => !c.parent_id && c.name !== NOT_AN_ANSWER)
  return roots.flatMap(root => [
    { category: root.name, subcategory: null },
    ...tree.filter(c => c.parent_id === root.id).map(c => ({ category: root.name, subcategory: c.name })),
  ])
}

// 0 an exact name, 1 a name that starts with the first word, 2 a word of a name
// starts with it, 3 anywhere else.
function rank(option, words) {
  const name = (option.subcategory ?? option.category).toLowerCase()
  if (words.length === 1 && name === words[0]) return 0
  if (name.startsWith(words[0])) return 1
  if (name.split(/[\s&]+/).some(w => w.startsWith(words[0]))) return 2
  return 3
}

/** Options whose names contain every word typed ("food caf" finds Food & Dining > Cafes), best first. */
export function matchCategories(options, query) {
  const words = query.toLowerCase().split(/[\s>]+/).filter(Boolean)
  if (words.length === 0) return []
  return options
    .map((option, order) => ({ option, order, haystack: labelOf(option).toLowerCase() }))
    .filter(({ haystack }) => words.every(w => haystack.includes(w)))
    .sort((a, b) => rank(a.option, words) - rank(b.option, words) || a.order - b.order)
    .slice(0, MAX_MATCHES)
    .map(({ option }) => option)
}
