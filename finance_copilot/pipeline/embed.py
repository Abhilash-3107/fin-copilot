"""Transaction descriptions → embeddings; upsert into vec_items and embedding_meta.

Provider seam (settings.embedding_provider): "ollama" calls the Ollama API,
"local" runs fastembed/onnxruntime in-process (weights auto-download into the
data dir on first use), "none" raises EmbeddingsUnavailable which every caller
treats as "quietly skip the embedding-dependent feature".
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from pathlib import Path

import httpx

from finance_copilot.config import current_embedding_model, settings
from finance_copilot.db.queries.embeddings import upsert_embedding
from finance_copilot.models.transaction import TxnRow
from finance_copilot.parsers.description import is_known_shape, parse_description

logger = logging.getLogger(__name__)


class EmbeddingsUnavailable(RuntimeError):
    """No usable embedding provider: "none" is configured, or "local" is
    configured but fastembed is not installed."""


def local_embeddings_installed() -> bool:
    """Whether the in-process provider can run here.

    fastembed is a hard dependency except on Pythons that onnxruntime publishes
    no wheels for (see the marker in pyproject.toml). There the app installs
    and runs without it, and "local" behaves like "none".
    """
    import importlib.util

    return importlib.util.find_spec("fastembed") is not None


def build_embed_text(txn: TxnRow) -> str:
    """Canonical embedding text: '{debit_credit} {description}', lowercased.

    Understood UPI lines embed as '{dc} upi/{payee}[/{note}]', where payee is the
    stored, resolved counterparty_key when present, so truncation variants of one
    payee ('OBEROIFC TUCKSH' / 'OBEROIFC TUCKS') embed identically. Boilerplate
    notes ('UPI', 'NO REMARKS', ...) are dropped and the note appears once.

    Other rows keep the previous format: '{dc} {raw description} {upi note}'.

    The amount is deliberately excluded: it is per-transaction noise that dilutes
    the merchant/counterparty signal the retriever depends on.

    The result is lowercased: nomic-embed-text tokenizes ALL-CAPS bank-statement
    text into near-meaningless fragments, so distinct uppercase personal-name
    payees (e.g. 'M S KINGSTON PR' vs 'AABID ALI SO NA') collapse to *identical*
    embeddings (cosine 1.0). Lowercasing recovers normal subword tokens and the
    names separate cleanly (~0.7).

    Each vector records a hash of this text (embedding_meta.text_hash), so a
    change to the output is picked up row by row by refresh_embeddings.
    """
    dc = txn.get("debit_credit", "")
    raw = txn.get("raw_description", "") or ""
    parsed = parse_description(raw, settings.upi_noise_keywords)
    if is_known_shape(parsed) and parsed.counterparty:
        head = "upi" if parsed.rail == "upi" else "rev-upi"
        payee = txn["counterparty_key"] if parsed.rail == "upi" else parsed.counterparty
        desc = f"{head}/{payee}/{parsed.note}" if parsed.note else f"{head}/{payee}"
        return " ".join(p for p in (dc, desc) if p).strip().lower()

    upi_note = json.loads(txn["upi_meta"])["note"] if txn.get("upi_meta") else None
    return " ".join(p for p in (dc, raw, upi_note) if p).strip().lower()


def embed_text_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:16]


# One in-process fastembed model, loaded lazily on first embed. Keyed by model
# name so a settings change mid-process swaps the instance instead of serving
# stale vectors.
_local_embedder = None
_local_embedder_model: str | None = None


def _get_local_embedder():
    global _local_embedder, _local_embedder_model
    if _local_embedder is None or _local_embedder_model != settings.local_embedding_model:
        from fastembed import TextEmbedding  # deferred: pulls onnxruntime

        cache_dir = Path(settings.data_dir) / "models" / "embeddings"
        cache_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            "loading local embedding model %s (first use downloads ~100MB into %s)",
            settings.local_embedding_model, cache_dir,
        )
        _local_embedder = TextEmbedding(
            model_name=settings.local_embedding_model, cache_dir=str(cache_dir)
        )
        _local_embedder_model = settings.local_embedding_model
    return _local_embedder


def _ollama_embeddings_batch(texts: list[str], timeout: float) -> list[list[float]]:
    url = f"{settings.ollama_url}/api/embed"
    payload = {
        "model": settings.ollama_embedding_model,
        "input": texts,
    }
    # Short connect: a stopped or unreachable Ollama should fail in seconds,
    # not hold a run for the whole read timeout.
    response = httpx.post(url, json=payload, timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)))
    response.raise_for_status()
    return response.json()["embeddings"]


def get_embeddings_batch(
    texts: list[str],
    timeout: float = 120.0,
) -> list[list[float]]:
    """Embed a batch of texts with the configured provider. Returns vectors."""
    provider = settings.embedding_provider
    if provider == "none":
        raise EmbeddingsUnavailable("embedding_provider is 'none'")
    if provider == "local":
        if not local_embeddings_installed():
            raise EmbeddingsUnavailable(
                "embedding_provider is 'local' but fastembed is not installed "
                "(onnxruntime has no wheels for this Python version)"
            )
        return [vec.tolist() for vec in _get_local_embedder().embed(texts)]
    return _ollama_embeddings_batch(texts, timeout)


def get_embedding_single(text: str, timeout: float = 60.0) -> list[float]:
    """Get embedding for a single text."""
    return get_embeddings_batch([text], timeout=timeout)[0]


def embed_transaction(conn: sqlite3.Connection, transaction_id: str) -> bool:
    """Embed one transaction so it can serve as a RAG donor immediately.

    Best-effort: returns False instead of raising when the transaction is missing
    or the embedding service is down (the bulk /embeddings/generate endpoint
    backfills gaps), so annotation writes never fail because of Ollama.
    """
    try:
        row = conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if row is None:
            return False
        text = build_embed_text(dict(row))
        upsert_embedding(conn, transaction_id, get_embedding_single(text), current_embedding_model(), embed_text_hash(text))
        conn.commit()
        return True
    except Exception as e:
        logger.warning("embed on write failed | txn=%s  error=%s", transaction_id, e)
        return False


def embed_transactions(
    conn: sqlite3.Connection,
    transaction_ids: list[str],
    batch_size: int = 32,
) -> int:
    """Embed a known set of transactions in batches; returns how many landed.

    Best-effort like embed_transaction: missing ids are skipped, a dead
    provider stops the run, and a failed batch is logged and skipped (the bulk
    /embeddings/generate endpoint backfills gaps). Never raises.
    """
    rows: list[dict] = []
    for i in range(0, len(transaction_ids), 500):
        chunk = transaction_ids[i : i + 500]
        placeholders = ",".join("?" * len(chunk))
        rows += [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM transactions WHERE id IN ({placeholders})", chunk
            ).fetchall()
        ]

    model_version = current_embedding_model()
    embedded = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        texts = [build_embed_text(t) for t in batch]
        try:
            vectors = get_embeddings_batch(texts)
        except EmbeddingsUnavailable:
            break
        except Exception:
            logger.warning("embedding batch failed; skipping %d texts", len(batch), exc_info=True)
            continue
        for txn, text, vec in zip(batch, texts, vectors, strict=True):
            upsert_embedding(conn, txn["id"], vec, model_version, embed_text_hash(text))
            embedded += 1
        conn.commit()
    return embedded


def stale_embedding_rows(conn: sqlite3.Connection, statement_id: str | None = None) -> list[tuple[dict, str]]:
    """Annotated transactions whose vector is missing, from another model, or
    built from text other than what build_embed_text produces now; each with
    its current text. Annotated rows are the RAG donor pool."""
    query = """
        SELECT t.*, em.model_version AS _model, em.text_hash AS _hash
        FROM transactions t
        JOIN annotations a ON a.transaction_id = t.id
        LEFT JOIN embedding_meta em ON em.transaction_id = t.id
    """
    params: list = []
    if statement_id:
        query += " WHERE t.statement_id = ?"
        params.append(statement_id)
    model = current_embedding_model()
    stale = []
    for row in conn.execute(query + " ORDER BY t.id", params):
        txn = dict(row)
        text = build_embed_text(txn)
        if txn.pop("_model") != model or txn.pop("_hash") != embed_text_hash(text):
            stale.append((txn, text))
    return stale


def refresh_embeddings(
    conn: sqlite3.Connection,
    statement_id: str | None = None,
    batch_size: int = 32,
    progress_cb=None,
) -> dict:
    """Embed every stale annotated transaction (see stale_embedding_rows).

    Incremental and resumable: rows already current are skipped, and a run cut
    short leaves the rest stale for the next one. Raises on a dead provider: a
    rebuild that silently produces nothing would strand RAG with no
    explanation.
    """
    stale = stale_embedding_rows(conn, statement_id)
    model = current_embedding_model()
    done = 0
    for i in range(0, len(stale), batch_size):
        batch = stale[i : i + batch_size]
        vectors = get_embeddings_batch([text for _txn, text in batch])
        for (txn, text), vec in zip(batch, vectors, strict=True):
            upsert_embedding(conn, txn["id"], vec, model, embed_text_hash(text))
            done += 1
        conn.commit()
        if progress_cb:
            progress_cb(done, len(stale))
    return {"embedded": done, "model": model}
