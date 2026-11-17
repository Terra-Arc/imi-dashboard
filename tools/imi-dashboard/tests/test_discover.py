import pytest

from imi_dashboard.discover import discover

from .conftest import SINGLE, TIMESERIES


def test_single_period_sample_detected():
    d = discover(SINGLE)
    assert d.mode == "single"
    assert len(d.periods) == 1
    p = d.periods[0]
    assert p.idx == 0
    assert p.netcdf_dir.name == "netCDF"
    assert (p.netcdf_dir / "posterior.nc").exists()


def test_timeseries_sample_detected_and_sorted():
    d = discover(TIMESERIES)
    assert d.mode == "timeseries"
    # The sample deliberately lacks period_3_plots: idx stays a contiguous
    # 0-based counter while number keeps the folder numbering, gap included.
    assert [p.label for p in d.periods] == ["period_1", "period_2", "period_4"]
    assert [p.idx for p in d.periods] == [0, 1, 2]
    assert [p.number for p in d.periods] == [1, 2, 4]


def test_missing_input_folder_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        discover(tmp_path / "does_not_exist")


def test_empty_folder_rejected(tmp_path):
    (tmp_path / "garbage").mkdir()
    with pytest.raises(ValueError):
        discover(tmp_path)


def test_timeseries_sorting_is_numeric_not_lexical(tmp_path):
    # Ensure period_2 < period_10 (lexical sort would put period_10 first).
    for n in (10, 2, 7):
        d = tmp_path / f"period_{n}_plots" / "netCDF"
        d.mkdir(parents=True)
    result = discover(tmp_path)
    assert [p.label for p in result.periods] == ["period_2", "period_7", "period_10"]


def test_gap_yields_contiguous_idx_and_sparse_numbers(tmp_path):
    # Folders 202–204 are missing: idx stays a dense 0-based counter (bin
    # naming / incremental reuse depend on it) while number carries the real
    # folder numbering, gaps included.
    for n in (200, 201, 205):
        (tmp_path / f"period_{n}_plots" / "netCDF").mkdir(parents=True)
    result = discover(tmp_path)
    assert [p.idx for p in result.periods] == [0, 1, 2]
    assert [p.number for p in result.periods] == [200, 201, 205]


def test_single_period_number_is_zero():
    d = discover(SINGLE)
    assert d.periods[0].number == 0
