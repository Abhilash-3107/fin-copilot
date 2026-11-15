// Comparing two snapshots of one ledger, and holding every difference against
// the list of changes a release makes on purpose (expected-changes.json).
//
// A difference has a path such as
//   transactions[demo_202611_044].counterparty_key
//   months[2026-11].breakdown[Food & Dining].net
// Arrays are keyed by what identifies a row, not by position, so a row that
// moved is not a change; a row that appeared or vanished is one difference at
// the row's own path, not one per field. An array of plain values is a set.

const KEYS = ['id', 'transaction_id', 'counterparty_key', 'category', 'name']

// A key must not contain what the path syntax uses.
const safe = (key) => key.replace(/[.[\]]/g, '_')

function rowKey(row, index) {
  if (row === null || typeof row !== 'object') return safe(String(row))
  for (const key of KEYS) {
    if (typeof row[key] === 'string' && row[key] !== '') return safe(row[key])
  }
  return String(index)
}

/** An array as a map from row key to row. A repeated key gets a suffix, so no row is merged away. */
function keyed(rows) {
  const out = new Map()
  rows.forEach((row, index) => {
    const base = rowKey(row, index)
    let key = base
    for (let n = 2; out.has(key); n++) key = `${base}#${n}`
    out.set(key, row)
  })
  return out
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

/** @returns {{path: string, kind: 'added'|'removed'|'changed', from?: unknown, to?: unknown}[]} */
export function diff(before, after, path = '') {
  if (Array.isArray(before) && Array.isArray(after)) {
    const a = keyed(before)
    const b = keyed(after)
    const changes = []
    for (const [key, row] of a) {
      if (!b.has(key)) changes.push({ path: `${path}[${key}]`, kind: 'removed', from: row })
      else changes.push(...diff(row, b.get(key), `${path}[${key}]`))
    }
    for (const [key, row] of b) if (!a.has(key)) changes.push({ path: `${path}[${key}]`, kind: 'added', to: row })
    return changes
  }
  if (isObject(before) && isObject(after)) {
    const changes = []
    const join = (key) => (path === '' ? key : `${path}.${key}`)
    for (const key of Object.keys(before)) {
      if (!(key in after)) changes.push({ path: join(key), kind: 'removed', from: before[key] })
      else changes.push(...diff(before[key], after[key], join(key)))
    }
    for (const key of Object.keys(after)) if (!(key in before)) changes.push({ path: join(key), kind: 'added', to: after[key] })
    return changes
  }
  return same(before, after) ? [] : [{ path, kind: 'changed', from: before, to: after }]
}

/**
 * A pattern is a path in which `*` stands for one key or name and `**` for
 * anything, including nothing. `{month}` is replaced by the month the run happened in.
 */
export function patternToRegExp(pattern, vars = {}) {
  const filled = pattern.replace(/\{(\w+)\}/g, (_, name) => {
    if (!(name in vars)) throw new Error(`Unknown placeholder {${name}} in "${pattern}"`)
    return vars[name]
  })
  const source = filled
    .split('**')
    .map((part) =>
      part
        .split('*')
        .map((piece) => piece.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('[^.[\\]]*'),
    )
    .join('.*')
  return new RegExp(`^${source}$`)
}

function appends(change, tail) {
  const { from, to } = change
  return typeof from === 'string' && typeof to === 'string' && to.startsWith(from) && new RegExp(tail).test(to.slice(from.length))
}

/**
 * Sort a diff into the changes the release declares and the ones it does not.
 *
 * An entry matches a change when one of its path patterns matches (`path` is a
 * pattern or a list of them), its kind (if the entry limits it) is right, and
 * its `from` and `to` (if given) equal the old and the new value. A value that
 * is absent counts as null. `appends`, a regular expression, says the new value
 * is the old string followed by text the expression matches ("/ORDER").
 *
 * @param {object[]} changes the output of diff()
 * @param {object[]} expected entries of one scope from expected-changes.json
 * @param {Record<string, string>} vars placeholders for the patterns
 * @returns {{unexplained: object[], hits: Map<object, object[]>, unused: object[]}}
 *   `unused` is every entry that matched nothing and is not marked optional: a
 *   change the release no longer makes is a stale entry, which would otherwise
 *   hide a regression the day it comes back.
 */
export function explain(changes, expected, vars = {}) {
  const compiled = expected.map((entry) => ({ entry, regexps: [entry.path].flat().map((p) => patternToRegExp(p, vars)) }))
  const hits = new Map(expected.map((entry) => [entry, []]))
  const unexplained = []
  for (const change of changes) {
    const match = compiled.find(
      ({ entry, regexps }) =>
        regexps.some((regexp) => regexp.test(change.path)) &&
        (entry.kinds === undefined || entry.kinds.includes(change.kind)) &&
        (!('from' in entry) || same(entry.from, change.from ?? null)) &&
        (!('to' in entry) || same(entry.to, change.to ?? null)) &&
        (entry.appends === undefined || appends(change, entry.appends)),
    )
    if (match) hits.get(match.entry).push(change)
    else unexplained.push(change)
  }
  const unused = expected.filter((entry) => hits.get(entry).length === 0 && !entry.optional)
  return { unexplained, hits, unused }
}
