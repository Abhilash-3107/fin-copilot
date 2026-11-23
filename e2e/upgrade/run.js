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
import { derivedFigures, paymentAccounts, storedFacts } from '../lib/snapshot.js'
import { uploadFixture, uploadFixtureNov } from '../lib/statement.js'
import { compareSnapshots } from './compare.js'
import { plantLegacyRows, storedColumns } from './db.js'
import { categorise } from './previous-release.js'
import { baseVersion } from './version.js'

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

// Text the journey plants where a migration of this release acts. expected-changes.json
// names the same strings, because a migration's effect is part of what the release declares.
const CLOSING_BALANCE_ROW = 'UPI/ZEPTO/ORDER/400112235/UPI'
const BANK_LINE_MERCHANT = 'UPI/RAVI KUMAR SHA/120777386275/self'
const TYPED_MERCHANT = 'Chai/Stall/Koramangala'
const DUPLICATE_SUBCATEGORY = 'legacy-copy-of-restaurants'
const RENAMED_BANK = 'Salary account'

/**
 * Build the ledger the way a user of the previous release builds one: the demo
 * data, a real statement with machine answers, then what they add by hand.
 * Only routes that release has are used.
 *
 * @returns the day the demo was seeded on, and what the journey planted for the
 *   migrations to repair (ids by the names expected-changes.json uses)
 */
async function buildLedger(api) {
  // The demo is anchored to the day the previous release believes it is, which is
  // the real date (it has no FC_TODAY). The run on the new release is pinned to that day, so a
  // midnight in the middle of the seeding would leave the two runs a day apart.
  const dayBefore = localToday()
  await api.post('/data/demo')
  const today = localToday()
  if (today !== dayBefore) throw new Error(`Midnight passed while the demo was being seeded (${dayBefore} -> ${today}): run the journey again.`)

  const statement = await uploadFixture(api)
  // The same account a month later, left as the parser read it.
  await uploadFixtureNov(api)
  // The old Rename wrote text into the statement's bank (there was no account to name).
  // It is planted on October only: the upgrade must fold that statement into the
  // account of the real bank that November's statement still carries, and keep
  // the words as the account's name.
  await api.patch(`/statements/${statement.id}`, { bank_name: RENAMED_BANK })
  await categorise(api, statement.id)

  const queue = await api.get('/annotations/review-queue')
  if (queue.length < 3) throw new Error(`Expected a review queue of at least 3, got ${queue.length}`)
  const rows = await api.get('/transactions?include=annotation&limit=1000')

  // Sorting: a relabel, a confirm, a skip, a label on a payment that had none.
  const confident = rows.find((t) => t.annotation_id && !queue.some((q) => q.annotation_id === t.annotation_id))
  await api.patch(`/annotations/${confident.annotation_id}`, { category: 'Shopping', subcategory: 'General Retail' })
  await api.post(`/annotations/${queue[0].annotation_id}/confirm`)
  await api.post(`/annotations/${queue[1].annotation_id}/skip`)
  const [bare, bankLine] = rows.filter((t) => !t.annotation_id && t.debit_credit === 'debit')
  await api.post('/annotations', { transaction_id: bare.id, category: 'Food & Dining', subcategory: 'Restaurants', source: 'manual', confidence: 1 })

  // Data that migrations 030 (merchant names with two slashes), 027 (a balance label
  // on a description) and 033 (a duplicate subcategory) act on. A merchant the user
  // typed must survive 030; one a machine echoed from the bank line must not.
  const typed = rows.find((t) => t.annotation_id && t.id !== confident.id && !queue.some((q) => q.annotation_id === t.annotation_id))
  await api.patch(`/annotations/${typed.annotation_id}`, { merchant: TYPED_MERCHANT })
  await api.post('/annotations', {
    transaction_id: bankLine.id, merchant: BANK_LINE_MERCHANT, category: 'Shopping', subcategory: 'General Retail', source: 'llm', confidence: 0.9,
  })
  const closing = rows.find((t) => t.statement_id === statement.id && t.raw_description === CLOSING_BALANCE_ROW)
  if (!closing) throw new Error(`The fixture statement has no payment described ${CLOSING_BALANCE_ROW}`)
  const categories = await api.get('/categories')
  const dining = categories.find((c) => c.name === 'Food & Dining' && c.parent_id === null)
  const restaurants = categories.find((c) => c.name === 'Restaurants' && c.parent_id === dining.id)

  // A person, a rule, and a group member.
  const person = await api.post('/people', { name: 'Upgrade Friend', upi: 'upgrade.friend@upi', relationship: 'friend' })
  const rule = await api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
  await api.post(`/rules/${rule.id}/apply`)
  const group = await api.post('/groups', { name: 'Upgrade dinner', note: 'Split two ways' })
  const debit = rows.find((t) => t.debit_credit === 'debit' && t.statement_id === statement.id)
  await api.post(`/groups/${group.id}/members`, { transaction_id: debit.id, people: [person.id], txn_type: 'split' })

  return {
    today,
    planted: {
      ids: { closingBalanceRow: closing.id, bankLineRow: bankLine.id, duplicateSubcategory: DUPLICATE_SUBCATEGORY },
      duplicateOf: restaurants.id,
      typedMerchant: { id: typed.id, value: TYPED_MERCHANT },
    },
  }
}

