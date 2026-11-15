# Changelog

## 0.1.3 - unreleased

Every number on screen agrees with every other number or says what it counts.
Teach Me holds every payment that still has no category.
The categorizer and the statement reader got a round of fixes, and there is now a browser test suite behind all of it.

### Before you upgrade

Download a backup first (Settings > Data > Download backup).
This release rewrites data in your ledger: it fills category ids that sample data left empty, moves "skipped" from the label to the payment, and re-reads every payment's description.
If anything looks wrong afterwards, restore the backup.
A migration that fails now rolls back whole instead of stopping halfway.

### Figures that change in this release

Your payments, balances, categories, people, groups and rules are unchanged, and so are In, Out, Invested and the Kept tile.
What moves is how some of them are added up and compared, so the same ledger reads differently on these screens:

- The Dashboard headline.
  It used to say you "kept" what you invested plus what stayed in the bank.
  Kept is now In - Out - Invested everywhere, in the headline, the tile and Money Map, and the headline says what you invested.
  Expect the headline to drop by the amount you invested that month.
- Comparisons for a month still running.
  A partial month is compared with the same days of the previous month, not with all of it, and the screen says the month is in progress.
  The first month in your ledger has no month before it, so it shows no comparison and no change, instead of comparing with zero.
- Where it went.
  The rows now end with the lines that make them add up to Out (Transfers and cash, and refunds and paybacks taken off the rows above), plus a total.
  The category rows themselves are unchanged.
- The unsorted figure.
  One definition everywhere: a payment with no category counts, whether it has no label at all or is labelled Uncategorized.
  The Dashboard, Money Map and the Uncategorized filter on Transactions show the same number, and what you click through to adds up to it.
- Recurring.
  "Spoken for each month" on the Dashboard used to add every active recurring charge, and Money Map added only the monthly ones, so they disagreed.
  Both now show the monthly charges that are still running, and the header equals its rows.
- The people panel on Money Map.
  It shows the month it sits on.
  It used to show all-time totals under every month.

### Teach Me

- Every payment without a category now reaches Teach Me, including payments with no label at all and ones the machine labelled Uncategorized.
  A card with no guess offers no Confirm, and it says "Nothing to sort yet" on an empty ledger instead of "All done".
- Skipping a payment sticks for every kind of payment, and the "set aside" count and the done check come from the server.
  The phantom skipped item is gone.
- A failed load shows the error with a Retry button, never the done screen.
- Keyboard focus moves to the new card, and Enter on a button or chip no longer also triggers the card's shortcut.
- The queue asks one question per payee and direction, with "apply my answer to all N payments", and you can make your own category or subcategory from the card.
- Review policies (Settings > Rules) sort small payments to people in one rule of thumb, and a human label re-decides the machine labels it could change, so you teach each payee once.
- A quick double press of c or Enter saves once.

### Sample data

- A quiet strip tells you while sample data is in the ledger, and you can remove it in one click from the strip, Settings > Data or Add Statement.
  Removing it leaves anything you added.
  On the hosted demo it is not offered.
- Uploads are refused while sample data is present, so the two never mix.
  If a ledger made by 0.1.2 holds both, the strip offers to remove just the sample rows.
- Sample data now carries category ids, so Settings > Categories no longer reports every sample subcategory as an orphan.
  A ledger seeded by 0.1.2 is fixed on first start.
  Sample payments from 0.1.2 also lose their hand-written notes, because the notes are now read from the payment's description, as for real statements.

### Groups, People and Money Map

- The group header shows your share and who owes you, or "Settled", instead of calling the gross outflow "net".
  Who owes whom is never netted across people.
- Every per-person figure says what it counts and over what period: People reads "more sent" or "more received" over all time, with no red for sending money, and Money Map says its figure is for the month.
- The Transactions Uncategorized filter includes payments with no label, and shows a count with what went out and came in.
  The summary line describes exactly the rows listed.
- Month screens no longer offer All time, and the month picker only offers months the data covers.
- Landlord is offered as a relationship.
- The Settings links on Add Statement open Settings instead of the Dashboard.

### Privacy mode

- Percentages, the Kept rate, the chart, chart tooltips, the palette hint, toasts and server messages no longer show amounts while privacy mode is on.

### Statements and categorization

- Kotak's new statement layout (mid-2026) is read, and the bank is identified from the statement header, not from a counterparty named in a line.
  A locked PDF says it needs a password.
- Payment descriptions are read once, by one grammar, so lowercase bank codes and payee names containing a slash (M/S.) no longer trigger the format warning.
  The first start of 0.1.3 re-reads your stored descriptions and rebuilds the AI memory in the background.
