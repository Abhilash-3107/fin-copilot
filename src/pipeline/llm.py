"""Ollama LLM client for transaction annotation."""
from __future__ import annotations

import json
import time

import httpx
from pydantic import BaseModel, Field, ValidationError

from src.config import settings


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
    "tags is a list of short lowercase strings."
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
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(txn, category_list)},
        ],
    }

    for attempt in range(max_retries + 1):
        try:
            response = httpx.post(url, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            content = data["message"]["content"]
            return AnnotationResponse.model_validate_json(content)
        except (httpx.HTTPError, httpx.TimeoutException):
            pass
        except (KeyError, json.JSONDecodeError, ValidationError):
            pass

        if attempt < max_retries:
            time.sleep(1.0)

    return None
