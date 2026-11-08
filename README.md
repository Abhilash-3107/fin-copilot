# Finance Copilot

Finance Copilot is a local-first personal finance application for parsing bank statements, organising transactions and analysing expenses.

```bash
uvx finance-copilot
```

That's it.
The app installs, starts on localhost, and opens in your browser.
Drop in a bank statement PDF (or click "Load demo data") and watch every transaction get parsed, balance-checked, and categorized.
No account, no cloud, no data leaving your computer.

> Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) (one installer command, no Python needed). Prefer pipx? `pipx run finance-copilot` works too. First run takes a minute while dependencies download.
>
> On Python 3.15 the app runs with built-in embeddings off, because their runtime has no 3.15 build yet. For the full experience run `uvx --python 3.14 finance-copilot`.

Out of the box you get statement parsing with running-balance verification, rule-based and history-based categorization, expense groups, people tracking, and insights.
Add AI when you want it: point Settings › AI & Model at [Ollama](https://ollama.com) or paste any OpenAI-compatible API key.

## Highlights

- **Local-first** - one SQLite file on your machine; AI runs through Ollama, a built-in local model, or an OpenAI-compatible endpoint you choose
- **Multi-stage annotation pipeline** - Your rules > built-in rules > learned merchants > RAG Direct > RAG Prompted > Plain LLM, with graceful fallbacks
- **Your own rules** - Settings > Rules: a word or phrase and a category, with a live preview of what it matches before you save
- **Human-in-the-loop** - Low-confidence predictions surface in an intuitive and fast review queue for correction
- **Bank statement parsing** - Dedicated Kotak parser plus a generic parser that handles most banks' tabular statement PDFs, with every row verified against the running balance; UPI metadata extracted automatically
- **Vector similarity search** - sqlite-vec powers few-shot retrieval for better categorization over time
- **React dashboard** - Transaction management, annotation review, expense groups, and insights/charts

## How the Pipeline Works

When you trigger auto-annotation, each transaction flows through four stages. The first match wins:

```
Transaction
    |
    v
[Stage 1: Rules]          - Your own rules first, then known-person match (UPI handle
    |  confidence: 0.95       from people table), merchant keywords & UPI notes
    |                         (~70 built-in rules), then learned merchant rules
    |  no match? ↓
    v
[Stage 2: RAG Direct]     - Find top-5 similar annotated transactions via embeddings
    |  confidence: dynamic    If best match similarity >= 0.92 and donor is trusted,
    |                         copy annotation (cosine × agreement × margin factors)
    |  no match? ↓
    v
[Stage 3: RAG Prompted]   - Few-shot LLM call with retrieved examples as context
    |  confidence: dynamic    llm_conf × calibrated dampening (base 0.92, adapts with feedback)
    |  no match? ↓
    v
[Stage 4: Plain LLM]      - Cold LLM call without examples
    |  confidence: dynamic    llm_conf × calibrated dampening (base 0.85, adapts with feedback)
    v
 Annotation saved → below threshold (0.85)? → Review Queue
```

Confidence dampening for Stages 3 and 4 uses Bayesian calibration, starting from static base values but dynamically adjusting per `(source, category)` as human feedback (confirmations, corrections) accumulates.

See [`docs/annotation-pipeline.md`](https://github.com/Abhilash-3107/fin-copilot/blob/main/docs/annotation-pipeline.md) for the full Mermaid flowchart.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async Python), Pydantic v2 |
| Frontend | React 18, React Router, Vite |
| Styling | Tailwind CSS, Lucide React icons |
| Database | SQLite + sqlite-vec (vectors in the same DB file) |
| LLM | Ollama (qwen3.5:4b), any OpenAI-compatible API, or a built-in local model via llama.cpp (`finance-copilot[local-llm]`) |
| Embeddings | Ollama (nomic-embed-text) or built-in fastembed (BAAI/bge-small-en-v1.5) |
| PDF Parsing | pdfplumber |
| Charts | Chart.js + react-chartjs-2 |
| CLI | Typer + Rich (terminal review queue) |
| IDs | ULID (sortable, unique) |

## Developing

### Prerequisites

- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.com) (optional, for the default AI setup)

To use Ollama, pull the default models:

```bash
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
```

### Backend

```bash
# Install dependencies (creates .venv from pyproject.toml + uv.lock)
uv sync

# Start the API server (runs migrations automatically on startup)
# Binds to 127.0.0.1 by default - loopback only, so your statements are not
# exposed on the network. Set RELOAD=1 for auto-reload during development.
RELOAD=1 uv run python -m finance_copilot
```

> The server binds to `127.0.0.1` by default and needs no login there.
> To expose it on your LAN, set `HOST=0.0.0.0` (you will get a startup warning) and set `API_TOKEN` to a shared secret: every API request must then carry it, and the app asks for it once in the browser.
> `PORT` defaults to `8000`.

### Frontend

```bash
cd ui
npm install
npm run dev      # Dev server at http://localhost:5173 (proxies API to :8000)
npm run build    # Production build → ui/dist/ (served by FastAPI)
```

### Environment Variables

Create a `.env` file in the project root:

```env
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:4b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
CONFIDENCE_THRESHOLD=0.85
```

For password-protected statement PDFs, enter the password in the upload form: it is sent with the upload request, not stored.

All values have sensible defaults in `finance_copilot/config.py`, so the `.env` file is optional unless you need to override them.
Only a source checkout reads this file, from the repo root.
The installed app (`uvx finance-copilot`) ignores any `.env` and takes overrides from real environment variables instead.

### CLI Review Queue

```bash
uv run python -m finance_copilot.cli review
```

Interactive terminal UI for reviewing and correcting low-confidence annotations.

## Project Structure

```
finance_copilot/
├── api/routes/          # FastAPI endpoints (statements, transactions, annotations, ...)
├── db/
│   ├── migrations/      # Versioned SQL migrations (applied once, tracked in schema_migrations)
│   └── queries/         # SQL query builders
├── models/              # Pydantic schemas
├── parsers/
│   └── banks/           # Bank-specific PDF parsers (kotak.py)
├── pipeline/            # Annotation engine (rules, embed, annotate, llm, calibration)
├── config.py            # All settings (Pydantic BaseSettings, reads .env)
├── cli.py               # Typer CLI for terminal review
└── main.py              # FastAPI app entry point

ui/
├── src/
│   ├── pages/           # Dashboard, ReviewQueue, Transactions, Upload, Groups, People, Insights
│   ├── components/      # AnnotationPanel, CategoryPicker, TransactionTable, TagInput, ...
│   ├── contexts/        # StatementContext, ToastContext
│   └── lib/api.js       # HTTP client wrapper
└── vite.config.js

data/                    # SQLite database (gitignored)
docs/                    # Pipeline documentation
```

## Your Data

Everything lives in one SQLite file, `finance.db`, in the app's data folder:
`~/Library/Application Support/FinanceCopilot` on macOS, `%LOCALAPPDATA%\FinanceCopilot` on Windows, and `~/.local/share/FinanceCopilot` on Linux.
Pass `--data-dir` to keep it somewhere else.

Settings › Data downloads a backup of the whole ledger.
The file leaves out your AI provider's API key, so it is safe to share or attach to a bug report.
Restore from the same tab: the app checks the file, saves your current data as `pre-restore-<timestamp>.db` next to `finance.db`, then swaps the backup in.

To restore by hand instead, quit the app, replace `finance.db` in the data folder with the backup file, and start it again.
Delete any `finance.db-wal` and `finance.db-shm` files next to it first, or SQLite may replay them over the restored file.

## Demo Data

Click "Load demo data" on an empty dashboard to explore with four months of fictional transactions, people, and an expense group.
To build a separate demo database instead (it never touches your real ledger):

```bash
uv run python scripts/seed_demo_data.py          # writes data/demo.db
uv run python scripts/seed_demo_data.py --wipe    # remove demo rows
```

The demo data includes:
- Pre-annotated transactions from all pipeline stages (rule, rag_direct, rag_prompted, llm, manual)
- Low-confidence annotations that appear in the review queue
- Unannotated transactions ready for auto-annotate
- Synthetic people for known-person matching, and a settled Goa trip group

After seeding, trigger auto-annotate on the remaining unannotated transactions to see the full pipeline in action.

## Bank Statement Parsing

Uploads are auto-detected by the parser registry (`finance_copilot/parsers/registry.py`):

1. **Dedicated parsers** (currently Kotak) are tried first.
2. The **generic parser** (`finance_copilot/parsers/generic.py`) handles everything else: it
   extracts tables from the PDF, infers which columns are date / description /
   debit / credit / balance from the header row, and then **verifies every row
   arithmetically against the running balance** (`balance[i] == balance[i-1] ± amount`).
   It only claims a statement when ≥80% of those checks pass, so it cannot
   silently ingest wrong amounts; unsupported layouts are rejected with an error
   instead.

Limitations of the generic parser: it needs a text-native PDF (no scans/OCR), a
tabular layout with a header row, and a running-balance column (so most savings
account statements work; credit-card statements generally won't yet).

### Adding a Dedicated Bank Parser

Only needed when the generic parser can't handle a bank's layout:

1. Create a new file under `finance_copilot/parsers/banks/` (e.g., `sbi.py`)
2. Subclass `StatementParser` from `finance_copilot/parsers/base.py`
3. Implement the `parse()` method to extract transactions from the PDF
4. Register it in `finance_copilot/parsers/registry.py` **before** `GenericStatementParser`

## Testing

```bash
uv run pytest finance_copilot/tests/
```
