# Changelog

## 0.2.0 - unreleased

The app speaks in your words, and the core job, sorting what the machine could not, can be done from the keyboard.
Every payment is shown by who it went to and what you wrote, you can rename it, sorting starts by itself when a statement lands, and a failure says it failed and offers Retry.
Every number on screen agrees with every other number or says what it counts, and Teach Me holds every payment that still has no category.
The categorizer and the statement reader got a round of fixes, and there is now a browser test suite behind all of it.

### Trying the release candidate

0.2.0rc1 is a release candidate.
Plain `uvx finance-copilot` stays on 0.1.2, the last stable release, until 0.2.0 itself is out.
It rewrites data in your ledger (see below), so try it on a copy first:
copy your data folder, then run `uvx finance-copilot==0.2.0rc1 --data-dir <a copy of your data folder>`.
Your own folder is untouched until you point the new version at it.

### Before you upgrade

Download a backup first (Settings > Data > Download backup).
This release rewrites data in your ledger, and the new version cannot be undone by installing the old one on the same folder.
On the first start of 0.2.0 it:

- fills category ids that sample data left empty, and moves "skipped" from the label to the payment;
- re-reads every payment's description and stores what it finds;
- removes a trailing "Closing Balance" or "Opening Balance" from a payment's description, which an older Kotak reader had left there;
- clears a merchant name that has two or more slashes in it ("UPI/NAME/1207.../self"), where a machine copied it from the bank line.
  A merchant you typed or imported yourself, or set with your own rule, is kept, whatever it contains;
- deletes a subcategory that has exactly the same name and parent as another and that nothing uses (no label, rule or policy).
  The copy that is used stays;
- adds the places that hold the names you give to payees and payments, the records that let Teach Me undo an answer, and what a waiting sorting job covers.
  They start empty and rewrite nothing you have.

A migration that fails now rolls back whole instead of stopping halfway.

Only one copy of the app can use a data folder at a time.
Starting a second copy on the same folder no longer touches it: it opens the copy that is already running, or tells you to quit that one first.

To go back to 0.1.2, do not run it on the same folder: on a ledger 0.2.0 has opened, 0.1.2 fails (Teach Me does not load), and a 0.1.2 backup restored into 0.2.0 is migrated again.
Run `uvx finance-copilot==0.1.2 --data-dir <a new folder>` and restore your backup there (Settings > Data).

### Figures that change in this release

Your payments, amounts, dates, balances, people, groups and rules are unchanged, and so are In, Out, Invested and the Kept tile.
Categories are unchanged too, apart from an unused duplicate subcategory (above).
What moves is how some of them are added up, compared and named, so the same ledger reads differently on these screens:

- The Dashboard headline.
  It used to say you "kept" what you invested plus what stayed in the bank.
  Kept is now In - Out - Invested everywhere, in the headline, the tile and Money Map, and the headline says what you invested.
  Expect the headline to drop by the amount you invested that month.
- Comparisons for a month still running.
  A partial month is compared with the same days of the previous month, not with all of it, and the screen says the month is in progress.
  The first month in your ledger has no month before it, so it shows no comparison and no change, instead of comparing with zero.
  "What changed" for a month still running also leaves out a category the same days of the previous month did not hold, and lists one that held money then and none now.
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
- The Sorted column on Add Statement.
  It counts the payments Teach Me is not waiting on, the same as everywhere else.
  It used to count every payment the machine had labelled, however unsure it was, so it reads lower for a statement with guesses still to check, and for payments labelled Uncategorized.
- Names, not money.
  A recurring charge, or a row under Top payees on Money Map, for a payee with no merchant name is now listed under the payee exactly as the bank printed it (CHAI WALA CORNER where it used to read Chai Wala Corner), the same name Transactions, Teach Me and Money Map use.
  No amount, count or category moves.

### Your payments, by name

- Rows on Transactions lead with who the payment went to, and under it what you wrote and how it was paid.
  The bank's own line moved behind the row, under "As printed on the statement".
- Rename a payee, and every payment to it, past and future, carries the name, or rename just one payment.
  A name only changes what the payment is called: categories, rules, search by the bank's text and matching are untouched.
  Search and the Money Map drill-through find the names you chose.
- Money out reads in ink with a minus and money in in green with a plus.
  Red is kept for a change in the bad direction.
- The same payee is called the same thing on every screen.

### Adding statements

- Sorting starts by itself when a statement lands, and waits its turn behind any sort already running.
- Drop several statements at once.
  Each file shows its own progress, balance check and summary, files that are not PDFs are refused up front, and the summary leads to Teach Me.
