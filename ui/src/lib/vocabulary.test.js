import { describe, it, expect } from 'vitest'
import * as espree from 'espree'

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

// [pattern, what to say instead]
const BANNED = [
  [/annotat\w*/i, 'Sort ("Sort", "Sort again", "Sorted 41 payments")'],
  [/embedding\w*/i, 'remembered payees'],
  [/counterpart\w*/i, 'payee'],
  [/\bRAG\b/, 'matched from history'],
  [/\bkNN\b/i, 'matched from history'],
  [/pipeline/i, 'say what it does, not how'],
  [/threshold/i, '"When to ask you"'],
  [/\b(re-?)?categori[sz]\w*/i, 'sort / sorted'],
  [/\bmerchants?\b/i, 'payee'],
  [/\btxns?\b/i, 'payments'],
  [/\bupload\w*/i, 'statement ("Add statement", "Adding...")'],
  [/\bmemor(y|ies|iz\w*)\b/i, 'remembered payees'],
  [/\blearn\w*/i, 'remembered payees'],
  [/\bconfidence\b/i, 'a guess, "68% sure"'],
  [/\bAI guess\b/i, 'a guess, "68% sure"'],
  [/\b(your|the|my) copilot\b/i, 'you (second person)'],
  [/\bI\b/, 'you (second person)'],  // also I'll, I'm
  [/\bmy\b/i, 'you (second person)'],
  [/\bfailed\b|Something went wrong/i, `"Couldn't X." then the reason`],
]

// "Transactions" is a page name and may stand alone; in running text it is "payments".
const TRANSACTION = /\btransactions?\b/i

const modules = import.meta.glob(['../**/*.{js,jsx}', '!../**/*.test.{js,jsx}', '!../test/**'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

const WORD_ATTRIBUTES = ['title', 'placeholder', 'aria-label', 'alt', 'aria-description', 'label']

// Every user-facing string in a source, with the line it is on.
export function userStrings(source) {
  const ast = espree.parse(source, {
    ecmaVersion: 2022, sourceType: 'module', ecmaFeatures: { jsx: true }, loc: true,
  })
  const found = []
  function visit(node, parent, key) {
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
    }
    // Otherwise a single token that is not capitalised is an identifier, a
    // route or a class name ('annotate', '/transactions', 'learned_rule'),
    // not a sentence a person reads.
    if (text && text.trim() && (isWord || !(/^\S+$/.test(text) && !/^[A-Z]/.test(text)))) {
      found.push({ line: node.loc.start.line, text: text.replace(/\s+/g, ' ').trim() })
    }
    for (const k of Object.keys(node)) {
      if (k === 'loc') continue
      const child = node[k]
      if (Array.isArray(child)) child.forEach(c => visit(c, node, k))
      else if (child && typeof child === 'object') visit(child, node, k)
    }
  }
  visit(ast, null, null)
  return found
}

export function problems(strings) {
  return strings.flatMap(({ line, text }) => {
    const hits = BANNED.filter(([re]) => re.test(text)).map(([re, use]) => `"${text.match(re)[0]}": use ${use}`)
    if (TRANSACTION.test(text) && text !== 'Transactions') hits.push('"transaction": use "payment" (only the page name is "Transactions")')
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
