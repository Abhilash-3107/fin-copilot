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
  '/transactions': (page) => page.getByRole('button', { name: 'Auto-annotate' }),
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

/** Wait until a Transactions list has finished loading its rows. */
export async function waitForRows(page) {
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
