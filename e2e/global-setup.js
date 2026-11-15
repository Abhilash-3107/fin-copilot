import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readdirSync, rmSync, statSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Every data folder the harness makes starts with this, so leftovers can be told apart. */
export const TEMP_PREFIX = 'fc-e2e-'

// A test lasts a few minutes at most. A folder older than this was left by a
// run that was interrupted, and no run in progress, this one or another, owns it.
const STALE_AFTER_MS = 60 * 60 * 1000

/** Remove data folders that interrupted runs left behind. */
function removeStaleTempFolders() {
  const root = os.tmpdir()
  const cutoff = Date.now() - STALE_AFTER_MS
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    if (!entry.isDirectory() || !entry.name.startsWith(TEMP_PREFIX)) continue
    const folder = path.join(root, entry.name)
    if (statSync(folder).mtimeMs < cutoff) rmSync(folder, { recursive: true, force: true })
  }
}

export function serverMode() {
  const mode = process.env.FC_E2E_SERVER || 'source'
  if (mode !== 'source' && mode !== 'wheel') {
    throw new Error(`FC_E2E_SERVER must be "source" or "wheel", got "${mode}"`)
  }
  return mode
}

/** FC_E2E_WHEEL may be the wheel itself or a folder holding exactly one. */
export function wheelPath(given = process.env.FC_E2E_WHEEL) {
  if (!given) throw new Error('FC_E2E_SERVER=wheel needs FC_E2E_WHEEL=<path to the built .whl>')
  const resolved = path.resolve(given)
  if (!existsSync(resolved)) throw new Error(`FC_E2E_WHEEL does not exist: ${resolved}`)
  if (!statSync(resolved).isDirectory()) return resolved
  const wheels = readdirSync(resolved).filter((f) => f.endsWith('.whl'))
  if (wheels.length !== 1) throw new Error(`Expected one .whl in ${resolved}, found ${wheels.length}`)
  return path.join(resolved, wheels[0])
}

/**
 * Install the wheel into a fresh environment and return its path.
 * `uvx --from <wheel>` caches its environment by version, so a rebuilt wheel
 * with the same version number can silently run the previous build's code.
 */
export function installWheel(wheel = wheelPath()) {
  const venv = mkdtempSync(path.join(os.tmpdir(), `${TEMP_PREFIX}venv-`))
  const stdio = ['ignore', 'ignore', 'inherit']
  execFileSync('uv', ['venv', '--quiet', venv], { stdio })
  execFileSync('uv', ['pip', 'install', '--quiet', '--python', path.join(venv, 'bin', 'python'), wheel], { stdio })
  return venv
}

export default async function globalSetup() {
  removeStaleTempFolders()
  if (serverMode() === 'source') {
    if (!existsSync(path.join(repoRoot, 'ui', 'dist', 'index.html'))) {
      throw new Error('Source mode serves ui/dist, which is not built. Run: cd ui && npm ci && npm run build')
    }
    return
  }
  process.env.FC_E2E_WHEEL_VENV = installWheel()
}

/** The environment globalSetup installed the wheel into, in wheel mode. */
export function wheelBin() {
  const venv = process.env.FC_E2E_WHEEL_VENV
  if (!venv) throw new Error('FC_E2E_WHEEL_VENV is not set: globalSetup did not install the wheel')
  return path.join(venv, 'bin', 'finance-copilot')
}