async function snapshot(api, version) {
  const [facts, figures, accounts] = await Promise.all([storedFacts(api, { version }), derivedFigures(api, { version }), paymentAccounts(api)])
  // Not compared between the releases (it is each statement's account read through the row): checked against the statements.
  return { facts, figures, copies: { paymentAccounts: accounts } }
}

export async function main() {
  const expected = JSON.parse(readFileSync(expectedFile, 'utf8'))
  const wheel = wheelPath(process.env.FC_E2E_WHEEL ?? path.join(here, '..', '..', 'dist'))
  const wheelVersion = path.basename(wheel).split('-')[1]
  // A release candidate (0.3.0rc1) runs the journey of the release it leads to (0.3.0).
  if (baseVersion(wheelVersion) !== expected.version) {
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
    const previousLaunch = (dir, port) => ({
      cmd: 'uvx',
      args: ['--from', `finance-copilot==${expected.from}`, 'finance-copilot', '--data-dir', dir, '--port', String(port), '--no-browser'],
      cwd: os.tmpdir(),
    })
    // The first start downloads the release and its dependencies, so it gets longer than a start from a cache.
    const startPrevious = async () => {
      const started = await startServer(dataDir, null, { launch: previousLaunch, timeoutMs: 300_000 })
      const running = (await makeApi(started.url).get('/health')).version
      if (running !== expected.from) {
        await killTree(started.child)
        throw new Error(`Asked for ${expected.from} but the server reports ${running}`)
      }
      return started
    }
    console.log(`Starting finance-copilot ${expected.from} from PyPI on ${dataDir}`)
    server = await startPrevious()
    const { today, planted } = await buildLedger(makeApi(server.url))
    await killTree(server.child)
    // Rows the previous release's API cannot make, written with it stopped, then it reads them back.
    plantLegacyRows(dataDir, { closingBalanceRow: planted.ids.closingBalanceRow, duplicateOf: planted.duplicateOf, duplicateId: DUPLICATE_SUBCATEGORY })
    server = await startPrevious()
    const before = await snapshot(makeApi(server.url), expected.from)
    await killTree(server.child)
    server = null
    Object.assign(before.facts, storedColumns(dataDir))

    // 2. The release under test, on the same folder.
    console.log(`Starting the ${wheelVersion} wheel on the same folder`)
    venv = installWheel(wheel)
    const currentLaunch = (dir, port) => ({
      cmd: path.join(venv, 'bin', 'finance-copilot'),
      args: ['--data-dir', dir, '--port', String(port), '--no-browser'],
      cwd: os.tmpdir(),
    })
    // The same day the previous release seeded on, so a midnight between the two runs cannot move a figure.
    server = await startServer(dataDir, null, { launch: currentLaunch, today })
    const api = makeApi(server.url)
    const now = (await api.get('/health')).version
    if (now !== wheelVersion) throw new Error(`Expected ${wheelVersion} but the server reports ${now}`)
    const after = await snapshot(api, undefined)
    const startupLog = server.log.all()
    await killTree(server.child)
    server = null
    Object.assign(after.facts, storedColumns(dataDir))

    // 3. Compare.
    mkdirSync(reportDir, { recursive: true })
    for (const [name, value] of Object.entries({ before, after, context: { today, planted } })) {
      writeFileSync(path.join(reportDir, `${name}.json`), `${JSON.stringify(value, null, 2)}\n`)
    }
    const { problems, report } = compareSnapshots(before, after, expected, { today, planted })
    writeFileSync(path.join(reportDir, 'report.txt'), `${report.join('\n')}\n`)
    console.log(report.join('\n'))
    if (/Traceback|ERROR/.test(startupLog)) problems.push('the new release logged an error while opening the old ledger')

    if (problems.length > 0) {
      fail(`${problems.length} problem(s)\n  - ${problems.slice(0, 40).join('\n  - ')}\nSnapshots and report: ${reportDir}`)
    } else {
      console.log(`\nJ15 passed: ${expected.from} -> ${wheelVersion}. Snapshots and report: ${reportDir}`)
    }
  } finally {
    if (server) await killTree(server.child)
    if (venv) rmSync(venv, { recursive: true, force: true })
    rmSync(root, { recursive: true, force: true })
  }
}

// Only when run as the journey: the tests load this file to check its imports.
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    fail(error.stack ?? String(error))
  })
}
