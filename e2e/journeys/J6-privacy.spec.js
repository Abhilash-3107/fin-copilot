// J6 - Privacy mode. With amounts hidden, no screen may show a rupee amount or
// a percentage of money: not in the text, a tooltip, a toast, the command
// palette, a chart, a field, the accessibility tree, or a title or aria-label.
import { test, expect } from '../fixtures.js'
import { accessibleMoneyLeaks, moneyLeaks, open, openSettingsTab } from '../lib/ui.js'

const hideWithButton = async (page) => {
  await page.getByRole('button', { name: 'Hide amounts' }).click()
  await expect(page.getByRole('button', { name: 'Show amounts' })).toBeVisible()
}

// Figures arrive after the heading does; blurred ones still hold their rupee sign.
const figuresLoaded = (page) => expect(page.getByText('₹').first()).toBeVisible()

// A policy limit ("small payments to people up to 500") is a rule setting, not
// a money figure, so it stays readable with amounts hidden: in its fields, in
// the toast that confirms it, and in Teach Me's offer of a policy. Nothing else
// is excused, and the allowance names exactly these.
const POLICY_LIMIT = {
  fields: ['Amount limit', 'Payments to people up to'],
  text: [/Limit set to ₹[\d,]+\./, /Sort small payments \(up to ₹[\d,]+\)/],
}

async function expectNoLeaks(page, where) {
  expect(await moneyLeaks(page, POLICY_LIMIT), `money showing on ${where} with amounts hidden`).toEqual([])
}

async function addPolicy(app) {
  const [category] = await app.api.get('/categories')
  await app.api.post('/policies', { max_amount: 500, category: category.category ?? category.name })
}

// What proves each screen has its content, so it is read after it has loaded and not before.
const CONTENT = {
  '/insights': (page) => figuresLoaded(page),
  '/transactions': (page) => figuresLoaded(page),
  '/people': (page) => figuresLoaded(page),
  '/groups': (page) => figuresLoaded(page),
  '/review': (page) => expect(page.getByRole('combobox', { name: 'Another category' })).toBeVisible(),
  // A statement row, under the table's header row.
  '/upload': (page) => expect(page.getByRole('row').nth(1)).toBeVisible(),
}

