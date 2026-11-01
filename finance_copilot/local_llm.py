"""Fully-local LLM provider: llama-cpp-python running a GGUF in-process.

Shipped as the `finance-copilot[local-llm]` pip extra - llama-cpp-python wheels
are large and platform-fussy, so the base install never depends on them. This
module degrades to honest status reporting when the extra is absent.

The model itself is never inside the wheel: it downloads on demand into
<data_dir>/models/llm/, resumable and sha256-verified against the manifest
below. The manifest indirection (key → url/sha256) is what lets a future
fine-tuned categorizer become the default via a metadata change only.
"""
from __future__ import annotations

import atexit
import hashlib
import importlib.util
import logging
import platform
import shutil
import threading
from pathlib import Path

import httpx

from finance_copilot.config import settings

logger = logging.getLogger(__name__)

# Pinned artifacts (unsloth/Qwen3.5-4B-GGUF, sha256 = HF lfs oid). The default
# matches the Ollama baseline model family (qwen3.5:4b) so eval comparisons are
# apples-to-apples. The smaller quant exists for constrained connections;
# resumable download is the primary mitigation for flaky Indian home bandwidth.
MODEL_MANIFEST: dict[str, dict] = {
    "qwen3.5-4b-q4_k_m": {
        "label": "Qwen 3.5 4B (Q4_K_M, recommended)",
        "filename": "Qwen3.5-4B-Q4_K_M.gguf",
        "url": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q4_K_M.gguf",
        "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
        "size": 2_740_937_888,
        "quant": "Q4_K_M",
        # Model + KV cache + OS headroom; below this the machine swap-thrashes
        # and a BYOM key is the better recommendation.
        "min_ram_bytes": 8 * 1024**3,
    },
    "qwen3.5-4b-iq3_xxs": {
        "label": "Qwen 3.5 4B (IQ3_XXS, smaller download)",
        "filename": "Qwen3.5-4B-UD-IQ3_XXS.gguf",
        "url": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-UD-IQ3_XXS.gguf",
        "sha256": "00aab66a2359a9afc52f0ab757e61f9be1656e6fcaba8468bd95579a8b2cbc40",
        "size": 1_949_047_968,
        "quant": "IQ3_XXS",
        "min_ram_bytes": 6 * 1024**3,
    },
}

DEFAULT_MODEL_KEY = "qwen3.5-4b-q4_k_m"


class LocalLLMError(RuntimeError):
    """The local provider can't run: extra missing, model missing, or load failure."""


def extra_installed() -> bool:
    return importlib.util.find_spec("llama_cpp") is not None


def models_dir() -> Path:
    return Path(settings.data_dir) / "models" / "llm"


def _entry(key: str) -> dict:
    entry = MODEL_MANIFEST.get(key)
    if entry is None:
        raise LocalLLMError(f"Unknown local model '{key}'")
    return entry


def model_path(key: str | None = None) -> Path:
    return models_dir() / _entry(key or settings.local_llm_model)["filename"]


def total_ram_bytes() -> int | None:
    """Physical RAM without a psutil dependency; None when undetectable."""
    try:
        if platform.system() == "Windows":
            import ctypes

            class MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MemoryStatusEx()
            stat.dwLength = ctypes.sizeof(MemoryStatusEx)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return int(stat.ullTotalPhys)
        import os

        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return None


