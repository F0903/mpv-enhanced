"""Loading ``manifest.toml`` and resolving what a profile installs."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..errors import ManifestError, UnsupportedPlatformError
from ..platforms import PlatformInfo
from .component import Component, parse_component, with_inherited_fields
from .fields import read_table
from .interpolation_options import InterpolationOptions, parse_options
from .method import Method, MethodChoice, parse_method, with_inherited_method
from .profile import Profile, ResolvedProfile, parse_profile


@dataclass(frozen=True, slots=True)
class Manifest:
    """Validated contents of ``manifest.toml``."""

    components: Mapping[str, Component]
    profiles: Mapping[str, Profile]
    default_profile: str | None = None
    methods: Mapping[str, Method] = field(default_factory=dict)
    interpolation: InterpolationOptions | None = None

    @classmethod
    def load(cls, path: str | Path) -> Manifest:
        path = Path(path)
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
            python_version = (path.parent / ".python-version").read_text("utf-8")
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ManifestError(f"cannot read {path}: {exc}") from exc
        return cls.from_data(data, python_version=python_version.strip())

    @classmethod
    def from_data(
        cls,
        data: Mapping[str, Any],
        *,
        python_version: str | None = None,
    ) -> Manifest:
        _validate_schema_version(data)
        raw_components = read_table(data.get("components"), "components")
        raw_profiles = read_table(data.get("profiles"), "profiles")
        components = {
            name: parse_component(
                name, with_inherited_fields(name, raw_components), python_version
            )
            for name in raw_components
        }
        profiles = {
            name: parse_profile(name, read_table(table, f"profiles.{name}"))
            for name, table in raw_profiles.items()
        }
        if not components:
            raise ManifestError("the manifest contains no components")
        if not profiles:
            raise ManifestError("the manifest contains no profiles")
        default_profile = data.get("default_profile")
        if default_profile is not None and default_profile not in profiles:
            raise ManifestError(f"unknown default profile {default_profile!r}")
        raw_methods = read_table(data.get("methods", {}), "methods")
        methods = {
            name: parse_method(name, with_inherited_method(name, raw_methods))
            for name in raw_methods
        }
        raw_options = data.get("interpolation")
        manifest = cls(
            components=MappingProxyType(components),
            profiles=MappingProxyType(profiles),
            default_profile=default_profile,
            methods=MappingProxyType(methods),
            interpolation=parse_options(raw_options) if raw_options else None,
        )
        # Fail before touching an installation if any profile or method has an
        # unknown parent/component or an inheritance cycle.
        for name in profiles:
            manifest.resolve_profile(name)
        for name in methods:
            manifest.resolve_profile(next(iter(profiles)), methods=(name,))
        return manifest

    def available_methods(self, platform: PlatformInfo) -> tuple[Method, ...]:
        return tuple(
            item
            for item in self.methods.values()
            if not item.platforms or platform.key in item.platforms
        )

    def method_choices(self, names: Sequence[str]) -> tuple[MethodChoice, ...]:
        """The choices of these methods, best first, without repeats."""
        unique = {
            choice.id: choice for name in names for choice in self.methods[name].choices
        }
        return tuple(sorted(unique.values(), key=lambda choice: choice.rank))

    def optional_components(self, platform: PlatformInfo) -> tuple[Component, ...]:
        return tuple(
            item
            for item in self.components.values()
            if item.optional and platform.key in item.platforms
        )

    def resolve_profile(
        self,
        name: str,
        platform: PlatformInfo | None = None,
        *,
        methods: tuple[str, ...] = (),
        optional: tuple[str, ...] = (),
    ) -> ResolvedProfile:
        if name not in self.profiles:
            choices = ", ".join(sorted(self.profiles))
            raise ManifestError(
                f"unknown profile {name!r}; available profiles: {choices}"
            )
        ordered_names: list[str] = []
        extras: list[str] = []
        visited: set[str] = set()

        def visit(profile_name: str, stack: tuple[str, ...]) -> None:
            if profile_name in stack:
                cycle = " -> ".join((*stack, profile_name))
                raise ManifestError(f"profile inheritance cycle: {cycle}")
            if profile_name in visited:
                return
            try:
                profile = self.profiles[profile_name]
            except KeyError as exc:
                raise ManifestError(f"unknown parent profile {profile_name!r}") from exc
            for parent in profile.extends:
                visit(parent, (*stack, profile_name))
            for component_name in profile.components:
                if component_name not in self.components:
                    raise ManifestError(
                        f"profile {profile_name!r} references unknown component "
                        f"{component_name!r}"
                    )
                if component_name not in ordered_names:
                    ordered_names.append(component_name)
            visited.add(profile_name)

        visit(name, ())
        for method_name in methods:
            method = self.methods.get(method_name)
            if method is None:
                choices = ", ".join(self.methods) or "none"
                raise ManifestError(
                    f"unknown interpolation method {method_name!r}; "
                    f"available: {choices}"
                )
            if (
                platform is not None
                and method.platforms
                and platform.key not in method.platforms
            ):
                raise UnsupportedPlatformError(
                    f"interpolation method {method_name!r} does not support "
                    f"{platform.key}"
                )
            for component_name in method.components:
                if component_name not in self.components:
                    raise ManifestError(
                        f"interpolation method {method_name!r} references unknown "
                        f"component {component_name!r}"
                    )
                if component_name not in ordered_names:
                    ordered_names.append(component_name)
            extras.extend(item for item in method.extras if item not in extras)
        for item in optional:
            component = self.components.get(item)
            if component is None or not component.optional:
                choices = ", ".join(
                    sorted(c.name for c in self.components.values() if c.optional)
                )
                raise ManifestError(
                    f"unknown optional component {item!r}; available: {choices or 'none'}"
                )
            if platform is not None and platform.key not in component.platforms:
                raise UnsupportedPlatformError(
                    f"optional component {item!r} does not support {platform.key}"
                )
            if item not in ordered_names:
                ordered_names.append(item)
        profile = self.profiles[name]
        components = tuple(
            replace(item, runtime=replace(item.runtime, extras=tuple(extras)))
            if item.runtime
            else item
            for item in (self.components[item] for item in ordered_names)
        )
        if platform is not None:
            platforms = self._profile_platforms(name)
            if platforms and platform.key not in platforms:
                raise UnsupportedPlatformError(
                    f"profile {name!r} does not support {platform.key}"
                )
            components = tuple(item for item in components if item.supports(platform))
            for component in components:
                if component.platforms and platform.key not in component.platforms:
                    raise UnsupportedPlatformError(
                        f"component {component.name!r} does not support {platform.key}"
                    )
        return ResolvedProfile(
            name=name,
            description=profile.description,
            components=components,
            extras=tuple(extras),
            methods=methods,
            choices=self.method_choices(methods),
        )

    def _profile_platforms(self, name: str) -> tuple[str, ...]:
        """A profile's own ``os`` table, or else the first one it inherits."""
        profile = self.profiles[name]
        return profile.platforms or next(
            filter(None, (self._profile_platforms(p) for p in profile.extends)), ()
        )


def _validate_schema_version(data: Mapping[str, Any]) -> None:
    value = data.get("schema_version")
    if not isinstance(value, int) or isinstance(value, bool) or value != 1:
        raise ManifestError("manifest schema_version must be 1")
