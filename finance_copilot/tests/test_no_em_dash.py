"""The em dash reads as machine-written: no Python source may contain one.

The UI has the same guard (ui/src/lib/noEmDash.test.js). Written with escapes
so this file holds no em dash itself.
"""
from __future__ import annotations

from pathlib import Path

EM_DASH = "—"
FORMS = (EM_DASH, "\\u2014", "&mdash;", "&#8212;")

ROOT = Path(__file__).resolve().parents[2]

# LLM prompt text is sent to a model, and changing a character there changes
# what the model reads and the eval results. List every such file here.
PROMPT_FILES = {"finance_copilot/pipeline/llm.py"}
# This file spells the forms it looks for.
THIS_FILE = "finance_copilot/tests/test_no_em_dash.py"


def _python_sources() -> list[Path]:
    folders = [ROOT / "finance_copilot", ROOT / "scripts"]
    return sorted(p for folder in folders for p in folder.rglob("*.py"))


def test_finds_the_sources_it_is_meant_to_check() -> None:
    assert len(_python_sources()) > 40
    assert (ROOT / "finance_copilot/pipeline/llm.py").exists()


def test_python_sources_have_no_em_dash() -> None:
    offenders = []
    for path in _python_sources():
        rel = path.relative_to(ROOT).as_posix()
        if rel in PROMPT_FILES or rel == THIS_FILE:
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
