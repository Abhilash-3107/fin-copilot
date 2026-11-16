// J9 - Failure states. When the API fails, a data block says it could not load,
// with a Retry that works once the API answers again. It never shows its empty
// state, because "No transactions" and "Nothing here yet" tell a person with a
// full ledger that their data is gone.
import { test, expect } from '../fixtures.js'
import { open, openSettingsTab } from '../lib/ui.js'

/**
 * Make every request whose URL matches `pattern` fail with a 503, until `heal()`.
 * The browser reports a 503 as an HTTP error, which this suite fails on unless
 * it is listed, so the test lists it.
 */
async function breakApi(page, tolerate, pattern) {
  let broken = true
  tolerate(pattern, 503)
  await page.route(pattern, (route) =>
    broken ? route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'down for the test' }) }) : route.fallback(),
  )
  return { heal: () => { broken = false } }
}

const alertWith = (page, text) => page.getByRole('alert').filter({ hasText: text })
// The toast area is an alert region that is always on the page, empty until a toast shows.
const failures = (page) => page.getByRole('alert').filter({ hasText: /Couldn't/ })

test.describe('J9 failure states', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  test('Dashboard says it could not load, never "Welcome", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/insights/)
    await page.goto('/#/dashboard')
    await expect(alertWith(page, "Couldn't load your dashboard.")).toBeVisible()
    await expect(page.getByText(/Welcome!/)).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Load demo data' })).toHaveCount(0)

    api.heal()
    await alertWith(page, "Couldn't load your dashboard.").getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByRole('heading', { name: 'Your money at a glance' })).toBeVisible()
    await expect(page.getByText('Needs your attention')).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('a Dashboard block that fails says so in its own place while the rest stays', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/annotations\/accuracy/)
    await open(page, '/dashboard')
    await expect(alertWith(page, "Couldn't load the accuracy figures.")).toBeVisible()
    await expect(page.getByText('Needs your attention')).toBeVisible()
    await expect(page.getByText(/Not enough corrections yet/)).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(failures(page)).toHaveCount(0)
    await expect(page.getByText('Where I need your help most')).toBeVisible()
  })

  test('Transactions says the list could not load, never "No transactions", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/transactions(\?|$)/)
    await page.goto('/#/transactions')
    await expect(alertWith(page, "Couldn't load your transactions.")).toBeVisible()
    await expect(page.getByText('No transactions')).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByRole('row').nth(1)).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('Teach Me says the list could not load, never "all done", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/annotations\/review-queue(\?|$)/)
    await page.goto('/#/review')
    await expect(alertWith(page, "Couldn't load your review list.")).toBeVisible()
    await expect(page.getByText("You're all done!")).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByRole('group', { name: /^Payment to / })).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('Money Map says it could not load, never "Nothing here yet", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/insights/)
    await page.goto('/#/insights')
    await expect(alertWith(page, "Couldn't load your Money Map.")).toBeVisible()
    await expect(page.getByText(/Nothing here yet/)).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText(/Where it went/)).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('People says the list could not load, never "No people yet", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/people/)
    await page.goto('/#/people')
    await expect(alertWith(page, "Couldn't load your people.")).toBeVisible()
    await expect(page.getByText('No people yet')).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('Neha Gupta')).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('Groups says the list could not load, never "No groups yet", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/groups/)
    await page.goto('/#/groups')
    await expect(alertWith(page, "Couldn't load your groups.")).toBeVisible()
    await expect(page.getByText('No groups yet')).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('Goa trip with Rahul & Neha')).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('Add Statement says the statement list could not load, never "No statements yet", and recovers', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/statements(\?|$)/)
    await page.goto('/#/upload')
    await expect(alertWith(page, "Couldn't load your statements.")).toBeVisible()
    await expect(page.getByText(/No statements yet/)).toHaveCount(0)
    // One message, not one here and another in the page frame.
    await expect(failures(page)).toHaveCount(1)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(failures(page)).toHaveCount(0)
    await expect(page.getByRole('table')).toBeVisible()
  })

  test('Settings says a tab could not load its rules, never "No rules yet", and recovers', async ({ page, tolerate, app }) => {
    // A rule of the user's own, so that the recovered list differs from the empty one.
    await app.api.post('/rules', { pattern: 'bookstore', category: 'Shopping', subcategory: 'General Retail' })
    const api = await breakApi(page, tolerate, /\/api\/rules(\?|$)/)
    await openSettingsTab(page, 'Rules')
    await expect(alertWith(page, "Couldn't load your rules.")).toBeVisible()
    await expect(page.getByText(/No rules yet/)).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('"bookstore"')).toBeVisible()
    await expect(failures(page)).toHaveCount(0)
  })

  test('Settings does not show default values when the settings could not load', async ({ page, tolerate }) => {
    const api = await breakApi(page, tolerate, /\/api\/config$/)
    await page.goto('/#/settings')
    await expect(alertWith(page, "Couldn't load your settings.")).toBeVisible()
    await expect(page.getByText('Developer mode')).toHaveCount(0)

    api.heal()
    await failures(page).getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('Developer mode')).toBeVisible()
  })

  test('the search says it could not search, quietly, and Retry works', async ({ page, tolerate }) => {
    await open(page, '/dashboard')
    const api = await breakApi(page, tolerate, /\/api\/transactions\?q=/)
    await page.getByRole('button', { name: 'Search' }).click()
    await page.getByRole('combobox', { name: 'Search' }).fill('swiggy')
    await expect(page.getByText("Couldn't search just now.")).toBeVisible()
    await expect(page.getByText('No matches.')).toHaveCount(0)

    api.heal()
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByRole('option', { name: /Swiggy/ }).first()).toBeVisible()
  })

  test('an unknown address says the page is not found, and links home', async ({ page }) => {
    await page.goto('/#/nope')
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
    await page.getByRole('link', { name: 'Back to the Dashboard' }).click()
    await expect(page.getByRole('heading', { name: 'Your money at a glance' })).toBeVisible()
  })
})
