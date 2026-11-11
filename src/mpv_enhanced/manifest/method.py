"""An interpolation method the installer can add, and the choices it gives the player."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ..errors import ManifestError
from .fields import _integer, _platforms, _string, _string_list, _table


@dataclass(frozen=True, slots=True)
class MethodChoice:
    """One entry in the player's method list."""

    id: str
    title: str
    rank: int
    settings: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Method:
    """An interpolation method the installer can add, with any inheritance applied."""

    name: str
    description: str
    components: tuple[str, ...]
    extras: tuple[str, ...]
    platforms: tuple[str, ...]
    choices: tuple[MethodChoice, ...]
    preselect: bool = True


_CHOICE_ID = re.compile(r"[a-z0-9][a-z0-9.-]*\Z")


def _with_inherited_method(
    name: str,
    methods: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Apply a method's one-level ``extends``: a child adds to its parent's lists."""
    table = _table(methods[name], f"methods.{name}")
    parent = table.get("extends")
    if parent is None:
        return table
    if parent not in methods:
        raise ManifestError(f"method {name!r} extends unknown method {parent!r}")
    inherited = _table(methods[parent], f"methods.{parent}")
    merged = {"os": inherited.get("os"), **table}
    for key in ("components", "extras", "choices"):
        merged[key] = [*inherited.get(key, []), *table.get(key, [])]
    return merged


def _parse_method(name: str, data: Mapping[str, Any]) -> Method:
    label = f"methods.{name}"
    preselect = data.get("preselect", True)
    if not isinstance(preselect, bool):
        raise ManifestError(f"{label}.preselect must be true or false")
    raw_choices = data.get("choices")
    if not isinstance(raw_choices, list) or not raw_choices:
        raise ManifestError(f"{label}.choices must list at least one choice")
    return Method(
        name=name,
        description=_string(data.get("description"), f"{label}.description"),
        components=_string_list(data.get("components"), f"{label}.components"),
        extras=_string_list(data.get("extras"), f"{label}.extras"),
        platforms=_platforms(data, label),
        choices=tuple(
            _parse_choice(item, f"{label}.choices[{index}]")
            for index, item in enumerate(raw_choices)
        ),
        preselect=preselect,
    )


def _parse_choice(value: object, label: str) -> MethodChoice:
    data = _table(value, label)
    identifier = _string(data.get("id"), f"{label}.id")
    # The id is passed through mpv's filter options, and "none" is reserved.
    if not _CHOICE_ID.fullmatch(identifier) or identifier == "none":
        raise ManifestError(f"{label}.id is not a usable choice id: {identifier!r}")
    return MethodChoice(
        id=identifier,
        title=_string(data.get("title"), f"{label}.title"),
        rank=_integer(data.get("rank"), f"{label}.rank"),
        settings=MappingProxyType(
            dict(_table(data.get("settings"), f"{label}.settings"))
        ),
    )
