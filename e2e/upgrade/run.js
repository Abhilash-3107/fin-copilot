// J15 - Upgrade. A ledger made by the previous published release opens in the
// one being released, with every stored fact intact and no figure moved except
// the ones the release declares in expected-changes.json.
//
//   uv build && cd e2e && npm run upgrade
//
// Needs network access to PyPI for the previous release. The wheel to test is
// FC_E2E_WHEEL (the .whl, or a folder holding exactly one) and defaults to ../dist.
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { TEMP_PREFIX, installWheel, wheelPath } from '../global-setup.js'
import { killTree, makeApi, startServer } from '../lib/server.js'
import { derivedFigures, storedFacts } from '../lib/snapshot.js'
import { categorise, uploadFixture } from '../lib/statement.js'
import { storedColumns } from './db.js'
import { diff, explain } from './diff.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const expectedFile = path.join(here, 'expected-changes.json')
const reportDir = process.env.FC_UPGRADE_REPORT || path.join(here, '..', 'test-results', 'upgrade')

function fail(message) {
  console.error(`\nJ15 FAILED: ${message}`)
  process.exitCode = 1
}

/** The day the previous release seeds its demo on, which is the real date: 0.1.2 has no FC_TODAY. */
function localToday() {
  const now = new Date()
  const two = (n) => String(n).padStart(2, '0')
  return `${now.getFullYear()}-${two(now.getMonth() + 1)}-${two(now.getDate())}`
}

/**
 * Build the ledger the way a user of the previous release builds one: the demo
 * data, a real statement with machine answers, then what they add by hand.
 * Only routes that release has are used.
 */
async function buildLedger(api) {
  await api.post('/data/demo')
  const statement = await uploadFixture(api)
  await categorise(api, statement.id)

  const queue = await api.get('/annotations/review-queue')
  if (queue.length < 3) throw new Error(`Expected a review queue of at least 3, got ${queue.length}`)
  const rows = await api.get('/transactions?include=annotation&limit=1000')

  // Sorting: a relabel, a confirm, a skip, a label on a payment that had none.
  const confident = rows.find((t) => t.annotation_id && !queue.some((q) => q.annotation_id === t.annotation_id))
  await api.patch(`/annotations/${confident.annotation_id}`, { category: 'Shopping', subcategory: 'General Retail' })
  await api.post(`/annotations/${queue[0].annotation_id}/confirm`)
  await api.post(`/annotations/${queue[1].annotation_id}/skip`)
  const bare = rows.find((t) => !t.annotation_id)
  await api.post('/annotations', { transaction_id: bare.id, category: 'Food & Dining', subcategory: 'Restaurants', source: 'manual', confidence: 1 })

  // A person, a rule, and a group member.
  const person = await api.post('/people', { name: 'Upgrade Friend', upi: 'upgrade.friend@upi', relationship: 'friend' })
  const rule = await api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
  await api.post(`/rules/${rule.id}/apply`)
  const group = await api.post('/groups', { name: 'Upgrade dinner', note: 'Split two ways' })
  const debit = rows.find((t) => t.debit_credit === 'debit' && t.statement_id === statement.id)
  await api.post(`/groups/${group.id}/members`, { transaction_id: debit.id, people: [person.id], txn_type: 'split' })
}

async function snapshot(api, version) {
  const [facts, figures] = await Promise.all([storedFacts(api, { version }), derivedFigures(api, { version })])
  return { facts, figures }
}

function describeChange(change) {
  const show = (v) => JSON.stringify(v)
  if (change.kind === 'added') return `${change.path}: added ${show(change.to)}`
  if (change.kind === 'removed') return `${change.path}: removed (was ${show(change.from)})`
  return `${change.path}: ${show(change.from)} -> ${show(change.to)}`
}

