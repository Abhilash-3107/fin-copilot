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

## Layout

- `journeys/J<n>-<slug>.spec.js`: one file per journey.
- `fixtures.js`: the app, stub, clock and hygiene fixtures, plus `expectedFailure`.
- `stub-model.js`: the model stub.
- `lib/ui.js`: reading figures off the screen, and `open(page, route)`, which waits for that route's own landmark.
  A new route needs an entry in `ARRIVED`.
- `lib/snapshot.js`: `storedFacts(api)`, whole stored rows minus a short deny-list of volatile fields, used by J8 and later by the upgrade journey.
  `derivedFigures(api)` records the per-month figures the app computes, for the upgrade journey.
- `lib/statement.js`: the fixture statement and categorisation helpers.
