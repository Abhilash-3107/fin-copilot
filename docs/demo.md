# Demo runbook

A scripted walkthrough for demoing Finance Copilot with the synthetic demo ledger.
The seed generator (`scripts/seed_demo_data.py`) is anchored to today's date, so the data always looks current no matter when you run it.

## What the demo ledger contains

- Three full months of history plus the current partial month, about 180 transactions.
- Monthly commitments (rent, home loan EMI, two SIPs, Netflix, Spotify, Jio, ACT, Cult.fit) with stable amounts and UPI counterparties, so the recurring panel and learned rules light up.
- Story arcs that exercise the netting machinery: a Goa trip expense group where two friends paid their shares back, a linked Amazon refund, an unlinked Swiggy refund matched by counterparty, and a monthly self-transfer excluded from the cash verdict.
- A review backlog of about 8 genuinely ambiguous recent transactions (an unlabeled UPI number at 18% confidence, an unknown NEFT, a first-time Decathlon purchase).
- Six never-seen merchants from the last two days (Blue Tokai, Licious, metro recharge, a bakery, Apollo Pharmacy, Dunzo) left unannotated as fodder for a live auto-annotate.
- History is deterministic: the same day-of-run always produces the same ledger.

## Setup (2 minutes)

```bash
# 1. Seed the demo ledger (targets data/demo.db, never your real ledger)
uv run python scripts/seed_demo_data.py

# 2. Embed the annotated history so RAG retrieval works live (needs Ollama)
DB_PATH=data/demo.db PYTHONPATH=. uv run python scripts/reembed_all.py

# 3. Serve the demo ledger
DB_PATH=data/demo.db PORT=8080 uv run python -m src
```

Open http://localhost:8080.

To reset between takes:

```bash
rm -f data/demo.db data/demo.db-wal data/demo.db-shm
```

Then repeat setup.
Re-running the seed against an existing demo DB is idempotent for history but will not undo reviews you did during a demo, so a fresh DB per take is cleaner.

## The demo script

### Beat 1 - Dashboard (30s)

Open the Dashboard.
Talking points:

- The headline verdict: "you kept X% of what came in" - one sentence, not a wall of charts.
- "Needs your attention" - the app tells you what to do next: transactions to teach, uncategorized money, an unsettled friend balance.
- "Spoken for each month" - commitments the app worked out on its own from recurring charges.

### Beat 2 - Money Map, last full month (90s)

Go to Money Map and switch the period selector to the last full month.
This is the hero view; the current month is always partial.
Talking points:

- The four tiles are a cash view: In = Out + Invested + Kept by construction, so they can never contradict each other.
- Balance over time shows the salary sawtooth across the full history.
- "What changed" calls out the month's deltas (the trip month vs the month after is a good story).
- Category bars use net spend: expand Shopping and point at the offset - a returned Amazon order netted against the gross.
- Recurring & subscriptions: rent, SIPs, and subscriptions detected from cadence, with monthly total.
- "Between you and your people": the Goa trip shares friends paid back are settled inside the trip group, so they do not show up as loose credits here.

### Beat 3 - Groups and People (30s)

Open Groups and show the "Goa trip with Rahul & Neha" group.
Talking point: the group's credits offset the trip's spend in the Money Map, so a shared holiday does not inflate your travel number.

### Beat 4 - Teach Me, the review queue (60s)

Open Teach Me.
The first card is a bare UPI number the model flagged at 18% confidence.
Talking points:

- Everything below the 85% confidence bar lands here instead of silently polluting your data.
- Keyboard-first flow: confirm with Enter, correct with a click, skip with s.
- Correct one or two cards (label the UPI number, confirm the Decathlon guess).
- Every answer feeds the calibration and the learned rules - the app gets better with each correction.

### Beat 5 - The money shot: live auto-annotate (60s)

Go to Transactions, filter to the unannotated recent transactions, and hit Auto-annotate.
Six fresh merchants (Blue Tokai, Licious, metro, bakery, pharmacy, Dunzo) get categorized live through the pipeline: rules first, then RAG over your own history, then the local LLM.
Talking points:

- This runs entirely on-device via Ollama; no statement ever leaves the machine.
- Confident labels are applied; anything uncertain goes to Teach Me instead of being guessed silently.
- Afterwards, open Settings and show the learned rules the app derived from your confirmations.

### Beat 6 - Privacy toggle (10s)

Click the "Amounts" toggle in the top bar to blur every number.
Talking point: built for screen-sharing your finances without oversharing.

## Gotchas

- Run the seed and the demo on the same day; the generator anchors to today and the freshest transactions are dated today and yesterday.
- The auto-annotate beat needs Ollama running with `qwen3.5:4b` and `nomic-embed-text` pulled, and the embed step from setup done.
- Verify the health check before going live: `curl localhost:8080/api/health` should report both models available.
- Never demo against `data/finance.db`; the seed script targets `data/demo.db` by default, and `DB_PATH` keeps the server pointed at the demo ledger.
