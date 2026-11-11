"""A named set of components, and what it resolves to on one platform."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .component import Component
from .fields import _platforms, _string, _string_list
from .method import MethodChoice


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    description: str
    components: tuple[str, ...]
    extends: tuple[str, ...]
    platforms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedProfile:
    name: str
    description: str
    components: tuple[Component, ...]
    extras: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    choices: tuple[MethodChoice, ...] = ()


def _parse_profile(name: str, data: Mapping[str, Any]) -> Profile:
    return Profile(
        name=name,
        description=_string(
            data.get("description"), f"profiles.{name}.description", default=name
        ),
        components=_string_list(data.get("components"), f"profiles.{name}.components"),
        extends=_string_list(data.get("extends"), f"profiles.{name}.extends"),
        platforms=_platforms(data, f"profiles.{name}"),
    )