def preflight(key: str | None = None) -> dict:
    """Disk and RAM checks before download/enable. Never blocks, only reports."""
    entry = _entry(key or settings.local_llm_model)
    problems: list[str] = []

    models_dir().mkdir(parents=True, exist_ok=True)
    free_disk = shutil.disk_usage(models_dir()).free
    needed = entry["size"] - (
        model_path(key).stat().st_size if model_path(key).exists() else 0
    )
    if free_disk < needed + 500 * 1024**2:  # keep 500MB headroom
        problems.append(
            f"Not enough disk space: the model needs {entry['size'] / 1024**3:.1f}GB "
            f"and only {free_disk / 1024**3:.1f}GB is free."
        )

    ram = total_ram_bytes()
    if ram is not None and ram < entry["min_ram_bytes"]:
        problems.append(
            f"This machine has {ram / 1024**3:.0f}GB RAM; the local model wants "
            f"{entry['min_ram_bytes'] / 1024**3:.0f}GB to run without swapping. "
            "An API key (Settings > AI & Model) is the better option here."
        )

    return {"ok": not problems, "problems": problems}


def status() -> dict:
    """Everything Settings needs to render the local-AI card."""
    key = settings.local_llm_model
    entry = MODEL_MANIFEST.get(key, {})
    path = models_dir() / entry.get("filename", "missing.gguf")
    present = path.exists() and path.stat().st_size == entry.get("size")
    # An unfinished download lives at .part until its sha256 checks out.
    part = path.with_suffix(".part")
    partial = not present and part.exists()
    if present:
        downloaded = path.stat().st_size
    else:
        downloaded = part.stat().st_size if partial else 0
    return {
        "extra_installed": extra_installed(),
        "model_key": key,
        "model_present": present,
        "download_partial": partial,
        "downloaded_bytes": downloaded,
        "path": str(path),
        "quant": entry.get("quant"),
        "size": entry.get("size"),
        "models": [
            {"key": k, "label": e["label"], "size": e["size"], "quant": e["quant"]}
            for k, e in MODEL_MANIFEST.items()
        ],
        "preflight": preflight(key),
        # Copy-paste commands for adding the extra, per install flavor.
        "install_hint": {
            "uvx": "uvx --with 'finance-copilot[local-llm]' finance-copilot",
            "pipx": "pipx inject finance-copilot 'llama-cpp-python'",
            "pip": "pip install 'finance-copilot[local-llm]'",
        },
    }


# One cancel flag per process: only one download can run (single-job guard).
_download_cancel = threading.Event()


def request_download_cancel() -> None:
    _download_cancel.set()


def download_model(key: str | None = None, progress_cb=None) -> Path:
    """Resumable, sha256-verified model download into the data dir.

    Partial downloads resume via HTTP Range; a hash mismatch deletes the file
    and raises (a truncated or tampered model must never load). progress_cb
    receives (downloaded_bytes, total_bytes). Raises LocalLLMError on cancel.
    """
    entry = _entry(key or settings.local_llm_model)
    _download_cancel.clear()
    dest = model_path(key)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")

    start = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={start}-"} if start else {}
    mode = "ab" if start else "wb"

    with httpx.stream(
        "GET", entry["url"], headers=headers, timeout=60.0, follow_redirects=True
    ) as response:
        if start and response.status_code != 206:
            # Server ignored the range; start over.
            start = 0
            mode = "wb"
        response.raise_for_status()
        with open(tmp, mode) as fh:
            done = start
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                if _download_cancel.is_set():
                    raise LocalLLMError("Download cancelled")
                fh.write(chunk)
                done += len(chunk)
                if progress_cb:
                    progress_cb(done, entry["size"])

    digest = hashlib.sha256()
    with open(tmp, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024 * 4), b""):
            digest.update(block)
    if digest.hexdigest() != entry["sha256"]:
        tmp.unlink(missing_ok=True)
        raise LocalLLMError(
            "Downloaded model failed its integrity check (sha256 mismatch); "
            "the partial file was removed - try again."
        )
    tmp.replace(dest)
    logger.info("local model ready | %s (%.1fGB)", dest, entry["size"] / 1024**3)
    return dest


def remove_model(key: str | None = None) -> bool:
    """Delete the model file (and any partial). Unloads the in-process instance."""
    unload_model()
    removed = False
    for p in (model_path(key), model_path(key).with_suffix(".part")):
        if p.exists():
            p.unlink()
            removed = True
    return removed


