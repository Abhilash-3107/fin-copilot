// J4 - Sort from the keyboard. With the demo ledger, every payment waiting in
// Teach Me can be sorted without the mouse: choose by number, type any
// category with "/", skip with S, take back with Z, and reach zero. A card with
// no real guess cannot be confirmed with Enter, and the finish says what the
// session achieved.
import { test, expect } from '../fixtures.js'
import { open } from '../lib/ui.js'

const progress = (page) => page.getByText(/^\d+ \/ \d+$/)
const card = (page) => page.getByRole('group', { name: /^Payment to / })
const done = (page) => page.getByText("You're all done!")
const answers = /\/api\/annotations\/(bulk-label|[^/]+\/confirm)$/

/** How many payments have been answered, as the progress line says it, or 'done' on the finish screen. */
async function where(page) {
  if (await done(page).isVisible()) return 'done'
  const [handled] = (await progress(page).innerText()).split('/')
  return handled.trim()
}

/** Wait for the page to move on from `before`: another card answered, or the finish. */
async function movedOn(page, before) {
  await expect.poll(() => where(page), { message: 'Teach Me should move on' }).not.toBe(before)
}

/** Answer the card on screen with the keyboard alone, and wait until the page has moved on. */
async function sortWithKeys(page) {
  const before = await where(page)
  if (await page.getByText('Which is it?').isVisible()) {
    await page.keyboard.press('1') // the best suggestion
  } else {
    await page.keyboard.press('/')
    await page.keyboard.type('groc')
    await page.keyboard.press('Enter') // picks "Food & Dining > Groceries"
  }
  await expect(page.getByRole('button', { name: /^Confirm/ })).toBeVisible()
  await page.keyboard.press('Enter')
  await movedOn(page, before)
}

