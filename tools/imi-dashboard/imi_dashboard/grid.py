"""Grid metadata and alignment helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import xarray as xr


@dataclass(frozen=True)
class GridMeta:
    """Metadata for the deployment's reference grid.

    All grid figures are read from the IMI inversion's emissions netCDF
    (e.g. ``posterior.nc``) — they are never hard-coded.
    """
    cols: int                       # number of lon cells
    rows: int                       # number of lat cells
    lat: np.ndarray                 # 1-D lat values, length rows
    lon: np.ndarray                 # 1-D lon values, length cols
    dlat: float                     # grid spacing in degrees lat
    dlon: float                     # grid spacing in degrees lon

    @property
    def lat_min(self) -> float: return float(self.lat[0])

    @property
    def lat_max(self) -> float: return float(self.lat[-1])

    @property
    def lon_min(self) -> float: return float(self.lon[0])

    @property
    def lon_max(self) -> float: return float(self.lon[-1])

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """[west, south, east, north] — MapLibre's fitBounds order."""
        return (self.lon_min, self.lat_min, self.lon_max, self.lat_max)

    def to_dict(self) -> dict:
        return {
            "cols": self.cols,
            "rows": self.rows,
            "lat": self.lat.tolist(),
            "lon": self.lon.tolist(),
            "dlat": self.dlat,
            "dlon": self.dlon,
            "bbox": list(self.bbox),
        }


def grid_from_dataset(ds: xr.Dataset) -> GridMeta:
    """Extract grid metadata from a dataset that has ``lat`` and ``lon`` coords.

    The coordinates are sorted ascending, so row 0 is the southernmost row
    and column 0 the westernmost whatever order the file stores them in;
    :func:`align_to_grid` reorders each variable to match."""
    if "lat" not in ds.coords or "lon" not in ds.coords:
        raise ValueError("dataset is missing required lat/lon coordinate variables")
    lat = np.sort(np.asarray(ds["lat"].values, dtype=np.float64))
    lon = np.sort(np.asarray(ds["lon"].values, dtype=np.float64))
    dlat = float(np.median(np.diff(lat))) if lat.size > 1 else 0.0
    dlon = float(np.median(np.diff(lon))) if lon.size > 1 else 0.0
    return GridMeta(
        cols=lon.size,
        rows=lat.size,
        lat=lat,
        lon=lon,
        dlat=dlat,
        dlon=dlon,
    )


def align_to_grid(da: xr.DataArray, grid: GridMeta) -> np.ndarray:
    """Reindex a DataArray onto the reference grid, as a (lat, lon) array.

    Uses ``method='nearest'`` so a smaller-extent grid (e.g. obs_ds_regridded)
    aligns to the matching cells of the emissions grid; cells outside the
    source's extent become NaN. Size-1 dimensions other than lat/lon (such
    as a single time step) are dropped, and the result is transposed to
    (lat, lon) whatever the variable's own dimension order; reindexing onto
    the ascending grid coordinates also reverses a descending latitude.
    """
    extra = [d for d in da.dims if d not in ("lat", "lon")]
    if any(da.sizes[d] != 1 for d in extra) or not {"lat", "lon"} <= set(da.dims):
        raise ValueError(
            f"variable {da.name!r} has dimensions {dict(da.sizes)}; "
            "expected lat and lon plus, at most, dimensions of size 1"
        )
    aligned = da.squeeze(extra, drop=True).transpose("lat", "lon").reindex(
        lat=grid.lat,
        lon=grid.lon,
        method="nearest",
        tolerance=max(abs(grid.dlat), abs(grid.dlon)) * 0.51,
    )
    return np.asarray(aligned.values, dtype=np.float32)


def mask_index_bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    """Tightest (r0, r1, c0, c1) cell-index bbox enclosing non-zero mask cells.

    ``r0..r1`` and ``c0..c1`` are inclusive. Returns ``None`` if the mask is
    empty."""
    rows_with_data = np.where(mask.any(axis=1))[0]
    cols_with_data = np.where(mask.any(axis=0))[0]
    if rows_with_data.size == 0 or cols_with_data.size == 0:
        return None
    return (
        int(rows_with_data[0]),
        int(rows_with_data[-1]),
        int(cols_with_data[0]),
        int(cols_with_data[-1]),
    )
