import assert from 'node:assert/strict'
import { test } from 'node:test'
import { diff, explain, patternToRegExp } from './diff.js'

test('rows are keyed by identity, so a reordered list is not a change', () => {
  const before = { rows: [{ id: 'a', x: 1 }, { id: 'b', x: 2 }] }
  const after = { rows: [{ id: 'b', x: 2 }, { id: 'a', x: 1 }] }
  assert.deepEqual(diff(before, after), [])
})

test('added, removed and changed values are reported with their path', () => {
  const before = { rows: [{ id: 'a', x: 1, gone: true }], same: 1 }
  const after = { rows: [{ id: 'a', x: 2, fresh: 'y' }], same: 1 }
  assert.deepEqual(diff(before, after), [
    { path: 'rows[a].x', kind: 'changed', from: 1, to: 2 },
    { path: 'rows[a].gone', kind: 'removed', from: true },
    { path: 'rows[a].fresh', kind: 'added', to: 'y' },
  ])
})

test('a row that appears or vanishes is one difference at the row, not one per field', () => {
  const row = { id: 'b', x: 2, y: 3 }
  assert.deepEqual(diff({ rows: [] }, { rows: [row] }), [{ path: 'rows[b]', kind: 'added', to: row }])
  assert.deepEqual(diff({ rows: [row] }, { rows: [] }), [{ path: 'rows[b]', kind: 'removed', from: row }])
})

test('an array of plain values is a set', () => {
  assert.deepEqual(diff({ q: ['a', 'b'] }, { q: ['c', 'b', 'a'] }), [{ path: 'q[c]', kind: 'added', to: 'c' }])
})

test('a value that changes type is one change', () => {
  assert.deepEqual(diff({ t: null }, { t: { orphans: [] } }), [{ path: 't', kind: 'changed', from: null, to: { orphans: [] } }])
})

test('keys keep the path syntax unambiguous', () => {
  assert.deepEqual(diff({ r: [] }, { r: [{ name: 'Dr. [X]' }] }).map((c) => c.path), ['r[Dr_ _X_]'])
})

test('a duplicate key is told apart, not merged', () => {
  const rows = [{ name: 'a' }, { name: 'a' }]
  assert.deepEqual(diff({ r: [] }, { r: rows }).map((c) => c.path), ['r[a]', 'r[a#2]'])
})

test('a pattern matches one key with * and anything with **', () => {
  assert.ok(patternToRegExp('t[*].category_id').test('t[demo_1].category_id'))
  assert.ok(!patternToRegExp('t[*].category_id').test('t[a].b.category_id'))
  assert.ok(patternToRegExp('months[*].**').test('months[2026-11].comparison.in'))
  assert.ok(patternToRegExp('months[{month}].in', { month: '2026-11' }).test('months[2026-11].in'))
  assert.throws(() => patternToRegExp('{nope}'), /Unknown placeholder/)
})

test('a change is explained only by an entry whose kind and old value agree', () => {
  const changes = [
    { path: 't[a].category_id', kind: 'changed', from: null, to: 'c1' },
    { path: 't[b].category_id', kind: 'changed', from: 'c0', to: 'c1' },
    { path: 't[c].amount', kind: 'changed', from: 1, to: 2 },
  ]
  const expected = [{ path: 't[*].category_id', kinds: ['changed'], from: null }]
  const { unexplained, hits, unused } = explain(changes, expected)
  assert.deepEqual(unexplained.map((c) => c.path), ['t[b].category_id', 't[c].amount'])
  assert.equal(hits.get(expected[0]).length, 1)
  assert.deepEqual(unused, [])
})

test('an entry can pin the new value as well as the old', () => {
  const changes = [
    { path: 'tidyUp', kind: 'changed', from: null, to: { orphans: [] } },
    { path: 'policies', kind: 'changed', from: null, to: [{ id: 'p' }] },
  ]
  const expected = [
    { path: 'tidyUp', from: null, to: { orphans: [] } },
    { path: 'policies', from: null, to: [] },
  ]
  assert.deepEqual(explain(changes, expected).unexplained.map((c) => c.path), ['policies'])
})

test('an entry that matches nothing is unused unless it is optional', () => {
  const expected = [{ path: 'gone' }, { path: 'maybe', optional: true }]
  assert.deepEqual(explain([], expected).unused, [{ path: 'gone' }])
})

test('an entry can list several paths', () => {
  const changes = [
    { path: 'a[1].x', kind: 'added', to: 1 },
    { path: 'b[1].y', kind: 'added', to: 1 },
    { path: 'c[1].z', kind: 'added', to: 1 },
  ]
  const { unexplained } = explain(changes, [{ path: ['a[*].x', 'b[*].y'], kinds: ['added'] }])
  assert.deepEqual(unexplained.map((c) => c.path), ['c[1].z'])
})

test('an entry can say the new value is the old one plus a word', () => {
  const change = (from, to) => ({ path: 't[a].key', kind: 'changed', from, to })
  const entry = [{ path: 't[*].key', appends: '^/[A-Za-z]+$' }]
  const unexplained = (c) => explain([c], entry).unexplained.length
  assert.equal(unexplained(change('SWIGGY', 'SWIGGY/ORDER')), 0)
  assert.equal(unexplained(change('SWIGGY', 'ZOMATO')), 1)
  assert.equal(unexplained(change('SWIGGY', 'SWIGGYX/ORDER')), 1)
  assert.equal(unexplained(change('SWIGGY', 'SWIGGY/ORDER/4001')), 1)
  assert.equal(unexplained(change('SWIGGY', 'SWIGGY/')), 1)
  assert.equal(unexplained(change(null, 'SWIGGY/ORDER')), 1)
})
