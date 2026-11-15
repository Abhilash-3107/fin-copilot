// Fails the run when any page logged a console error or got a failed request,
// even in a test marked as an expected failure. Such a test absorbs every
// error, so without this the problem would never reach anyone.
const ATTACHMENT = 'hygiene-violations' // keep in step with HYGIENE_ATTACHMENT in fixtures.js

export default class HygieneReporter {
  violations = []

  onTestEnd(test, result) {
    for (const attachment of result.attachments) {
      if (attachment.name !== ATTACHMENT) continue
      this.violations.push({ title: test.titlePath().slice(1).join(' > '), body: attachment.body?.toString() ?? '' })
    }
  }

  onEnd() {
    if (this.violations.length === 0) return undefined
    console.error('\nBrowser console and network problems (these fail the run, expected failure or not):')
    for (const { title, body } of this.violations) {
      console.error(`  ${title}`)
      for (const line of body.split('\n')) console.error(`    ${line}`)
    }
    return { status: 'failed' }
  }

  printsToStdio() {
    return false
  }
}
