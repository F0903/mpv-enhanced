"""Fetching the newest GitHub release and installing it as a new version."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable
from importlib.metadata import metadata
from typing import Any

from .. import VERSION
from ..archives import extract_archive
from ..downloads import Downloader, download_url, sha256_file
from ..errors import InstallerError
from .layout import Layout, version_key


def _repository() -> str:
    for item in metadata("mpv-enhanced").get_all("Project-URL") or []:
        label, _, url = item.partition(",")
        if label.strip() == "Repository":
            return url.strip().removesuffix(".git").split("github.com/", 1)[1]
    raise InstallerError("the installer has no repository URL")


def _fetch_latest_release(repository: str) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"mpv-enhanced/{VERSION}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise InstallerError("no releases have been published yet") from exc
        raise InstallerError(f"cannot check for updates: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise InstallerError(f"cannot check for updates: {exc}") from exc


def _asset_url(release: dict[str, Any], suffix: str) -> tuple[str, str]:
    for asset in release.get("assets", []):
        if asset["name"].endswith(suffix):
            return asset["name"], asset["browser_download_url"]
    raise InstallerError(f"the latest release has no {suffix} file")


def update(
    layout: Layout,
    *,
    fetch: Callable[[str], dict[str, Any]] = _fetch_latest_release,
    repository: Callable[[], str] = _repository,
    download: Downloader = download_url,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> str | None:
    """Install the newest release and return its version, or None if up to date."""
    if layout.is_checkout:
        raise InstallerError("this is a git checkout; use git pull to update it")
    active = layout.active()
    profile = layout.profile(active) if active else None
    if active is None or profile is None:
        raise InstallerError("nothing is installed yet; run mpve install first")

    release = fetch(repository())
    version = str(release["tag_name"]).removeprefix("v")
    if version_key(version) <= version_key(active):
        return None

    workspace = layout.live / f".update-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True)
    try:
        zip_name, zip_url = _asset_url(release, ".zip")
        _, sums_url = _asset_url(release, "SHA256SUMS")
        archive = workspace / zip_name
        sums = workspace / "SHA256SUMS"
        download(zip_url, archive)
        download(sums_url, sums)
        expected = next(
            (
                line.split()[0].lower()
                for line in sums.read_text("utf-8").splitlines()
                if line.split()[-1].lstrip("*") == zip_name
            ),
            None,
        )
        if expected != sha256_file(archive):
            raise InstallerError(f"{zip_name} failed its checksum")
        extract_archive(archive, workspace / "extracted", archive_type="zip")
        tops = list((workspace / "extracted").iterdir())
        if len(tops) != 1 or not (tops[0] / "pyproject.toml").is_file():
            raise InstallerError(f"{zip_name} is not an mpv-enhanced release")

        destination = layout.release(version)
        shutil.rmtree(layout.live / version, ignore_errors=True)
        destination.parent.mkdir(parents=True)
        shutil.move(str(tops[0]), destination)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    # Run the new release's own manager; it activates the version when it succeeds.
    uv = os.environ.get("UV") or shutil.which("uv")
    if uv is None:
        raise InstallerError("uv is unavailable; run mpve.cmd or mpve.sh")
    environment = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    environment["MPVE_ROOT"] = str(layout.root)
    result = run(
        [
            uv,
            "run",
            "--project",
            str(destination),
            "--locked",
            "--no-dev",
            "--no-editable",
            "mpv-enhanced",
            "install",
            "--profile",
            profile,
        ],
        cwd=destination,
        env=environment,
        check=False,
    )
    if result.returncode != 0 or layout.active() != version:
        shutil.rmtree(layout.live / version, ignore_errors=True)
        raise InstallerError(f"installing {version} failed; {active} is still active")
    return version
