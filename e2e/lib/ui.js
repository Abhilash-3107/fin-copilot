// Helpers for reading figures off the screen. They work from visible text, so
// they survive restyling as long as the words and the rupee figures stay.
import { expect } from '@playwright/test'

const RUPEE = /(−|-|\+)?\s*₹\s*([\d,]+(?:\.\d+)?)/g

/** Every rupee figure in a piece of text, as numbers. A leading minus makes it negative. */
export function rupeeFigures(text) {
  return [...text.matchAll(RUPEE)].map(([, sign, digits]) => {
    const value = Number(digits.replaceAll(',', ''))
    return sign === '−' || sign === '-' ? -value : value
  })
}

/** The first rupee figure in a piece of text. Throws if there is none. */
export function firstRupees(text) {
  const [first] = rupeeFigures(text)
  if (first === undefined) throw new Error(`No rupee figure in: ${JSON.stringify(text)}`)
  return first
}

/** The figure in the block that carries `label`, e.g. a tile labelled "Kept". */
export async function figureNear(page, label) {
  const block = page.getByText(label, { exact: true }).first().locator('..')
  await expect(block).toBeVisible()
  return firstRupees(await block.innerText())
}

/**
 * The card under a heading: the nearest ancestor of the heading's text that
 * holds a table or a list. `heading` is a string or a regular expression.
 */
export function sectionOf(page, heading) {
  return page
    .getByText(heading)
    .first()
    .locator('xpath=ancestor::div[.//table or .//ul][1]')
}

/**
 * What proves a route has rendered, and not the page that was showing before.
 * A route with a heading names it. Transactions waits for its own button. Teach Me
 * has neither, so open() waits for the request only that page makes, its queue.
 */
const ARRIVED = {
  '/dashboard': (page) => page.getByRole('heading', { name: 'Your money at a glance' }),
  '/insights': (page) => page.getByRole('heading', { name: 'Money Map' }),
  '/people': (page) => page.getByRole('heading', { name: 'People', exact: true }),
  '/groups': (page) => page.getByRole('heading', { name: 'Groups', exact: true }),
  '/upload': (page) => page.getByRole('heading', { name: 'Add Statement' }),
  '/settings': (page) => page.getByRole('heading', { name: 'Settings', exact: true }),
  '/transactions': (page) => page.getByRole('searchbox'),
  // Teach Me has no fixed text: its empty and finished states are what some journeys judge.
  '/review': null,
}

/** Go to a route and wait until that page, not the one before it, is on screen. */
export async function open(page, route) {
  const key = route.split('?')[0]
  const arrived = ARRIVED[key]
  if (arrived === undefined) throw new Error(`open(): no landmark known for ${key}. Add it to ARRIVED in lib/ui.js.`)
  // Hash navigation inside the page does not reload it, so a route that is
  // already showing would keep its old data. Reload for a fresh read.
  const same = new URL(page.url(), 'http://x').hash === `#${route}`
  const queue = key === '/review' ? page.waitForResponse((r) => r.url().includes('/api/annotations/review-queue')) : null
  if (same) await page.reload()
  else await page.goto(`/#${route}`)
  await queue
  if (arrived) await expect(arrived(page)).toBeVisible()
  await expect(page.getByRole('main')).toBeVisible()
}

/**
 * Sum what a Transactions list shows: debits count as money out, credits
 * reduce it, so the total is the net spend the figure a user clicked claims.
 */
export async function visibleSpend(page) {
  const amounts = await page.getByRole('row').locator('td').filter({ hasText: /^[−+]₹/ }).allInnerTexts()
  let out = 0
  for (const text of amounts) {
    const value = Math.abs(firstRupees(text))
    out += text.trim().startsWith('+') ? -value : value
  }
  return Math.round(out * 100) / 100
}

/**
 * Wait until a Transactions list has finished loading its rows. The Ledger's own
 * button comes first, so a row of another page, or of this one still loading,
 * cannot satisfy the wait.
 */
export async function waitForRows(page) {
  await ARRIVED['/transactions'](page).waitFor()
  await expect(page.getByRole('row').nth(1)).toBeVisible()
}

const MONTHS = 'January|February|March|April|May|June|July|August|September|October|November|December'

/**
 * Words that say over what period a figure is counted: "all time", "every
 * month", "in November" or "November 2026". A bare month name does not count,
 * because a person called April would satisfy it.
 */
export const PERIOD_WORDS = new RegExp(
  `all[ -]time|every month|lifetime|since\\s+(?:${MONTHS})|this month|\\b(?:in|during|for)\\s+(?:${MONTHS})\\b|\\b(?:${MONTHS})\\s+\\d{4}\\b`,
  'i',
)

/** Open Settings on one of its tabs. The tab names match whole, so "Data" is not "Remove sample data". */
export async function openSettingsTab(page, name) {
  await open(page, '/settings')
  await page.getByRole('button', { name, exact: true }).click()
}

