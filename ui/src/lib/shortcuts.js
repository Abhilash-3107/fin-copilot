// Every keyboard shortcut the app has, in one list: the `?` sheet renders it, so
// a shortcut added here shows up there. The handlers live beside what they do
// (the palette, Teach Me, the Transactions page) and are described here.

// The keys themselves. A handler reads its key from here, and so do the
// legends that name it (the sheet, Teach Me's footer, the card's buttons), so
// a key changed here changes everywhere. Letters are lowercase; `keyLabel` shows them.
export const PALETTE_KEY = 'k' // with the modifier
export const PRIVACY_KEY = 'p' // with Shift
export const SHORTCUTS_KEY = '?'
export const TEACH_ME_KEYS = {
  choose: ['1', '2', '3'],
  confirm: 'Enter',
  another: '/',
  skip: 's',
  undo: 'z',
  group: 'a',
}
export const LEDGER_KEYS = { closePanel: 'Escape' }

const KEY_LABELS = { Enter: '↵', Escape: 'Esc' }
export const keyLabel = key => KEY_LABELS[key] ?? key.toUpperCase()

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
  const t = TEACH_ME_KEYS
  return [
    {
      scope: 'Everywhere',
      items: [
        { keys: [mod, keyLabel(PALETTE_KEY)], label: 'Search payments, people and groups, or jump to a page' },
        { keys: ['Shift', keyLabel(PRIVACY_KEY)], label: 'Hide or show amounts' },
        { keys: [keyLabel(SHORTCUTS_KEY)], label: 'Show this list' },
      ],
    },
    {
      scope: 'Search',
      items: [
        { keys: ['↑', '↓'], label: 'Move through the results' },
        { keys: [keyLabel('Enter')], label: 'Open the highlighted result' },
        { keys: [keyLabel('Escape')], label: 'Close search' },
      ],
    },
    {
      scope: 'Teach Me',
      items: [
        { keys: t.choose.map(keyLabel), label: 'Choose a suggested category' },
        { keys: [keyLabel(t.confirm)], label: 'Confirm the highlighted category' },
        { keys: [keyLabel(t.another)], label: 'Type to find another category' },
        { keys: [keyLabel(t.skip)], label: 'Skip this payment' },
        { keys: [keyLabel(t.undo)], label: 'Undo the last answer' },
        { keys: [keyLabel(t.group)], label: 'Apply to every payment from this payee' },
      ],
    },
    {
      scope: 'Transactions',
      items: [
        { keys: [keyLabel(LEDGER_KEYS.closePanel)], label: 'Close the open payment' },
      ],
    },
  ]
}
