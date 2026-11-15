// Test fixtures: an isolated app instance per test, a model stub, a pinned
// clock, and console/network hygiene applied to every page.
import { mkdtempSync, rmSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { test as base, expect } from '@playwright/test'
import { TEMP_PREFIX } from './global-setup.js'
import { makeApi, killTree, startServer } from './lib/server.js'
import { startStubModel } from './stub-model.js'

/** The day every journey runs on. The server reads it from FC_TODAY. */
export const TODAY = '2026-11-15'

/** Name of the attachment that carries console and network problems to the reporter. */
export const HYGIENE_ATTACHMENT = 'hygiene-violations'

/** Let a page finish what it is loading. A page that never goes quiet (polling) is not an error. */
async function settle(page) {
  if (page.isClosed()) return
  try {
    await page.waitForLoadState('networkidle', { timeout: 2000 })
  } catch (error) {
    if (error.name !== 'TimeoutError' && !page.isClosed()) throw error
  }
}

export const test = base.extend({
  // 'stub' points both providers at the model stub; 'none' turns both off.
  // Choose per file or describe block with test.use({ modelMode: 'none' }).
  modelMode: ['stub', { option: true }],

  app: [
    async ({ modelMode }, use, testInfo) => {
      const stub = modelMode === 'stub' ? await startStubModel() : null
      const dataDir = mkdtempSync(path.join(os.tmpdir(), TEMP_PREFIX))
      let server
      try {
        const started = Date.now()
        server = await startServer(dataDir, stub?.url, { today: TODAY })
        testInfo.annotations.push({ type: 'server-start-ms', description: String(Date.now() - started) })
        const api = makeApi(server.url)
        await use({
          url: server.url,
          dataDir,
          api,
          stub,
          /** Load the demo ledger the way the Dashboard button does. */
          loadDemo: () => api.post('/data/demo'),
        })
      } finally {
        if (server && testInfo.status !== testInfo.expectedStatus) {
          await testInfo.attach('server-log', { body: server.chunks.join(''), contentType: 'text/plain' })
        }
        if (server) await killTree(server.child)
        await stub?.close()
        rmSync(dataDir, { recursive: true, force: true })
      }
    },
    { scope: 'test', timeout: 150_000 },
  ],

  baseURL: async ({ app }, use) => use(app.url),

  // Playwright insists the first argument is a destructuring pattern, even an empty one.
  // eslint-disable-next-line no-empty-pattern
  allowedResponses: async ({}, use) => use([]),

  // Allow a known error response, e.g. tolerate(/\/api\/statements\/upload/, 422).
  tolerate: async ({ allowedResponses }, use) => {
    await use((urlPattern, status) => allowedResponses.push({ urlPattern, status }))
  },

  page: async ({ page, allowedResponses }, use, testInfo) => {
    const problems = []
    const isAllowed = (url, status) =>
      allowedResponses.some((a) => a.urlPattern.test(url) && (a.status === undefined || a.status === status))

    function watch(target) {
      target.on('console', (msg) => {
        // The failing response itself is reported below, with its URL.
        if (msg.type() === 'error' && !msg.text().startsWith('Failed to load resource')) {
          problems.push(`console error: ${msg.text()}`)
        }
      })
      target.on('pageerror', (err) => problems.push(`page error: ${err.message}`))
      target.on('requestfailed', (req) => {
        // An aborted request is the browser cancelling its own fetch on navigation.
        if (req.failure()?.errorText === 'net::ERR_ABORTED') return
        problems.push(`request failed: ${req.method()} ${req.url()} (${req.failure()?.errorText})`)
      })
      target.on('response', (res) => {
        if (res.status() >= 400 && !isAllowed(res.url(), res.status())) {
          problems.push(`HTTP ${res.status()}: ${res.request().method()} ${res.url()}`)
        }
      })
    }

    // Pages the test opens later (a popup, a new tab) get the same watch.
    const context = page.context()
    context.pages().forEach(watch)
    context.on('page', watch)

    await page.clock.setFixedTime(new Date(`${TODAY}T12:00:00+05:30`))
    await use(page)

    for (const open of context.pages()) await settle(open)
    if (problems.length > 0) {
      // A test marked as an expected failure absorbs any error, so the problems
      // also go to the reporter, which fails the run whatever the test's status.
      await testInfo.attach(HYGIENE_ATTACHMENT, { body: problems.join('\n'), contentType: 'text/plain' })
    }
    expect(problems, 'browser console and network problems').toEqual([])
  },
})

export { expect }

/**
 * Mark the running test as an expected failure until the fix lands.
 * CI stays green while the bug exists and goes red the moment the fix makes
 * the test pass, which is the cue to delete this call.
 * E2E_SHOW_FAILURES=1 skips the marker so the real failure message shows.
 */
export function expectedFailure(reason) {
  test.fail(!process.env.E2E_SHOW_FAILURES, reason)
}
