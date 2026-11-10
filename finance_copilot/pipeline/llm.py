"""Ollama LLM client for transaction annotation."""
from __future__ import annotations

import json
import logging
import math
import re
import time
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from finance_copilot.config import settings
from finance_copilot.models.transaction import TxnRow
from finance_copilot.parsers.description import is_known_shape, parse_description
from finance_copilot.pipeline.merchant_catalog import strip_payment_app

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class LLMUnavailableError(RuntimeError):
    """The provider can't serve requests: unreachable after retries, or it
    rejected the request outright (bad key, unknown model, misconfiguration).

    This is about the provider, not the transaction, so the next call would
    fail the same way. Callers running a batch should stop calling the LLM for
    the rest of the run instead of paying the full retry budget per row. A bad
    answer for one transaction is not this error: those calls return None.
    """


# Statuses worth another try: the provider is busy, restarting or loading a
# model. Every other 4xx means the request itself is wrong and will stay wrong.
_RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_CONNECT_TIMEOUT_S = 5.0
_MAX_RETRY_AFTER_S = 30.0
_backoff = wait_exponential_jitter(initial=1, max=8)


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    # Connect/read/write errors and timeouts, but not a malformed URL scheme.
    return isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.UnsupportedProtocol)


def _retry_after_s(response: httpx.Response) -> float | None:
    """Seconds from a numeric Retry-After header (429/503), capped; else None."""
    try:
        value = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return min(max(value, 0.0), _MAX_RETRY_AFTER_S)


def _wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, httpx.HTTPStatusError):
        hinted = _retry_after_s(exc.response)
        if hinted is not None:
            return hinted
    return _backoff(retry_state)


def _http_timeout() -> httpx.Timeout:
    """Short connect (a stopped Ollama refuses at once), long read (generation)."""
    return httpx.Timeout(settings.llm_timeout_s, connect=_CONNECT_TIMEOUT_S)


def _post_chat(url: str, payload: dict, headers: dict, *, txn_id: str, log_prefix: str) -> dict:
    """POST one chat request, retrying transient failures. Returns the JSON body.

    Raises LLMUnavailableError when retries run out or the provider rejects
    the request; a body that isn't JSON surfaces as json.JSONDecodeError for
    the caller's per-transaction handling.
    """

    def _log_retry(state: RetryCallState) -> None:
        logger.warning(
            "%s | txn=%s  attempt=%d/%d  transient=%s  retrying in %.1fs",
            log_prefix, txn_id, state.attempt_number, settings.llm_max_attempts,
            state.outcome.exception() if state.outcome else None,
            state.next_action.sleep if state.next_action else 0.0,
        )

    def _send() -> httpx.Response:
        response = httpx.post(url, json=payload, headers=headers, timeout=_http_timeout())
        response.raise_for_status()
        return response

    retrying = Retrying(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(max(1, settings.llm_max_attempts)),
        wait=_wait,
        before_sleep=_log_retry,
        reraise=True,
    )
    try:
        response = retrying(_send)
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        detail = _error_detail(e.response)
        if status in _RETRYABLE_STATUS:
            msg = f"provider still failing after {settings.llm_max_attempts} attempts (HTTP {status}{detail})"
        else:
            msg = f"provider rejected the request (HTTP {status}{detail})"
        raise LLMUnavailableError(msg) from e
    except httpx.HTTPError as e:
        # Transport errors once retries run out, plus anything else httpx raises
        # (bad URL scheme, redirect loop): none of it is about this transaction.
        tries = f" after {settings.llm_max_attempts} attempts" if _is_transient(e) else ""
        raise LLMUnavailableError(f"can't reach the provider{tries}: {type(e).__name__}: {e}") from e
    return response.json()


def _error_detail(response: httpx.Response) -> str:
    """': <message>' from an Ollama/OpenAI error body, for a readable error; '' if none."""
    try:
        body = response.json()
    except ValueError:
        return ""
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        err = err.get("message")
    return f": {str(err)[:200]}" if err else ""


