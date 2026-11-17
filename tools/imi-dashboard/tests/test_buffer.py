"""Buffer-region identification in IMI state vectors."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from imi_dashboard.config import load_config
from imi_dashboard.grid import grid_from_dataset
from imi_dashboard.pack import convert
from imi_dashboard.readers import (
    identify_buffer_elements,
    read_buffer_regions,
)

from .conftest import EXAMPLE_CONFIG, SAMPLES, SINGLE, TIMESERIES


# Expected split for each shipped sample. The two samples use the two buffer
# schemes IMI has had:
#   single_period — IMI 2.3.1 Permian quick-start, BufferRings: 3 with
#                   BufferReductionFactor: 4: 63 one-cell ROI elements, then 33
#                   buffer elements of 2-9 cells in three rings (132 cells).
#   timeseries    — older IMI, 8 buffer clusters of 233-545 cells after 351
#                   one-cell ROI elements.
# (path, n_elements, first buffer id, n_buffers, ROI cells, min gap ratio)
SAMPLE_STATE_VECTORS = [
    (SAMPLES / "single_period" / "netCDF" / "state_vector.nc", 96, 64, 33, 63, 2.0),
    *[
        (SAMPLES / "timeseries" / f"period_{n}_plots" / "netCDF" / "state_vector.nc",
         359, 352, 8, 351, 100.0)
        for n in (1, 2, 4)
    ],
]


@pytest.mark.parametrize(
    "sv_path,n_elements,first_buffer,n_buffers,roi_cells,min_gap",
    SAMPLE_STATE_VECTORS,
    ids=lambda v: v.parent.parent.name if isinstance(v, Path) else None,
)
def test_sample_inversions_buffer_split(sv_path, n_elements, first_buffer, n_buffers, roi_cells, min_gap):
    """Buffers are the contiguous high-index tail of the element numbers and
    enclose the ROI, in both IMI buffer schemes."""
    import xarray as xr

    with xr.open_dataset(sv_path) as ds:
        grid = grid_from_dataset(ds)
    info = read_buffer_regions(sv_path, grid)

    assert info is not None
    assert info.n_elements == n_elements
    assert info.buffer_ids == list(range(first_buffer, n_elements + 1))
    assert info.n_buffers == n_buffers
    assert info.roi_ids == list(range(1, first_buffer))
    assert int(info.roi_mask.sum()) == roi_cells
    assert info.roi_enclosed
    assert info.size_gap_ratio >= min_gap
    # buffer and ROI partition the run with no overlap
    assert not np.any(info.buffer_mask & info.roi_mask)


def test_missing_file_returns_none():
    assert read_buffer_regions(None, grid=None) is None  # type: ignore[arg-type]


def test_clean_toy_state_vector():
    # 10x10: a 2-cell-thick buffer frame split into 4 large edge clusters, with
    # single-cell ROI elements filling the 6x6 interior (the older IMI scheme).
    sv = np.zeros((10, 10), dtype=float)
    nxt = 1
    for r in range(2, 8):                 # 6x6 interior = single-cell ROI ids 1..36
        for c in range(2, 8):
            sv[r, c] = nxt
            nxt += 1
    sv[:2, :] = 37        # top band; buffers are appended after the ROI (IMI order)
    sv[-2:, :] = 38       # bottom band
    sv[2:-2, :2] = 39     # left band (interior rows only)
    sv[2:-2, -2:] = 40    # right band
    info = identify_buffer_elements(sv)
    assert info.buffer_ids == [37, 38, 39, 40]
    assert info.roi_enclosed
    assert info.size_gap_ratio == 12.0    # smallest band (12 cells) / 1-cell ROI
    assert len(info.roi_ids) == 36


def test_ring_of_small_buffer_elements():
    # IMI 2.3.x style: a 1-cell-wide ring of 2-cell buffer elements around a
    # 4x4 block of single-cell ROI elements. The size step is only 2x.
    sv = np.zeros((6, 6), dtype=float)
    sv[1:5, 1:5] = np.arange(1, 17).reshape(4, 4)
    ring = [(0, c) for c in range(6)] + [(r, 5) for r in range(1, 6)] \
        + [(5, c) for c in range(4, -1, -1)] + [(r, 0) for r in range(4, 0, -1)]
    for k, (r, c) in enumerate(ring):     # 20 ring cells -> elements 17..26
        sv[r, c] = 17 + k // 2
    info = identify_buffer_elements(sv)
    assert info.buffer_ids == list(range(17, 27))
    assert info.roi_ids == list(range(1, 17))
    assert info.size_gap_ratio == 2.0
    assert info.roi_enclosed


def test_no_gap_warns_and_reports_zero_buffers():
    flat = np.arange(1, 17, dtype=float).reshape(4, 4)  # all single-cell
    with pytest.warns(UserWarning, match="no buffer elements detected"):
        info = identify_buffer_elements(flat)
    assert info.buffer_ids == []
    assert info.n_buffers == 0
    assert info.roi_mask.sum() == 16


def test_large_elements_not_at_the_tail_are_not_buffers():
    # Large clusters numbered *first* and small elements last: IMI never
    # orders buffers like that, so there is no buffer tail.
    sv = np.zeros((1, 32), dtype=float)
    for e in range(1, 4):
        sv[0, (e - 1) * 10:e * 10] = e    # elements 1-3: 10 cells each
    sv[0, 30], sv[0, 31] = 4, 5           # elements 4-5: 1 cell each
    with pytest.warns(UserWarning, match="no buffer elements detected"):
        info = identify_buffer_elements(sv)
    assert info.buffer_ids == []


def test_tail_that_does_not_enclose_the_roi_is_rejected():
    # A size step at the tail, but the "ROI" reaches the edge of the run area,
    # so the tail cannot be a buffer ring around it.
    sv = np.zeros((4, 6), dtype=float)
    sv[:, :3] = np.arange(1, 13).reshape(4, 3)   # 12 single-cell elements, left half
    sv[:2, 3:] = 13                              # two 6-cell elements, right half
    sv[2:, 3:] = 14
    with pytest.warns(UserWarning, match="no buffer elements detected"):
        info = identify_buffer_elements(sv)
    assert info.buffer_ids == []
    assert not info.roi_enclosed


# ── End-to-end: region selection drops buffers from the dashboard ──────────


def _convert(out: Path, region: str, input_folder: Path = SINGLE) -> dict:
    cfg = load_config(EXAMPLE_CONFIG)
    convert(input_folder=input_folder, output_folder=out, cfg=cfg, region=region)
    return json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))


def test_region_roi_drops_buffers_and_crops(tmp_path):
    """`region=roi` must exclude buffer cells and crop tighter than `domain`."""
    dom = _convert(tmp_path / "dom", "domain")
    roi = _convert(tmp_path / "roi", "roi")

    assert dom["region"] == "domain"
    assert roi["region"] == "roi"

    dom_cells = dom["grid"]["rows"] * dom["grid"]["cols"]
    roi_cells = roi["grid"]["rows"] * roi["grid"]["cols"]
    assert roi_cells < dom_cells          # cropped to the region of interest

    dom_mask = np.fromfile(tmp_path / "dom" / "data" / "globals.bin", dtype=np.uint8)
    roi_mask = np.fromfile(tmp_path / "roi" / "data" / "globals.bin", dtype=np.uint8)
    # The Permian ROI is a 9x7 block of 63 single-cell elements; the domain
    # adds the 132 cells of the three buffer rings.
    assert (roi["grid"]["rows"], roi["grid"]["cols"]) == (9, 7)
    assert int(roi_mask.sum()) == 63
    assert int(dom_mask.sum()) == 63 + 132


def test_domain_keeps_full_grid(tmp_path):
    """`region=domain` keeps every cell of the inversion grid."""
    m = _convert(tmp_path / "out", "domain")
    assert m["region"] == "domain"
    # full Permian sample grid: 23 latitudes x 21 longitudes
    assert m["grid"]["rows"] == 23 and m["grid"]["cols"] == 21


def test_region_default_is_roi(tmp_path):
    """With no region flag and no `region:` key the dashboard is cropped to
    the region of interest."""
    from imi_dashboard.config import Config

    out = tmp_path / "out"
    convert(input_folder=SINGLE, output_folder=out, cfg=Config())
    m = json.loads((out / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert m["region"] == "roi"
    assert (m["grid"]["rows"], m["grid"]["cols"]) == (9, 7)


@pytest.mark.parametrize("input_folder", [SINGLE, TIMESERIES], ids=["single", "timeseries"])
def test_roi_emission_bounds_come_from_roi_cells_only(tmp_path, input_folder):
    """In ROI mode each sector's colorbar minimum is the minimum posterior
    emission over ROI cells only, across all periods.

    Regression for the "buffer leaks into colorbar bounds" bug: an irregular
    ROI fills only part of its bounding box (the timeseries sample's 351 ROI
    cells fill 67% of an 18 x 29 box), so buffer cells used to survive inside
    the crop rectangle. Buffer cells carry large negative posterior values
    (over 1,000 cells per sector in the timeseries sample's first period), so
    a leak would pull the minimum far below the ROI's own. The ROI minimum
    itself may be negative: the timeseries run allows negative scale factors,
    and three ROI cells of its first period have them.
    Run on both samples, one per IMI buffer scheme."""
    from imi_dashboard.discover import discover
    from imi_dashboard.pack import EMIS_KG_M2_S_TO_KG_KM2_H
    from imi_dashboard.readers import open_emissions_grid, read_emissions

    m = _convert(tmp_path / "roi", "roi", input_folder)
    cfg = load_config(EXAMPLE_CONFIG)
    periods = discover(input_folder).periods
    grid = open_emissions_grid(periods[0].netcdf_dir / "posterior.nc")
    roi = read_buffer_regions(periods[0].netcdf_dir / "state_vector.nc", grid).roi_mask

    for sector in cfg.sectors:
        var = sector.imi_var
        roi_vals, all_vals = [], []
        for p in periods:
            fields = read_emissions(p.netcdf_dir / "posterior.nc", grid, [var])
            if var not in fields:
                break
            a = fields[var] * EMIS_KG_M2_S_TO_KG_KM2_H
            roi_vals.append(a[roi & np.isfinite(a)])
            all_vals.append(a[np.isfinite(a)])
        if not roi_vals:
            continue
        roi_min = float(np.concatenate(roi_vals).min())
        got = m["sectorBounds"][sector.id]["min"]
        assert got == pytest.approx(roi_min, rel=1e-5, abs=1e-12), sector.id
        assert got >= float(np.concatenate(all_vals).min())


def test_max_percentile_caps_colorbar_max(tmp_path):
    """A max_percentile below 100 clips the colorbar top under the raw global
    maximum, so a lone hot cell can't stretch the whole linear scale."""
    cfg_pct = load_config(EXAMPLE_CONFIG)            # ships max_percentile: 99.5
    assert cfg_pct.map.max_percentile < 100
    cfg_max = load_config(EXAMPLE_CONFIG)
    cfg_max.map.max_percentile = 100.0

    out_pct, out_max = tmp_path / "pct", tmp_path / "max"
    convert(input_folder=SINGLE, output_folder=out_pct, cfg=cfg_pct, region="roi")
    convert(input_folder=SINGLE, output_folder=out_max, cfg=cfg_max, region="roi")
    b_pct = json.loads((out_pct / "data" / "manifest.json").read_text())["sectorBounds"]
    b_max = json.loads((out_max / "data" / "manifest.json").read_text())["sectorBounds"]

    # Percentile max never exceeds the true max, and is strictly lower for at
    # least one populated sector (the sample has long-tailed emission fields).
    assert all(b_pct[s]["max"] <= b_max[s]["max"] + 1e-9 for s in b_pct)
    assert any(b_pct[s]["max"] < b_max[s]["max"] for s in b_pct)
