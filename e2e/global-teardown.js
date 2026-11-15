import { rmSync } from 'node:fs'

export default async function globalTeardown() {
  if (process.env.FC_E2E_WHEEL_VENV) rmSync(process.env.FC_E2E_WHEEL_VENV, { recursive: true, force: true })
}
