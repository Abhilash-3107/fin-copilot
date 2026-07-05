"""The server entry point must default to loopback and warn on any exposed bind."""
from __future__ import annotations

import logging

import pytest

from src.__main__ import _is_loopback, warn_if_exposed


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.5"])
def test_loopback_hosts_recognized(host):
    assert _is_loopback(host) is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::", "example.com"])
def test_non_loopback_hosts_recognized(host):
    assert _is_loopback(host) is False


def test_no_warning_on_loopback(caplog):
    with caplog.at_level(logging.WARNING, logger="src"):
        warn_if_exposed("127.0.0.1")
    assert caplog.records == []


def test_warns_on_exposed_bind(caplog):
    with caplog.at_level(logging.WARNING, logger="src"):
        warn_if_exposed("0.0.0.0")
    assert any("NO authentication" in r.message for r in caplog.records)
