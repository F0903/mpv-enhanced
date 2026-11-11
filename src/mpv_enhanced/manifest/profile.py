"""A named set of components, and what it resolves to on one platform."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .component import Component
from .fields import read_platforms, read_string, read_string_list
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


def parse_profile(name: str, data: Mapping[str, Any]) -> Profile:
    return Profile(
        name=name,
        description=read_string(
            data.get("description"), f"profiles.{name}.description", default=name
        ),
        components=read_string_list(
            data.get("components"), f"profiles.{name}.components"
        ),
        extends=read_string_list(data.get("extends"), f"profiles.{name}.extends"),
        platforms=read_platforms(data, f"profiles.{name}"),
    )
