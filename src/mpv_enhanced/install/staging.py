"""Swapping a staged installation into place, and swapping it back."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from ..errors import PromotionError


def promote_staging(staging: Path, target: Path) -> Path | None:
    """Promote *staging* and retain exactly one sibling ``.previous`` tree."""
    if not staging.is_dir():
        raise PromotionError(f"staging directory does not exist: {staging}")
    if staging.parent.resolve() != target.parent.resolve():
        raise PromotionError("staging and installation directories must be siblings")
    previous = target.with_name(f"{target.name}.previous")
    stale: Path | None = None
    target_moved = False
    try:
        if target.exists():
            if previous.exists():
                stale = previous.with_name(f".{previous.name}.stale-{uuid.uuid4().hex}")
                previous.rename(stale)
            target.rename(previous)
            target_moved = True
        staging.rename(target)
    except OSError as exc:
        try:
            if target_moved and previous.exists() and not target.exists():
                previous.rename(target)
            if stale is not None and stale.exists() and not previous.exists():
                stale.rename(previous)
        except OSError as recovery_exc:
            raise PromotionError(
                f"promotion failed ({exc}) and recovery failed ({recovery_exc})"
            ) from recovery_exc
        raise PromotionError(f"could not promote staged installation: {exc}") from exc
    if stale is not None and stale.exists():
        # Promotion is already complete. A locked file in an older backup must
        # not turn a successful install into a reported failure.
        shutil.rmtree(stale, ignore_errors=True)
    return previous if target_moved else None


def rollback_installation(target: Path) -> None:
    """Atomically swap the current and previous installation trees."""
    previous = target.with_name(f"{target.name}.previous")
    if not previous.is_dir():
        raise PromotionError(f"no previous installation exists at {previous}")
    if not target.exists():
        try:
            previous.rename(target)
            return
        except OSError as exc:
            raise PromotionError(
                f"could not restore previous installation: {exc}"
            ) from exc
    temporary = target.with_name(f".{target.name}.rollback-{uuid.uuid4().hex}")
    try:
        target.rename(temporary)
    except OSError as exc:
        raise PromotionError(f"could not move the current installation: {exc}") from exc
    try:
        previous.rename(target)
    except OSError as exc:
        try:
            temporary.rename(target)
        except OSError as recovery_exc:
            raise PromotionError(
                f"rollback failed ({exc}) and recovery failed ({recovery_exc})"
            ) from recovery_exc
        raise PromotionError(f"could not restore previous installation: {exc}") from exc
    try:
        temporary.rename(previous)
    except OSError as exc:
        try:
            target.rename(previous)
            temporary.rename(target)
        except OSError as recovery_exc:
            raise PromotionError(
                f"rollback swap failed ({exc}) and recovery failed ({recovery_exc})"
            ) from recovery_exc
        raise PromotionError(
            f"could not retain the replaced installation: {exc}"
        ) from exc
