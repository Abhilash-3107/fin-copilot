// J1 - First run, load demo data.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { test, expect } from '../fixtures.js'
import { figureNear, open, openSettingsTab } from '../lib/ui.js'

// The words no screen may use: the list the UI and the server guards read too.
const WORDS = JSON.parse(readFileSync(fileURLToPath(new URL('../../ui/src/lib/bannedWords.json', import.meta.url)), 'utf8'))
const BANNED = WORDS.banned.map((w) => [new RegExp(w.pattern, w.flags), w.use])
const machineWords = (line) => (WORDS.wholeStringsAllowed.includes(line.trim()) ? [] : BANNED.filter(([re]) => re.test(line)).map(([re]) => line.match(re)[0]))

test.describe('J1 first run', () => {
  test('a fresh app offers to load demo data', async ({ page }) => {
    await open(page, '/dashboard')
    await expect(page.getByRole('button', { name: 'Load demo data' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'Add your first statement' })).toBeVisible()
  })

  test('loading demo data fills the home screen', async ({ page, app }) => {
    await open(page, '/dashboard')
    await page.getByRole('button', { name: 'Load demo data' }).click()

    for (const label of ['In', 'Out', 'Kept']) {
      expect(await figureNear(page, label), `${label} figure`).toBeGreaterThan(0)
    }
    await expect(page.getByRole('button', { name: 'Load demo data' })).toHaveCount(0)

    // The counts and accuracy refresh with it: the page does not keep what it fetched when it was empty.
    const { unsorted, guesses } = await app.api.get('/annotations/review-queue/counts')
    expect(unsorted + guesses, 'payments waiting in the demo').toBeGreaterThan(0)
    await expect(page.getByRole('link', { name: /sorted yet|to check/ }).first()).toBeVisible()
    if (guesses > 0) await expect(page.getByRole('link', { name: new RegExp(`${guesses} guess(es)? to check`) })).toBeVisible()
  })

  test('Settings > Categories shows no tidy-up warnings', async ({ page, app }) => {
    await app.loadDemo()
    await openSettingsTab(page, 'Categories')
    await expect(page.getByText('Refine the subcategories')).toBeVisible()
    await expect(page.getByText('Tidy up')).toHaveCount(0)
  })

  test('demo data is visibly marked once loaded', async ({ page, app }) => {
    await app.loadDemo()
    await open(page, '/dashboard')
    await expect(page.getByText(/demo data|sample data/i).first()).toBeVisible()
  })

  test('the sample has two named accounts, and the monthly move to SBI is a transfer, not spending', async ({ page, app }) => {
    await app.loadDemo()
    const accounts = await app.api.get('/accounts')
    expect(accounts.map((a) => a.account_label).sort()).toEqual(['Kotak savings', 'SBI savings'])
    for (const account of accounts) expect(account.balance, `${account.account_label} balance`).not.toBeNull()

    const moved = (await app.api.get('/transactions?category=Self%20Transfers&include=annotation&limit=1000'))
    expect(moved.length, 'payments paired as transfers').toBeGreaterThanOrEqual(6)
    expect(new Set(moved.map((t) => t.transfer?.text))).toEqual(new Set(['Moved to SBI savings', 'Moved from Kotak savings']))
    // Out leaves them out: the month's Out is the debits that are not transfers or investments.
    const month = '2026-10'
    const all = await app.api.get(`/transactions?month=${month}&include=annotation&limit=1000`)
    const spent = all.filter((t) => t.debit_credit === 'debit' && !['Self Transfers', 'Investments'].includes(t.category)).reduce((sum, t) => sum + t.amount, 0)
    expect((await app.api.get(`/insights?month=${month}`)).verdict.money_out).toBeCloseTo(spent, 2)

    await open(page, '/transactions')
    await page.getByRole('combobox').first().selectOption({ label: 'All statements' })
    await expect(page.getByRole('row').filter({ hasText: 'Moved to SBI savings' }).first()).toBeVisible()
    await open(page, '/upload')
    await expect(page.getByText('Kotak savings').first()).toBeVisible()
    await expect(page.getByText('SBI savings').first()).toBeVisible()
  })

  test('removing the demo data leaves what the user added', async ({ page, app }) => {
    await app.loadDemo()
    // Things the user made themselves, which removal must not touch.
    const person = await app.api.post('/people', { name: 'E2E Friend', upi: 'e2e.friend@upi', relationship: 'friend' })
    const rule = await app.api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
    await app.api.put('/config', { confidence_threshold: 0.7 })

    await openSettingsTab(page, 'Data')
    await page.getByRole('main').getByRole('button', { name: 'Remove sample data', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: 'Remove sample data', exact: true }).click()
    // The app returns to the Dashboard by itself once the data is gone.
    await expect(page.getByRole('button', { name: 'Load demo data' })).toBeVisible()

    expect(await app.api.get('/statements')).toEqual([])
    expect((await app.api.get('/people')).map((p) => p.id)).toEqual([person.id])
    expect((await app.api.get('/rules')).map((r) => r.id)).toEqual([rule.id])
    expect((await app.api.get('/config')).confidence_threshold).toBe(0.7)
  })

  // The search opens what it found, wherever that is: another month than the one on
  // screen, with the payment's panel open, and a person's results are their payments,
  // not a text search that can land on "No transactions".
  test('search opens the exact payment from another month', async ({ page, app }) => {
    await app.loadDemo()
    const [payment] = await app.api.get('/transactions?q=September%20rent')
    expect(payment, 'the demo payment noted "September rent"').toBeDefined()
    expect(payment.txn_date.slice(0, 7)).toBe('2026-09')

    await open(page, '/dashboard')
    await page.keyboard.press('Control+K')
    await page.getByRole('combobox', { name: 'Search' }).fill('September rent')
    await page.getByRole('option', { name: /01 Sep/ }).click()

    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await expect(page.getByText(payment.raw_description)).toBeVisible()
    // The period followed the payment, and its row is on the page behind the panel.
    await expect(page.locator('select').filter({ has: page.locator('option[value="2026-09"]') })).toHaveValue('2026-09')
    await expect(page.getByRole('row', { name: /01 Sep 26 Amit Kumar September rent/ })).toBeVisible()
    await expect(page).not.toHaveURL(/txn=/)
  })

  test('search for a person lists their payments across every month', async ({ page, app }) => {
    await app.loadDemo()
    await open(page, '/dashboard')
    await page.getByRole('button', { name: 'Search' }).click()
    await page.getByRole('combobox', { name: 'Search' }).fill('neha')
    await page.getByRole('option', { name: /Neha Gupta/ }).filter({ hasText: 'person' }).click()

    await expect(page.getByRole('button', { name: /Person: Neha Gupta/ })).toBeVisible()
    await expect(page.getByRole('row').nth(1)).toBeVisible()
    await expect(page.getByText('No payments')).toHaveCount(0)
    await expect(page.locator('select').filter({ has: page.locator('option[value="all"]') })).toHaveValue('all')
  })

  test('a repeated payee is one result that filters to all of its payments', async ({ page, app }) => {
    await app.loadDemo()
    await open(page, '/dashboard')
    await page.keyboard.press('Control+K')
    await page.getByRole('combobox', { name: 'Search' }).fill('swiggy')
    const payee = page.getByRole('option', { name: /Swiggy - all payments/i })
    await expect(payee).toHaveCount(1)
    await payee.click()
    await expect(page.getByRole('button', { name: /Payee: Swiggy/i })).toBeVisible()
    await expect(page.getByRole('row').nth(1)).toBeVisible()
  })

  // One vocabulary: outside developer mode no screen speaks the machine room's
  // words. This reads what a person can see or hear on every screen, tooltips
  // and labels included, which the source guard cannot see for text the server sends.
  test('no screen speaks the machine room\'s words', async ({ page, app, tolerate }) => {
    await app.loadDemo()
    const spoken = () => page.evaluate(() => {
      const text = [document.body.innerText]
      for (const el of document.querySelectorAll('[title],[aria-label],[placeholder],[alt]')) {
        text.push(el.getAttribute('title'), el.getAttribute('aria-label'), el.getAttribute('placeholder'), el.getAttribute('alt'))
      }
      return text.filter(Boolean).join('\n')
    })
    const seen = {}
    const check = async (where) => {
      await page.waitForLoadState('networkidle')
      const found = (await spoken()).split('\n').filter((line) => machineWords(line).length)
      if (found.length) seen[where] = found.slice(0, 3)
    }

    for (const route of ['/dashboard', '/transactions', '/review', '/groups', '/people', '/upload', '/insights']) {
      await open(page, route)
      await check(route)
    }
    // A payment open in its panel.
    await open(page, '/transactions')
    await page.getByRole('row').nth(1).click()
    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await check('a payment panel')
    for (const tab of ['General', 'AI & Model', 'Teach Me', 'Categories', 'Rules', 'Data']) {
      await openSettingsTab(page, tab)
      if (tab === 'AI & Model') await page.getByRole('button', { name: 'Advanced' }).click()
      await check(`Settings > ${tab}`)
    }

    // A toast: turning developer mode on says so, then off again.
    await openSettingsTab(page, 'General')
    const developer = page.getByRole('switch')
    await developer.click()
    await expect(page.getByText('Developer mode on')).toBeVisible()
    await check('a toast')
    await developer.click()
    await expect(page.getByText('Developer mode off')).toBeVisible()

    // The statement menu and a dialog it opens.
    await open(page, '/upload')
    await page.getByRole('button', { name: /^More for / }).first().click()
    await expect(page.getByRole('menu')).toBeVisible()
    await check('the statement menu')
    await page.getByRole('menuitem', { name: 'Start over' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await check('a dialog')
    await page.getByRole('button', { name: 'Cancel' }).click()

    // An error state: the accuracy block when its request fails.
    tolerate(/\/api\/annotations\/accuracy/, 503)
    await page.route(/\/api\/annotations\/accuracy/, (route) => route.fulfill({
      status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'down for the test' }),
    }))
    await open(page, '/dashboard')
    await expect(page.getByRole('alert').filter({ hasText: "Couldn't load the accuracy figures." })).toBeVisible()
    await check('an error state')

    expect(seen, 'lines that use a word from the machine room').toEqual({})
  })

  test('Settings opens the Teach Me tab from an old Insights link, and keeps the machine room behind Advanced', async ({ page }) => {
    await page.goto('/#/settings?tab=insights')
    await expect(page.getByRole('heading', { name: 'When to ask you' })).toBeVisible()
    await expect(page).toHaveURL(/#\/settings\?tab=teach$/)

    await openSettingsTab(page, 'AI & Model')
    // Plain status first, in the app's own words.
    await expect(page.getByRole('status').filter({ hasText: /Sorting uses/ })).toContainText(/through Ollama on your computer/)
    await expect(page.getByRole('heading', { name: 'Matching similar payments' })).toHaveCount(0)
    const advanced = page.getByRole('button', { name: 'Advanced' })
    await expect(advanced).toHaveAttribute('aria-expanded', 'false')
    await advanced.click()
    await expect(page.getByRole('heading', { name: 'Matching similar payments' })).toBeVisible()
  })

  test('Advanced says how many payments are ready to match, and Update now gets the rest ready', async ({ page, app }) => {
    await app.loadDemo()
    await openSettingsTab(page, 'AI & Model')
    await page.getByRole('button', { name: 'Advanced' }).click()
    const line = page.getByText(/\d+ of \d+ payments? ready to match/)
    await expect(line).toBeVisible()
    await expect(page.getByRole('button', { name: /Forget/ })).toHaveCount(0)

    const { ready, total } = await app.api.get('/embeddings/coverage')
    const update = page.getByRole('button', { name: 'Update now' })
    if (ready === total) {
      await expect(update).toHaveCount(0)
      return
    }
    await expect(line).toContainText(`${ready} of ${total} payments`)
    await update.click()
    await expect(line).toContainText(`${total} of ${total} payments ready to match`)
    await expect(update).toHaveCount(0)
  })
})