def insecure_key_transport() -> bool:
    """True when a BYOM API key would be sent over cleartext http to a remote host.

    Sending a bearer token over plain http to anything but the local machine
    leaks it to the network. Localhost http is fine (loopback never hits the
    wire); https is fine anywhere; no key means nothing to leak.
    """
    if settings.llm_provider != "openai" or not settings.llm_api_key:
        return False
    parsed = urlparse(settings.llm_base_url)
    if parsed.scheme != "http":
        return False
    host = (parsed.hostname or "").lower()
    return host not in _LOCAL_HOSTS


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

    @field_validator("merchant")
    @classmethod
    def _not_a_bank_line(cls, v: str | None) -> str | None:
        # Small models often echo the description ("UPI/NAME/1207.../self") as
        # the merchant; that is the bank's encoding, not a name, and once a user
        # confirms it a learned rule would copy it onto every future payment.
        return None if v and v.count("/") >= 2 else v

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
    "Be conservative — only use 0.9+ when you are very certain.\n"
    "A payee that is a person's name is a person, not a business. Use Self "
    "Transfers only when the examples show this payee as a self transfer."
)


def _llm_view(raw: str, debit_credit: str, stored_note: str | None) -> tuple[str, str | None]:
    """(description, note) as the model sees a transaction line.

    An understood UPI line becomes 'UPI payment to <payee>' with its cleaned
    note (boilerplate, the VPA and the ref already removed by the parser, and
    any payment-app phrase stripped): the reference, the IFSC bank prefix
    ('HDFC', read as a credit card) and the value date are encoding, and a
    small model takes them as facts. Other lines are shown verbatim with the
    note stored for them.
    """
    parsed = parse_description(raw, settings.upi_noise_keywords)
    if not is_known_shape(parsed) or not parsed.counterparty:
        return raw, stored_note
    if parsed.rail == "upi_reversal":
        description = f"UPI reversal (refund) from {parsed.counterparty}"
    elif debit_credit == "credit":
        description = f"UPI payment from {parsed.counterparty}"
    else:
        description = f"UPI payment to {parsed.counterparty}"
    return description, strip_payment_app(parsed.note)


def _catch_all(category_list: list[str]) -> str:
    """The category this install uses for "don't know"."""
    return "Miscellaneous" if "Miscellaneous" in category_list else "Uncategorized"


def _build_user_prompt(txn: TxnRow, category_list: list[str]) -> str:
    categories_text = "\n".join(f"  - {c}" for c in category_list)
    stored_note = json.loads(txn["upi_meta"])["note"] if txn.get("upi_meta") else None
    description, upi_note = _llm_view(txn["raw_description"], txn["debit_credit"], stored_note)

    lines = [
        f"Date: {txn.get('txn_date', '')}",
        f"Amount: {txn.get('amount', '')}",
        f"Direction: {txn.get('debit_credit', '')}",
        f"Description: {description}",
    ]
    if upi_note:
        lines.append(f"UPI Note: {upi_note}")

    lines.append("")
    lines.append("Available categories:")
    lines.append(categories_text)
    lines.append("")
    lines.append(
        "If you do not recognise the payee, do not guess a business type from its name: "
        f"answer {_catch_all(top_level_categories(category_list))} with low confidence."
    )
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
        ex_description, ex_note = _llm_view(ex["raw_description"], ex["debit_credit"], ex["upi_note"])
        lines.append(f"  Date: {ex.get('txn_date', '')}")
        lines.append(f"  Description: {ex_description}")
        if ex_note:
            lines.append(f"  UPI Note: {ex_note}")
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
        if insecure_key_transport():
            raise ValueError(
                "Refusing to send the API key over cleartext http to a remote host. "
                "Use https, or a localhost endpoint. Set llm_base_url accordingly."
            )
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

    if settings.llm_provider == "local":
        # In-process llama-cpp: OpenAI-shaped payload, no URL. JSON validity is
        # grammar-enforced (schema → GBNF inside llama-cpp), not requested.
        payload = {
            "model": settings.local_llm_model,
            "max_tokens": 512,
            "response_format": {"type": "json_object", "schema": schema},
            "messages": messages,
            "logprobs": settings.llm_logprob_confidence,
        }
        return "", {}, payload

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
    max_retries: int,
) -> AnnotationResult | None:
    """Shared provider chat call. Returns None when this transaction's answer
    stays unusable; raises LLMUnavailableError when the provider itself is.

    Two separate retry budgets. Transport failures (connection refused, timeout,
    429/5xx) are retried inside _post_chat with backoff, up to
    settings.llm_max_attempts. An answer that arrives but isn't valid JSON for
    the schema gets up to max_retries self-repair re-asks here.
    """
    if settings.llm_provider == "none":
        logger.debug("%s | txn=%s  llm_provider=none — skipping", log_prefix, txn_id)
        return None

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        url, headers, payload = _build_provider_request(messages, category_list)
    except ValueError as e:  # refused config (e.g. API key over remote http)
        raise LLMUnavailableError(str(e)) from e

    logger.debug(
        "%s | txn=%s  provider=%s  model=%s  prompt=\n%s",
        log_prefix, txn_id, settings.llm_provider,
        payload.get("model"), user_prompt,
    )

    for attempt in range(max_retries + 1):
        t0 = time.monotonic()
        content = ""
        data = None
        try:
            if settings.llm_provider == "local":
                from finance_copilot import local_llm

                try:
                    data = local_llm.chat_completion(payload)
                except local_llm.LocalLLMError as e:
                    # A missing extra or model can't heal mid-run.
                    raise LLMUnavailableError(f"local model unavailable: {e}") from e
            else:
                data = _post_chat(url, payload, headers, txn_id=txn_id, log_prefix=log_prefix)
            elapsed = time.monotonic() - t0
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
        except (KeyError, IndexError) as e:
            # The provider answered with an unexpected shape (no message/choices).
            raw = content or (json.dumps(data)[:500] if data is not None else "")
            logger.warning("%s | txn=%s  attempt=%d  bad_shape=%r  raw=%s", log_prefix, txn_id, attempt, e, raw)
        except json.JSONDecodeError as e:
            logger.warning("%s | txn=%s  attempt=%d  json_error=%s  raw=%s", log_prefix, txn_id, attempt, e, content)
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
    max_retries: int = 2,
) -> AnnotationResult | None:
    """Call the configured LLM provider with few-shot examples injected.

    Returns None when the answer stays unusable after max_retries re-asks;
    raises LLMUnavailableError when the provider can't be used at all.
    """
    return _call_llm(
        _build_fewshot_user_prompt(
            txn, category_list, similar_examples, majority_category, majority_count
        ),
        category_list,
        txn_id=txn.get("id", "?"),
        log_prefix="llm_with_examples",
        max_retries=max_retries,
    )


