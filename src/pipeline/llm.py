"""Ollama LLM client for transaction annotation."""
from __future__ import annotations

import json
import logging
import math
import re
import time

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from src.config import settings
from src.models.transaction import TxnRow

logger = logging.getLogger(__name__)


def _strip_code_fence(content: str) -> str:
    """Unwrap a markdown code fence (```json ... ```) some models emit despite the JSON schema."""
    stripped = content.strip()
    if not stripped.startswith("```"):
        return content
    # Drop the opening fence line (```/```json) and the trailing fence.
    body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
    if body.rstrip().endswith("```"):
        body = body.rstrip()[: -len("```")]
    return body.strip()


def _salvage_dropping_reasoning(content: str) -> AnnotationResponse | None:
    """Parse the response after dropping a malformed `reasoning` value.

    Small models intermittently mangle the free-text `reasoning` string (escaped
    `\\"`, nested quotes, stray braces) into invalid JSON, which fails parsing
    *before* any field validator runs. The category/subcategory/confidence are
    still recoverable — strip the reasoning field and retry, so a cosmetic prose
    error never costs us a usable classification. Returns None if unrecoverable.
    """
    # Remove a `"reasoning": "..."` (or `\"..."`) entry, greedy to the last
    # quote before the next key or the closing brace.
    cleaned = re.sub(
        r',?\s*"reasoning"\s*:\s*\\?".*(?=,\s*"|\s*})',
        "",
        content,
        flags=re.DOTALL,
    )
    # Also handle reasoning as the trailing field with a broken closer.
    cleaned = re.sub(r',?\s*"reasoning"\s*:\s*\\?".*$', "}", cleaned, flags=re.DOTALL)
    try:
        return AnnotationResponse.model_validate_json(cleaned)
    except (ValidationError, json.JSONDecodeError):
        return None


class AnnotationResponse(BaseModel):
    category: str
    subcategory: str | None = None
    merchant: str | None = None
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str | None = Field(
        default=None,
        description=(
            "One short sentence (<=160 chars) explaining why this category was "
            "chosen. Avoid nested quotes."
        ),
    )

    @field_validator("reasoning")
    @classmethod
    def _truncate_reasoning(cls, v: str | None) -> str | None:
        # Reasoning is a dev-mode nicety; a slightly-too-long sentence must never
        # reject an otherwise-valid classification (small models overshoot the
        # length hint). Truncate instead of failing validation.
        if v and len(v) > 160:
            return v[:157].rstrip() + "..."
        return v


class AnnotationResult(AnnotationResponse):
    """AnnotationResponse plus call telemetry for the reasoning trace.

    A subclass (not extra fields on AnnotationResponse) because the response
    JSON schema sent to the provider is generated from AnnotationResponse and
    must not grow telemetry fields.
    """
    model_name: str | None = None
    prompt_tokens: int | None = None
    prompt_truncated: bool = False
    verbalized_confidence: float | None = None  # what the model wrote
    logprob_confidence: float | None = None     # token-logprob mass, when available


def _logprob_category_confidence(data: dict, category: str) -> float | None:
    """Derive confidence from token logprobs over the category value span.

    With an enum-constrained grammar, the probability the model assigns to the
    chosen category's tokens is a direct, continuous confidence signal (vs the
    quantized verbalized number). Locates the `"category"` value span in the
    reconstructed output and sums the logprobs of the tokens overlapping it.
    Returns None when logprobs are missing or the span can't be located.
    """
    logprobs = (data.get("message") or {}).get("logprobs") or data.get("logprobs")
    if not logprobs or not category:
        return None
    try:
        tokens = [(lp["token"], lp["logprob"]) for lp in logprobs]
    except (KeyError, TypeError):
        return None
    text = "".join(t for t, _ in tokens)
    key_idx = text.find('"category"')
    if key_idx < 0:
        return None
    val_idx = text.find(category, key_idx)
    if val_idx < 0:
        return None
    val_end = val_idx + len(category)
    total = 0.0
    pos = 0
    for tok, lp in tokens:
        tok_start, tok_end = pos, pos + len(tok)
        pos = tok_end
        if tok_end <= val_idx:
            continue
        if tok_start >= val_end:
            break
        total += lp
    prob = math.exp(total)
    return max(0.0, min(1.0, prob))


def top_level_categories(category_list: list[str]) -> list[str]:
    """Collapse 'Category > Subcategory' strings to unique top-level category names."""
    seen: list[str] = []
    for c in category_list:
        top = c.split(" > ", 1)[0].strip()
        if top and top not in seen:
            seen.append(top)
    return seen


def _response_schema(category_list: list[str]) -> dict:
    """JSON schema for Ollama structured output, with category constrained to the taxonomy.

    The enum makes a small model pick a real category instead of inventing one
    (e.g. 'Food' or 'Subscriptions'); server-side validation in annotate.py is
    the backstop.
    """
    schema = AnnotationResponse.model_json_schema()
    tops = top_level_categories(category_list)
    if tops:
        schema["properties"]["category"]["enum"] = tops
    return schema


