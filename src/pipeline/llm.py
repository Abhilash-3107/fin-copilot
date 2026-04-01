"""Ollama LLM client for transaction annotation."""
from __future__ import annotations

import json
import logging
import time

import httpx
from pydantic import BaseModel, Field, ValidationError

from src.config import settings

logger = logging.getLogger(__name__)


class AnnotationResponse(BaseModel):
    category: str
    subcategory: str | None = None
    merchant: str | None = None
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


_SYSTEM_PROMPT = (
    "You are a personal finance categorizer for Indian bank transactions. "
    "Classify each transaction into the provided categories. "
    "Return ONLY valid JSON matching this schema:\n"
    '{"category": "...", "subcategory": "...", "merchant": "...", "tags": [...], "confidence": 0.0}\n'
    "confidence must be between 0 and 1. subcategory and merchant may be null. "
    "tags is a list of short lowercase strings.\n"
    "Confidence guidelines:\n"
    "- 0.95: Exact merchant match, unambiguous category (e.g. 'Netflix' → Entertainment)\n"
    "- 0.85: Strong match with minor ambiguity (e.g. generic 'food' description)\n"
    "- 0.70: Reasonable guess but multiple categories are plausible\n"
    "- 0.50: Weak signal, essentially guessing\n"
    "Be conservative — only use 0.9+ when you are very certain."
)


def _build_user_prompt(txn: dict, category_list: list[str]) -> str:
    categories_text = "\n".join(f"  - {c}" for c in category_list)
    upi_note = ""
    if txn.get("upi_meta"):
        try:
            meta = json.loads(txn["upi_meta"]) if isinstance(txn["upi_meta"], str) else txn["upi_meta"]
            upi_note = str(meta.get("note", ""))
        except Exception:
            pass

    lines = [
        f"Date: {txn.get('txn_date', '')}",
        f"Amount: {txn.get('amount', '')}",
        f"Direction: {txn.get('debit_credit', '')}",
        f"Description: {txn.get('raw_description', '')}",
    ]
    if upi_note:
        lines.append(f"UPI Note: {upi_note}")

    lines.append("")
    lines.append("Available categories:")
    lines.append(categories_text)
    lines.append("")
    lines.append("Respond with JSON only.")

    return "\n".join(lines)


def _build_fewshot_user_prompt(
    txn: dict,
    category_list: list[str],
    similar_examples: list[dict],
) -> str:
    """Build user prompt with few-shot examples from RAG retrieval injected before the transaction."""
    parts = ["Here are similar transactions that were previously categorized:\n"]

    for i, ex in enumerate(similar_examples, 1):
        lines = [f"Example {i}:"]
        lines.append(f"  Date: {ex.get('txn_date', '')}")
        lines.append(f"  Description: {ex.get('raw_description', '')}")
        if ex.get("upi_note"):
            lines.append(f"  UPI Note: {ex['upi_note']}")
        lines.append(f"  Amount: {ex.get('amount', '')} ({ex.get('debit_credit', '')})")
        cat = ex.get("category", "")
        sub = ex.get("subcategory")
        lines.append(f"  Category: {cat + ' > ' + sub if sub else cat}")
        if ex.get("merchant"):
            lines.append(f"  Merchant: {ex['merchant']}")
        parts.append("\n".join(lines))

    parts.append("\n---\n")
    parts.append("Now classify this transaction:")
    parts.append(_build_user_prompt(txn, category_list))
    return "\n\n".join(parts)


def annotate_transaction_llm_with_examples(
    txn: dict,
    category_list: list[str],
    similar_examples: list[dict],
    timeout: float = 60.0,
    max_retries: int = 2,
) -> AnnotationResponse | None:
    """Call Ollama with few-shot examples injected into the prompt. Returns None on final failure."""
    url = f"{settings.ollama_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",
        "options": {
            "num_ctx": 2048,
            "num_predict": 256,
        },
        "think": False,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_fewshot_user_prompt(txn, category_list, similar_examples)},
        ],
    }

    txn_id = txn.get("id", "?")
    logger.debug(
        "llm_with_examples | txn=%s  model=%s  examples=%d  prompt=\n%s",
        txn_id, settings.ollama_model, len(similar_examples),
        payload["messages"][1]["content"],
    )

    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
            elapsed = time.monotonic() - t0
            response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]
            result = AnnotationResponse.model_validate_json(content)
            logger.debug(
                "llm_with_examples | txn=%s  attempt=%d  latency=%.2fs  response=%s",
                txn_id, attempt, elapsed, content,
            )
            return result
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            elapsed = time.monotonic() - t0
            logger.warning(
                "llm_with_examples | txn=%s  attempt=%d  latency=%.2fs  http_error=%s",
                txn_id, attempt, elapsed, e,
            )
        except KeyError as e:
            logger.warning("llm_with_examples | txn=%s  attempt=%d  missing_key=%s  raw=%s", txn_id, attempt, e, response.text)
        except json.JSONDecodeError as e:
            logger.warning("llm_with_examples | txn=%s  attempt=%d  json_error=%s  raw=%s", txn_id, attempt, e, response.text)
        except ValidationError as e:
            logger.warning("llm_with_examples | txn=%s  attempt=%d  validation_error=%s  raw=%s", txn_id, attempt, e, content)

        if attempt < max_retries:
            time.sleep(1.0)

    logger.error("llm_with_examples | txn=%s  all %d attempts failed", txn_id, max_retries + 1)
    return None


def annotate_transaction_llm(
    txn: dict,
    category_list: list[str],
    timeout: float = 60.0,
    max_retries: int = 2,
) -> AnnotationResponse | None:
    """Call Ollama to annotate a transaction. Returns None on final failure."""
    url = f"{settings.ollama_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "format": "json",
        "options": {
            "num_ctx": 2048,
            "num_predict": 256,
        },
        "think": False,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(txn, category_list)},
        ],
    }

    txn_id = txn.get("id", "?")
    logger.debug(
        "llm | txn=%s  model=%s  prompt=\n%s",
        txn_id, settings.ollama_model, payload["messages"][1]["content"],
    )

    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
            elapsed = time.monotonic() - t0
            response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]
            result = AnnotationResponse.model_validate_json(content)
            logger.debug(
                "llm | txn=%s  attempt=%d  latency=%.2fs  response=%s",
                txn_id, attempt, elapsed, content,
            )
            return result
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            elapsed = time.monotonic() - t0
            logger.warning(
                "llm | txn=%s  attempt=%d  latency=%.2fs  http_error=%s",
                txn_id, attempt, elapsed, e,
            )
        except KeyError as e:
            logger.warning("llm | txn=%s  attempt=%d  missing_key=%s  raw=%s", txn_id, attempt, e, response.text)
        except json.JSONDecodeError as e:
            logger.warning("llm | txn=%s  attempt=%d  json_error=%s  raw=%s", txn_id, attempt, e, response.text)
        except ValidationError as e:
            logger.warning("llm | txn=%s  attempt=%d  validation_error=%s  raw=%s", txn_id, attempt, e, content)

        if attempt < max_retries:
            time.sleep(1.0)

    logger.error("llm | txn=%s  all %d attempts failed", txn_id, max_retries + 1)
    return None
