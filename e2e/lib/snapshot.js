// "Stored facts": everything the user put into the ledger or the app derived
// once and stored. Taken through the API so it does not depend on the screen.
// J8 compares one before a backup and after a restore. J15 compares one across
// an upgrade. Figures computed on the fly (totals, deltas, stats) are left out.

const PAGE = 1000

async function allTransactions(api) {
  const rows = []
  let after = ''
  for (;;) {
    const page = await api.get(`/transactions?include=annotation&limit=${PAGE}${after}`)
    rows.push(...page)
    if (page.length < PAGE) return rows
    after = `&after=${page.at(-1).id}`
  }
}

const byId = (a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)

export async function storedFacts(api) {
  const [statements, transactions, people, groupList, rules, policies, categories, config, llm] =
    await Promise.all([
      api.get('/statements'),
      allTransactions(api),
      api.get('/people'),
      api.get('/groups'),
      api.get('/rules'),
      api.get('/policies'),
      api.get('/categories'),
      api.get('/config'),
      api.get('/config/llm'),
    ])

  const groups = []
  for (const { id } of groupList) {
    const g = await api.get(`/groups/${id}`)
    groups.push({
      id: g.id,
      name: g.name,
      note: g.note,
      labels: g.labels,
      members: [...g.members]
        .map((m) => ({ transaction_id: m.transaction_id, txn_type: m.txn_type, people: m.people }))
        .sort((a, b) => (a.transaction_id < b.transaction_id ? -1 : 1)),
    })
  }

  return {
    // The vector store is rebuilt on demand, so what is embedded is not a stored fact.
    statements: statements
      .map(({ embedded_count, ...rest }) => rest) // eslint-disable-line no-unused-vars
      .sort(byId),
    transactions: transactions
      .map((t) => ({
        id: t.id,
        statement_id: t.statement_id,
        txn_date: t.txn_date,
        amount: t.amount,
        debit_credit: t.debit_credit,
        raw_description: t.raw_description,
        running_balance: t.running_balance,
        category: t.category ?? null,
        subcategory: t.subcategory ?? null,
        merchant: t.merchant ?? null,
        tags: t.tags ?? [],
        source: t.source ?? null,
        confidence: t.confidence ?? null,
      }))
      .sort(byId),
    people: people.map(({ stats, ...rest }) => rest).sort(byId), // eslint-disable-line no-unused-vars
    groups: groups.sort(byId),
    rules: [...rules].sort(byId),
    policies: [...policies].sort(byId),
    categories: [...categories].sort(byId),
    settings: { config, llm },
  }
}
