import assert from 'node:assert/strict'
import { test } from 'node:test'
import { baseVersion } from './version.js'

test('a release candidate belongs to the release it leads to', () => {
  assert.equal(baseVersion('0.3.0rc1'), '0.3.0')
  assert.equal(baseVersion('0.3.0.dev2'), '0.3.0')
  assert.equal(baseVersion('0.3.0.post1'), '0.3.0')
  assert.equal(baseVersion('0.1.3'), '0.1.3')
  assert.equal(baseVersion('1.0'), '1.0')
})

test('a release candidate of another release is not this release', () => {
  assert.notEqual(baseVersion('0.3.1rc1'), '0.3.0')
})

test('something that is not a version is refused', () => {
  assert.throws(() => baseVersion('latest'), /Not a version/)
})