async function main() {
  const expected = JSON.parse(readFileSync(expectedFile, 'utf8'))
  const wheel = wheelPath(process.env.FC_E2E_WHEEL ?? path.join(here, '..', '..', 'dist'))
  const wheelVersion = path.basename(wheel).split('-')[1]
  if (wheelVersion !== expected.version) {
    throw new Error(
      `The wheel is ${wheelVersion} but expected-changes.json describes ${expected.version}. ` +
        'Each release needs its own list: set "version" and "from" and review every entry.',
    )
  }

  const root = mkdtempSync(path.join(os.tmpdir(), `${TEMP_PREFIX}upgrade-`))
  const dataDir = path.join(root, 'data')
  mkdirSync(dataDir)
  let venv
  let server
  try {
    // 1. The previous published release, from PyPI, as a user runs it.
    const today = localToday()
    const previousLaunch = (dir, port) => ({
      cmd: 'uvx',
      args: ['--from', `finance-copilot==${expected.from}`, 'finance-copilot', '--data-dir', dir, '--port', String(port), '--no-browser'],
      cwd: os.tmpdir(),
    })
    console.log(`Starting finance-copilot ${expected.from} from PyPI on ${dataDir}`)
    // The first start downloads the release and its dependencies, so it gets longer than a start from a cache.
    server = await startServer(dataDir, null, { launch: previousLaunch, timeoutMs: 300_000 })
    let api = makeApi(server.url)
    const running = (await api.get('/health')).version
    if (running !== expected.from) throw new Error(`Asked for ${expected.from} but the server reports ${running}`)
    await buildLedger(api)
    const before = await snapshot(api, expected.from)
    await killTree(server.child)
    server = null
    Object.assign(before.facts, storedColumns(dataDir))

    // 2. The release under test, on the same folder.
    console.log(`Starting the ${expected.version} wheel on the same folder`)
    venv = installWheel(wheel)
    const currentLaunch = (dir, port) => ({
      cmd: path.join(venv, 'bin', 'finance-copilot'),
      args: ['--data-dir', dir, '--port', String(port), '--no-browser'],
      cwd: os.tmpdir(),
    })
    // The same day the previous release seeded on, so a midnight between the two runs cannot move a figure.
    server = await startServer(dataDir, null, { launch: currentLaunch, today })
    api = makeApi(server.url)
    const now = (await api.get('/health')).version
    if (now !== expected.version) throw new Error(`Expected ${expected.version} but the server reports ${now}`)
    const after = await snapshot(api, undefined)
    const startupLog = server.log.all()
    await killTree(server.child)
    server = null
    Object.assign(after.facts, storedColumns(dataDir))

    // 3. Compare.
    mkdirSync(reportDir, { recursive: true })
    for (const [name, value] of Object.entries({ before, after })) {
      writeFileSync(path.join(reportDir, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`)
    }
    const vars = { month: today.slice(0, 7) }
    const problems = []
    const report = []
    for (const scope of ['stored', 'derived']) {
      const [a, b] = scope === 'stored' ? [before.facts, after.facts] : [before.figures, after.figures]
      const changes = diff(a, b)
      const entries = expected.changes.filter((c) => c.scope === scope)
      const { unexplained, hits, unused } = explain(changes, entries, vars)
      report.push(`\n${scope}: ${changes.length} differences, ${changes.length - unexplained.length} expected`)
      for (const [entry, matched] of hits) {
        if (matched.length > 0) report.push(`  [${entry.id}] ${matched.length} x ${entry.path}`)
      }
      for (const entry of unused) problems.push(`expected change "${entry.id}" (${scope}) did not happen: remove it, or find out why it stopped`)
      for (const change of unexplained) problems.push(`unexpected ${scope} change: ${describeChange(change)}`)
    }
    writeFileSync(path.join(reportDir, 'report.txt'), `${report.join('\n')}\n`)
    console.log(report.join('\n'))

    // 4. What a list of paths cannot say. Teach Me now holds every payment with
    // no category; so each payment it newly holds must be one that had none.
    const unsorted = new Set(
      before.facts.transactions.filter((t) => t.annotation_id === null || t.category === null || t.category === 'Uncategorized').map((t) => t.id),
    )
    for (const id of after.facts.reviewQueue.filter((q) => !before.facts.reviewQueue.includes(q))) {
      if (!unsorted.has(id)) problems.push(`Teach Me newly holds payment ${id}, which already had a category`)
    }
    if (/Traceback|ERROR/.test(startupLog)) problems.push('the new release logged an error while opening the old ledger')

    if (problems.length > 0) {
      fail(`${problems.length} problem(s)\n  - ${problems.slice(0, 40).join('\n  - ')}\nSnapshots and report: ${reportDir}`)
    } else {
      console.log(`\nJ15 passed: ${expected.from} -> ${expected.version}. Snapshots and report: ${reportDir}`)
    }
  } finally {
    if (server) await killTree(server.child)
    if (venv) rmSync(venv, { recursive: true, force: true })
    rmSync(root, { recursive: true, force: true })
  }
}

main().catch((error) => {
  fail(error.stack ?? String(error))
})
