"""The MPV Enhanced menu's settings file, as the installer sees it."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

SETTINGS = "portable_config/script-opts/mpv_enhanced.json"
NO_METHOD = "none"


def load_settings(app: Path) -> dict[str, Any] | None:
    """The saved settings, or None if the file is missing or malformed."""
    try:
        data = json.loads((app / SETTINGS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_settings(app: Path, data: dict[str, Any]) -> None:
    path = app / SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def set_enabled(app: Path, enabled: bool) -> None:
    save_settings(app, {**(load_settings(app) or {}), "enabled": enabled})


def set_targets(app: Path, targets: list[dict[str, Any]]) -> None:
    save_settings(app, {**(load_settings(app) or {}), "targets": targets})


def reconcile_targets(app: Path, installed_ids: Sequence[str]) -> None:
    """Move settings off methods that are no longer installed.

    A malformed file is left for the user: the player warns about it and it may
    hold edits they'd want back.
    """
    data = load_settings(app)
    if data is None or not installed_ids:
        return
    valid = {*installed_ids, NO_METHOD}
    changed = False
    if "default_method" in data and data["default_method"] not in valid:
        data["default_method"] = installed_ids[0]
        changed = True
    targets = data.get("targets")
    if isinstance(targets, list):
        for target in targets:
            if isinstance(target, dict) and target.get("method") not in valid:
                target["method"] = installed_ids[0]
                changed = True
    if changed:
        save_settings(app, data)
