#!/bin/sh
# mpv-enhanced bootstrap for Linux and macOS.
# On Windows shells (Git Bash, MSYS2, Cygwin) it hands over to install.cmd.
set -eu

# Pinned uv that runs the installer. Keep the version in sync with install.cmd.
uv_version=0.12.5

project_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

case "$(uname -s)" in
    MINGW* | MSYS*)
        # MSYS rewrites "/c" into a path; "//c" reaches cmd.exe as "/c".
        # "call" stops cmd from stripping the quotes around a path with spaces.
        exec cmd.exe //c call "$(cygpath -w "$project_root/install.cmd")" "$@"
        ;;
    CYGWIN*)
        exec cmd.exe /c call "$(cygpath -w "$project_root/install.cmd")" "$@"
        ;;
    Linux)
        if grep -qi microsoft /proc/sys/kernel/osrelease 2>/dev/null; then
            printf '%s\n' \
                'note: this is WSL, so the Linux version will be installed inside WSL.' \
                '      To install for Windows, run install.cmd from Windows instead.' >&2
        fi
        os=unknown-linux-gnu
        ;;
    Darwin) os=apple-darwin ;;
    *) fail "unsupported operating system: $(uname -s)" ;;
esac

case "$(uname -m)" in
    x86_64 | amd64) arch=x86_64 ;;
    arm64 | aarch64) arch=aarch64 ;;
    *) fail "unsupported CPU architecture: $(uname -m)" ;;
esac

target="$arch-$os"
case "$target" in
    x86_64-unknown-linux-gnu) uv_sha256=68a509da24b06b4223a1c0175fb5eb5bc79342b76cbeff0cfe51ac3f5b17b6b2 ;;
    aarch64-apple-darwin) uv_sha256=5bb0e5fe008a773c3dbcb97ff79cd89e1241464fe9d2f986d52ad8f1b037bd62 ;;
    x86_64-apple-darwin) uv_sha256=b3b2137477cf96c9686ebfb71524614cec780c673fd73e59bce099aef02e70e8 ;;
    *) fail "mpv-enhanced does not support $target yet" ;;
esac

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

MPV_ENHANCED_UV="$uv_bin"
UV_PYTHON_INSTALL_DIR="$project_root/runtime/controller-python"
UV_CACHE_DIR="$project_root/.uv-cache"
export MPV_ENHANCED_UV UV_PYTHON_INSTALL_DIR UV_CACHE_DIR
cd "$project_root"
exec "$uv_bin" run --project "$project_root" --locked --no-dev --no-editable mpv-enhanced "$@"
