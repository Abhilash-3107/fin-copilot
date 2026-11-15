// Two facts the API does not show but the migrations in a release rewrite: the
// category ids on annotations (migration 036 fills the ones the old demo seeder
// left empty) and each skipped payment's snooze time (migration 035 moves it
// from the annotation to the payment). They are read straight from the SQLite
// file, with the server stopped, through Python's own sqlite3.
//
// plantLegacyRows writes into the same file two rows the previous release's API
// cannot make, so the migrations that repair them have something to repair.
import { execFileSync } from 'node:child_process'
import path from 'node:path'

const SCRIPT = `
import json, sqlite3, sys
conn = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
def columns(table):
    return {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")}
ids = [
    {"transaction_id": row[0], "category_id": row[1], "subcategory_id": row[2]}
    for row in conn.execute("SELECT transaction_id, category_id, subcategory_id FROM annotations")
]
# Before migration 035 the snooze lives on the annotation, after it on the payment.
if "review_snoozed_at" in columns("transactions"):
    sql = "SELECT id, review_snoozed_at FROM transactions WHERE review_snoozed_at IS NOT NULL"
else:
    sql = "SELECT transaction_id, review_snoozed_at FROM annotations WHERE review_snoozed_at IS NOT NULL"
snoozed = [{"transaction_id": row[0], "review_snoozed_at": row[1]} for row in conn.execute(sql)]
print(json.dumps({"annotationCategoryIds": ids, "snoozedAt": snoozed}, sort_keys=True))
`

/** @param {string} dataDir the folder that holds finance.db; the server using it must be stopped */
export function storedColumns(dataDir) {
  const out = execFileSync('uv', ['run', '--no-project', 'python', '-I', '-c', SCRIPT, path.join(dataDir, 'finance.db')], {
    encoding: 'utf8',
  })
  return JSON.parse(out)
}

const PLANT = `
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = json.loads(sys.argv[2])
# Migration 027: the Kotak parser of the previous releases left the statement's
# balance label on the description of a statement's last payment.
changed = conn.execute(
    "UPDATE transactions SET raw_description = raw_description || ' Closing Balance' WHERE id = ?",
    (row["closingBalanceRow"],),
).rowcount
assert changed == 1, "no payment " + row["closingBalanceRow"]
# Migration 033: a second subcategory with exactly the same name and parent, which nothing uses.
original = conn.execute(
    "SELECT name, parent_id FROM categories WHERE id = ? AND parent_id IS NOT NULL", (row["duplicateOf"],)
).fetchone()
assert original is not None, "no subcategory " + row["duplicateOf"]
conn.execute("INSERT INTO categories (id, name, parent_id) VALUES (?, ?, ?)", (row["duplicateId"], original[0], original[1]))
conn.commit()
`

/**
 * @param {string} dataDir the folder that holds finance.db; the server using it must be stopped
 * @param {{ closingBalanceRow: string, duplicateOf: string, duplicateId: string }} row
 *   the payment that gets the trailing label, the subcategory to copy, and the copy's id
 */
export function plantLegacyRows(dataDir, row) {
  execFileSync('uv', ['run', '--no-project', 'python', '-I', '-c', PLANT, path.join(dataDir, 'finance.db'), JSON.stringify(row)], {
    encoding: 'utf8',
  })
}
