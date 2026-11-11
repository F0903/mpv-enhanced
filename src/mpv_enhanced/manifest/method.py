"""An interpolation method the installer can add, and the choices it gives the player."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..errors import ManifestError
from ..parsed import as_list
from .fields import (
    read_integer,
    read_platforms,
    read_string,
    read_string_list,
    read_table,
)


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


def with_inherited_method(
    name: str,
    methods: Mapping[str, object],
) -> Mapping[str, object]:
    """Apply a method's one-level ``extends``: a child adds to its parent's lists."""
    table = read_table(methods[name], f"methods.{name}")
    parent = table.get("extends")
    if parent is None:
        return table
    if not isinstance(parent, str) or parent not in methods:
        raise ManifestError(f"method {name!r} extends unknown method {parent!r}")
    inherited = read_table(methods[parent], f"methods.{parent}")
    merged = {"os": inherited.get("os"), **table}
    for key in ("components", "extras", "choices"):
        merged[key] = [
            *_listed(inherited.get(key), f"methods.{parent}.{key}"),
            *_listed(table.get(key), f"methods.{name}.{key}"),
        ]
    return merged


def _listed(value: object, label: str) -> list[object]:
    """An array as written, a lone string as its only item, and nothing as empty."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    items = as_list(value)
    if items is None:
        raise ManifestError(f"{label} must be an array")
    return items


def parse_method(name: str, data: Mapping[str, object]) -> Method:
    label = f"methods.{name}"
    preselect = data.get("preselect", True)
    if not isinstance(preselect, bool):
        raise ManifestError(f"{label}.preselect must be true or false")
    raw_choices = as_list(data.get("choices"))
    if not raw_choices:
        raise ManifestError(f"{label}.choices must list at least one choice")
    return Method(
        name=name,
        description=read_string(data.get("description"), f"{label}.description"),
        components=read_string_list(data.get("components"), f"{label}.components"),
        extras=read_string_list(data.get("extras"), f"{label}.extras"),
        platforms=read_platforms(data, label),
        choices=tuple(
            _parse_choice(item, f"{label}.choices[{index}]")
            for index, item in enumerate(raw_choices)
        ),
        preselect=preselect,
    )


def _parse_choice(value: object, label: str) -> MethodChoice:
    data = read_table(value, label)
    identifier = read_string(data.get("id"), f"{label}.id")
    # The id is passed through mpv's filter options, and "none" is reserved.
    if not _CHOICE_ID.fullmatch(identifier) or identifier == "none":
        raise ManifestError(f"{label}.id is not a usable choice id: {identifier!r}")
    return MethodChoice(
        id=identifier,
        title=read_string(data.get("title"), f"{label}.title"),
        rank=read_integer(data.get("rank"), f"{label}.rank"),
        settings=MappingProxyType(
            dict(read_table(data.get("settings"), f"{label}.settings"))
        ),
    )
