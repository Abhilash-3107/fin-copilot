// What migration 041 must have made of a ledger, worked out again from the
// statements the previous release held, without looking at the migration. J15
// holds the upgraded accounts against this, so an account that splits, folds the
// wrong way, goes by a raw key or takes a name nobody typed is a failure.
//
// The rules, as release notes would say them:
//   * A bank is the stored bank_name trimmed, lower case, without " (auto)".
//   * Text that is not a bank key a parser reports is what a user typed with the
//     old Rename. On a number that a real bank's statements carry, it joins that
//     bank; either way it becomes the account's name (the latest statement's text).
//   * Within a bank, one number (or none) is one account holding every statement,
//     unnumbered ones included; two or more numbers are one account each, and the
//     unnumbered statements one more.

const BANKS = {
  kotak: 'Kotak', hdfc: 'HDFC', icici: 'ICICI', sbi: 'SBI', axis: 'Axis', 'yes bank': 'Yes Bank', idfc: 'IDFC FIRST',
  indusind: 'IndusInd', 'punjab national': 'Punjab National', canara: 'Canara', 'union bank': 'Union Bank',
  'bank of baroda': 'Bank of Baroda', federal: 'Federal', rbl: 'RBL', 'au small finance': 'AU Small Finance', generic: 'Bank',
}

const newest = (a, b) => {
  const key = (s) => `${s.statement_month ?? ''}|${s.uploaded_at ?? ''}|${s.id}`
  return key(a) < key(b) ? 1 : -1
}

/** The words the app shows for a bank key: the known name, else the key in capitals, else "Bank". */
export function bankWords(bank) {
  return BANKS[bank] ?? (bank === '' ? 'Bank' : bank.replace(/\b\p{L}/gu, (c) => c.toUpperCase()))
}

export function lastFour(ref) {
  const digits = (ref ?? '').replace(/[- ]/g, '')
  return digits ? digits.slice(-4) : null
}

/**
 * @param {{id: string, bank_name: string, account_ref: string|null, statement_month?: string, uploaded_at?: string}[]} statements
 * @returns {Map<string, {key: string, bank: string, ref: string|null, name: string|null, label: string}>} by statement id
 */
export function expectedAccounts(statements) {
  const read = statements.map((s) => {
    let typed = (s.bank_name ?? '').trim()
    if (typed.toLowerCase().endsWith('(auto)')) typed = typed.slice(0, -6).trim()
    const ref = (s.account_ref ?? '').trim() || null
    const bank = typed.toLowerCase()
    return { ...s, typed, ref, bank, isBank: Object.hasOwn(BANKS, bank) }
  })
  const placed = read.map((s) => {
    let bank = s.bank
    if (!s.isBank && s.ref) {
      const home = read.filter((c) => c.isBank && c.ref === s.ref).sort(newest)[0]
      if (home) bank = home.bank
    }
    return { ...s, bank, text: !s.isBank && s.typed !== '' ? s.typed : null }
  })
  const numbers = new Map()
  for (const s of placed) if (s.ref) numbers.set(s.bank, new Set([...(numbers.get(s.bank) ?? []), s.ref]))
  const grouped = placed.map((s) => {
    const own = numbers.get(s.bank)
    return { ...s, ref: s.ref ?? (own?.size === 1 ? [...own][0] : null) }
  })
  const out = new Map()
  for (const s of grouped) {
    const key = JSON.stringify([s.bank, s.ref])
    const name = grouped.filter((x) => JSON.stringify([x.bank, x.ref]) === key && x.text).sort(newest)[0]?.text ?? null
    const last4 = lastFour(s.ref)
    out.set(s.id, { key, bank: s.bank, ref: s.ref, name, label: name ?? (last4 ? `${bankWords(s.bank)} ··${last4}` : bankWords(s.bank)) })
  }
  return out
}
