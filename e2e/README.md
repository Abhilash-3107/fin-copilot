# End-to-end journeys

Playwright specs that drive the real app in a real browser.
Each test boots its own instance of the app on a free port with a fresh data folder, so tests share nothing and run in parallel.
This folder is not part of the wheel or the sdist.

## Run it

```
cd e2e
npm ci
npx playwright install chromium
npm test
```

Run one journey with `npx playwright test J2`, or one test with `npx playwright test -g "Kept is one figure"`.
Pass `--headed` or `--ui` to watch it.
`npm run lint` checks the journeys with ESLint and allows no warnings.

## Two server modes

`FC_E2E_SERVER` picks how the app is started for each test.

- `source` (the default): `uv run python -m finance_copilot` from the repository root.
  It serves `ui/dist`, so build the UI first with `cd ui && npm ci && npm run build`.
- `wheel` (CI and the Release workflow): the wheel is installed once into a fresh environment, then each test runs `finance-copilot --data-dir <tmp> --port <port> --no-browser` from it.
  Set `FC_E2E_WHEEL` to the wheel, or to a folder that holds exactly one: `uv build`, then `FC_E2E_SERVER=wheel FC_E2E_WHEEL=dist npm test`.
  This catches packaging faults that a source run hides.

The fixtures start process groups, so they need macOS or Linux.

## What every test gets

- A fresh data folder, deleted afterwards.
- The date pinned to 2026-11-15.
  The server reads `FC_TODAY` (see `finance_copilot/clock.py`) and the browser clock is fixed to the same day.
  The demo ledger is anchored to "today", so this keeps its shape the same on every run.
- A model stub (`stub-model.js`) standing in for Ollama, with scripted chat answers and hashed-trigram embeddings.
  Put `test.use({ modelMode: 'none' })` in a spec to turn both providers off instead.
- An environment built from an allow-list (PATH, HOME, uv's own settings), not copied from the shell, with every setting that changes behaviour set explicitly.
  `FC_IGNORE_DOTENV=1` keeps a checkout's own `.env` out of source runs.
- A failure on any console error, page error, failed request, or HTTP response of 400 or more, unless the test lists it with `tolerate(urlPattern, status)`.
- A 1440 x 900 viewport.
  Screenshots and traces are kept for failures only.

Assert on roles, labels and visible text.
Do not assert on CSS classes, colours or pixels.
Do not add screenshot baselines.

## Expected failures

A journey that fails on today's code is written anyway and marked with `expectedFailure('R1 item 2: why')`, which calls `test.fail()`.
CI stays green while the bug exists.
When the fix lands the test starts to pass, Playwright reports that as a failure, and the fix's pull request deletes the `expectedFailure` line.

Console and network problems fail the run even inside an expected-failure test: the fixture attaches them and `hygiene-reporter.js` fails the run.

Run with `E2E_SHOW_FAILURES=1` to skip the marker and see the real failure message.
Use it to check that a test fails for the reason it states.

An expected-failure test has to fail by assertion, not by timeout, because a timed-out test never counts as an expected failure.
The config sets a 10 second action timeout for this reason.

## The upgrade journey (J15)

J15 opens a ledger made by the previous published release in the release being built.
It is not a Playwright test: it starts two builds one after the other on one data folder, so it is a script.

```
uv build
cd e2e && npm run upgrade
```

It needs network access to PyPI.
`FC_E2E_WHEEL` picks the wheel and defaults to `../dist`.

1. `uvx --from finance-copilot==<from> finance-copilot --data-dir <tmp> ...` starts the previous release.
   The journey loads the demo data, uploads the fixture statement, and sorts a few payments, adds a person, a rule and a group member, all through that release's own API.
   It also plants what the migrations that rewrite data act on: a merchant typed by hand with two slashes (migration 030 must keep it), a machine-labelled merchant copied from a bank line (030 clears it), a description ending in "Closing Balance" (027 strips it) and an unused duplicate subcategory (033 deletes it).
   The first three go through the API; the last two cannot be made there, so `upgrade/db.js` writes them with the server stopped, and the old release is started again to read them back.
   The day the demo is seeded on is read right after it, and the journey stops if midnight fell inside the seeding.
2. It records stored facts (`lib/snapshot.js` `storedFacts`, plus what `upgrade/db.js` reads from the SQLite file: the annotation category ids and snooze times that migrations 035 and 036 rewrite, and the schema itself, meaning which migrations ran, the tables and columns they add and the start count of each sorting job) and derived figures (`derivedFigures`: In, Out, Invested, Kept, the breakdown, the recurring totals, the people panel, per month).
   Both take the release as an argument, because the old and new API differ; each release's payload is mapped to one neutral shape in `lib/snapshot.js`.
3. It stops that release, starts the wheel on the same folder, and records both again.
4. Every difference must be explained by an entry in `upgrade/expected-changes.json` for this version, with its reason.
   An entry that matches nothing is also a failure (a stale entry would hide the day the change comes back), unless it is marked `optional`, which a date-dependent change is.
   The same list goes into `CHANGELOG.md` under "Figures that change in this release".

An entry has a `scope` (`stored` or `derived`), a `path` pattern or a list of them (`*` is one key, `**` is anything; `{month}` is the month the run happens in, `{firstMonth}` the first month of the ledger, and the ids of the planted rows have their own names), optional `kinds` (`added`, `removed`, `changed`), optional `from` and `to` values, optional `appends` (a regular expression for the text a changed string gained at its end), and a `reason` written for the person reading the changelog.
Pin `from` and `to` wherever the values are fixed, and scope a path to the rows or months it explains (`demo_*` rows, the first month): a loose entry lets a regression through.
`upgrade/invariants.js` holds what paths cannot say: the new value in relation to the old one (an id names its label, a person's months add up to the old all-time figure, unsorted never shrinks, the headline equals Kept).
`upgrade/compare.test.js` damages a good upgrade in one way at a time and checks the comparison fails each time, so an entry that gets looser breaks a test.
Snapshots and a report go to `test-results/upgrade/`.

For a new release, set `version` and `from` at the top of `expected-changes.json` and review every entry: the file describes one release.
A release candidate (`0.3.0rc1`) runs the file of the release it leads to (`0.3.0`): the journey compares base versions, and still checks that the server reports the wheel's exact version.
`npm run test:lib` unit-tests the comparison and the damage it must catch.
J15 runs in the Release workflow, between the build and the publish, and not on every push, because it depends on PyPI.

## Layout

- `journeys/J<n>-<slug>.spec.js`: one file per journey.
- `fixtures.js`: the app, stub, clock and hygiene fixtures, plus `expectedFailure`.
- `stub-model.js`: the model stub.
- `lib/ui.js`: reading figures off the screen, and `open(page, route)`, which waits for that route's own landmark.
  A new route needs an entry in `ARRIVED`.
- `lib/snapshot.js`: `storedFacts(api)`, whole stored rows minus a short deny-list of volatile fields, used by J8 and by the upgrade journey.
  `derivedFigures(api)` records the per-month figures the app computes, for the upgrade journey.
  Both take `{ version }` for a release whose API differs.
- `lib/server.js`: starting and stopping the app as a child process, and the API client. Shared by `fixtures.js` and `upgrade/run.js`.
- `upgrade/`: J15. `run.js` is the journey, `diff.js` compares snapshots and holds them against `expected-changes.json`, `compare.js` is the whole comparison, `invariants.js` the checks beside the list, `db.js` reads the two facts the API does not show and plants the two rows it cannot make, `version.js` maps a release candidate to its release.
- `lib/statement.js`: the fixture statement and categorisation helpers.
