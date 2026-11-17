import assert from 'node:assert/strict'
import { test } from 'node:test'
import { expectedAccounts } from './accounts-oracle.js'

const statement = (id, bank_name, account_ref = null, statement_month = '2026-01') => ({ id, bank_name, account_ref, statement_month })

test('a state bank read by the generic parser is SBI, with no name', () => {
  const out = expectedAccounts([statement('a', 'state bank (auto)', '3250508074')])
  assert.equal(out.get('a').bank, 'sbi')
  assert.equal(out.get('a').name, null)
  assert.equal(out.get('a').label, 'SBI ··8074')
})

test('state bank and sbi with one number are one account', () => {
  const out = expectedAccounts([
    statement('a', 'state bank (auto)', '3250508074'),
    statement('b', 'sbi (auto)', '3250508074', '2026-02'),
    statement('c', 'sbi', '3250508074', '2026-03'),
  ])
  assert.equal(new Set([...out.values()].map((x) => x.key)).size, 1)
})

test('a marked value is a key, never a name; unmarked text is', () => {
  const out = expectedAccounts([statement('a', 'Foo (AUTO)'), statement('b', 'Foo', null, '2026-02')])
  assert.equal(out.get('a').name, 'Foo')
  const alone = expectedAccounts([statement('a', 'Foo bank (auto)', '777')])
  assert.equal(alone.get('a').name, null)
})

test('text typed on one statement folds into the bank another statement of the number keeps', () => {
  const out = expectedAccounts([
    statement('oct', 'Salary account', 'XXXX0000', '2026-10'),
    statement('nov', 'generic', 'XXXX0000', '2026-11'),
  ])
  assert.equal(out.get('oct').key, out.get('nov').key)
  assert.equal(out.get('oct').bank, 'generic')
  assert.equal(out.get('nov').name, 'Salary account')
})
