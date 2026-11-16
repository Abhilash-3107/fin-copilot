import { describe, it, expect } from 'vitest'
import * as espree from 'espree'
import words from './bannedWords.json'

// One vocabulary (design direction 8). The words below belong to the machine
// room: a person reading the app never meets them. This reads every string a
// source file can put on screen (JSX text, string and template literals) and
// fails on any of them, so a new button or toast cannot bring one back.
//
// Developer mode shows the machinery on purpose, so the components that exist
// only for it are listed here, by name. Nothing else is exempt.
const DEVELOPER_MODE_ONLY = [
  '../components/ReasoningPanel.jsx',
  '../components/RunSummary.jsx',
]

// One list for the interface and the server (finance_copilot/tests/test_server_words.py):
// [pattern, what to say instead].
const BANNED = words.banned.map(w => [new RegExp(w.pattern, w.flags), w.use])

const modules = import.meta.glob(['../**/*.{js,jsx}', '!../**/*.test.{js,jsx}', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

// An object of display labels, like SOURCE_LABEL in lib/sources.js: each value
// is a word a person reads even when it is one lowercase word ('manual').
const LABEL_MAP_NAME = /(LABEL|TITLE|NAME|COPY)S?$/

const WORD_ATTRIBUTES = ['title', 'placeholder', 'aria-label', 'alt', 'aria-description', 'label']

// Every user-facing string in a source, with the line it is on.
export function userStrings(source) {
  const ast = espree.parse(source, {
    ecmaVersion: 2022, sourceType: 'module', ecmaFeatures: { jsx: true }, loc: true,
  })
  const found = []
  function visit(node, parent, key, inLabelMap = false) {
    if (!node || typeof node.type !== 'string') return
    let text = null
    let isWord = false // text a person reads even when it is one lowercase word
    if (node.type === 'JSXText') {
      text = node.value
      isWord = true
    } else if (node.type === 'TemplateElement') {
      text = node.value.cooked
    } else if (node.type === 'Literal' && typeof node.value === 'string') {
      const isModuleName = /^(Import|Export)\w*Declaration$/.test(parent?.type)
      const isKey = parent?.type === 'Property' && key === 'key' && !parent.computed
      if (!isModuleName && !isKey) text = node.value
      // title="payment", count(n, 'payment'): a word by position.
      isWord = (parent?.type === 'JSXAttribute' && WORD_ATTRIBUTES.includes(parent.name.name))
        || (parent?.type === 'CallExpression' && ['count', 'plural'].includes(parent.callee.name))
        || (inLabelMap && parent?.type === 'Property' && key === 'value')
    }
    // Otherwise a single token that is not capitalised is an identifier, a
    // route or a class name ('annotate', '/transactions', 'learned_rule'),
    // not a sentence a person reads.
    if (text && text.trim() && (isWord || !(/^\S+$/.test(text) && !/^[A-Z]/.test(text)))) {
      found.push({ line: node.loc.start.line, text: text.replace(/\s+/g, ' ').trim() })
    }
    const labels = inLabelMap
      || (node.type === 'VariableDeclarator' && LABEL_MAP_NAME.test(node.id?.name ?? '') && node.init?.type === 'ObjectExpression')
    for (const k of Object.keys(node)) {
      if (k === 'loc') continue
      const child = node[k]
      if (Array.isArray(child)) child.forEach(c => visit(c, node, k, labels))
      else if (child && typeof child === 'object') visit(child, node, k, labels)
    }
  }
  visit(ast, null, null)
  return found
}

export function problems(strings) {
  return strings.flatMap(({ line, text }) => {
    // "Transactions" is a page name and may stand alone; in running text it is "payments".
    if (words.wholeStringsAllowed.includes(text)) return []
    const hits = BANNED.filter(([re]) => re.test(text)).map(([re, use]) => `"${text.match(re)[0]}": use ${use}`)
    return hits.map(h => `line ${line}: ${h} in "${text.slice(0, 80)}"`)
  })
}

describe('user-facing vocabulary', () => {
  it('reads the strings a source can show', () => {
    const strings = userStrings(`
      import x from './annotations/thing.js'
      const a = { annotation_id: 'learned_rule', title: 'Save changes' }
      const b = <p title="Hello there">Pay <b>now</b> {\`and \${a} later\`}</p>
    `).map(s => s.text)
    expect([...strings].sort()).toEqual(['Hello there', 'Pay', 'Save changes', 'and', 'later', 'now'])
  })

  it('flags the machine-room words and ignores identifiers', () => {
    const bad = problems(userStrings(`
      const x = <button>Auto-annotate</button>
      const y = 'Categorized 3 transactions'
      const z = \`Upload failed: \${e}\`
    `))
    expect(bad.length).toBeGreaterThanOrEqual(4)
    expect(problems(userStrings(`const ok = { source: 'annotate', url: '/annotations/jobs', name: 'Transactions' }`))).toEqual([])
  })

  it('reads the single lowercase words in a label map', () => {
    const bad = problems(userStrings(`
      const SOURCE_LABEL = { rag: 'annotated', manual: 'manual' }
      const route = { source: 'annotate' }
    `))
    expect(bad).toHaveLength(1)
    expect(bad[0]).toContain('annotat')
  })

  it('flags first person ("I\'ll", "my") in text', () => {
    const bad = problems(userStrings(`const a = <p>I'll remember that</p>; const b = 'My best guess'`))
    expect(bad).toHaveLength(2)
  })

  it('finds the sources it is meant to check', () => {
    expect(Object.keys(modules).length).toBeGreaterThan(40)
    for (const name of DEVELOPER_MODE_ONLY) expect(modules).toHaveProperty([name])
  })

  it('shows no machine-room word outside developer mode', () => {
    const offenders = Object.entries(modules)
      .filter(([file]) => !DEVELOPER_MODE_ONLY.includes(file))
      .flatMap(([file, source]) => problems(userStrings(source)).map(p => `${file} ${p}`))
    expect(offenders).toEqual([])
  })
})