// What a leak looks like: a rupee amount (the minus may come before the rupee
// sign or after it), or a share of money.
const LEAK_PATTERNS = [
  /.{0,30}[₹]\s*[-−]?\d.{0,20}/g,
  /.{0,30}\d+\s*%\s*(of\s+)?(what came in|money in|money out).{0,10}/gi,
  /.{0,10}\b(kept|saved|spent)\b.{0,24}\d+\s*%.{0,10}/gi,
]
const patternArgs = () => LEAK_PATTERNS.map((re) => [re.source, re.flags])

/**
 * What privacy mode must hide, found on the screen as it is now. A hidden
 * figure stays in the page, blurred, inside an element marked
 * data-privacy-mask (and aria-hidden), so the walk skips those. What is left
 * must hold no rupee amount and no percentage of money. Text, tooltips, the
 * title, aria-label, placeholder and alt of every element, and the value of
 * every field are read. Confidence and accuracy percentages are not money and
 * are left alone. Returns the offending snippets.
 *
 * `allow` is for what is deliberately not hidden: `fields` lists the
 * accessible names (a prefix is enough) of number fields whose value may show,
 * `text` lists regular expressions for sentences that may show an amount.
 * A number field holds money in digits with no rupee sign, so any one that is
 * not allowed counts as a leak.
 */
export async function moneyLeaks(page, allow = {}) {
  const { fields = [], text: allowedText = [] } = allow
  return page.evaluate(
    ({ patterns, fields, allowedText }) => {
      const allowed = allowedText.map((source) => new RegExp(source, 'g'))
      const fieldLeaks = []
      const seen = []
      // Fields first, read from the page itself: a value typed or set after load
      // is a property, not an attribute.
      const nameOf = (el) =>
        el.getAttribute('aria-label') || [...(el.labels ?? [])].map((l) => l.textContent).join(' ').trim()
      for (const el of document.querySelectorAll('input, textarea')) {
        const value = el.value ?? ''
        if (!value) continue
        const name = nameOf(el)
        if (fields.some((f) => name.startsWith(f))) continue
        if (el.type === 'number' || /₹\s*[-−]?\d/.test(value)) fieldLeaks.push(`field "${name || el.type}": ${value}`)
      }

      const clone = document.body.cloneNode(true)
      for (const el of clone.querySelectorAll('[data-privacy-mask]')) el.remove()
      for (const el of clone.querySelectorAll('script, style')) el.remove()
      // A space between elements keeps "39%" and "of what came in" in one sentence
      // while stopping neighbouring cells from running together.
      const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT)
      let body = ''
      for (let node = walker.nextNode(); node; node = walker.nextNode()) body += ` ${node.nodeValue}`
      seen.push(body.replace(/\s+/g, ' '))
      for (const el of clone.querySelectorAll('*')) {
        for (const name of ['title', 'aria-label', 'placeholder', 'alt']) {
          const value = el.getAttribute(name)
          if (value) seen.push(value)
        }
      }
      const res = patterns.map(([source, flags]) => new RegExp(source, flags))
      const lines = seen.map((line) => allowed.reduce((rest, re) => rest.replace(re, ' '), line))
      return [...fieldLeaks, ...lines.flatMap((line) => res.flatMap((re) => line.match(re) ?? []))]
    },
    { patterns: patternArgs(), fields, allowedText: allowedText.map((re) => re.source) },
  )
}

/**
 * What a screen reader is given: the page's accessibility tree, as text, held
 * to the same patterns. A hidden figure must read as "Amount hidden", not as
 * its digits. Returns the offending snippets.
 */
export async function accessibleMoneyLeaks(page) {
  const tree = await page.locator('body').ariaSnapshot()
  return LEAK_PATTERNS.flatMap((re) => tree.match(re) ?? [])
}

/**
 * Teach Me: press Skip until the card for a payment whose accessible name matches `pattern`
 * is showing, and return it. The other cards are not the journey's business.
 */
export async function skipToPayment(page, pattern) {
  const wanted = page.getByRole('group', { name: pattern })
  const anyCard = page.getByRole('group', { name: /^Payment (to|from) / })
  await expect(anyCard).toBeVisible()
  for (let i = 0; i < 12; i++) {
    const label = await anyCard.getAttribute('aria-label')
    if (pattern.test(label)) break
    await page.keyboard.press('s')
    await expect(anyCard).not.toHaveAttribute('aria-label', label)
  }
  await expect(wanted).toBeVisible()
  return wanted
}

/**
 * Open a row's "More" menu. The menu closes when the page scrolls under it, so a button
 * that Playwright has to scroll into view is brought into view first and the scroll's own
 * events are let through before the click.
 */
export async function openMenu(menuButton) {
  await menuButton.scrollIntoViewIfNeeded()
  await menuButton.page().evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await menuButton.click()
}
