"""Shared paths, the in-process CLI helper, and a fake web bundle for every test."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES = REPO_ROOT / "sample_files"
SINGLE = SAMPLES / "single_period"
TIMESERIES = SAMPLES / "timeseries"
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "config.example.yaml"


def invoke(*args: str):
    """Run the imi-dashboard command in-process (typer's CliRunner), which
    keeps stdout and stderr apart and avoids an interpreter start-up per call."""
    from typer.testing import CliRunner

    from imi_dashboard.cli import app

    return CliRunner().invoke(app, list(args))


def write_fake_web_app(folder: Path, contents: dict[str, str] | None = None, **marker) -> Path:
    """Write a fake built web app into ``folder``: the given files (by
    default an index.html and assets/index.js) and the marker the web build
    writes, listing them; keyword arguments override marker keys."""
    from imi_dashboard import pack

    files = contents if contents is not None else {"index.html": "<!doctype html>", "assets/index.js": "export {}"}
    for name, text in files.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text, encoding="utf-8")
    content = {"app": pack.APP_NAME, "manifestVersion": pack.MANIFEST_VERSION, "files": sorted(files), **marker}
    (folder / pack.APP_MARKER).write_text(json.dumps(content), encoding="utf-8")
    return folder


@pytest.fixture(scope="session", autouse=True)
def fake_web_bundle(tmp_path_factory) -> Iterator[Path]:
    """Point the converter's bundle lookup at a tiny fake web/dist/ (an
    index.html, an assets/ folder and the web build's marker, with no web
    source beside it, so it is never stale), and at no packaged copy.

    Without this, conversions would copy the clone's real web/dist/ when it
    has been built (locally) and fail for want of a web app when it has not
    (on CI). Session-scoped so that module-scoped conversion fixtures see it too;
    tests of the bundle lookup itself override both paths with monkeypatch.
    """
    from imi_dashboard import pack

    root = tmp_path_factory.mktemp("fake_clone")
    dist = root / "web" / "dist"
    (dist / "assets").mkdir(parents=True)
    write_fake_web_app(dist)
    patch = pytest.MonkeyPatch()
    patch.setattr(pack, "REPO_BUNDLE_DIR", dist)
    patch.setattr(pack, "PACKAGE_BUNDLE_DIR", root / "no_package_bundle")
    yield dist
    patch.undo()
