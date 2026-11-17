"""Words the server says to a person.

HTTP error details, job errors and health messages reach the screen, so they
follow the same vocabulary as the interface: the shared list in
ui/src/lib/bannedWords.json (read here and by ui/src/lib/vocabulary.test.js).
This walks every Python source in finance_copilot/ and reads the string
literals that become those messages.
"""
from __future__ import annotations

import ast
import json
import re

import pytest
from source_tree import NOT_A_CHECKOUT, source_root

ROOT = source_root()
if ROOT is None:
    pytest.skip(NOT_A_CHECKOUT, allow_module_level=True)

PACKAGE = ROOT / "finance_copilot"
WORDS = json.loads((ROOT / "ui/src/lib/bannedWords.json").read_text(encoding="utf-8"))

BANNED = [(re.compile(w["pattern"], re.I if "i" in w["flags"] else 0), w["use"]) for w in WORDS["banned"]]

# Keys and names whose string value is shown to a person.
SHOWN_KEYS = {"detail", "message", "error", "ai_message"}
SHOWN_NAME = re.compile(r"(^|_)(error|message|detail|reason)s?$|^GAVE_UP|^BUSY", re.I)
# A job's error written straight into its SQL: error='...' or ELSE '...' END.
SQL_ERROR = re.compile(r"\berror\s*=\s*'([^']+)'|\bELSE\s+'([^']+)'\s+END", re.I)


def _strings(node: ast.AST) -> list[str]:
    """Every literal piece of a string expression: a constant, the constant
    parts of an f-string, either side of a + or a parenthesised concatenation."""
    return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def _target_names(node: ast.AST) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [t.id for t in targets if isinstance(t, ast.Name)]


def server_messages(source: str) -> list[tuple[int, str]]:
    """(line, text) of the strings in a source that a person can read."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name == "HTTPException":
                for kw in node.keywords:
                    if kw.arg == "detail":
                        found += [(kw.value.lineno, s) for s in _strings(kw.value)]
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in SHOWN_KEYS:
                    found += [(value.lineno, s) for s in _strings(value)]
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
            if any(SHOWN_NAME.search(n) for n in _target_names(node)):
                found += [(node.value.lineno, s) for s in _strings(node.value)]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for match in SQL_ERROR.finditer(node.value):
                found.append((node.lineno, match.group(1) or match.group(2)))
    return found


def problems(messages: list[tuple[int, str]]) -> list[str]:
    out = []
    for line, text in messages:
        if text.strip() in WORDS["wholeStringsAllowed"]:
            continue
        for pattern, use in BANNED:
            hit = pattern.search(text)
            if hit:
                out.append(f"line {line}: \"{hit.group(0)}\": use {use} in \"{text[:80]}\"")
    return out


def test_reads_the_messages_it_is_meant_to_check() -> None:
    source = '''
from fastapi import HTTPException
GAVE_UP = "Stopped: the worker died."
def f(x):
    ai_message = ("Sorting is using your rules. " "Add a model.")
    conn.execute("UPDATE jobs SET error='Went stale', t=1")
    conn.execute("... ELSE 'Interrupted' END")
    raise HTTPException(status_code=404, detail=f"Annotation {x} not found")
    return {"ok": False, "detail": "Exception: boom"}
'''
    texts = [t for _, t in server_messages(source)]
    for expected in ("Stopped: the worker died.", "Add a model.", "Went stale", "Interrupted", "Annotation ", "Exception: boom"):
        assert any(expected in t for t in texts), expected
    assert problems(server_messages(source)), "the banned words in that sample are not flagged"


def test_finds_the_sources_it_is_meant_to_check() -> None:
    sources = [p for p in PACKAGE.rglob("*.py") if "tests" not in p.parts]
    assert len(sources) > 30
    all_messages = [m for p in sources for m in server_messages(p.read_text(encoding="utf-8"))]
    assert len(all_messages) > 60


def test_server_messages_use_the_interface_vocabulary() -> None:
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "tests" in path.parts:
            continue
        rel = path.relative_to(ROOT).as_posix()
        offenders += [f"{rel} {p}" for p in problems(server_messages(path.read_text(encoding="utf-8")))]
    assert offenders == []


class TestMessagesThatReachThePerson:
    def test_a_model_that_cannot_be_reached_is_explained_without_exception_names(self):
        from unittest.mock import patch

        import httpx

        from finance_copilot.config import settings
        from finance_copilot.pipeline.llm import test_connection

        with patch.object(settings, "llm_provider", "ollama"), patch.object(
            settings, "ollama_url", "http://localhost:11434"
        ), patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            result = test_connection()
        assert result == {
            "ok": False,
            "detail": "Couldn't reach the model at http://localhost:11434: nothing is listening there.",
        }

    def test_a_vanished_annotation_says_the_category_changed_elsewhere(self, tmp_path):
        from fastapi.testclient import TestClient

        from finance_copilot.api.deps import get_db as api_get_db
        from finance_copilot.db.connection import get_connection, init_db
        from finance_copilot.main import app

        conn = get_connection(str(tmp_path / "t.db"))
        init_db(conn)
        app.dependency_overrides[api_get_db] = lambda: conn
        try:
            r = TestClient(app).patch("/api/annotations/nope", json={"category": "Food"})
        finally:
            app.dependency_overrides.pop(api_get_db, None)
            conn.close()
        assert r.status_code == 404
        assert r.json()["detail"] == "That payment's category changed elsewhere. Reload and try again."
