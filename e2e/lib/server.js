// Starting and stopping the app under test as a child process, and talking to
// it over the API. Shared by the journeys (fixtures.js) and the upgrade
// journey (upgrade/run.js), which starts two different builds on one folder.
import { spawn } from 'node:child_process'
import net from 'node:net'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { serverMode, wheelBin } from '../global-setup.js'
import { CHAT_MODEL, EMBED_MODEL } from '../stub-model.js'

/** The day every journey runs on, and the upgrade journey unless FC_UPGRADE_TODAY says otherwise. The server reads it from FC_TODAY. */
export const TODAY = '2026-11-15'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')

export function freePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer()
    probe.once('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

/** How to launch the app under test: the installed wheel, or the source checkout. */
export function serverCommand(dataDir, port) {
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
// and not copied from the shell. TZ is on it so the app and node (which tells it
// what day it is) agree on where midnight falls.
const INHERITED = /^(PATH|HOME|USER|LOGNAME|SHELL|TMPDIR|TEMP|TMP|TERM|TZ|LANG|LC_\w+|UV_\w+|XDG_\w+|SSL_CERT_\w+|REQUESTS_CA_BUNDLE|(?:https?|all)_proxy)$/i

/**
 * @param {string|null} today the day the app should believe it is (FC_TODAY), or
 *   null to leave the app on the real date, as a release that predates FC_TODAY is
 */
function serverEnv(dataDir, port, modelUrl, today) {
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
  if (today) env.FC_TODAY = today
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
async function waitUntilServing(child, url, port, log, timeoutMs) {
  const announced = new RegExp(`(?:Uvicorn running on|Finance Copilot is running at) ${url.replace(/[.]/g, '\\.')}(?!\\d)`)
  const deadline = Date.now() + timeoutMs
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
  throw new Error(`Server did not announce ${url} and become healthy within ${timeoutMs / 1000}s (port ${port}).\n${log.text()}`)
}

class ServerExited extends Error {
  constructor(code, text) {
    super(`Server exited with code ${code} before it was serving.\n${text}`)
    this.exited = true
  }
}

export function killTree(child) {
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
export async function startServer(dataDir, modelUrl, { launch = serverCommand, today = null, timeoutMs = 90_000 } = {}) {
  const chunks = []
  const log = { all: () => chunks.join(''), text: () => chunks.join('').slice(-4000) }
  for (let attempt = 1; ; attempt++) {
    const port = await freePort()
    const url = `http://127.0.0.1:${port}`
    const { cmd, args, cwd } = launch(dataDir, port)
    const child = spawn(cmd, args, {
      cwd,
      env: serverEnv(dataDir, port, modelUrl, today),
      detached: true, // its own process group, so the whole tree can be killed
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    child.stdout.on('data', (d) => chunks.push(String(d)))
    child.stderr.on('data', (d) => chunks.push(String(d)))
    try {
      await waitUntilServing(child, url, port, log, timeoutMs)
      return { child, url, log, chunks }
    } catch (error) {
      await killTree(child)
      if (!error.exited || attempt === 3) throw error
      chunks.push(`\n--- start attempt ${attempt} failed, trying another port ---\n`)
    }
  }
}

export function makeApi(url) {
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
