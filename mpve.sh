#!/bin/sh
# Fetches the pinned uv (see uv.pin) and hands over to the active release's manager.
# On Windows shells (Git Bash, MSYS2, Cygwin) mpve.cmd does that instead.
set -eu

# mpve use symlinks this script into ~/.local/bin, so resolve the link first.
project_root=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd)
release=$project_root
if [ ! -e "$project_root/.git" ] && [ -f "$project_root/live/current" ]; then
    active=$(cat "$project_root/live/current")
    if [ -f "$project_root/live/$active/release/pyproject.toml" ]; then
        release=$project_root/live/$active/release
    fi
fi

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

pin() {
    sed -n "s/^$1=//p" "$release/uv.pin"
}

case "$(uname -s)" in
    MINGW* | MSYS*)
        # MSYS rewrites "/c" into a path; "//c" reaches cmd.exe as "/c".
        # "call" stops cmd from stripping the quotes around a path with spaces.
        exec cmd.exe //c call "$(cygpath -w "$project_root/mpve.cmd")" "$@"
        ;;
    CYGWIN*)
        exec cmd.exe /c call "$(cygpath -w "$project_root/mpve.cmd")" "$@"
        ;;
    Linux) os=unknown-linux-gnu ;;
    Darwin) os=apple-darwin ;;
    *) fail "unsupported operating system: $(uname -s)" ;;
esac

case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    arm64 | aarch64) arch=aarch64 ;;
    *) fail "unsupported CPU architecture: $(uname -m)" ;;
esac

target="$arch-$os"
uv_version=$(pin version)
uv_sha256=$(pin "$target")
[ -n "$uv_sha256" ] || fail "mpv-enhanced does not support $target yet"

uv_dir="$project_root/.bootstrap/uv-$uv_version-$target"
uv_bin="$uv_dir/uv"

if [ ! -x "$uv_bin" ]; then
    mkdir -p "$project_root/.bootstrap"
    archive="$uv_dir.tar.gz"
    url="https://github.com/astral-sh/uv/releases/download/$uv_version/uv-$target.tar.gz"
    printf 'Downloading uv %s...\n' "$uv_version" >&2
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL -o "$archive" "$url" || fail "could not download $url"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$archive" "$url" || fail "could not download $url"
    else
        fail "curl or wget is required to download uv"
    fi
    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$archive" | cut -d ' ' -f 1)
    else
        actual=$(shasum -a 256 "$archive" | cut -d ' ' -f 1)
    fi
    if [ "$actual" != "$uv_sha256" ]; then
        rm -f "$archive"
        fail "uv download has SHA-256 $actual, expected $uv_sha256"
    fi
    rm -rf "$uv_dir.tmp" "$uv_dir"
    mkdir "$uv_dir.tmp"
    # The tarball's single top-level directory is uv-<target>/.
    tar -xzf "$archive" -C "$uv_dir.tmp" --strip-components=1 || fail "could not extract uv"
    rm -f "$archive"
    mv "$uv_dir.tmp" "$uv_dir"
fi

UV_PYTHON_INSTALL_DIR="$project_root/runtime/controller-python"
UV_CACHE_DIR="$project_root/.uv-cache"
MPVE_ROOT="$project_root"
export UV_PYTHON_INSTALL_DIR UV_CACHE_DIR MPVE_ROOT
cd "$release"
exec "$uv_bin" run --project "$release" --locked --no-dev --no-editable mpv-enhanced "$@"
