// Test fixtures: an isolated app instance per test, a model stub, a pinned
// clock, and console/network hygiene applied to every page.
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { test as base, expect } from '@playwright/test'
import { TEMP_PREFIX, serverMode, wheelBin } from './global-setup.js'
import { CHAT_MODEL, EMBED_MODEL, startStubModel } from './stub-model.js'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** The day every journey runs on. The server reads it from FC_TODAY. */
export const TODAY = '2026-11-15'

/** Name of the attachment that carries console and network problems to the reporter. */
export const HYGIENE_ATTACHMENT = 'hygiene-violations'

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
      cmd: wheelBin(),
      args: ['--data-dir', dataDir, '--port', String(port), '--no-browser'],
      cwd: os.tmpdir(),
    }
  }
  return { cmd: 'uv', args: ['run', 'python', '-m', 'finance_copilot'], cwd: repoRoot }
}

// What uv and node need to run, and nothing else. A developer's shell can set
// any setting the app reads, so the app's environment is built from this list
// and not copied from the shell.
const INHERITED = /^(PATH|HOME|USER|LOGNAME|SHELL|TMPDIR|TEMP|TMP|TERM|LANG|LC_\w+|UV_\w+|XDG_\w+|SSL_CERT_\w+|REQUESTS_CA_BUNDLE|(?:https?|all)_proxy)$/i

function serverEnv(dataDir, port, modelUrl) {
  const env = {}
  for (const [key, value] of Object.entries(process.env)) {
    if (INHERITED.test(key) && value !== undefined) env[key] = value
  }
  // Every setting that changes what the app does is set here, not left to a default.
  Object.assign(env, {
    FC_DATA_DIR: dataDir,
    PORT: String(port),
    HOST: '127.0.0.1',
    RELOAD: '0',
    FC_TODAY: TODAY,
    // A checkout reads its own .env, which would override the settings below.
    FC_IGNORE_DOTENV: '1',
    FC_DEMO_MODE: '0',
    DEMO_MODE: '0',
    API_TOKEN: '',
    DEV_MODE: 'false',
    // The model stub and the app both live on loopback, so a proxy must not sit between them.
    NO_PROXY: '127.0.0.1,localhost',
    no_proxy: '127.0.0.1,localhost',
  })
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Wait until this child, and not some other process that took the port first,
 * is serving. The child announces its own address once it is listening, so the
 * wait needs that line as well as a healthy /api/health, and it stops at once
 * if the child exits.
 */
async function waitUntilServing(child, url, port, log) {
  const announced = new RegExp(`(?:Uvicorn running on|Finance Copilot is running at) ${url.replace(/[.]/g, '\\.')}(?!\\d)`)
  const deadline = Date.now() + 90_000
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new ServerExited(child.exitCode, log.text())
    if (announced.test(log.all())) {
      try {
        const res = await fetch(`${url}/api/health`)
        if (res.ok && child.exitCode === null) return
      } catch {
        // announced but not answering yet
      }
    }
    await sleep(100)
  }
  throw new Error(`Server did not announce ${url} and become healthy within 90s (port ${port}).\n${log.text()}`)
}

class ServerExited extends Error {
  constructor(code, text) {
    super(`Server exited with code ${code} before it was serving.\n${text}`)
    this.exited = true
  }
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

/**
 * Start the app on a free port. The port is chosen before the server binds it,
 * so another process can take it in between: that shows up as the child
 * exiting, and the start is tried again on a new port.
 */
async function startServer(dataDir, modelUrl) {
  const chunks = []
  const log = { all: () => chunks.join(''), text: () => chunks.join('').slice(-4000) }
  for (let attempt = 1; ; attempt++) {
    const port = await freePort()
    const url = `http://127.0.0.1:${port}`
    const { cmd, args, cwd } = serverCommand(dataDir, port)
    const child = spawn(cmd, args, {
      cwd,
      env: serverEnv(dataDir, port, modelUrl),
      detached: true, // its own process group, so the whole tree can be killed
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.stdout.on('data', (d) => chunks.push(String(d)))
    child.stderr.on('data', (d) => chunks.push(String(d)))
    try {
      await waitUntilServing(child, url, port, log)
      return { child, url, log, chunks }
    } catch (error) {
      await killTree(child)
      if (!error.exited || attempt === 3) throw error
      chunks.push(`\n--- start attempt ${attempt} failed, trying another port ---\n`)
    }
  }
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
    patch: (route, body) => call('PATCH', route, body),
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
        server = await startServer(dataDir, stub?.url)
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