_SYSTEM_PROMPT = (
    "You are a personal finance categorizer for Indian bank transactions. "
    "Classify each transaction into the provided categories. "
    "Return ONLY valid JSON matching this schema:\n"
    '{"category": "...", "subcategory": "...", "merchant": "...", "tags": [...], "confidence": 0.0, "reasoning": "..."}\n'
    "confidence must be between 0 and 1. subcategory and merchant may be null. "
    "tags is a list of short lowercase strings. "
    "reasoning is ONE short sentence explaining why you chose this category.\n"
    "When example transactions are provided, they are confirmed categorizations of "
    "transactions similar to this one and are your primary signal — weigh them above "
    "your own prior knowledge.\n"
    "Confidence guidelines:\n"
    "- 0.95: Exact merchant match, unambiguous category (e.g. 'Netflix' → Entertainment)\n"
    "- 0.85: Strong match with minor ambiguity (e.g. generic 'food' description)\n"
    "- 0.70: Reasonable guess but multiple categories are plausible\n"
    "- 0.50: Weak signal, essentially guessing\n"
    "Be conservative — only use 0.9+ when you are very certain."
)


def _build_user_prompt(txn: TxnRow, category_list: list[str]) -> str:
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
    txn: TxnRow,
    category_list: list[str],
    similar_examples: list[dict],
    majority_category: str | None = None,
    majority_count: int = 0,
) -> str:
    """Build user prompt with few-shot examples from RAG retrieval injected before the transaction.

    When majority_category is given, an agreement hint and a guardrail instruction
    are added: the LLM should prefer a category that appears among the examples and
    only pick one absent from all of them when the transaction is clearly different.
    This counters the failure where the model falls back on its pretraining prior
    (e.g. 'named person + UPI → peer transfer') and invents a category none of the
    retrieved neighbors used.
    """
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

    guidance = [
        "Prefer a category that appears among the examples above. Only choose a "
        "category that none of the examples use if this transaction is clearly "
        "different from every one of them (e.g. a merchant name you recognize that "
        "contradicts them)."
    ]
    if majority_category and majority_count > 0:
        guidance.insert(
            0,
            f"Note: {majority_count} of the examples above were categorized as "
            f'"{majority_category}".',
        )
    parts.append("\n".join(guidance))

    parts.append("Now classify this transaction:")
    parts.append(_build_user_prompt(txn, category_list))
    return "\n\n".join(parts)


def _build_provider_request(
    messages: list[dict], category_list: list[str]
) -> tuple[str, dict, dict]:
    """Return (url, headers, payload) for the configured provider.

    Both providers get token-level structured output: Ollama via `format`,
    OpenAI-compatible endpoints via `response_format: json_schema` (supported by
    OpenAI, vLLM, LM Studio, OpenRouter; endpoints that ignore it still hit the
    server-side validation backstop in annotate.py).
    """
    schema = _response_schema(category_list)
    if settings.llm_provider == "openai":
        url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
        payload = {
            "model": settings.llm_model,
            "temperature": 0,
            "seed": 42,
            "max_tokens": 512,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "annotation", "schema": schema, "strict": False},
            },
            "messages": messages,
        }
        return url, headers, payload

    url = f"{settings.ollama_url}/api/chat"
    payload = {
        "model": settings.ollama_model,
        "stream": False,
        # Keep the model resident between calls — cold-loading a 4B model costs
        # 5-15 s and auto-annotate runs are bursty.
        "keep_alive": "30m",
        "format": schema,
        "options": {
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": 512,  # headroom for the one-sentence reasoning field
            # Categorization is deterministic, not creative: temperature 0 + a fixed
            # seed make the structured output stable run-to-run. Without this, the
            # default temperature (~0.8) makes a small model mangle the free-text
            # `reasoning` field into invalid JSON on some samples, which fails schema
            # validation and silently degrades the label. See
            # scripts/diff_reannotate_april.py.
            "temperature": 0,
            "seed": 42,
        },
        "think": False,
        "messages": messages,
    }
    if settings.llm_logprob_confidence:
        payload["logprobs"] = True
    return url, {}, payload


def _extract_content(data: dict) -> str:
    """Pull the assistant text out of an Ollama or OpenAI-shaped chat response."""
    if "choices" in data:  # OpenAI-compatible
        return data["choices"][0]["message"]["content"] or ""
    return data["message"]["content"]


