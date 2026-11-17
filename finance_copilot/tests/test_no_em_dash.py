"""The em dash reads as machine-written: no Python or SQL source may contain one.

The UI has the same guard (ui/src/lib/noEmDash.test.js). The forms it looks for
are built from pieces, so this file holds none of them and needs no exemption.
"""
from __future__ import annotations

from pathlib import Path

EM_DASH = chr(0x2014)
FORMS = (EM_DASH, "\\" + "u2014", "&md" + "ash;", "&#" + "8212;")

ROOT = Path(__file__).resolve().parents[2]

# LLM prompt text is sent to a model, and changing a character there changes
# what the model reads and the eval results. List every such file here.
PROMPT_FILES = {"finance_copilot/pipeline/llm.py"}


def _sources() -> list[Path]:
    folders = [ROOT / "finance_copilot", ROOT / "scripts"]
    return sorted(p for folder in folders for suffix in ("*.py", "*.sql") for p in folder.rglob(suffix))


def test_finds_the_sources_it_is_meant_to_check() -> None:
    sources = _sources()
    assert len([p for p in sources if p.suffix == ".py"]) > 40
    assert len([p for p in sources if p.suffix == ".sql"]) > 20  # the migrations
    assert (ROOT / "finance_copilot/pipeline/llm.py").exists()


def test_sources_have_no_em_dash() -> None:
    offenders = []
    for path in _sources():
        rel = path.relative_to(ROOT).as_posix()
        if rel in PROMPT_FILES:
            continue
        text = path.read_text(encoding="utf-8")
        if any(form in text for form in FORMS):
            offenders.append(rel)
    assert offenders == []


def test_prompt_files_only_use_it_inside_prompt_text() -> None:
    # A prompt file may keep an em dash in a string sent to the model, never in
    # a comment or docstring.
    for rel in PROMPT_FILES:
        for number, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            if EM_DASH not in line:
                continue
            stripped = line.strip()
            assert stripped.startswith(('"', "'")), f"{rel}:{number} has an em dash outside prompt text"
