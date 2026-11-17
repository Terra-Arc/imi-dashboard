"""Grid alignment and the CSV reader, on small synthetic inputs."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from imi_dashboard.grid import align_to_grid, grid_from_dataset
from imi_dashboard.readers import read_viz_stats

LAT = np.array([10.0, 10.5, 11.0])          # 3 rows, south to north
LON = np.array([20.0, 20.625])              # 2 columns, west to east
# values[r, c] = 10 * r + c on the ascending (lat, lon) grid
VALUES = 10 * np.arange(3)[:, None] + np.arange(2)[None, :]


def _grid():
    return grid_from_dataset(xr.Dataset(coords={"lat": LAT, "lon": LON}))


def test_align_to_grid_transposes_lon_lat_and_drops_a_single_time_step():
    da = xr.DataArray(VALUES.T[None, :, :], dims=("time", "lon", "lat"), coords={"lat": LAT, "lon": LON})
    out = align_to_grid(da, _grid())
    assert out.shape == (3, 2)
    np.testing.assert_array_equal(out, VALUES)


def test_descending_latitude_is_read_south_to_north():
    ds = xr.Dataset(
        {"v": (("lat", "lon"), VALUES[::-1])},
        coords={"lat": LAT[::-1], "lon": LON},
    )
    grid = grid_from_dataset(ds)
    np.testing.assert_array_equal(grid.lat, LAT)
    assert grid.dlat == 0.5
    assert grid.bbox == (20.0, 10.0, 20.625, 11.0)
    np.testing.assert_array_equal(align_to_grid(ds["v"], grid), VALUES)


def test_align_to_grid_rejects_a_dimension_beyond_lat_lon():
    da = xr.DataArray(np.zeros((2, 3, 2)), dims=("time", "lat", "lon"), coords={"lat": LAT, "lon": LON}, name="v")
    with pytest.raises(ValueError, match=r"variable 'v' has dimensions .*'time': 2"):
        align_to_grid(da, _grid())


def test_viz_csv_empty_cells_are_none(tmp_path: Path):
    csv = tmp_path / "viz_notebook_statistics.csv"
    csv.write_text("StartDate,EndDate,DOFS,PriorEmissions\n,20200201,,1.5\n", encoding="utf-8")
    assert read_viz_stats(csv) == {"StartDate": None, "EndDate": "2020-02-01", "DOFS": None, "PriorEmissions": 1.5}


@pytest.mark.parametrize("preset", [None, "C:/my/plugins"])
def test_hdf5_plugin_search_is_off_on_windows(preset):
    """Importing the package empties HDF5_PLUGIN_PATH on Windows unless the
    user set it (netCDF4 >= 1.7.4's bundled plugins make every filter check
    slow there); elsewhere it is left unset. A subprocess, so that the
    package is imported before netCDF4, as in the command."""
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if k != "HDF5_PLUGIN_PATH"}
    if preset is not None:
        env["HDF5_PLUGIN_PATH"] = preset
    code = "import os, imi_dashboard, netCDF4; print(repr(os.environ.get('HDF5_PLUGIN_PATH')))"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    shown = r.stdout.strip()
    if preset is not None:
        assert shown == repr(preset)
    elif os.name == "nt":
        assert shown == "''"
    else:
        # netCDF4 may set its own plugin folder on other platforms; the package does not touch it.
        assert shown == "None" or "netCDF4" in shown
