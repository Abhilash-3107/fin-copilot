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

    await page.getByRole('button', { name: 'Undo', exact: true }).first().click()
    await expect(card(page)).toHaveAttribute('aria-label', `Payment to ${bare.payee ?? bare.raw_description}`)
    expect(await rowOf()).toEqual(before)
  })
})
