"""Incremental rebuild behaviour of the converter.

The converter reuses already-written period binaries when the inputs are
unchanged, rebuilding only new/modified periods unless ``force=True``.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest

from imi_dashboard.config import load_config
from imi_dashboard.pack import convert

from .conftest import EXAMPLE_CONFIG, TIMESERIES


def _convert(out: Path, **kw) -> dict:
    return convert(
        input_folder=TIMESERIES,
        output_folder=out,
        cfg=load_config(EXAMPLE_CONFIG),
        **kw,
    )


def _bin_mtimes(out: Path) -> dict[str, int]:
    periods_dir = out / "data" / "periods"
    return {p.name: p.stat().st_mtime_ns for p in sorted(periods_dir.glob("*.bin"))}


def test_first_run_is_a_full_build(tmp_path: Path):
    summary = _convert(tmp_path / "out")
    # A fresh output has nothing to reuse, so the incremental counters are
    # absent (this went through the full-build path).
    assert "reused" not in summary
    assert summary["period_count"] >= 2


def test_second_run_reuses_all_periods(tmp_path: Path):
    out = tmp_path / "out"
    first = _convert(out)
    before = _bin_mtimes(out)

    summary = _convert(out)
    after = _bin_mtimes(out)

    assert summary.get("reused") == summary["period_count"]
    assert summary.get("rebuilt") == 0
    # No binary was rewritten.
    assert before == after
    # Period set preserved, indices still a contiguous 0-based range.
    m1 = json.loads((out / "data" / "manifest.json").read_text())
    assert summary["period_count"] == first["period_count"]
    assert [p["idx"] for p in m1["periods"]] == list(range(len(m1["periods"])))


def test_incremental_run_updates_conversion_time(tmp_path: Path, monkeypatch):
    from imi_dashboard import pack

    out = tmp_path / "out"
    monkeypatch.setattr(pack, "_utc_now", lambda: "2026-01-01T00:00:00Z")
    _convert(out)
    monkeypatch.setattr(pack, "_utc_now", lambda: "2026-02-03T04:05:06Z")
    summary = _convert(out)

    assert summary.get("reused") == summary["period_count"]  # the incremental path
    m = json.loads((out / "data" / "manifest.json").read_text())
    assert m["provenance"]["convertedAt"] == "2026-02-03T04:05:06Z"
    assert m["provenance"]["input"] == "timeseries"


def test_force_rebuilds_everything(tmp_path: Path):
    out = tmp_path / "out"
    _convert(out)
    periods_dir = out / "data" / "periods"
    original = {p.name: p.read_bytes() for p in periods_dir.glob("*.bin")}
    # Overwrite every bin with zeros of the same size: an incremental run
    # would reuse them (same size, same input fingerprint), so getting the
    # original bytes back shows that a forced rebuild rewrote each one. (Comparing
    # modification times instead fails when the clock is stepped back between
    # the two runs, as WSL2 does when it resynchronises.)
    for name, data in original.items():
        (periods_dir / name).write_bytes(bytes(len(data)))

    summary = _convert(out, force=True)

    # Full rebuild path: no incremental counters, and every bin was rewritten.
    assert "reused" not in summary
    assert {p.name: p.read_bytes() for p in periods_dir.glob("*.bin")} == original


def test_modified_period_is_rebuilt_others_reused(tmp_path: Path):
    # Work against a *copy* of the sample so bumping an input's mtime can't
    # pollute the shared fixture for other tests.
    src = tmp_path / "input"
    shutil.copytree(TIMESERIES, src)
    out = tmp_path / "out"

    def _convert_copy(**kw):
        return convert(
            input_folder=src,
            output_folder=out,
            cfg=load_config(EXAMPLE_CONFIG),
            **kw,
        )

    _convert_copy()
    before = _bin_mtimes(out)

    # Mark one period's input as newer than its binary → it must rebuild.
    m = json.loads((out / "data" / "manifest.json").read_text())
    target = m["periods"][1]
    period_num = target["label"].split("_")[1]
    netcdf = src / f"period_{period_num}_plots" / "netCDF" / "posterior.nc"
    assert netcdf.exists()
    future = (out / "data" / "periods" / Path(target["file"]).name).stat().st_mtime + 10_000
    os.utime(netcdf, (future, future))

    summary = _convert_copy()
    after = _bin_mtimes(out)

    assert summary.get("rebuilt") == 1
    assert summary.get("reused") == summary["period_count"] - 1
    changed = [k for k in before if after[k] != before[k]]
    assert changed == [Path(target["file"]).name]


def test_reused_bins_match_a_forced_rebuild(tmp_path: Path):
    """Incrementally-updated output must be byte-identical to a clean rebuild."""
    inc = tmp_path / "inc"
    _convert(inc)
    _convert(inc)  # incremental no-op pass

    full = tmp_path / "full"
    _convert(full, force=True)

    inc_m = json.loads((inc / "data" / "manifest.json").read_text())
    full_m = json.loads((full / "data" / "manifest.json").read_text())
    assert inc_m["sectorBounds"] == full_m["sectorBounds"]
    assert inc_m["grid"] == full_m["grid"]

    for entry in full_m["periods"]:
        a = (inc / "data" / entry["file"]).read_bytes()
        b = (full / "data" / entry["file"]).read_bytes()
        assert a == b
    # Land mask identical too.
    assert (inc / "data" / "globals.bin").read_bytes() == (full / "data" / "globals.bin").read_bytes()


def test_other_input_folder_forces_full_rebuild(tmp_path: Path):
    """A dashboard converted from one run is not updated from another: the
    copies keep the original file times, so only the recorded input folder
    tells them apart."""
    run_a, run_b, out = tmp_path / "run_a", tmp_path / "run_b", tmp_path / "out"
    shutil.copytree(TIMESERIES, run_a)
    shutil.copytree(TIMESERIES, run_b)
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=run_a, output_folder=out, cfg=cfg)
    summary = convert(input_folder=run_b, output_folder=out, cfg=cfg)
    assert "reused" not in summary
    assert summary["period_count"] == 3


def test_input_replaced_with_older_times_is_rebuilt(tmp_path: Path):
    """An input file restored or copied with an older timestamp than the
    period binary still counts as changed."""
    src, out = tmp_path / "input", tmp_path / "out"
    shutil.copytree(TIMESERIES, src)
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=src, output_folder=out, cfg=cfg)
    old = 1_577_836_800  # 2020-01-01, long before the binaries were written
    netcdf = src / "period_2_plots" / "netCDF" / "posterior.nc"
    os.utime(netcdf, (old, old))

    summary = convert(input_folder=src, output_folder=out, cfg=cfg)
    assert summary.get("rebuilt") == 1
    assert summary.get("reused") == 2


def test_changed_imi_var_forces_full_rebuild(tmp_path: Path):
    """Pointing a sector at another netCDF variable rewrites its data."""
    out = tmp_path / "out"
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=TIMESERIES, output_folder=out, cfg=cfg)
    swapped = cfg.model_copy(update={"sectors": [
        s.model_copy(update={"imi_var": "EmisCH4_Livestock"}) if s.id == "oil" else s
        for s in cfg.sectors
    ]})
    summary = convert(input_folder=TIMESERIES, output_folder=out, cfg=swapped)
    assert "reused" not in summary
    m = json.loads((out / "data" / "manifest.json").read_text())
    assert m["source"]["sectorVars"]["oil"] == "EmisCH4_Livestock"
    assert m["sectorBounds"]["oil"] == m["sectorBounds"]["livestock"]


def test_dashboard_from_before_the_soil_sink_change_is_reconverted(tmp_path: Path):
    """Dashboards written before the total map and the posterior − prior map
    left out the soil sink recorded `total` as EmisCH4_Total and no
    deltaEmissionsVar. Such a dashboard is reconverted once, because the
    total's variable and the posterior − prior map's change."""
    from .conftest import SINGLE

    for name, src in (("permian", SINGLE), ("romania", TIMESERIES)):
        out = tmp_path / name
        convert(input_folder=src, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
        manifest_path = out / "data" / "manifest.json"
        m = json.loads(manifest_path.read_text())
        m["source"]["sectorVars"]["total"] = "EmisCH4_Total"
        del m["source"]["deltaEmissionsVar"]
        manifest_path.write_text(json.dumps(m))

        assert "reused" not in convert(input_folder=src, output_folder=out, cfg=load_config(EXAMPLE_CONFIG)), name
        m = json.loads(manifest_path.read_text())
        assert m["source"]["deltaEmissionsVar"] == "EmisCH4_Total_ExclSoilAbs"
        assert m["source"]["sectorVars"]["total"] == "EmisCH4_Total_ExclSoilAbs"
        assert convert(input_folder=src, output_folder=out, cfg=load_config(EXAMPLE_CONFIG)).get("reused") == len(m["periods"])
        # The incremental path writes the same record of the total's definition.
        assert json.loads(manifest_path.read_text())["totalDefinition"] == {
            "numbersIncludeSoilSink": False, "mapIncludesSoilSink": False,
        }


@pytest.mark.parametrize("recorded", [None, "without-soil-sink"])
def test_dashboard_from_before_the_totals_without_soil_sink_is_reconverted(tmp_path: Path, recorded):
    """Dashboards whose totals were IMI's PriorEmissions/PosteriorEmissions
    (which in Kalman mode mix the totals with and without the soil sink)
    record no totalNumbers, and those converted before IMI 2.2.1's ensemble
    range was moved off the soil sink record "without-soil-sink". Their
    periods would otherwise be reused, since the variables did not change:
    they are reconverted once."""
    out = tmp_path / "out"
    convert(input_folder=TIMESERIES, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    manifest_path = out / "data" / "manifest.json"
    m = json.loads(manifest_path.read_text())
    assert m["source"]["totalNumbers"] == "without-soil-sink-2"
    if recorded is None:
        del m["source"]["totalNumbers"]
    else:
        m["source"]["totalNumbers"] = recorded
    manifest_path.write_text(json.dumps(m))

    summary = convert(input_folder=TIMESERIES, output_folder=out, cfg=load_config(EXAMPLE_CONFIG))
    assert "reused" not in summary
    assert json.loads(manifest_path.read_text())["source"]["totalNumbers"] == "without-soil-sink-2"
    assert convert(input_folder=TIMESERIES, output_folder=out, cfg=load_config(EXAMPLE_CONFIG)).get("reused") == 3


def test_manifest_without_source_forces_full_rebuild(tmp_path: Path):
    """A dashboard from a converter that did not record its input is
    reconverted once, then updated incrementally again."""
    out = tmp_path / "out"
    _convert(out)
    manifest_path = out / "data" / "manifest.json"
    m = json.loads(manifest_path.read_text())
    del m["source"]
    for e in m["periods"]:
        del e["inputs"]
    manifest_path.write_text(json.dumps(m))

    assert "reused" not in _convert(out)
    assert _convert(out).get("reused") == 3


def test_on_period_is_called_once_per_period_on_both_paths(tmp_path: Path):
    """The progress callback gets (done, total, label) with done running
    1…n: on a full conversion after each period is read, and on an update
    for every period, reused ones included, so a progress bar reaches 100 %."""
    src = tmp_path / "input"
    shutil.copytree(TIMESERIES, src)
    out = tmp_path / "out"
    cfg = load_config(EXAMPLE_CONFIG)

    calls: list[tuple[int, int, str]] = []
    convert(input_folder=src, output_folder=out, cfg=cfg, on_period=lambda *a: calls.append(a))
    assert calls == [(1, 3, "period_1"), (2, 3, "period_2"), (3, 3, "period_4")]

    # Make period 2's input newer than its binary: periods 1 and 4 are
    # reused (reported first), period 2 is rebuilt.
    netcdf = src / "period_2_plots" / "netCDF" / "posterior.nc"
    future = (out / "data" / "periods" / "0001.bin").stat().st_mtime + 10_000
    os.utime(netcdf, (future, future))
    calls.clear()
    summary = convert(input_folder=src, output_folder=out, cfg=cfg, on_period=lambda *a: calls.append(a))
    assert (summary["reused"], summary["rebuilt"]) == (2, 1)
    assert calls == [(1, 3, "period_1"), (2, 3, "period_4"), (3, 3, "period_2")]
