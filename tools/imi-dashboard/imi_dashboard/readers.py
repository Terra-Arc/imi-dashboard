"""xarray wrappers for IMI netCDF + CSV outputs.

Each function loads exactly one file and returns numpy arrays / dicts. No
config logic lives here — that's in pack.py.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from .grid import GridMeta, align_to_grid, grid_from_dataset


@dataclass
class PeriodFiles:
    """Resolved netCDF paths for one period. Missing files are None."""
    posterior: Path
    prior: Path
    scale: Path | None
    avkern: Path | None
    obs_regridded: Path | None
    state_vector: Path | None
    viz_csv: Path | None

    @classmethod
    def discover(cls, netcdf_dir: Path) -> "PeriodFiles":
        period_root = netcdf_dir.parent
        def maybe(name: str, root: Path = netcdf_dir) -> Path | None:
            p = root / name
            return p if p.exists() else None
        posterior = netcdf_dir / "posterior.nc"
        prior = netcdf_dir / "prior.nc"
        if not posterior.exists():
            raise FileNotFoundError(f"missing required posterior.nc in {netcdf_dir}")
        if not prior.exists():
            raise FileNotFoundError(f"missing required prior.nc in {netcdf_dir}")
        return cls(
            posterior=posterior,
            prior=prior,
            scale=maybe("scale.nc"),
            avkern=maybe("avkern.nc"),
            obs_regridded=maybe("obs_ds_regridded.nc"),
            state_vector=maybe("state_vector.nc"),
            viz_csv=maybe("viz_notebook_statistics.csv", root=period_root),
        )


def open_emissions_grid(posterior_path: Path) -> GridMeta:
    with xr.open_dataset(posterior_path) as ds:
        return grid_from_dataset(ds)


# The total emission, with and without the soil sink: the methane taken up
# (oxidised) by soils, which the IMI stores as negative emissions in
# EmisCH4_SoilAbsorb.
TOTAL_VAR = "EmisCH4_Total"
TOTAL_EXCL_SOIL_SINK_VAR = "EmisCH4_Total_ExclSoilAbs"


def read_emissions(path: Path, grid: GridMeta, sector_vars: list[str]) -> dict[str, np.ndarray]:
    """Read emission rasters for a list of netCDF variable names.

    Returns a dict mapping variable name → ``np.float32`` array shape (rows, cols).
    Missing variables are skipped (logged via the returned dict's omission).
    """
    out: dict[str, np.ndarray] = {}
    with xr.open_dataset(path) as ds:
        for v in sector_vars:
            if v not in ds.variables:
                continue
            arr = align_to_grid(ds[v], grid)
            out[v] = arr
    return out


def read_area(posterior_path: Path, grid: GridMeta) -> np.ndarray | None:
    """Return cell areas (m²) on the emissions grid, or None if AREA absent."""
    with xr.open_dataset(posterior_path) as ds:
        if "AREA" not in ds.variables:
            return None
        return align_to_grid(ds["AREA"], grid)


def read_scalar_field(path: Path | None, var: str, grid: GridMeta) -> np.ndarray | None:
    if path is None:
        return None
    with xr.open_dataset(path) as ds:
        if var not in ds.variables:
            return None
        return align_to_grid(ds[var], grid)


def read_state_vector_mask(path: Path | None, grid: GridMeta) -> np.ndarray | None:
    """Land/run mask from state_vector.nc — nonzero cells are "in the run"."""
    if path is None:
        return None
    with xr.open_dataset(path) as ds:
        if "StateVector" not in ds.variables:
            return None
        arr = align_to_grid(ds["StateVector"], grid)
    return np.where(np.isfinite(arr) & (arr > 0), 1, 0).astype(np.uint8)


# An IMI state vector mixes two kinds of element. The region-of-interest (ROI)
# elements sit at (near-)native resolution, so each maps to only a few grid
# cells. The **buffer** elements tile the domain *around* the ROI to absorb
# emissions transported in from outside; each aggregates several native cells.
# No IMI output labels which elements are which, so they are told apart from
# the state vector itself, using two properties that hold across IMI versions:
#
# 1. IMI numbers the ROI elements first and appends the buffers, so the buffers
#    are a contiguous tail of the element numbers, and every buffer element is
#    larger (in cells) than every ROI element. Older releases build a handful of
#    large buffer clusters (``nBufferClusters``, default 8, hundreds of cells
#    each); 2.3.x builds concentric rings of small elements (``BufferRings`` /
#    ``BufferReductionFactor``, a few cells each).
# 2. The buffers enclose the ROI, so no ROI cell lies on the outer edge of the
#    run area.
#
# MIN_BUFFER_SIZE_GAP_RATIO is the minimum of (smallest tail element) /
# (largest preceding element). The tightest real case seen is IMI 2.3.1's
# Permian quick-start: 2-cell buffer elements after 1-cell ROI elements, a
# ratio of 2.0 (clustered-buffer runs show 20-250). 1.5 keeps a margin below
# that while still demanding a real step in size; the enclosure test is the
# main guard against a false split.
MIN_BUFFER_SIZE_GAP_RATIO = 1.5


@dataclass
class BufferRegions:
    """Buffer/ROI split of an IMI state vector, aligned to the reference grid."""

    n_elements: int                 # total state-vector elements
    buffer_ids: list[int]           # element labels classified as buffer
    roi_ids: list[int]              # element labels classified as ROI
    buffer_mask: np.ndarray         # (rows, cols) bool: True where a cell is buffer
    roi_mask: np.ndarray            # (rows, cols) bool: True where a cell is ROI
    size_gap_ratio: float           # smallest buffer element / largest ROI element (cells); 0 if none
    roi_enclosed: bool              # no ROI cell on the outer edge of the run area

    @property
    def n_buffers(self) -> int:
        return len(self.buffer_ids)


def identify_buffer_elements(
    state_vector: np.ndarray,
    *,
    min_gap_ratio: float = MIN_BUFFER_SIZE_GAP_RATIO,
) -> BufferRegions:
    """Classify state-vector elements into buffer vs ROI from a 2-D label grid.

    ``state_vector`` is a ``(rows, cols)`` array of integer element labels
    (NaN / non-positive outside the run). For every possible split of the
    element numbers into a head (ROI candidates) and a tail (buffer
    candidates), compute ratio = min(tail sizes) / max(head sizes). A split is
    accepted when ratio >= ``min_gap_ratio`` and the head's cells do not touch
    the edge of the bounding box of the run area; of the accepted splits the one
    with the largest ratio wins. If none is accepted, there are no buffers and a
    warning is issued (the whole run is then treated as ROI).
    """
    sv = np.asarray(state_vector, dtype=float)
    if sv.ndim != 2:
        raise ValueError(f"expected a 2-D (rows, cols) state vector, got shape {sv.shape}")

    valid = np.isfinite(sv) & (sv > 0)
    ids = np.unique(sv[valid]).astype(int)
    n_elements = len(ids)
    labels = np.where(valid, sv, 0).astype(int)
    counts = np.bincount(labels.ravel())[ids] if n_elements else np.array([], int)

    edge = np.zeros(sv.shape, bool)
    if valid.any():
        r, c = np.where(valid)
        r0, r1, c0, c1 = r.min(), r.max(), c.min(), c.max()
        edge[[r0, r1], c0:c1 + 1] = True
        edge[r0:r1 + 1, [c0, c1]] = True
        edge &= valid
    # Elements with at least one cell on the run-area edge.
    on_edge = np.zeros(n_elements, bool)
    if n_elements:
        on_edge = np.isin(ids, np.unique(labels[edge]))

    best_t, best_ratio = None, 0.0
    if n_elements >= 2:
        head_max = np.maximum.accumulate(counts)[:-1]            # split t: head = ids[:t]
        tail_min = np.minimum.accumulate(counts[::-1])[::-1][1:]
        head_on_edge = np.logical_or.accumulate(on_edge)[:-1]
        ratios = tail_min / head_max
        ok = (ratios >= min_gap_ratio) & ~head_on_edge
        if ok.any():
            t = int(np.argmax(np.where(ok, ratios, -np.inf))) + 1
            best_t, best_ratio = t, float(ratios[t - 1])

    buffer_ids = [int(e) for e in ids[best_t:]] if best_t is not None else []
    roi_ids = [int(e) for e in ids[:best_t]] if best_t is not None else [int(e) for e in ids]
    buffer_mask = np.isin(labels, buffer_ids) if buffer_ids else np.zeros(sv.shape, bool)
    roi_mask = valid & ~buffer_mask
    roi_enclosed = bool(roi_mask.any() and not (roi_mask & edge).any())

    if not buffer_ids:
        warnings.warn(
            "no buffer elements detected: no tail of the state-vector element "
            f"numbers is >= {min_gap_ratio}x larger than the elements before it "
            "while enclosing them — the whole domain will be treated as ROI",
            stacklevel=2,
        )

    return BufferRegions(
        n_elements=n_elements,
        buffer_ids=buffer_ids,
        roi_ids=roi_ids,
        buffer_mask=buffer_mask,
        roi_mask=roi_mask,
        size_gap_ratio=best_ratio,
        roi_enclosed=roi_enclosed,
    )


def read_buffer_regions(path: Path | None, grid: GridMeta) -> BufferRegions | None:
    """Buffer/ROI split from state_vector.nc, aligned to the reference grid.

    Returns ``None`` if the file is missing or has no ``StateVector`` variable.
    """
    if path is None:
        return None
    with xr.open_dataset(path) as ds:
        if "StateVector" not in ds.variables:
            return None
        arr = align_to_grid(ds["StateVector"], grid)
    return identify_buffer_elements(arr)


def read_obs_count(obs_path: Path | None, grid: GridMeta) -> np.ndarray | None:
    return read_scalar_field(obs_path, "observation_count", grid)


# The observed-column variables in obs_ds_regridded.nc are named after the
# instrument. Current IMI releases call it "satellite" (``satellite``,
# ``diff_satellite_prior``, ...); older releases wrote "tropomi" (``tropomi``,
# ``diff_tropomi_prior``, ...). Try the current name first.
SATELLITE_VAR_STEMS = ("satellite", "tropomi")


def _satellite_stem(ds: xr.Dataset) -> str | None:
    for stem in SATELLITE_VAR_STEMS:
        if stem in ds.variables:
            return stem
    return None


def read_satellite_xch4(obs_path: Path | None, grid: GridMeta) -> np.ndarray | None:
    """Period-averaged satellite XCH4 (ppb) from the regridded observation dataset.

    Cells without observations hold 0 in the source file; a 0 ppb column is
    physically impossible, so non-positive cells are mapped to NaN ("no data")
    rather than letting them drag the colorscale floor to zero."""
    if obs_path is None:
        return None
    with xr.open_dataset(obs_path) as ds:
        stem = _satellite_stem(ds)
        if stem is None:
            return None
        arr = align_to_grid(ds[stem], grid)
    return np.where(arr > 0, arr, np.nan)


def read_delta_xch4(obs_path: Path | None, grid: GridMeta) -> np.ndarray | None:
    """Posterior−prior ΔXCH4 from regridded observation dataset.

    Cells without super-observations hold 0 in every variable of the source
    file, so their difference is exactly 0, which would read as "no change".
    They are mapped to NaN ("no data") using ``observation_count``."""
    if obs_path is None:
        return None
    with xr.open_dataset(obs_path) as ds:
        for stem in SATELLITE_VAR_STEMS:
            post, prior = f"diff_{stem}_posterior", f"diff_{stem}_prior"
            if {post, prior} <= set(ds.variables):
                delta = align_to_grid(ds[post] - ds[prior], grid)
                if "observation_count" in ds.variables:
                    count = align_to_grid(ds["observation_count"], grid)
                    delta = np.where(count > 0, delta, np.nan).astype(np.float32)
                return delta
    return None


class UnreadableFileWarning(UserWarning):
    """An optional input file exists but cannot be read, so it is treated
    as missing."""


def short_error(exc: BaseException) -> str:
    """The first line of an exception's message, with xarray's long "no
    backend matched" text (a file that is empty or not netCDF at all)
    replaced by a short one."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    if "did not find a match in any of xarray's currently installed IO backends" in text:
        return "not a netCDF file"
    return text


def netcdf_read_problem(path: Path) -> str | None:
    """Why ``path`` cannot be read as a netCDF file, or None if it can.

    Opens the file and loads every variable, which is what reading it for a
    dashboard does: a file truncated while IMI was still writing it fails
    here (netCDF4 reports an ``HDF error``), as does an empty file or one
    that is not netCDF."""
    try:
        with xr.open_dataset(path) as ds:
            ds.load()
    except (OSError, ValueError, RuntimeError) as exc:
        return short_error(exc)
    return None


def _parse_imi_date(s: str | int) -> date:
    return datetime.strptime(str(int(s)), "%Y%m%d").date()


def read_viz_stats(csv_path: Path | None) -> dict | None:
    """Parse one row of viz_notebook_statistics.csv into a flat dict.

    Returns None if the file is missing, empty or cannot be parsed as a CSV
    table; the last two with an ``UnreadableFileWarning`` naming the file,
    since the period then has no dates, totals or scalar diagnostics.
    Numeric values stay floats; the date columns are converted to ISO
    ``YYYY-MM-DD`` strings, a date that is not of IMI's form ``YYYYMMDD``
    to None with a warning, and empty cells of any column to None. Unknown
    columns are passed through untouched so the converter can
    opportunistically pick up new fields IMI might add.
    """
    if csv_path is None or not csv_path.exists():
        return None
    try:
        df = pd.read_csv(csv_path)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        warnings.warn(
            f"{csv_path} cannot be read as a CSV table ({short_error(exc)}), so "
            "the dashboard shows no dates, emission totals or scalar "
            "diagnostics for its period",
            UnreadableFileWarning,
            stacklevel=2,
        )
        return None
    if df.empty:
        return None
    row = df.iloc[0].to_dict()
    out: dict = {}
    for k, v in row.items():
        if k in ("StartDate", "EndDate"):
            if pd.isna(v):
                out[k] = None   # an empty cell, not the string "nan"
                continue
            try:
                out[k] = _parse_imi_date(v).isoformat()
            except (TypeError, ValueError):
                warnings.warn(
                    f"{k} {v!r} in {csv_path} is not a date of the form "
                    "YYYYMMDD, so its period is shown without it",
                    UnreadableFileWarning,
                    stacklevel=2,
                )
                out[k] = None
        elif isinstance(v, float) and np.isnan(v):
            out[k] = None
        elif isinstance(v, (np.integer, np.floating)):
            out[k] = float(v) if isinstance(v, np.floating) else int(v)
        else:
            out[k] = v
    return out


def csv_number(row: dict | None, key: str) -> float | None:
    """A column of a parsed viz CSV row (``read_viz_stats``) as a finite
    float; None when there is no row, no such column, or the cell is empty,
    not a number, NaN or infinite (the manifest is strict JSON)."""
    if row is None:
        return None
    v = row.get(key)
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None
