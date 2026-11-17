import assert from 'node:assert/strict'
import { test } from 'node:test'

// run.js drives a whole upgrade (PyPI, two servers) and cannot run in CI, but a
// named import of something another module no longer exports only fails when
// the file is loaded. Loading it here, without running main, catches that.
test('the upgrade journey loads: every name it imports exists', async () => {
  const journey = await import('./run.js')
  assert.equal(typeof journey.main, 'function')
})

test('the old-release helpers it needs are exported', async () => {
  const previous = await import('./previous-release.js')
  assert.equal(typeof previous.categorise, 'function')
})