# --- In-process inference -----------------------------------------------------

_llama = None
_llama_path: str | None = None
# One model instance, and generation itself is serialized: llama.cpp contexts
# are not thread-safe, and the app's single-job guard doesn't cover ad-hoc
# calls like test_connection.
_llama_lock = threading.Lock()
_unload_registered = False


def unload_model() -> None:
    """Free the in-process model now.

    Must also happen before the interpreter exits: ggml's Metal backend asserts
    (GGML_ASSERT([rsets->data count] == 0)) if a model is still resident when it
    tears down, which aborts the app on quit with a crash report on macOS.
    """
    global _llama, _llama_path
    with _llama_lock:
        if _llama is not None:
            _llama.close()
        _llama = None
        _llama_path = None


def _get_llama():
    global _llama, _llama_path, _unload_registered
    if not extra_installed():
        raise LocalLLMError(
            "The local-llm extra isn't installed. Add it with: "
            + status()["install_hint"]["uvx"]
        )
    path = model_path()
    if not path.exists():
        raise LocalLLMError(
            "The local model isn't downloaded yet - use Settings > AI & Model > Enable local AI."
        )
    if _llama is None or _llama_path != str(path):
        from llama_cpp import Llama

        # CPU-baseline everywhere; Metal kicks in automatically on Apple Silicon
        # wheels. GPU wheel matrices (CUDA/Vulkan) are deliberately out of scope.
        n_gpu_layers = -1 if (platform.system() == "Darwin" and platform.machine() == "arm64") else 0
        logger.info("loading local model %s (n_gpu_layers=%d)", path, n_gpu_layers)
        _llama = Llama(
            model_path=str(path),
            n_ctx=max(settings.ollama_num_ctx, 4096),
            n_gpu_layers=n_gpu_layers,
            seed=42,
            verbose=False,
            logits_all=True,  # required for token logprobs
        )
        _llama_path = str(path)
        if not _unload_registered:
            atexit.register(unload_model)
            _unload_registered = True
    return _llama


def chat_completion(payload: dict) -> dict:
    """OpenAI-shaped chat completion against the in-process model.

    Accepts the payload built by _build_provider_request's local branch and
    returns a dict _extract_content/_logprob_category_confidence understand.
    JSON validity is enforced by grammar (llama-cpp converts the JSON schema to
    GBNF), not hoped for - the reasoning-field regression taught us that.
    """
    llama = _get_llama()
    with _llama_lock:
        data = llama.create_chat_completion(
            messages=payload["messages"],
            temperature=0,
            seed=42,
            max_tokens=payload.get("max_tokens", 512),
            response_format=payload.get("response_format"),
            logprobs=payload.get("logprobs", False),
        )
    # Normalize logprobs to the flat [{"token","logprob"}] list the confidence
    # extractor expects (same as the Ollama shape).
    try:
        content_lps = data["choices"][0]["logprobs"]["content"]
        data["logprobs"] = [
            {"token": t["token"], "logprob": t["logprob"]} for t in content_lps
        ]
    except (KeyError, IndexError, TypeError):
        pass
    return data


def test_local_connection() -> dict:
    """test_connection()'s `local` branch: extra present, model present, loads,
    generates one token. Reports path + quantization on success."""
    if not extra_installed():
        return {
            "ok": False,
            "detail": "llama-cpp-python isn't installed. "
            + status()["install_hint"]["uvx"],
        }
    entry = MODEL_MANIFEST.get(settings.local_llm_model, {})
    path = model_path()
    if not path.exists():
        return {"ok": False, "detail": "Model not downloaded yet - click Enable local AI."}
    try:
        llama = _get_llama()
        with _llama_lock:
            llama.create_completion("Hi", max_tokens=1)
        return {
            "ok": True,
            "detail": f"Local model loaded: {entry.get('quant')} at {path}.",
        }
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
