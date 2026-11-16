// Every keyboard shortcut the app has, in one list: the `?` sheet renders it, so
// a shortcut added here shows up there. The handlers live beside what they do
// (the palette, Teach Me, the Transactions page) and are described here.

export const OPEN_PALETTE_EVENT = 'fc:open-palette'
export const OPEN_SHORTCUTS_EVENT = 'fc:open-shortcuts'

export function isMac() {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform ?? '')
}

// "⌘" on a Mac, "Ctrl" elsewhere.
export function modKeyLabel() {
  return isMac() ? '⌘' : 'Ctrl'
}

export function openPalette() {
  window.dispatchEvent(new Event(OPEN_PALETTE_EVENT))
}

export function openShortcuts() {
  window.dispatchEvent(new Event(OPEN_SHORTCUTS_EVENT))
}

export function shortcutScopes() {
  const mod = modKeyLabel()
  return [
    {
      scope: 'Everywhere',
      items: [
        { keys: [mod, 'K'], label: 'Search payments, people and groups, or jump to a page' },
        { keys: ['Shift', 'P'], label: 'Hide or show amounts' },
        { keys: ['?'], label: 'Show this list' },
      ],
    },
    {
      scope: 'Search',
      items: [
        { keys: ['↑', '↓'], label: 'Move through the results' },
        { keys: ['↵'], label: 'Open the highlighted result' },
        { keys: ['Esc'], label: 'Close search' },
      ],
    },
    {
      scope: 'Teach Me',
      items: [
        { keys: ['1', '2', '3'], label: 'Choose a suggested category' },
        { keys: ['↵'], label: 'Confirm the highlighted category' },
        { keys: ['/'], label: 'Type to find another category' },
        { keys: ['S'], label: 'Skip this payment' },
        { keys: ['Z'], label: 'Undo the last answer' },
        { keys: ['A'], label: 'Apply to every payment from this payee' },
      ],
    },
    {
      scope: 'Transactions',
      items: [
        { keys: ['Esc'], label: 'Close the open payment' },
      ],
    },
  ]
}