- Merchant knowledge: well-known merchants the rules missed are recognised, and personal names are not labelled Self Transfers unless you have told the app the account is yours.
- Matches from your own history count only when the neighbour is similar enough to be evidence, so teaching more merchants no longer makes exact matches score lower.
- Subcategory names the machine writes outside your taxonomy are snapped to the one they plainly mean, and Settings > Categories has a Tidy up panel for look-alike and orphaned subcategories.
- Sorting no longer fails with "database is locked" while a run is going, a stopped AI provider ends a run in seconds with a clear message, a hung one after two timed-out payments, and one bad answer fails one payment, not the run.
  `LLM_TIMEOUT_S` sets the per-call timeout.
- Plurals read correctly, and no visible text contains an em dash.

### Removed

- The terminal `review` command is gone.
  Use Teach Me in the app.

### For contributors

- `FC_TODAY=YYYY-MM-DD` pins the date the app believes it is, for tests and demos.
- `e2e/` holds Playwright journeys that drive the built wheel, and CI runs them on every push.
  The Release workflow also runs the upgrade journey, which opens a ledger made by the previous release in the new one and fails on any change not listed in `e2e/upgrade/expected-changes.json`.

## 0.1.2 - 2026-11-08

Packaging fixes, and a license change.

### License

- Finance Copilot is now source-available under FSL-1.1-MIT, shipped as the `LICENSE` file inside the package.
  You can use it, self-host it, modify it and contribute; offering it as a competing commercial product is not allowed.
  Each release becomes MIT-licensed two years after it ships.
  0.1.0 and 0.1.1 were published under MIT and stay that way.

### Fixes

- The installed app no longer reads a `.env` from the folder you launch it in.
  Running `uvx finance-copilot` inside a project whose `.env` set `DB_PATH` or an AI endpoint used to open that database or endpoint silently.
  Real environment variables and `--data-dir` still apply; a source checkout still reads its own `.env`.
- The terminal running `finance-copilot` no longer fills with a line for every request to Ollama.
- The "running at" message shows up even when the output goes to a file or another program.
- PyPI lists the supported Python versions, and the source archive no longer carries an unrelated deploy README.
- Stable dependencies are capped below their next major version, so a breaking upstream release can't break fresh installs.
- `python -m finance_copilot.cli version` reads the installed version instead of a hardcoded one.

## 0.1.1 - 2026-11-08

Documentation fixes for the PyPI page.

- The README no longer shows broken screenshots on PyPI, and its description of the pipeline, AI options and LAN access matches the app.
- PyPI now links to the changelog and the issue tracker.
- `finance-copilot version` now prints 0.1.1; it was still saying 0.1.0.

## 0.1.0 - 2026-11-08

The first public release: a one-command local app (`uvx finance-copilot`).

### Install and run

- One command: `uvx finance-copilot` installs, starts on localhost, and opens the browser.
- Your data lives in one SQLite file in the platform app-data folder; `--data-dir` moves it, and now always wins over a `DB_PATH` in a local `.env`.
- Works with no AI at all: rules, your history, and your own rules categorize without a model.
- AI when you want it: Ollama, any OpenAI-compatible endpoint, or a fully local model (`finance-copilot[local-llm]`, Qwen 3.5 4B downloaded in-app with resume and checksum verification).
- Runs on Python 3.10 to 3.15.
  On 3.15 the built-in embeddings are off (onnxruntime has no 3.15 wheels yet); `uvx --python 3.14 finance-copilot` gets them.

### Categorization

- Write your own rules in Settings > Rules: a word or phrase, the category it means, a live preview of what it matches, and an optional one-click apply to existing transactions that never overrides labels you set yourself.
  Your rules run before the built-in rules and anything learned.
- Learned merchant rules, history-based matching, and the AI stage, with a review queue for low-confidence guesses.

### Your data

- Backup downloads leave out your AI provider's API key, so they are safe to share.
- Restore a backup from Settings > Data; your current data is kept as a `pre-restore-<timestamp>.db` file first.
- Delete-everything says exactly what it removes, settings included.

### Fixes since the 2026-07-27 review

- Group settle-up counted repayments at half their value; money a friend sends back now counts in full, and the transaction type decides how payments you made are shared.
  Groups can tag people by name, and fully settled groups say so.
- Bulk recategorizing no longer fails on transactions deleted since the page loaded, and returns immediately instead of re-embedding row by row.
- Re-embedding an already-embedded transaction always failed silently (sqlite-vec rejects `INSERT OR REPLACE`); relabels and provider switches now refresh the vector memory.
- The optional LAN token check is constant-time and also protects the API docs pages.
- Searching a whole-rupee amount finds amounts with paise.
- Test connection no longer reports success for model settings that failed to save.
- Quitting after using the local model no longer crashes on macOS.
