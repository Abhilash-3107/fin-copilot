import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

export function serverMode() {
  const mode = process.env.FC_E2E_SERVER || 'source'
  if (mode !== 'source' && mode !== 'wheel') {
    throw new Error(`FC_E2E_SERVER must be "source" or "wheel", got "${mode}"`)
  }
  return mode
}

/** FC_E2E_WHEEL may be the wheel itself or a folder holding exactly one. */
export function wheelPath() {
  const given = process.env.FC_E2E_WHEEL
  if (!given) throw new Error('FC_E2E_SERVER=wheel needs FC_E2E_WHEEL=<path to the built .whl>')
  const resolved = path.resolve(given)
  if (!existsSync(resolved)) throw new Error(`FC_E2E_WHEEL does not exist: ${resolved}`)
  if (!statSync(resolved).isDirectory()) return resolved
  const wheels = readdirSync(resolved).filter((f) => f.endsWith('.whl'))
  if (wheels.length !== 1) throw new Error(`Expected one .whl in ${resolved}, found ${wheels.length}`)
  return path.join(resolved, wheels[0])
}

export default async function globalSetup() {
  if (serverMode() === 'source') {
    if (!existsSync(path.join(repoRoot, 'ui', 'dist', 'index.html'))) {
      throw new Error('Source mode serves ui/dist, which is not built. Run: cd ui && npm ci && npm run build')
    }
    return
  }
  // Resolve the wheel's dependencies once, so parallel workers do not all race
  // to build the same uvx environment.
  execFileSync('uvx', ['--from', wheelPath(), 'finance-copilot', '--version'], { stdio: ['ignore', 'ignore', 'inherit'] })
}
