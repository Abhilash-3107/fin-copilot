// J6 - Privacy mode. With amounts hidden, no screen may show a rupee amount or
// a percentage of money: not in the text, a tooltip, a toast, the command
// palette, or a title or aria-label.
import { test, expect } from '../fixtures.js'
import { moneyLeaks, open, openSettingsTab } from '../lib/ui.js'

const hideWithButton = async (page) => {
  await page.getByRole('button', { name: 'Hide amounts' }).click()
  await expect(page.getByRole('button', { name: 'Show amounts' })).toBeVisible()
}

// Figures arrive after the heading does; blurred ones still hold their rupee sign.
const figuresLoaded = (page) => expect(page.getByText('₹').first()).toBeVisible()

async function expectNoLeaks(page, where) {
  expect(await moneyLeaks(page), `money showing on ${where} with amounts hidden`).toEqual([])
}

test.describe('J6 privacy mode', () => {
  test.beforeEach(async ({ app }) => {
    await app.loadDemo()
  })

  test('the screens show no amount once hidden', async ({ page }) => {
    await open(page, '/dashboard')
    await figuresLoaded(page)
    // Before hiding, the check must see the figures it is meant to catch.
    expect((await moneyLeaks(page)).length, 'figures on the Dashboard with amounts shown').toBeGreaterThan(0)
    await hideWithButton(page)

    await expectNoLeaks(page, 'the Dashboard')

    for (const [route, name] of [
      ['/insights', 'Money Map'],
      ['/transactions', 'Transactions'],
      ['/review', 'Teach Me'],
      ['/people', 'People'],
      ['/groups', 'Groups'],
      ['/upload', 'Add Statement'],
      ['/settings', 'Settings'],
    ]) {
      await open(page, route)
      if (route === '/review') await expect(page.getByText('My best guess').first()).toBeVisible()
      if (!['/upload', '/settings'].includes(route)) await figuresLoaded(page)
      await expectNoLeaks(page, name)
    }
  })

  test('Shift+P hides and shows them, and the choice survives a reload', async ({ page }) => {
    await open(page, '/insights')
    await figuresLoaded(page)
    await page.keyboard.press('Shift+P')
    await expect(page.getByRole('button', { name: 'Show amounts' })).toBeVisible()
    await expectNoLeaks(page, 'Money Map')
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Money Map' })).toBeVisible()
    await figuresLoaded(page)
    await expectNoLeaks(page, 'Money Map after a reload')
    await page.keyboard.press('Shift+P')
    await expect(page.getByRole('button', { name: 'Hide amounts' })).toBeVisible()
    expect((await moneyLeaks(page)).length).toBeGreaterThan(0)
  })

  test('the command palette hides the amount of a payment it finds', async ({ page }) => {
    await open(page, '/dashboard')
    await hideWithButton(page)
    await page.keyboard.press('ControlOrMeta+k')
    const search = page.getByPlaceholder(/Search transactions/)
    await search.fill('swiggy')
    await expect(page.getByRole('button', { name: /Swiggy/ }).first()).toBeVisible()
    await expectNoLeaks(page, 'the command palette')
  })

  test('a chart tooltip is hidden too', async ({ page }) => {
    await open(page, '/dashboard')
    await figuresLoaded(page)
    await hideWithButton(page)
    const chart = page.locator('canvas').first()
    await expect(chart).toBeVisible()
    const box = await chart.boundingBox()
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.move(box.x + box.width / 2 + 5, box.y + box.height / 2)
    await expectNoLeaks(page, 'the chart')
  })

  test('a toast that carries an amount hides it', async ({ page, app }) => {
    const [category] = await app.api.get('/categories')
    await app.api.post('/policies', { max_amount: 500, category: category.category ?? category.name })
    await openSettingsTab(page, 'Rules')
    await hideWithButton(page)
    const limit = page.getByLabel('Amount limit')
    await limit.fill('700')
    await limit.press('Enter')
    await expect(page.getByText(/^Limit set to/)).toBeVisible()
    await expectNoLeaks(page, 'the toast')
  })

  test('the toggle tooltip stays inside the window and in one piece', async ({ page }) => {
    await open(page, '/dashboard')
    const button = page.getByRole('button', { name: 'Hide amounts' })
    const buttonBox = await button.boundingBox()
    await button.hover()
    const tip = page.getByText('Hide all amounts (Shift+P)')
    await expect(tip).toBeVisible()
    const box = await tip.boundingBox()
    const { width } = page.viewportSize()
    expect(box.x + box.width, 'tooltip right edge').toBeLessThanOrEqual(width)
    expect(box.height, 'tooltip height, one line').toBeLessThan(40)
    const overlaps = box.y < buttonBox.y + buttonBox.height && box.y + box.height > buttonBox.y
    expect(overlaps, 'tooltip covering the button').toBe(false)
  })
})
