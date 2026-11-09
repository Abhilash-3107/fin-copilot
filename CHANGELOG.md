# Changelog

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
