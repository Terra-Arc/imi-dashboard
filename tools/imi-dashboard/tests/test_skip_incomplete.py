"""Periods lacking posterior.nc or prior.nc are skipped, not fatal.

Inside an IMI run, a visualization notebook that fails part-way can leave a
period folder without its emission fields. The converter leaves such a
period out with an IncompletePeriodWarning; it then shows as a gap, exactly
like a missing period folder. Only when no period is complete does the
conversion fail.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from imi_dashboard.config import load_config
from imi_dashboard.discover import discover, split_incomplete
from imi_dashboard.pack import IncompletePeriodWarning, convert

from .conftest import EXAMPLE_CONFIG, SAMPLES


def _copy_run(tmp_path: Path, name: str = "timeseries") -> Path:
    run = tmp_path / "run"
    shutil.copytree(SAMPLES / name, run)
    return run


def _convert(run: Path, out: Path, **kw) -> dict:
    return convert(input_folder=run, output_folder=out, cfg=load_config(EXAMPLE_CONFIG), **kw)


def _manifest(out: Path) -> dict:
    return json.loads((out / "data" / "manifest.json").read_text())


def test_split_incomplete_reindexes_and_keeps_folder_numbers(tmp_path: Path):
    run = _copy_run(tmp_path)
    (run / "period_2_plots" / "netCDF" / "prior.nc").unlink()
    complete, skipped = split_incomplete(discover(run))
    assert [(p.idx, p.number) for p in complete.periods] == [(0, 1), (1, 4)]
    assert [(s.period.number, s.missing) for s in skipped] == [(2, ("prior.nc",))]


def test_split_incomplete_names_every_missing_file(tmp_path: Path):
    run = _copy_run(tmp_path)
    (run / "period_4_plots" / "netCDF" / "prior.nc").unlink()
    (run / "period_4_plots" / "netCDF" / "posterior.nc").unlink()
    _, skipped = split_incomplete(discover(run))
    assert [(s.period.number, s.missing) for s in skipped] == [(4, ("posterior.nc", "prior.nc"))]


def test_convert_skips_a_period_without_posterior(tmp_path: Path):
    run = _copy_run(tmp_path)
    (run / "period_2_plots" / "netCDF" / "posterior.nc").unlink()
    out = tmp_path / "out"
    with pytest.warns(IncompletePeriodWarning, match=r"period_2 \(no posterior.nc\)"):
        summary = _convert(run, out)

    assert summary["period_count"] == 2
    periods = _manifest(out)["periods"]
    # Folder numbers 1 and 4 remain: the web app draws 2 and 3 as gaps.
    assert [(p["idx"], p["number"], p["label"]) for p in periods] == [
        (0, 1, "period_1"), (1, 4, "period_4"),
    ]
    assert sorted(f.name for f in (out / "data" / "periods").glob("*.bin")) == ["0000.bin", "0001.bin"]


def test_convert_with_first_period_incomplete_uses_the_next(tmp_path: Path):
    """The grid, state vector and defaults are read from the first complete
    period, so an incomplete period 1 does not stop the conversion."""
    run = _copy_run(tmp_path)
    (run / "period_1_plots" / "netCDF" / "prior.nc").unlink()
    out = tmp_path / "out"
    with pytest.warns(IncompletePeriodWarning, match=r"period_1 \(no prior.nc\)"):
        summary = _convert(run, out)
    assert [p["number"] for p in _manifest(out)["periods"]] == [2, 4]
    # Same ROI crop as the complete sample (test_cli: roi_shape (18, 29)).
    assert (summary["rows"], summary["cols"]) == (18, 29)


def test_incremental_update_drops_a_period_that_became_incomplete(tmp_path: Path):
    """The raster at the vanished trailing index is removed."""
    run = _copy_run(tmp_path)
    out = tmp_path / "out"
    _convert(run, out)
    assert [p["number"] for p in _manifest(out)["periods"]] == [1, 2, 4]

    (run / "period_2_plots" / "netCDF" / "posterior.nc").unlink()
    with pytest.warns(IncompletePeriodWarning):
        summary = _convert(run, out)
    assert (summary["reused"], summary["rebuilt"]) == (1, 1)   # period 1 kept, period 4 moved to idx 1
    periods = _manifest(out)["periods"]
    assert [(p["idx"], p["number"]) for p in periods] == [(0, 1), (1, 4)]
    assert sorted(f.name for f in (out / "data" / "periods").glob("*.bin")) == ["0000.bin", "0001.bin"]


def test_convert_fails_when_no_period_is_complete(tmp_path: Path):
    run = _copy_run(tmp_path, "single_period")
    (run / "netCDF" / "posterior.nc").unlink()
    with pytest.raises(ValueError, match="no period has both posterior.nc and prior.nc"):
        _convert(run, tmp_path / "out")


@pytest.mark.parametrize("damage", ["missing", "truncated"])
def test_a_skipped_period_is_converted_once_its_files_are_complete(tmp_path: Path, damage: str):
    """A period left out because its posterior.nc is missing or cannot be
    read is not remembered as skipped: the first update that finds the file
    complete converts it, and moves the later periods up an index."""
    run = _copy_run(tmp_path)
    out = tmp_path / "out"
    posterior = run / "period_2_plots" / "netCDF" / "posterior.nc"
    whole = tmp_path / "posterior.nc"
    shutil.copy2(posterior, whole)
    if damage == "missing":
        posterior.unlink()
    else:
        posterior.write_bytes(whole.read_bytes()[:4096])   # IMI still writing it
    with pytest.warns(IncompletePeriodWarning):
        _convert(run, out)
    assert [p["number"] for p in _manifest(out)["periods"]] == [1, 4]

    shutil.copy2(whole, posterior)
    summary = _convert(run, out)
    # period 1 reused; period 2 new; period 4 reconverted (index 1 -> 2)
    assert (summary["reused"], summary["rebuilt"]) == (1, 2)
    assert [(p["idx"], p["number"]) for p in _manifest(out)["periods"]] == [(0, 1), (1, 2), (2, 4)]