test.describe('J4 sort from the keyboard', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  test('every waiting payment is sorted without the mouse, and the finish says what was achieved', async ({ page, app }) => {
    // Two earlier answers about one payee make the next one the third: it
    // becomes a payee the app sorts by itself, and the finish should say so.
    // (A payee with a built-in rule would leave the queue as soon as it is
    // taught, so this one is chosen because it has none.)
    const [chaiWala] = (await app.api.get('/annotations/review-queue')).filter((i) => i.payee === 'CHAI WALA CORNER')
    const earlier = (await app.api.get('/transactions?limit=500&include=annotation'))
      .filter((t) => t.counterparty_key === chaiWala.counterparty_key && t.id !== chaiWala.transaction_id && t.annotation_id)
    expect(earlier.length, 'earlier payments to CHAI WALA CORNER').toBeGreaterThanOrEqual(2)
    for (const t of earlier.slice(0, 2)) {
      await app.api.patch(`/annotations/${t.annotation_id}`, { category: 'Food & Dining', subcategory: 'Cafe & Snacks' })
    }
    const rulesBefore = (await app.api.get('/annotations/learned-rules')).length
    const waiting = (await app.api.get('/annotations/review-queue')).length
    expect(waiting, 'payments waiting in Teach Me').toBeGreaterThanOrEqual(13)

    const posted = []
    page.on('request', (r) => { if (r.method() === 'POST' && answers.test(r.url())) posted.push(r.url()) })
    await open(page, '/review')
    await expect(card(page)).toBeFocused()
    await expect(progress(page)).toHaveText(`0 / ${waiting}`)

    // The first card has no guess: Enter does not answer it.
    await expect(page.getByRole('button', { name: 'Pick a category' })).toBeVisible()
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Pick a category' })).toBeVisible()
    expect(posted, 'answers sent after Enter on a card with no guess').toEqual([])

    // S sets a card aside and Z brings it back.
    const first = await card(page).getAttribute('aria-label')
    await page.keyboard.press('s')
    await expect(card(page)).not.toHaveAttribute('aria-label', first)
    await expect(page.getByText('Set aside for later')).toBeVisible()
    await page.keyboard.press('z')
    await expect(card(page)).toHaveAttribute('aria-label', first)
    await expect(progress(page)).toHaveText(`0 / ${waiting}`)

    // Type a category, pick it, answer; then take the answer back.
    await page.keyboard.press('/')
    await page.keyboard.type('groc')
    await expect(page.getByRole('option', { name: 'Food & Dining > Groceries' })).toBeVisible()
    await page.keyboard.press('Enter')
    await expect(page.getByLabel('Your pick')).toContainText('Food & Dining > Groceries')
    await page.keyboard.press('Enter')
    await expect(page.getByText('Sorted as Food & Dining > Groceries')).toBeVisible()
    await expect(progress(page)).toHaveText(`1 / ${waiting}`)
    await page.keyboard.press('z')
    await expect(progress(page)).toHaveText(`0 / ${waiting}`)
    await expect(card(page)).toHaveAttribute('aria-label', first)

    // Now the whole queue, one card at a time, from the keyboard.
    let sawChaiWala = false
    for (let i = 0; i < waiting + 5 && (await where(page)) !== 'done'; i++) {
      if ((await card(page).getAttribute('aria-label')) === 'Payment to CHAI WALA CORNER') {
        sawChaiWala = true
        // The payee's own history is the first suggestion, with its reason.
        await expect(page.getByRole('button', { name: /You chose this 2 times for CHAI WALA CORNER/ })).toBeVisible()
      }
      await sortWithKeys(page)
    }

    await expect(done(page)).toBeVisible()
    expect(sawChaiWala, 'the payee with history came up in the queue').toBe(true)
    expect(await app.api.get('/annotations/review-queue'), 'payments still waiting').toEqual([])
    const counts = await app.api.get('/annotations/review-queue/counts')
    expect(counts).toMatchObject({ unsorted: 0, guesses: 0, set_aside: 0 })

    // The finish: how many were sorted, and the payees that now sort themselves (the server's count).
    await expect(page.getByText(/^You sorted \d+ payments?\.$/)).toBeVisible()
    const newRules = (await app.api.get('/annotations/learned-rules')).length - rulesBefore
    expect(newRules, 'payees that became learned rules').toBeGreaterThanOrEqual(1)
    const sentence = newRules === 1 ? '1 payee will now be sorted on its own from here.' : `${newRules} payees will now be sorted on their own from here.`
    await expect(page.getByText(sentence)).toBeVisible()
    await expect(page.getByRole('link', { name: 'Back to the Dashboard' })).toBeVisible()
  })

  test('a card with no real guess cannot be confirmed with Enter', async ({ page, app }) => {
    // Leave only the payment whose guess is Uncategorized waiting.
    const queue = await app.api.get('/annotations/review-queue')
    const uncategorized = queue.find((i) => i.category === 'Uncategorized')
    expect(uncategorized, 'a payment guessed as Uncategorized').toBeDefined()
    for (const item of queue.filter((i) => i !== uncategorized)) {
      await app.api.post('/annotations/review-queue/skip', { transaction_id: item.transaction_id })
    }
    const posted = []
    page.on('request', (r) => { if (r.method() === 'POST' && answers.test(r.url())) posted.push(r.url()) })

    await open(page, '/review')
    await expect(page.getByRole('button', { name: 'Pick a category' })).toBeVisible()
    await expect(page.getByRole('button', { name: /^Confirm/ })).toHaveCount(0)
    await page.keyboard.press('Enter')
    await expect(page.getByRole('button', { name: 'Pick a category' })).toBeVisible()
    expect(posted).toEqual([])

    // Choosing a category is what makes it answerable.
    await page.keyboard.press('/')
    await page.keyboard.type('groc')
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    // The others are set aside, so Teach Me says so instead of "all done".
    await expect(page.getByText(/payments are set aside/)).toBeVisible()
    expect(posted).toHaveLength(1)
  })

  test('typing a category that starts with S, Z or A sorts that card and does nothing else', async ({ page, app }) => {
    const queue = await app.api.get('/annotations/review-queue')
    const waiting = queue.length
    const [first] = queue

    await open(page, '/review')
    await expect(card(page)).toHaveAttribute('aria-label', `Payment to ${first.payee ?? first.raw_description}`)
    await page.keyboard.press('/')
    await page.keyboard.type('shop') // S is a shortcut on the card, and plain text in the filter
    await expect(page.getByRole('combobox', { name: 'Another category' })).toHaveValue('shop')
    await page.getByRole('option').first().waitFor()
    const picked = await page.getByRole('option').first().innerText()
    expect(picked).toMatch(/^Shopping/)
    await page.keyboard.press('Enter')
    await expect(page.getByLabel('Your pick')).toContainText(picked)
    // Nothing was skipped by the S, and the card is still the one that was on screen.
    await expect(card(page)).toHaveAttribute('aria-label', `Payment to ${first.payee ?? first.raw_description}`)
    expect((await app.api.get('/annotations/review-queue/counts')).set_aside).toBe(0)

    await page.keyboard.press('Enter')
    await expect(progress(page)).toHaveText(`1 / ${waiting}`)
    const row = (await app.api.get('/transactions?limit=500&include=annotation')).find((t) => t.id === first.transaction_id)
    expect(row.category).toBe('Shopping')
    expect((await app.api.get('/annotations/review-queue/counts')).set_aside).toBe(0)
  })

  test('Z restores the payment exactly as it was', async ({ page, app }) => {
    const [bare] = (await app.api.get('/annotations/review-queue')).filter((i) => !i.annotation_id)
    const rowOf = async () => (await app.api.get('/transactions?include=annotation')).find((t) => t.id === bare.transaction_id)
    const before = await rowOf()
    expect(before.annotation_id, 'a payment nothing has labelled').toBeFalsy()

    await open(page, '/review')
    await expect(card(page)).toHaveAttribute('aria-label', `Payment to ${bare.payee ?? bare.raw_description}`)
    await page.keyboard.press('/')
    await page.keyboard.type('groc')
    await page.keyboard.press('Enter')
    await page.keyboard.press('Enter')
    await expect(page.getByText('Sorted as Food & Dining > Groceries')).toBeVisible()
    expect((await rowOf()).category).toBe('Food & Dining')

    await page.keyboard.press('z')
    await expect(card(page)).toHaveAttribute('aria-label', `Payment to ${bare.payee ?? bare.raw_description}`)
    expect(await rowOf()).toEqual(before)
  })
})

