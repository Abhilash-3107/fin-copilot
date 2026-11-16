// "1 payment", "2 payments". Regular English plurals only: pass `plural` for
// anything irregular.
// "a", "a and b", "a, b and c".
export function joinWords(words) {
  return words.length < 2 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} and ${words.at(-1)}`
}

export function count(n, singular, plural = `${singular}s`) {
  return `${n} ${n === 1 ? singular : plural}`
}
