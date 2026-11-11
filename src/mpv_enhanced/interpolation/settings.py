"""The MPV Enhanced menu's settings file, as the installer sees it."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

from ..parsed import as_list, as_table

SETTINGS = "portable_config/script-opts/mpv_enhanced.json"
NO_METHOD = "none"


def load_settings(app: Path) -> dict[str, object] | None:
    """The saved settings, or None if the file is missing or malformed."""
    try:
        text = (app / SETTINGS).read_text(encoding="utf-8")
        return as_table(cast(object, json.loads(text)))
    except (OSError, ValueError):
        return None


def save_settings(app: Path, data: Mapping[str, object]) -> None:
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


def set_targets(app: Path, targets: Sequence[Mapping[str, object]]) -> None:
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
    for item in as_list(data.get("targets")) or []:
        target = as_table(item)
        if target is not None and target.get("method") not in valid:
            target["method"] = installed_ids[0]
            changed = True
    if changed:
        save_settings(app, data)
