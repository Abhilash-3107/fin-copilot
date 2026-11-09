# Contributing to Finance Copilot

Thanks for helping out.
Bug reports, new bank parsers, and fixes are all welcome.

## Before you start

For anything bigger than a small fix, open an issue first so we can agree on the approach before you spend time on it.

Never attach a real bank statement to an issue or PR.
Redact it, or describe the layout (column headers, a few made-up rows) instead.

## Contributor License Agreement

On your first pull request, the CLA Assistant bot asks you to sign the Contributor License Agreement.
You sign once, with one click, and it covers all your future contributions.
You keep the copyright to your work; the agreement gives the maintainer the rights needed to ship it under the project's license, now and later.
PRs can't be merged until it is signed.

## Development setup

You need [uv](https://docs.astral.sh/uv/) and Node.js 20+.

```bash
uv sync                          # Python deps into .venv
cd ui && npm ci && npm run build # the UI the backend serves
cd ..
RELOAD=1 uv run python -m finance_copilot
```

For UI work, also run `npm run dev` in `ui/` (Vite on :5173, proxying the API on :8000).
The README has more on environment variables and the project layout.

## Checks

All of these must pass before a PR can be merged; CI runs them too.

```bash
uv run ruff check finance_copilot/
uv run pytest
cd ui && npm run lint && npm test && npm run build
```

Add or update tests for the behaviour you change.
A bug fix should come with a test that fails without it.

## Pull requests

- Keep each PR focused on one change.
- Describe what changed and why, and how you verified it in the running app.
- Add a line under `## Unreleased` at the top of `CHANGELOG.md` for anything a user would notice (create the heading if it is not there).

## Adding a bank parser

See "Adding a Dedicated Bank Parser" in the README.
Include a small synthetic PDF fixture (no real account data) and a test that parses it.

## License

By contributing, you agree that your contributions are licensed as described in the CLA and in [LICENSE](LICENSE) (FSL-1.1-MIT).