def test_connection(timeout: float = 10.0) -> dict:
    """Probe the configured LLM provider with a trivial request.

    Returns {ok, detail, models?}. Used by the Settings › AI & Model
    test-connection button so a user can validate a BYOM endpoint before relying
    on it. Never raises — connection/HTTP errors are reported in `detail`.
    """
    provider = settings.llm_provider
    if provider == "none":
        return {"ok": True, "detail": "AI is disabled (provider = none)."}

    if provider == "local":
        from finance_copilot.local_llm import test_local_connection

        return test_local_connection()

    try:
        if provider == "openai":
            base = settings.llm_base_url.rstrip("/")
            if not base:
                return {"ok": False, "detail": "No endpoint URL set."}
            if insecure_key_transport():
                return {
                    "ok": False,
                    "detail": "This endpoint is cleartext http on a remote host, so the "
                    "API key would be sent unencrypted. Use https or a localhost endpoint.",
                }
            headers = {"Authorization": f"Bearer {settings.llm_api_key}"} if settings.llm_api_key else {}
            resp = httpx.get(f"{base}/models", headers=headers, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("id") for m in data.get("data", []) if m.get("id")]
            detail = f"Connected. {len(models)} model(s) available."
            if settings.llm_model and models and settings.llm_model not in models:
                detail += f" Warning: '{settings.llm_model}' not in the list."
            return {"ok": True, "detail": detail, "models": models[:50]}

        # ollama
        resp = httpx.get(f"{settings.ollama_url}/api/tags", timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        models = [m.get("name") for m in data.get("models", []) if m.get("name")]
        detail = f"Connected to Ollama. {len(models)} model(s) pulled."
        if settings.ollama_model and models and settings.ollama_model not in models:
            detail += f" Warning: '{settings.ollama_model}' is not pulled — run `ollama pull {settings.ollama_model}`."
        return {"ok": True, "detail": detail, "models": models}
    except (httpx.HTTPError, httpx.TimeoutException, ValueError, KeyError) as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def annotate_transaction_llm(
    txn: TxnRow,
    category_list: list[str],
    max_retries: int = 2,
) -> AnnotationResult | None:
    """Call the configured LLM provider to annotate a transaction.

    Returns None when the answer stays unusable after max_retries re-asks;
    raises LLMUnavailableError when the provider can't be used at all.
    """
    return _call_llm(
        _build_user_prompt(txn, category_list),
        category_list,
        txn_id=txn.get("id", "?"),
        log_prefix="llm",
        max_retries=max_retries,
    )
