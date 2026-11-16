import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ActionMenu from './ActionMenu.jsx'

function setup() {
  const onRename = vi.fn()
  const onDelete = vi.fn()
  render(
    <>
      <ActionMenu
        label="More for Kotak"
        items={[
          { label: 'Rename', onSelect: onRename },
          { label: 'Delete', onSelect: onDelete, danger: true },
        ]}
      />
      <p>outside</p>
    </>,
  )
  return { onRename, onDelete, user: userEvent.setup(), button: () => screen.getByRole('button', { name: 'More for Kotak' }) }
}

describe('ActionMenu', () => {
  it('is closed until opened, and says so to a screen reader', async () => {
    const { user, button } = setup()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button()).toHaveAttribute('aria-haspopup', 'menu')
    expect(button()).toHaveAttribute('aria-expanded', 'false')
    await user.click(button())
    expect(screen.getByRole('menu', { name: 'More for Kotak' })).toBeInTheDocument()
    expect(button()).toHaveAttribute('aria-expanded', 'true')
  })

  it('puts focus on the first item and moves with the arrow keys, wrapping', async () => {
    const { user, button } = setup()
    await user.click(button())
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
    await user.keyboard('{ArrowDown}')
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus()
    await user.keyboard('{ArrowUp}')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
    await user.keyboard('{Home}')
    expect(screen.getByRole('menuitem', { name: 'Rename' })).toHaveFocus()
    await user.keyboard('{End}')
    expect(screen.getByRole('menuitem', { name: 'Delete' })).toHaveFocus()
  })

  it('runs the chosen item with Enter, closes, and hands focus back to the button', async () => {
    const { user, button, onRename, onDelete } = setup()
    await user.click(button())
    await user.keyboard('{Enter}')
    expect(onRename).toHaveBeenCalledTimes(1)
    expect(onDelete).not.toHaveBeenCalled()
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button()).toHaveFocus()
  })

  it('closes on Escape and returns focus to the button', async () => {
    const { user, button } = setup()
    await user.click(button())
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(button()).toHaveFocus()
  })

  it('closes on Tab and on a click outside, without running anything', async () => {
    const { user, button, onRename, onDelete } = setup()
    await user.click(button())
    await user.keyboard('{Tab}')
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    await user.click(button())
    await user.click(screen.getByText('outside'))
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(onRename).not.toHaveBeenCalled()
    expect(onDelete).not.toHaveBeenCalled()
  })

  it('closes when the button is pressed again', async () => {
    const { user, button } = setup()
    await user.click(button())
    await user.click(button())
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  describe('placement', () => {
    const realRect = Element.prototype.getBoundingClientRect
    const realHeight = Object.getOwnPropertyDescriptor(window, 'innerHeight')
    const realWidth = Object.getOwnPropertyDescriptor(window, 'innerWidth')
    afterEach(() => {
      Element.prototype.getBoundingClientRect = realRect
      Object.defineProperty(window, 'innerHeight', realHeight)
      Object.defineProperty(window, 'innerWidth', realWidth)
    })

    // jsdom has no layout: say where the button is and how tall the menu is.
    function layout({ button, viewport = [1440, 768], menuHeight = 80 }) {
      Object.defineProperty(window, 'innerWidth', { value: viewport[0], configurable: true })
      Object.defineProperty(window, 'innerHeight', { value: viewport[1], configurable: true })
      Element.prototype.getBoundingClientRect = function rect() {
        if (this.getAttribute('role') === 'menu') return { top: 0, left: 0, right: 176, bottom: menuHeight, width: 176, height: menuHeight }
        if (this.tagName === 'BUTTON' && this.getAttribute('aria-haspopup')) {
          const [top, right] = button
          return { top, bottom: top + 24, left: right - 24, right, width: 24, height: 24 }
        }
        return realRect.call(this)
      }
    }
    const menuStyle = () => screen.getByRole('menu').style

    it('opens below the button when there is room', async () => {
      layout({ button: [100, 1000] })
      const { user, button } = setup()
      await user.click(button())
      expect(menuStyle().top).toBe('128px')
      expect(menuStyle().left).toBe('824px')
    })

    it('flips above the button when it would run off the bottom of the window', async () => {
      layout({ button: [720, 1000] })
      const { user, button } = setup()
      await user.click(button())
      // 720 - 4 gap - 80 tall
      expect(menuStyle().top).toBe('636px')
    })

    it('stays inside the window at the left edge', async () => {
      layout({ button: [100, 60] })
      const { user, button } = setup()
      await user.click(button())
      expect(menuStyle().left).toBe('8px')
    })

    it('stays inside the window at the right edge', async () => {
      layout({ button: [100, 1440], viewport: [1440, 768] })
      const { user, button } = setup()
      await user.click(button())
      // right-aligned to the button would end at 1440; keep an 8px margin.
      expect(menuStyle().left).toBe(`${1440 - 176 - 8}px`)
    })
  })

  it('shows hover and a visible focus ring, on the button and on every item', async () => {
    const { user, button } = setup()
    expect(button().className).toContain('hover:bg-')
    expect(button().className).toContain('focus-visible:ring-[#818cf8]')
    await user.click(button())
    for (const item of screen.getAllByRole('menuitem')) {
      expect(item.className).toContain('hover:bg-')
      expect(item.className).toContain('focus:ring-[#818cf8]')
    }
  })

  it('hands its button to a caller that needs to return focus to it', async () => {
    const seen = vi.fn()
    render(<ActionMenu label="More" items={[{ label: 'Rename', onSelect: () => {} }]} triggerRef={seen} />)
    expect(seen).toHaveBeenCalledWith(screen.getByRole('button', { name: 'More' }))
  })
})
