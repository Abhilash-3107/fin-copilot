"""Hatch build hook: bundle the built SPA (ui/dist) into real wheels only.

A static force-include would also apply to the editable install that
`uv sync` performs, so a fresh clone (ui/dist is a gitignored build output)
could not even install dev dependencies. Editable installs don't need the
bundle: main.py serves the repo's ui/dist in a checkout. A real wheel without
the UI would be a broken app, so that case fails loudly instead.
"""
from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class BundleUIHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version: str, build_data: dict) -> None:
        if version == "editable":
            return
        ui_dist = Path(self.root) / "ui" / "dist"
        if not (ui_dist / "index.html").is_file():
            raise RuntimeError(
                f"{ui_dist} is missing or has no index.html. "
                "Build the UI first: cd ui && npm ci && npm run build"
            )
        build_data["force_include"][str(ui_dist)] = "finance_copilot/ui_dist"