// Every Settings tab, and a line that shows it has loaded.
const SETTINGS_TABS = {
  General: (page) => expect(page.getByText('Developer mode', { exact: true })).toBeVisible(),
  'AI & Model': (page) => expect(page.getByRole('heading', { name: 'AI provider' })).toBeVisible(),
  Insights: (page) => expect(page.getByRole('heading', { name: 'Review bar' })).toBeVisible(),
  Categories: (page) => expect(page.getByRole('heading', { name: 'Categories', exact: true })).toBeVisible(),
  // The policy row is the only one whose figure is a field.
  Rules: (page) => expect(page.getByLabel('Amount limit')).toBeVisible(),
  Data: (page) => expect(page.getByRole('heading', { name: 'Sample data' })).toBeVisible(),
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
    ]) {
      await open(page, route)
      await CONTENT[route](page)
      await expectNoLeaks(page, name)
    }
  })

  test('every Settings tab shows no amount once hidden, bar the policy limit', async ({ page, app }) => {
    await addPolicy(app)
    await openSettingsTab(page, 'Rules')
    await SETTINGS_TABS.Rules(page)
    // The check reads fields: without the allowance it must see the limit.
    expect(await moneyLeaks(page), 'the policy limit field with amounts shown').not.toEqual([])
    await hideWithButton(page)

    for (const [tab, loaded] of Object.entries(SETTINGS_TABS)) {
      await page.getByRole('button', { name: tab, exact: true }).click()
      await loaded(page)
      await expectNoLeaks(page, `Settings > ${tab}`)
    }
    // The limit is still there to read.
    await page.getByRole('button', { name: 'Rules', exact: true }).click()
    await expect(page.getByLabel('Amount limit')).toHaveValue('500')
  })

  test('a screen reader is told a figure is hidden, and never given it', async ({ page }) => {
    await open(page, '/insights')
    await figuresLoaded(page)
    expect((await accessibleMoneyLeaks(page)).length, 'figures in the accessibility tree with amounts shown').toBeGreaterThan(0)
    await hideWithButton(page)

    for (const route of ['/insights', '/dashboard', '/transactions']) {
      await open(page, route)
      await figuresLoaded(page)
      expect(await accessibleMoneyLeaks(page), `money read out on ${route} with amounts hidden`).toEqual([])
    }
    await expect(page.locator('body')).toContainText('Amount hidden')
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
    await expect(page.getByRole('option', { name: /Swiggy/ }).first()).toBeVisible()
    await expectNoLeaks(page, 'the command palette')
  })

  // A chart is drawn on a canvas, so its tick labels and tooltip are not in the
  // page's text. Every string the canvas is asked to draw is recorded instead.
  test('a chart draws no amount on its ticks or in its tooltip once hidden', async ({ page }) => {
    await page.addInitScript(() => {
      window.__canvasText = []
      const original = CanvasRenderingContext2D.prototype.fillText
      CanvasRenderingContext2D.prototype.fillText = function (text, ...rest) {
        window.__canvasText.push(String(text))
        return original.call(this, text, ...rest)
      }
    })
    const drawn = () => page.evaluate(() => window.__canvasText)
    const hover = async () => {
      const box = await page.locator('canvas').first().boundingBox()
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
      await page.mouse.move(box.x + box.width / 2 + 5, box.y + box.height / 2)
    }
    const MONEY = /₹\s*[-−]?\d/
    const TOOLTIP_TITLE = /^\d{1,2} \w{3} \d{4}$/
    // A chart animates for about a second after its options change; wait for that to end.
    const settled = () => expect.poll(async () => {
      const before = (await drawn()).length
      await page.waitForTimeout(300)
      return (await drawn()).length === before
    }, 'chart settled').toBe(true)

    await open(page, '/dashboard')
    await figuresLoaded(page)
    await expect(page.locator('canvas').first()).toBeVisible()
    // The fixture holds Date.now still, and Chart.js times its animations with it,
    // so a tooltip would never fade in. Let it run again on this page.
    await page.evaluate(() => {
      const start = performance.now()
      const base = Date.now()
      Date.now = () => base + (performance.now() - start)
    })
    await settled()
    // Shown: the tooltip carries a rupee amount, so the recording sees what it must catch.
    await hover()
    await expect.poll(async () => (await drawn()).some((t) => TOOLTIP_TITLE.test(t)), 'tooltip drawn while shown').toBe(true)
    expect((await drawn()).filter((t) => /^₹[\d,]+$/.test(t)).length, 'tooltip amount drawn while shown').toBeGreaterThan(0)

    await hideWithButton(page)
    await settled()
    await page.evaluate(() => { window.__canvasText = [] })
    await hover()
    // The date heading of the tooltip is there, so it was drawn.
    await expect.poll(async () => (await drawn()).some((t) => TOOLTIP_TITLE.test(t)), 'tooltip drawn while hidden').toBe(true)
    const texts = await drawn()
    expect(texts.filter((t) => MONEY.test(t)), 'amounts drawn on the chart while hidden').toEqual([])
    expect(texts.filter((t) => t.includes('••')).length, 'masked ticks and tooltip drawn').toBeGreaterThan(5)
  })

  // The amounts a toast can carry come from the server (an upload's balance
  // warnings, masked by the toast and covered in ToastContext.test.jsx), or are
  // a policy limit, which stays readable. So this is the one toast to trigger.
  test('the toast that confirms a policy limit keeps the limit and adds no other amount', async ({ page, app }) => {
    await addPolicy(app)
    await openSettingsTab(page, 'Rules')
    await hideWithButton(page)
    const limit = page.getByLabel('Amount limit')
    await limit.fill('700')
    await limit.press('Enter')
    await expect(page.getByText(/^Limit set to ₹700\./)).toBeVisible()
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
    const overlaps =
      box.x < buttonBox.x + buttonBox.width && box.x + box.width > buttonBox.x &&
      box.y < buttonBox.y + buttonBox.height && box.y + box.height > buttonBox.y
    expect(overlaps, 'tooltip covering the button').toBe(false)
    // Nor may it sit over the page's own controls, such as Add statement.
    const covered = await page.evaluate(
      ({ x, y, width: w, height: h }) =>
        [...document.querySelectorAll('main a, main button, main h1')]
          .filter((el) => {
            const r = el.getBoundingClientRect()
            return r.width > 0 && r.left < x + w && r.right > x && r.top < y + h && r.bottom > y
          })
          .map((el) => el.textContent.trim()),
      box,
    )
    expect(covered, 'page controls under the tooltip').toEqual([])
  })
})
