"""Runs the menu's Lua modules from Python tests."""

from pathlib import Path
from typing import Any

from lupa.luajit21 import LuaRuntime

SCRIPT_DIR = (
    Path(__file__).resolve().parents[1] / "portable_config" / "scripts" / "mpv_enhanced"
)
# lupa's stubs leave out this option and what a Lua table holds at run time.
LUA: Any = LuaRuntime(unpack_returned_tuples=True)  # pyright: ignore[reportCallIssue]
LUA.globals().package.path = f"{SCRIPT_DIR.as_posix()}/?.lua"


def load(name: str) -> Any:
    return LUA.require(name)


def to_lua(value: Any) -> Any:
    if isinstance(value, dict):
        return LUA.table_from({key: to_lua(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return LUA.table_from([to_lua(item) for item in value])
    return value


def to_py(value: Any) -> Any:
    if not hasattr(value, "items"):
        return value
    items = {key: to_py(item) for key, item in value.items()}
    if list(items) == list(range(1, len(items) + 1)):
        return list(items.values())
    return items or []
