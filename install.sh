#!/bin/sh
set -eu

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -x "$project_root/uv" ]; then
    uv_bin="$project_root/uv"
elif command -v uv >/dev/null 2>&1; then
    uv_bin=uv
else
    printf '%s\n' \
        'uv was not found beside this launcher or on PATH.' \
        'Download the bootstrap archive from the mpv-enhanced release page,' \
        'or install uv from https://docs.astral.sh/uv/.' >&2
    exit 1
fi

export MPV_ENHANCED_UV="$uv_bin"
export UV_PYTHON_INSTALL_DIR="$project_root/runtime/controller-python"
export UV_CACHE_DIR="$project_root/.uv-cache"
cd "$project_root"
exec "$uv_bin" run --project "$project_root" --locked --no-dev --no-editable mpv-enhanced "$@"