// The rest of the app obeys the same keys: dialogs answer to Escape and Enter,
// pickers to the arrows, and "?" lists them all.
test.describe('J4 the keyboard outside Teach Me', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  test('? lists every shortcut by scope, Escape closes it, and it stays out of text fields', async ({ page }) => {
    await open(page, '/transactions')
    await page.getByRole('searchbox').click()
    await page.keyboard.type('a?')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByRole('searchbox')).toHaveValue('a?')

    await page.getByRole('searchbox').blur()
    await page.keyboard.press('?')
    const sheet = page.getByRole('dialog', { name: 'Keyboard shortcuts' })
    await expect(sheet).toBeVisible()
    for (const scope of ['Everywhere', 'Search', 'Teach Me', 'Transactions']) {
      await expect(sheet.getByRole('group', { name: scope })).toBeVisible()
    }
    await expect(sheet.getByText('Hide or show amounts')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(sheet).toHaveCount(0)
  })

  test('a confirm dialog cancels on Escape and confirms on Enter', async ({ page, app }) => {
    const keep = await app.api.post('/groups', { name: 'Keep me' })
    const drop = await app.api.post('/groups', { name: 'Drop me' })
    await open(page, '/groups')

    const trash = (name) => page.getByRole('button', { name: `Expand ${name}` }).locator('..').getByRole('button', { name: 'Delete group' })
    await trash('Keep me').focus()
    await page.keyboard.press('Enter')
    const dialog = page.getByRole('dialog')
    await expect(dialog).toBeVisible()
    await expect(dialog).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    expect((await app.api.get('/groups')).map((g) => g.id)).toEqual(expect.arrayContaining([keep.id, drop.id]))

    // Delete "Drop me": its trash icon, then Enter on the dialog.
    await trash('Drop me').click()
    await expect(dialog).toContainText('Drop me')
    await page.keyboard.press('Enter')
    await expect(dialog).toHaveCount(0)
    await expect.poll(async () => (await app.api.get('/groups')).some((g) => g.id === drop.id)).toBe(false)
  })

  test('a group can be renamed from the keyboard, and a duplicate is refused where you typed it', async ({ page, app, tolerate }) => {
    // The refusals are 409s the page is expected to show.
    tolerate(/\/api\/groups/, 409)
    await app.api.post('/groups', { name: 'Flat' })
    const trip = await app.api.post('/groups', { name: 'Trip' })
    await open(page, '/groups')

    // A new group with a taken name, in any capitals, says so beside the field.
    await page.getByPlaceholder('New group name…').fill('flat')
    await page.keyboard.press('Enter')
    await expect(page.getByRole('alert').filter({ hasText: 'You already have a group called "Flat".' })).toBeVisible()
    expect((await app.api.get('/groups')).filter((g) => g.name.toLowerCase() === 'flat')).toHaveLength(1)

    // Rename Trip, add a note and a label; Enter saves.
    await page.getByRole('button', { name: 'Edit Trip' }).click()
    await expect(page.getByLabel('Group name', { exact: true })).toBeFocused()
    await page.getByLabel('Group name', { exact: true }).fill('Hill cabin')
    await page.getByLabel('Note').fill('Flights and stay')
    await page.getByPlaceholder(/Labels/).fill('travel')
    await page.keyboard.press('Enter') // adds the label
    await page.getByLabel('Group name', { exact: true }).press('Enter') // saves
    await expect(page.getByText('Hill cabin', { exact: true })).toBeVisible()
    await expect(page.getByText('Flights and stay')).toBeVisible()
    await expect(page.getByText('travel', { exact: true })).toBeVisible()
    const saved = (await app.api.get(`/groups/${trip.id}`))
    expect(saved).toMatchObject({ name: 'Hill cabin', note: 'Flights and stay', labels: ['travel'] })

    // Renaming onto a taken name is refused in the form, which stays open.
    await page.getByRole('button', { name: 'Edit Hill cabin' }).click()
    await page.getByLabel('Group name', { exact: true }).fill('FLAT')
    await page.keyboard.press('Enter')
    await expect(page.getByRole('alert').filter({ hasText: 'You already have a group called "Flat".' })).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByLabel('Group name', { exact: true })).toHaveCount(0)
    expect((await app.api.get(`/groups/${trip.id}`)).name).toBe('Hill cabin')
  })

  test('the people picker takes typing, arrows, Enter and Escape', async ({ page, app }) => {
    const [row] = await app.api.get('/transactions?limit=1')
    const group = await app.api.post('/groups', { name: 'Dinner' })
    await app.api.post(`/groups/${group.id}/members`, { transaction_id: row.id })
    const people = await app.api.get('/people')
    const neha = people.find((p) => p.name === 'Neha Gupta')

    await open(page, '/groups')
    await page.getByRole('button', { name: 'Expand Dinner' }).click()
    const picker = page.getByRole('combobox', { name: 'Search people' }).first()
    await picker.fill('neha')
    const option = page.getByRole('option', { name: /Neha Gupta/ })
    // The match shows as it is stored, with one at-sign.
    await expect(option).toContainText('neha.gupta@paytm')
    await expect(option).not.toContainText('@neha.gupta@paytm')
    await page.keyboard.press('Escape')
    await expect(page.getByRole('listbox')).toHaveCount(0)
    await expect(picker).toHaveValue('neha')

    await picker.fill('neh')
    await page.keyboard.press('Enter') // the highlighted match
    await expect.poll(async () => (await app.api.get(`/groups/${group.id}`)).members[0].people).toEqual([neha.id])
  })

  test('Escape in a field of the open payment leaves the field first, and closes the panel second', async ({ page }) => {
    await open(page, '/transactions')
    await page.getByRole('row').nth(1).click()
    await expect(page.getByText('As printed on the statement')).toBeVisible()
    const tags = page.getByPlaceholder(/type and press Enter/)
    await tags.click()
    await page.keyboard.type('weekend')
    await page.keyboard.press('Escape')
    await expect(page.getByText('As printed on the statement')).toBeVisible()
    await expect(tags).toHaveValue('weekend')
    await page.keyboard.press('Escape')
    await expect(page.getByText('As printed on the statement')).toHaveCount(0)
  })
})
