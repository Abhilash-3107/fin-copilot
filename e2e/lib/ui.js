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

/** Go to a page and wait for the shell to render. */
export async function open(page, route) {
  await page.goto(`/#${route}`)
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

export const PERIOD_WORDS =
  /all[ -]time|every month|lifetime|since\s+\w+|this month|\b(?:January|February|March|April|June|July|August|September|October|November|December)\b|\bMay\b/i
