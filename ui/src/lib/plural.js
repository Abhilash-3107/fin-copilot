// "1 payment", "2 payments". Regular English plurals only: pass `plural` for
// anything irregular.
export function count(n, singular, plural = `${singular}s`) {
  return `${n} ${n === 1 ? singular : plural}`
}
