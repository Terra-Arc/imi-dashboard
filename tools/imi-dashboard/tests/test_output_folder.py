"""What convert does to its output folder: which files it removes and
keeps, when it asks first, how it recognises a dashboard, how it finds and
copies the app bundle, and how it treats damaged input files."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from imi_dashboard import cli, pack
from imi_dashboard.config import Config, load_config
from imi_dashboard.pack import convert

from .conftest import EXAMPLE_CONFIG, REPO_ROOT, SINGLE, TIMESERIES, invoke, write_fake_web_app

# Entries a user might keep in a dashboard folder published on GitHub Pages.
FOREIGN = {".git/config": "[core]", "CNAME": "ch4.example.org", "my_notes.md": "keep me"}


def _manifest(out: Path) -> dict:
    return json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))


def _copy_run(tmp_path: Path, src: Path = TIMESERIES, name: str = "input") -> Path:
    run = tmp_path / name
    shutil.copytree(src, run)
    return run


def _add_foreign(out: Path) -> None:
    for name, text in FOREIGN.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_text(text, encoding="utf-8")


def _assert_foreign_kept(out: Path) -> None:
    for name, text in FOREIGN.items():
        assert (out / name).read_text(encoding="utf-8") == text, name


# ── what a full conversion removes and keeps ─────────────────────────────


@pytest.mark.parametrize("trigger", ["--rebuild", "region change"])
def test_full_rebuild_keeps_files_it_did_not_write(tmp_path: Path, trigger: str):
    out = tmp_path / "pages"
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg)
    _add_foreign(out)
    (out / "data" / "periods" / "0007.bin").write_bytes(b"stale")
    (out / "assets" / "index-OLD.js").write_text("old build", encoding="utf-8")

    if trigger == "--rebuild":
        summary = convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg, force=True)
    else:
        summary = convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg, region="domain")
    assert "reused" not in summary   # the full path

    _assert_foreign_kept(out)
    # Its own files are rewritten: no leftover period file or old web build.
    assert sorted(p.name for p in (out / "data" / "periods").iterdir()) == ["0000.bin", "0001.bin", "0002.bin"]
    assert sorted(p.name for p in (out / "assets").iterdir()) == ["index.js"]
    assert not (out / "data" / "manifest.json.tmp").exists()


def test_current_folder_as_output(tmp_path: Path, monkeypatch):
    """`cd dashboard && imi-dashboard convert ../run . --rebuild` used to empty
    the folder and then fail."""
    out = tmp_path / "dash"
    assert invoke("convert", str(TIMESERIES), str(out)).exit_code == 0
    _add_foreign(out)
    monkeypatch.chdir(out)
    r = invoke("convert", str(TIMESERIES), ".", "--rebuild")
    assert r.exit_code == 0, r.output
    assert _manifest(out)["mode"] == "timeseries"
    _assert_foreign_kept(out)


def test_log_file_inside_the_output_survives_a_full_rebuild(tmp_path: Path):
    out = tmp_path / "dash"
    log = out / "convert.log"
    assert invoke("convert", str(TIMESERIES), str(out)).exit_code == 0
    r = invoke("convert", str(TIMESERIES), str(out), "--rebuild", "--log-file", str(log))
    assert r.exit_code == 0, r.output
    assert "full conversion: --rebuild given" in log.read_text(encoding="utf-8")


def test_symlinked_output_folder(tmp_path: Path):
    real = tmp_path / "real"
    assert invoke("convert", str(TIMESERIES), str(real)).exit_code == 0
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    r = invoke("convert", str(TIMESERIES), str(link), "--rebuild")
    assert r.exit_code == 0, r.output
    assert link.is_symlink() and (real / "data" / "manifest.json").is_file()


# ── recognising a dashboard ──────────────────────────────────────────────


@pytest.mark.parametrize("content", ["{}", "[]", '{"provenance": {"converter": {"name": "other"}}}', '{"trunc'])
def test_manifest_not_written_by_imi_dashboard_is_refused(tmp_path: Path, content: str):
    out = tmp_path / "site"
    (out / "data").mkdir(parents=True)
    (out / "data" / "manifest.json").write_text(content, encoding="utf-8")
    (out / "index.html").write_text("my site", encoding="utf-8")
    for force in (False, True):
        with pytest.raises(ValueError, match="was not written by imi-dashboard"):
            convert(input_folder=SINGLE, output_folder=out, cfg=Config(), force=force)
    assert (out / "index.html").read_text(encoding="utf-8") == "my site"


def test_dashboard_manifest_of_the_wrong_shape_is_rebuilt(tmp_path: Path):
    """A manifest naming imi-dashboard but with unusable fields or periods
    leads to a full rebuild instead of a traceback."""
    out = tmp_path / "out"
    convert(input_folder=TIMESERIES, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    m = _manifest(out)
    m["fields"] = [{"offset": 0}]
    (out / "data" / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    summary = convert(input_folder=TIMESERIES, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    assert "reused" not in summary
    assert _manifest(out)["fields"][0]["name"] == "total"


def test_folder_without_manifest_mentions_an_interrupted_conversion(tmp_path: Path):
    out = tmp_path / "out"
    (out / "assets").mkdir(parents=True)
    (out / "index.html").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="If a conversion into it was interrupted"):
        convert(input_folder=SINGLE, output_folder=out, cfg=Config(), force=True)


def test_interrupted_full_rebuild_leaves_a_dashboard(tmp_path: Path, monkeypatch):
    """The old manifest stays until the new one replaces it, so a full
    rebuild that stops part-way leaves a folder the next run accepts."""
    out = tmp_path / "out"
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg)

    def boom(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(pack, "_write_manifest", boom)
    with pytest.raises(KeyboardInterrupt):
        convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg, force=True)
    monkeypatch.undo()
    assert pack._load_existing_manifest(out) is not None
    summary = convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg)
    assert summary["period_count"] == 3


# ── asking before data is removed ────────────────────────────────────────


def _drop_period_2(run: Path) -> None:
    shutil.rmtree(run / "period_2_plots")


def test_removing_periods_needs_yes_without_a_terminal(tmp_path: Path):
    run, out = _copy_run(tmp_path), tmp_path / "out"
    assert invoke("convert", str(run), str(out)).exit_code == 0
    before = _manifest(out)
    _drop_period_2(run)

    r = invoke("convert", str(run), str(out))
    assert r.exit_code == 1
    assert "1 period(s) of the dashboard in" in r.stderr
    assert "would be removed from it: period_2. Pass --yes" in r.stderr
    assert _manifest(out) == before   # nothing changed

    r = invoke("convert", str(run), str(out), "--yes")
    assert r.exit_code == 0, r.output
    assert [p["label"] for p in _manifest(out)["periods"]] == ["period_1", "period_4"]


@pytest.mark.parametrize("answer, exit_code, periods", [("n\n", 1, 3), ("y\n", 0, 2)])
def test_removing_periods_asks_on_a_terminal(tmp_path: Path, monkeypatch, answer: str, exit_code: int, periods: int):
    from typer.testing import CliRunner

    run, out = _copy_run(tmp_path), tmp_path / "out"
    assert invoke("convert", str(run), str(out)).exit_code == 0
    _drop_period_2(run)
    monkeypatch.setattr(cli, "_stdin_is_terminal", lambda: True)
    r = CliRunner().invoke(cli.app, ["convert", str(run), str(out)], input=answer)
    assert r.exit_code == exit_code, r.output
    assert "period_2. Continue? [y/N]" in r.stderr
    if exit_code:
        assert f"cancelled; {out} was not changed" in r.stderr
    assert len(_manifest(out)["periods"]) == periods


def test_replacing_a_dashboard_of_another_input_asks(tmp_path: Path):
    run_a, run_b, out = _copy_run(tmp_path, name="run_a"), _copy_run(tmp_path, name="run_b"), tmp_path / "out"
    assert invoke("convert", str(run_a), str(out)).exit_code == 0
    r = invoke("convert", str(run_b), str(out))
    assert r.exit_code == 1
    assert "holds a dashboard of 3 period(s) converted from another input folder ('run_a', on " in r.stderr
    assert invoke("convert", str(run_b), str(out), "-y").exit_code == 0
    assert _manifest(out)["provenance"]["input"] == "run_b"


def test_reconverting_the_same_periods_does_not_ask(tmp_path: Path):
    """--rebuild or a region change on the same input writes every period
    again: nothing is lost, so there is nothing to confirm."""
    out = tmp_path / "out"
    assert invoke("convert", str(TIMESERIES), str(out)).exit_code == 0
    assert invoke("convert", str(TIMESERIES), str(out), "--rebuild").exit_code == 0
    assert invoke("convert", str(TIMESERIES), str(out), "--region", "domain").exit_code == 0


# ── the converter version (review item 2.1) ──────────────────────────────


def test_converter_release():
    assert pack._converter_release("0.1.1") == "0.1.1"
    assert pack._converter_release("0.1.1.dev3+g1a2b3c4.d20261105") == "0.1.1"
    assert pack._converter_release("0.0.1.dev70+unknown") == "0.0.1"


def test_dashboard_of_another_converter_release_is_reconverted(tmp_path: Path):
    out = tmp_path / "out"
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg)
    assert _manifest(out)["source"]["converter"] == pack._converter_release()
    assert convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg).get("reused") == 3

    m = _manifest(out)
    m["source"]["converter"] = "0.0.0"
    (out / "data" / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    assert "reused" not in convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg)
    assert convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg).get("reused") == 3


# ── the app bundle ──────────────────────────────────────────────────────────


def test_manifest_version_matches_the_web_app():
    fmt = json.loads((REPO_ROOT / "web" / "src" / "data" / "format.json").read_text(encoding="utf-8"))
    assert fmt["manifestVersion"] == pack.MANIFEST_VERSION


def _refused_before_reading(tmp_path: Path, bundle: Path, match: str) -> None:
    calls: list = []
    with pytest.raises(ValueError, match=match):
        convert(input_folder=TIMESERIES, output_folder=tmp_path / "out", cfg=Config(),
                bundle_dir=bundle, on_period=lambda *a: calls.append(a))
    assert calls == [] and not (tmp_path / "out").exists()


def test_bundle_folder_without_marker_is_refused(tmp_path: Path):
    notabundle = tmp_path / "notabundle"
    (notabundle / "private").mkdir(parents=True)
    (notabundle / "index.html").write_text("", encoding="utf-8")
    (notabundle / "private" / "run.nc").write_text("secret", encoding="utf-8")
    _refused_before_reading(tmp_path, notabundle, "is not an app bundle")


@pytest.mark.parametrize("marker, match", [
    ({"manifestVersion": 99}, "reads data format version 99, and this converter writes version 1"),
    ({"files": ["index.html", "assets/gone.js"]}, "assets/gone.js, listed in its imi-dashboard-app.json, is missing"),
    ({"files": ["index.html", "../outside.txt"]}, "'../outside.txt', which leaves the folder"),
    ({"files": ["index.html", "data/manifest.json"]}, "which the converter writes itself"),
    ({"app": "something-else"}, "is not an imi-dashboard app bundle marker"),
])
def test_bad_web_app_marker_is_refused(tmp_path: Path, marker: dict, match: str):
    bundle = write_fake_web_app(tmp_path / "bundle", **marker)
    _refused_before_reading(tmp_path, bundle, match)


def test_only_the_listed_files_are_copied(tmp_path: Path):
    bundle = write_fake_web_app(tmp_path / "bundle")
    (bundle / "notes.txt").write_text("not part of the app", encoding="utf-8")
    out = tmp_path / "out"
    convert(input_folder=SINGLE, output_folder=out, cfg=Config(), bundle_dir=bundle)
    assert not (out / "notes.txt").exists()
    assert (out / "assets" / "index.js").is_file()


def test_bundle_overlapping_the_output_is_refused(tmp_path: Path):
    out = tmp_path / "out"
    convert(input_folder=TIMESERIES, output_folder=out, cfg=Config())
    for bundle, output in ((out, out), (tmp_path, out), (out, out / "inner")):
        with pytest.raises(ValueError, match="overlap"):
            pack._find_web_app(bundle, output)


def test_another_dashboard_as_bundle_copies_only_its_web_app(tmp_path: Path):
    """--app-bundle <dashboard> reuses that dashboard's app bundle; its data/ is not
    copied over the dashboard being updated."""
    permian, romania = tmp_path / "permian", tmp_path / "romania"
    convert(input_folder=SINGLE, output_folder=permian, cfg=Config())
    convert(input_folder=TIMESERIES, output_folder=romania, cfg=Config())
    bins = {p.name: p.read_bytes() for p in (permian / "data" / "periods").iterdir()}

    summary = convert(input_folder=SINGLE, output_folder=permian, cfg=Config(), bundle_dir=romania)
    assert summary.get("reused") == 1
    assert {p.name: p.read_bytes() for p in (permian / "data" / "periods").iterdir()} == bins
    assert _manifest(permian)["mode"] == "single"


def test_new_web_app_replaces_the_old_and_keeps_the_custom_logo(tmp_path: Path):
    cfg_path = tmp_path / "dashboard.yaml"
    (tmp_path / "logo.png").write_bytes(b"custom mark")
    cfg_path.write_text("header_brand: custom\nlogo: logo.png\n", encoding="utf-8")
    old = write_fake_web_app(tmp_path / "old", {"index.html": "old", "assets/index-AAA.js": "", "favicon.ico": ""})
    new = write_fake_web_app(tmp_path / "new", {"index.html": "new", "assets/index-BBB.js": ""})
    out = tmp_path / "out"
    convert(input_folder=SINGLE, output_folder=out, cfg=load_config(cfg_path), bundle_dir=old)
    convert(input_folder=SINGLE, output_folder=out, cfg=load_config(cfg_path), bundle_dir=new)
    assert sorted(p.name for p in (out / "assets").iterdir()) == ["custom-logo.png", "index-BBB.js"]
    assert not (out / "favicon.ico").exists()
    assert (out / "index.html").read_text(encoding="utf-8") == "new"


def test_no_web_app_for_a_new_dashboard_is_an_error_before_any_period_is_read(tmp_path: Path, monkeypatch):
    clone_web = tmp_path / "clone" / "web"
    clone_web.mkdir(parents=True)
    (clone_web / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pack, "REPO_BUNDLE_DIR", clone_web / "dist")
    monkeypatch.setattr(pack, "PACKAGE_BUNDLE_DIR", tmp_path / "no_pkg_bundle")
    r = invoke("convert", str(TIMESERIES), str(tmp_path / "out"), "-v")
    assert r.exit_code == 1
    # From a clone the remedy is to build web/; no period was read.
    assert "build it in this clone with `npm --prefix web run build`" in r.stderr
    assert "[1/3]" not in r.stderr
    assert not (tmp_path / "out").exists()


# ── damaged input files (review items 3.1, 3.2) ──────────────────────────


def test_damaged_statistics_csv_warns_and_the_period_converts(tmp_path: Path):
    from imi_dashboard.readers import UnreadableFileWarning

    run, out = _copy_run(tmp_path), tmp_path / "out"
    (run / "period_2_plots" / "viz_notebook_statistics.csv").write_bytes(b"")
    with pytest.warns(UnreadableFileWarning, match=r"period_2_plots[/\\]viz_notebook_statistics\.csv cannot be read as a CSV table \(No columns to parse from file\)"):
        convert(input_folder=run, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    p2 = next(p for p in _manifest(out)["periods"] if p["label"] == "period_2")
    assert (p2["start"], p2["sectorScalars"]["total"]["posterior"]) == (None, None)


def test_ensemble_statistics_csv_is_not_read(tmp_path: Path):
    run = _copy_run(tmp_path, SINGLE)
    (run / "ensemble_statistics.csv").write_bytes(b"")
    convert(input_folder=run, output_folder=tmp_path / "out", cfg=Config())
    assert _manifest(tmp_path / "out")["periods"][0]["sectorScalars"]["total"]["ens_min"] == pytest.approx(0.4939, abs=1e-4)


def test_unreadable_date_becomes_none_with_a_warning(tmp_path: Path):
    from imi_dashboard.readers import UnreadableFileWarning

    run, out = _copy_run(tmp_path, SINGLE), tmp_path / "out"
    csv = run / "viz_notebook_statistics.csv"
    csv.write_text(csv.read_text(encoding="utf-8").replace("20210501,", "foo,", 1), encoding="utf-8")
    with pytest.warns(UnreadableFileWarning, match="StartDate 'foo' in .* is not a date of the form YYYYMMDD"):
        convert(input_folder=run, output_folder=out, cfg=Config())
    p = _manifest(out)["periods"][0]
    assert (p["start"], p["end"]) == (None, "2021-06-01")


def test_truncated_posterior_leaves_its_period_out(tmp_path: Path):
    run, out = _copy_run(tmp_path), tmp_path / "out"
    posterior = run / "period_2_plots" / "netCDF" / "posterior.nc"
    posterior.write_bytes(posterior.read_bytes()[:5000])
    with pytest.warns(pack.IncompletePeriodWarning, match=r"1 period\(s\) whose files cannot be read, shown as gaps: period_2 \(posterior.nc: \[Errno -101\] NetCDF: HDF error"):
        convert(input_folder=run, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    assert [(p["idx"], p["label"]) for p in _manifest(out)["periods"]] == [(0, "period_1"), (1, "period_4")]


def test_damaged_optional_file_empties_its_maps(tmp_path: Path):
    from imi_dashboard.readers import UnreadableFileWarning

    run, out = _copy_run(tmp_path, SINGLE), tmp_path / "out"
    (run / "netCDF" / "obs_ds_regridded.nc").write_bytes(b"")
    with pytest.warns(UnreadableFileWarning, match=r"obs_ds_regridded.nc cannot be read \(not a netCDF file\), so single has no satellite observation maps"):
        convert(input_folder=run, output_folder=out, cfg=Config())
    m = _manifest(out)
    field = next(f for f in m["fields"] if f["name"] == "obs_count")
    data = np.fromfile(out / "data" / "periods" / "0000.bin", dtype="<f4")
    assert not np.isfinite(data[field["offset"]: field["offset"] + field["length"]]).any()


# ── an output folder inside a single-period input (review item 3.8) ──────


def test_output_inside_a_single_period_input_is_reused(tmp_path: Path):
    run = _copy_run(tmp_path, SINGLE)
    out = run / "dashboard"
    convert(input_folder=run, output_folder=out, cfg=Config())
    assert convert(input_folder=run, output_folder=out, cfg=Config()).get("reused") == 1
    # Ruling out the obvious: the output's own files really are under the input.
    assert any(os.scandir(out / "data" / "periods"))