def _call_llm(
    user_prompt: str,
    category_list: list[str],
    txn_id: str,
    log_prefix: str,
    timeout: float,
    max_retries: int,
) -> AnnotationResult | None:
    """Shared provider chat call with retries and error taxonomy. Returns None on final failure."""
    if settings.llm_provider == "none":
        logger.debug("%s | txn=%s  llm_provider=none — skipping", log_prefix, txn_id)
        return None

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    url, headers, payload = _build_provider_request(messages, category_list)

    logger.debug(
        "%s | txn=%s  provider=%s  model=%s  prompt=\n%s",
        log_prefix, txn_id, settings.llm_provider,
        payload.get("model"), user_prompt,
    )

    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        content = ""
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            elapsed = time.monotonic() - t0
            response.raise_for_status()
            data = response.json()
            content = _strip_code_fence(_extract_content(data))
            try:
                result = AnnotationResponse.model_validate_json(content)
            except (ValidationError, json.JSONDecodeError):
                # A malformed free-text `reasoning` value is the common culprit;
                # try to recover the classification by dropping it before failing.
                result = _salvage_dropping_reasoning(content)
                if result is None:
                    raise
                logger.info(
                    "%s | txn=%s  attempt=%d  salvaged classification by dropping malformed reasoning",
                    log_prefix, txn_id, attempt,
                )
            # Truncation is silent (Ollama drops from the front, i.e. the system
            # prompt) — log the actual prompt token count so it's observable.
            prompt_tokens = data.get("prompt_eval_count") or (data.get("usage") or {}).get("prompt_tokens")
            truncated = prompt_tokens is not None and prompt_tokens >= settings.ollama_num_ctx - 64
            if truncated:
                logger.warning(
                    "%s | txn=%s  prompt_eval_count=%d ~ num_ctx=%d - prompt likely truncated",
                    log_prefix, txn_id, prompt_tokens, settings.ollama_num_ctx,
                )
            lp_conf = None
            if settings.llm_logprob_confidence:
                lp_conf = _logprob_category_confidence(data, result.category)
                if lp_conf is not None:
                    logger.debug(
                        "%s | txn=%s  verbalized_conf=%.2f  logprob_conf=%.4f",
                        log_prefix, txn_id, result.confidence, lp_conf,
                    )
            logger.debug(
                "%s | txn=%s  attempt=%d  latency=%.2fs  prompt_tokens=%s  response=%s",
                log_prefix, txn_id, attempt, elapsed, prompt_tokens, content,
            )
            fields = result.model_dump()
            verbalized = fields["confidence"]
            if lp_conf is not None:
                fields["confidence"] = lp_conf
            return AnnotationResult(
                **fields,
                model_name=payload.get("model"),
                prompt_tokens=prompt_tokens,
                prompt_truncated=truncated,
                verbalized_confidence=verbalized,
                logprob_confidence=lp_conf,
            )
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            elapsed = time.monotonic() - t0
            logger.warning(
                "%s | txn=%s  attempt=%d  latency=%.2fs  http_error=%s",
                log_prefix, txn_id, attempt, elapsed, e,
            )
            if attempt < max_retries:
                time.sleep(1.0)
            continue
        except KeyError as e:
            logger.warning("%s | txn=%s  attempt=%d  missing_key=%s  raw=%s", log_prefix, txn_id, attempt, e, response.text)
        except json.JSONDecodeError as e:
            logger.warning("%s | txn=%s  attempt=%d  json_error=%s  raw=%s", log_prefix, txn_id, attempt, e, response.text)
        except ValidationError as e:
            logger.warning("%s | txn=%s  attempt=%d  validation_error=%s  raw=%s", log_prefix, txn_id, attempt, e, content)

        # Parse/validation failure: at temperature 0 with a fixed seed, resending
        # the identical payload deterministically fails identically. Feed the bad
        # output and the requirement back instead (one-shot self-repair).
        if attempt < max_retries:
            payload["messages"] = payload["messages"][:2] + [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": "Your previous response was not valid JSON for the schema. "
                    "Return ONLY the corrected JSON object, nothing else.",
                },
            ]

    logger.error("%s | txn=%s  all %d attempts failed", log_prefix, txn_id, max_retries + 1)
    return None


def annotate_transaction_llm_with_examples(
    txn: TxnRow,
    category_list: list[str],
    similar_examples: list[dict],
    majority_category: str | None = None,
    majority_count: int = 0,
    timeout: float = 60.0,
    max_retries: int = 2,
) -> AnnotationResult | None:
    """Call the configured LLM provider with few-shot examples injected. Returns None on final failure."""
    return _call_llm(
        _build_fewshot_user_prompt(
            txn, category_list, similar_examples, majority_category, majority_count
        ),
        category_list,
        txn_id=txn.get("id", "?"),
        log_prefix="llm_with_examples",
        timeout=timeout,
        max_retries=max_retries,
    )


def annotate_transaction_llm(
    txn: TxnRow,
    category_list: list[str],
    timeout: float = 60.0,
    max_retries: int = 2,
) -> AnnotationResult | None:
    """Call the configured LLM provider to annotate a transaction. Returns None on final failure."""
    return _call_llm(
        _build_user_prompt(txn, category_list),
        category_list,
        txn_id=txn.get("id", "?"),
        log_prefix="llm",
        timeout=timeout,
        max_retries=max_retries,
    )
