# Finance Copilot

Personal finance tool that auto-categorizes bank transactions using a multi-stage pipeline of rules, RAG, and LLM — powered entirely by Ollama. No cloud services, no API costs, full privacy.


## Highlights

- **100% local** — SQLite database + Ollama for inference, everything stays on your machine
- **Multi-stage annotation pipeline** — Rules > RAG Direct > RAG Prompted > Plain LLM, with graceful fallbacks
- **Human-in-the-loop** — Low-confidence predictions surface in an intuitive and fast review queue for correction
- **Bank statement parsing** — Upload PDFs from Kotak and HDFC; UPI metadata extracted automatically
- **Vector similarity search** — sqlite-vec powers few-shot retrieval for better categorization over time
- **React dashboard** — Transaction management, annotation review, expense groups, and insights/charts

## How the Pipeline Works

When you trigger auto-annotation, each transaction flows through four stages. The first match wins:

```
Transaction
    |
    v
[Stage 1: Rules]          -- Known-person match (UPI handle from people table),
    |  confidence: 0.95       then merchant keywords & UPI notes (~70 built-in rules)
    |  no match? ↓
    v
[Stage 2: RAG Direct]     -- Find top-5 similar annotated transactions via embeddings
    |  confidence: dynamic    If best match similarity >= 0.92 and donor is trusted,
    |                         copy annotation (cosine × agreement × margin factors)
    |  no match? ↓
    v
[Stage 3: RAG Prompted]   -- Few-shot LLM call with retrieved examples as context
    |  confidence: dynamic    llm_conf × calibrated dampening (base 0.92, adapts with feedback)
    |  no match? ↓
    v
[Stage 4: Plain LLM]      -- Cold LLM call without examples
    |  confidence: dynamic    llm_conf × calibrated dampening (base 0.85, adapts with feedback)
    v
 Annotation saved → below threshold (0.85)? → Review Queue
```

Confidence dampening for Stages 3 and 4 uses Bayesian calibration — starting from static base values but dynamically adjusting per `(source, category)` as human feedback (confirmations, corrections) accumulates.

See [`docs/annotation-pipeline.md`](docs/annotation-pipeline.md) for the full Mermaid flowchart.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI (async Python), Pydantic v2 |
| Frontend | React 18, React Router, Vite |
| Styling | Tailwind CSS, Lucide React icons |
| Database | SQLite + sqlite-vec (vectors in the same DB file) |
| LLM | Ollama — qwen3.5:4b (inference), nomic-embed-text (embeddings) |
| PDF Parsing | pdfplumber, pypdf (fallback) |
| Charts | Chart.js + react-chartjs-2 |
| CLI | Typer + Rich (terminal review queue) |
| IDs | ULID (sortable, unique) |

## Getting Started

### Prerequisites

- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.com) installed and running

Pull the required models:

```bash
ollama pull qwen3.5:4b
ollama pull nomic-embed-text
```

### Backend

```bash
# Install dependencies
uv pip install -r requirements.txt
# Or install individually:
# uv pip install fastapi uvicorn pdfplumber pydantic-settings typer rich sqlite-vec python-ulid httpx

# Start the API server (runs migrations automatically)
uv run uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

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
DB_PATH=data/finance.db
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:4b
OLLAMA_EMBEDDING_MODEL=nomic-embed-text
CONFIDENCE_THRESHOLD=0.85
KOTAK_PDF_PASSWORD=your_pdf_password
```

All values have sensible defaults in `src/config.py` — the `.env` file is optional unless you need to override them.

### CLI Review Queue

```bash
uv run python -m src.cli review
```

Interactive terminal UI for reviewing and correcting low-confidence annotations.

## Project Structure

```
src/
├── api/routes/          # FastAPI endpoints (statements, transactions, annotations, ...)
├── db/
│   ├── migrations/      # Idempotent SQL migrations (001–008)
│   └── queries/         # SQL query builders
├── models/              # Pydantic schemas
├── parsers/
│   └── banks/           # Bank-specific PDF parsers (kotak.py, hdfc.py)
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

## Demo Data

Seed the database with 50 synthetic transactions (entirely fictional) to explore the UI without uploading real bank statements:

```bash
uv run python scripts/seed_demo_data.py          # seed transactions, annotations, and people
uv run python scripts/seed_demo_data.py --wipe    # remove demo data
```

The demo data includes:
- Pre-annotated transactions from all pipeline stages (rule, rag_direct, rag_prompted, llm, manual)
- Low-confidence annotations that appear in the review queue
- Unannotated transactions ready for auto-annotate
- Synthetic people for known-person matching

After seeding, start the server and trigger auto-annotate on the remaining unannotated transactions to see the full pipeline in action.

## Adding a Bank Parser

1. Create a new file under `src/parsers/banks/` (e.g., `sbi.py`)
2. Subclass `StatementParser` from `src/parsers/base.py`
3. Implement the `parse()` method to extract transactions from the PDF
4. Register it in `src/parsers/registry.py`

The registry auto-detects the bank type when a PDF is uploaded.

## Demo Screenshots

### Dashboard
![Dashboard](docs/dashboard.png)
*Transaction overview with auto-annotate trigger and confidence scores*

### Review Queue
![Review Queue](docs/reviewqueue.png)
*Low-confidence annotations surfaced for human correction*

### Upload & Parse
![Upload](docs/upload.png)
*Drag-and-drop PDF upload with bank auto-detection*

### Transactions
![Transactions](docs/transaction.png)
*Full transaction table with category chips, inline editing, and filters*

### Insights
![Insights](docs/insights.png)
*Spending breakdowns and trends*

## Testing

```bash
uv run pytest src/tests/
```
