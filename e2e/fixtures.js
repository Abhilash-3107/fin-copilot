// Test fixtures: an isolated app instance per test, a model stub, a pinned
// clock, and console/network hygiene applied to every page.
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test as base, expect } from '@playwright/test'
import { serverMode, wheelPath } from './global-setup.js'
import { CHAT_MODEL, EMBED_MODEL, startStubModel } from './stub-model.js'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** The day every journey runs on. The server reads it from FC_TODAY. */
export const TODAY = '2026-11-15'

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

function serverCommand(dataDir, port) {
  if (serverMode() === 'wheel') {
    return {
      cmd: 'uvx',
      args: ['--from', wheelPath(), 'finance-copilot', '--data-dir', dataDir, '--port', String(port), '--no-browser'],
      cwd: os.tmpdir(),
    }
  }
  return { cmd: 'uv', args: ['run', 'python', '-m', 'finance_copilot'], cwd: repoRoot }
}

function serverEnv(dataDir, port, modelUrl) {
  const env = { ...process.env, FC_DATA_DIR: dataDir, PORT: String(port), FC_TODAY: TODAY }
  // uv warns when a different project's venv is active.
  delete env.VIRTUAL_ENV
  // Settings a developer's shell or .env might set must not leak into a journey.
  Object.assign(env, { FC_DEMO_MODE: '0', DEMO_MODE: '0', API_TOKEN: '', DEV_MODE: 'false', HOST: '127.0.0.1' })
  if (modelUrl) {
    Object.assign(env, {
      LLM_PROVIDER: 'ollama',
      EMBEDDING_PROVIDER: 'ollama',
      OLLAMA_URL: modelUrl,
      OLLAMA_MODEL: CHAT_MODEL,
      OLLAMA_EMBEDDING_MODEL: EMBED_MODEL,
    })
  } else {
    // Both providers off. The Ollama URL points at a closed port so a model
    // running on the developer's machine is never reached.
    Object.assign(env, { LLM_PROVIDER: 'none', EMBEDDING_PROVIDER: 'none', OLLAMA_URL: 'http://127.0.0.1:1' })
  }
  return env
}

async function waitForHealth(url, child, log) {
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`Server exited with code ${child.exitCode} before it was healthy.\n${log.text()}`)
    try {
      const res = await fetch(`${url}/api/health`)
      if (res.ok) return
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error(`Server did not become healthy within 90s.\n${log.text()}`)
}

function killTree(child) {
  if (child.exitCode !== null) return Promise.resolve()
  return new Promise((resolve) => {
    child.once('exit', resolve)
    try {
      process.kill(-child.pid, 'SIGTERM')
    } catch {
      resolve()
      return
    }
    setTimeout(() => {
      try {
        process.kill(-child.pid, 'SIGKILL')
      } catch {
        // already gone
      }
    }, 3000).unref()
  })
}

function makeApi(url) {
  async function call(method, route, body) {
    const res = await fetch(`${url}/api${route}`, {
      method,
      headers: body === undefined ? {} : { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    const text = await res.text()
    if (!res.ok) throw new Error(`${method} /api${route} -> ${res.status}: ${text}`)
    return text ? JSON.parse(text) : null
  }
  return {
    get: (route) => call('GET', route),
    post: (route, body = {}) => call('POST', route, body),
    put: (route, body) => call('PUT', route, body),
    delete: (route) => call('DELETE', route),
    /** Multipart upload of one file field, as the Add Statement page sends it. */
    async upload(route, fileName, bytes, type) {
      const form = new FormData()
      form.append('file', new Blob([bytes], { type }), fileName)
      const res = await fetch(`${url}/api${route}`, { method: 'POST', body: form })
      const text = await res.text()
      if (!res.ok) throw new Error(`POST /api${route} -> ${res.status}: ${text}`)
      return JSON.parse(text)
    },
  }
}

export const test = base.extend({
  // 'stub' points both providers at the model stub; 'none' turns both off.
  // Choose per file or describe block with test.use({ modelMode: 'none' }).
  modelMode: ['stub', { option: true }],

  app: [
    async ({ modelMode }, use, testInfo) => {
      const stub = modelMode === 'stub' ? await startStubModel() : null
      const dataDir = mkdtempSync(path.join(os.tmpdir(), 'fc-e2e-'))
      const port = await freePort()
      const url = `http://127.0.0.1:${port}`
      const { cmd, args, cwd } = serverCommand(dataDir, port)

      const chunks = []
      const log = { text: () => chunks.join('').slice(-4000) }
      const started = Date.now()
      const child = spawn(cmd, args, {
        cwd,
        env: serverEnv(dataDir, port, stub?.url),
        detached: true, // its own process group, so the whole tree can be killed
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      child.stdout.on('data', (d) => chunks.push(String(d)))
      child.stderr.on('data', (d) => chunks.push(String(d)))

      try {
        await waitForHealth(url, child, log)
        testInfo.annotations.push({ type: 'server-start-ms', description: String(Date.now() - started) })
        const api = makeApi(url)
        await use({
          url,
          dataDir,
          api,
          stub,
          /** Load the demo ledger the way the Dashboard button does. */
          loadDemo: () => api.post('/data/demo'),
        })
      } finally {
        if (testInfo.status !== testInfo.expectedStatus) {
          await testInfo.attach('server-log', { body: chunks.join(''), contentType: 'text/plain' })
        }
        await killTree(child)
        await stub?.close()
        rmSync(dataDir, { recursive: true, force: true })
      }
    },
    { scope: 'test', timeout: 150_000 },
  ],

  baseURL: async ({ app }, use) => use(app.url),

  allowedResponses: async ({}, use) => use([]),

  // Allow a known error response, e.g. tolerate(/\/api\/statements\/upload/, 422).
  tolerate: async ({ allowedResponses }, use) => {
    await use((urlPattern, status) => allowedResponses.push({ urlPattern, status }))
  },

  page: async ({ page, allowedResponses }, use) => {
    const allowed = allowedResponses
    const problems = []
    const isAllowed = (url, status) =>
      allowed.some((a) => a.urlPattern.test(url) && (a.status === undefined || a.status === status))

    page.on('console', (msg) => {
      // The failing response itself is reported below, with its URL.
      if (msg.type() === 'error' && !msg.text().startsWith('Failed to load resource')) {
        problems.push(`console error: ${msg.text()}`)
      }
    })
    page.on('pageerror', (err) => problems.push(`page error: ${err.message}`))
    page.on('requestfailed', (req) => {
      // An aborted request is the browser cancelling its own fetch on navigation.
      if (req.failure()?.errorText === 'net::ERR_ABORTED') return
      problems.push(`request failed: ${req.method()} ${req.url()} (${req.failure()?.errorText})`)
    })
    page.on('response', (res) => {
      if (res.status() >= 400 && !isAllowed(res.url(), res.status())) {
        problems.push(`HTTP ${res.status()}: ${res.request().method()} ${res.url()}`)
      }
    })

    await page.clock.setFixedTime(new Date(`${TODAY}T12:00:00+05:30`))
    await use(page)
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