- Add Statement says which banks it reads, how a locked PDF is handled and that nothing leaves your computer, before you add the first file.
- Each statement has a menu (Rename, Start over, Delete), and the machine controls that used to sit on the page are gone.
- A sort the app was closed in the middle of goes back in line at the next start.
  If it is interrupted a second time it fails with a plain message instead of coming back for ever.
  Deleting a statement waits for a sort that is running instead of failing.

### Teach Me

- Every payment without a category now reaches Teach Me, including payments with no label at all and ones the machine labelled Uncategorized.
  A card with no guess offers no Confirm, and it says "Nothing to sort yet" on an empty ledger instead of "All done".
- The sort card shows at most three suggestions with the reason for each, and number keys pick one.
  Type to find any category, and the payee and the note are decoded for you.
  There is no Confirm on Uncategorized, and Uncategorized is no longer offered as an answer.
- The keys: 1 to 3 choose a suggestion, Enter confirms, / finds another category, S skips, Z undoes your last answer, A does the same for all payments from this payee, and ? lists every shortcut.
  B and C are gone, and Z replaces Back.
- One message at a time, with Undo.
  Undo puts back what your last answer changed, one answer or "do the same for all", and it is kept for a day, up to your last 50 answers.
- Shortcut keys never fire while you type, and an open dialog or sheet blocks them.
- Skipping a payment sticks for every kind of payment, and the "set aside" count and the done check come from the server.
  The phantom skipped item is gone.
- A failed load shows the error with a Retry button, never the done screen.
- Keyboard focus moves to the new card, and Enter on a button or chip no longer also triggers the card's shortcut.
- The queue asks one question per payee and direction, with "Do the same for all N payments to <payee>" (on for businesses, off for people), and you can make your own category or subcategory from the card.
- Review policies (Settings > Rules) sort small payments to people in one rule of thumb, and a human label re-decides the machine labels it could change, so you teach each payee once.

### Errors, search and shortcuts

- A block that cannot load says so, in place, with a Retry button.
  It never shows an empty state or a stale count in its place.
- A search field in the header opens the command palette, and `?` opens a sheet of every shortcut.
  Choosing a payment in the palette opens that payment.
- Dialogs and pickers obey Escape and Enter.
  A dialog that deletes something opens on Cancel, and Enter never confirms it.

### Groups and people

- Rename a group, give it a note and labels.
  The group header stays current after an edit.
- A group or a person with a name that already exists is refused, even when two are created at once.
- A change to a person leaves the fields you did not touch as they were.

### The words

- One vocabulary: sort, payee and payment replace annotate, merchant and transaction outside developer mode.
  The payees the app has learned to sort on its own are "Remembered payees" (Settings > Rules), and what finds similar payments to compare against is "Matching similar payments" (Settings > AI & Model > Advanced), where the old AI memory was.
  A failure says what could not be done ("Couldn't load the payments.") and then why, and the app's own messages say payments and sorted too.
- The machine settings moved into Settings under Advanced.
  AI & Model leads with a plain sentence about what is switched on, and the Insights tab is now called Teach Me (old links still open it).
- The two dim greys and the accent blue are no longer used for small text, and white on the accent colour is deeper, so everything small reads at a contrast of at least 4.5 to 1.

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
  The first start of 0.2.0 re-reads your stored descriptions and rebuilds what Matching similar payments compares against (Settings > AI & Model > Advanced) in the background.
  For some UPI layouts the payee now includes the word after the name (SWIGGY/ORDER where 0.1.2 showed SWIGGY), so a payee, and a remembered payee listed in Settings > Rules > Remembered payees, can appear under the longer name.
- Merchant knowledge: well-known merchants the rules missed are recognised, and personal names are not labelled Self Transfers unless you have told the app the account is yours.
- Matches from your own history count only when the neighbour is similar enough to be evidence, so teaching more merchants no longer makes exact matches score lower.
- Subcategory names the machine writes outside your taxonomy are snapped to the one they plainly mean, and Settings > Categories has a Tidy up panel for look-alike and orphaned subcategories.
- Sorting no longer fails with "database is locked" while a run is going, a stopped AI provider ends a run in seconds with a clear message, a hung one after two timed-out payments, and one bad answer fails one payment, not the run.
  `LLM_TIMEOUT_S` sets the per-call timeout.
- Plurals read correctly, and no visible text contains an em dash.

### Removed

- The terminal `review` command is gone.
  Use Teach Me in the app.
- Forget, the per-statement control that cleared one statement's AI memory, is gone.
  The next launch rebuilt the memory anyway, so it did nothing lasting.
- The per-statement Build control on Add Statement moved to Settings > AI & Model > Advanced > Update now, which shows how many sorted payments are ready to match and adds the rest.

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
