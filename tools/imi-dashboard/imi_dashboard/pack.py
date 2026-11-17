"""Top-level converter pipeline.

Reads IMI outputs from an input folder + an optional YAML config, writes a complete
static site folder (data + bundled web app) to an output folder.

Output layout:
    <out>/
        index.html
        README.md                        # for whoever receives the dashboard (templates/README.md)
        assets/
            ...                          # copied from the prebuilt Vite bundle
        data/
            config.json                  # resolved branding + sectors + text
            manifest.json                # provenance, grid metadata, per-period scalars + offsets
            globals.bin                  # uint8 land/run mask
            periods/
                0000.bin
                0001.bin
                ...
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import warnings
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import numpy as np
import xarray as xr

from . import __version__

# Per-period binary layout: each sector, then each FIELD_LAYOUT_DIAGNOSTICS
# entry, becomes one Float32 block in periods/<idx>.bin. Order matters and is
# mirrored in manifest.fields.
from .config import FIELD_LAYOUT_DIAGNOSTICS, Config, SectorConfig
from .defaults import fill_defaults_from_data, imi_var_to_csv_stem, total_definition
from .discover import REQUIRED_FILES, Discovery, PeriodInput, describe_skipped, discover, split_incomplete, split_unreadable
from .grid import GridMeta, mask_index_bbox
from .readers import (
    TOTAL_EXCL_SOIL_SINK_VAR,
    TOTAL_VAR,
    PeriodFiles,
    UnreadableFileWarning,
    netcdf_read_problem,
    open_emissions_grid,
    read_area,
    read_buffer_regions,
    read_delta_xch4,
    read_emissions,
    read_obs_count,
    read_satellite_xch4,
    read_scalar_field,
    read_state_vector_mask,
    read_viz_stats,
)
from .readers import csv_number as _scalar
from .totals import TOTAL_NUMBERS, HeadlineTotals, TotalNumbers, headline_totals, report_totals, total_numbers

log = logging.getLogger(__name__)

# Called as on_period(done, total, label) once per period, with `done`
# running 1…total, so a caller (the command line's progress bar) can follow
# a conversion without parsing the log.
PeriodCallback = Callable[[int, int, str], None]


# IMI ships gridded emissions as kg m⁻² s⁻¹. The dashboard's map caption
# (matching the design) reports kg CH₄ km⁻² h⁻¹, so the converter rescales
# every gridded emission field once on the way out. Per-sector totals from
# the viz CSV are already in Mt/yr and are not touched.
EMIS_KG_M2_S_TO_KG_KM2_H = 1e6 * 3600.0  # = 3.6e9

# The posterior − prior emissions map is the difference of the totals
# excluding the soil sink (EmisCH4_Total when a file cannot give that), like
# the total's numbers (totals.py). Unless the run optimised the soil sink
# (OptimizeSoil), the IMI keeps the sink the same in prior.nc and
# posterior.nc, so this is then also the difference of the totals including
# it. EmisCH4_Total itself will not do for older IMI outputs: their
# prior.nc's EmisCH4_Total leaves the sink out while their posterior.nc's
# includes it.
DELTA_EMISSIONS_VAR = TOTAL_EXCL_SOIL_SINK_VAR


@dataclass
class PeriodPayload:
    """Everything one period contributes to the output."""
    idx: int
    number: int
    label: str
    start: str | None
    end: str | None
    sector_fields: dict[str, np.ndarray]   # sector_id -> field on the emissions grid
    diag_fields: dict[str, np.ndarray | None]
    scalars: dict[str, dict[str, float | None]]   # sector_id -> {prior, posterior, ens_min, ens_max}
    diag_scalars: dict[str, float | None]
    # The total's numbers and how the CSV's headline totals relate to them,
    # for report_totals (not written out).
    totals: TotalNumbers | None = None
    headline: HeadlineTotals | None = None


def _report_totals(payloads: list[PeriodPayload]) -> None:
    """totals.report_totals for the periods just converted."""
    report_totals([(p.label, p.totals, p.headline) for p in payloads if p.totals is not None])


def _build_period(
    period: PeriodInput,
    cfg: Config,
    grid: GridMeta,
    area_m2: np.ndarray | None,
) -> PeriodPayload:
    files = PeriodFiles.discover(period.netcdf_dir)
    # The optional netCDF files only fill diagnostic maps: one that cannot
    # be read (truncated, empty) is treated as missing, with a warning,
    # rather than stopping the conversion. posterior.nc and prior.nc were
    # checked by convert before any period was read (_unreadable_problem).
    for attr, what in (("scale", "scale factor"), ("avkern", "averaging kernel"),
                       ("obs_regridded", "satellite observation")):
        path = getattr(files, attr)
        problem = netcdf_read_problem(path) if path is not None else None
        if problem is not None:
            warnings.warn(
                f"{path} cannot be read ({problem}), so {period.label} has no "
                f"{what} maps",
                UnreadableFileWarning,
                stacklevel=2,
            )
            setattr(files, attr, None)
    # ensemble_statistics.csv is not read: the ensemble range (ens_min and
    # ens_max below) comes from the ensemble columns of this CSV.
    viz = read_viz_stats(files.viz_csv)

    # The totals are always read, listed or not: the posterior − prior
    # emissions map (delta_emissions) is computed from them.
    requested_vars = [s.imi_var for s in cfg.sectors if s.imi_var]
    read_vars = list(dict.fromkeys(requested_vars + [DELTA_EMISSIONS_VAR, TOTAL_VAR]))
    post_fields_raw = read_emissions(files.posterior, grid, read_vars)
    prior_fields_raw = read_emissions(files.prior, grid, read_vars)
    # Convert kg m⁻² s⁻¹ → kg km⁻² h⁻¹ once. The map colorbar reports the
    # latter; the unit conversion belongs here so the binary blob the
    # frontend reads is already in display units.
    post_fields = {k: (v * EMIS_KG_M2_S_TO_KG_KM2_H).astype(np.float32) for k, v in post_fields_raw.items()}
    prior_fields = {k: (v * EMIS_KG_M2_S_TO_KG_KM2_H).astype(np.float32) for k, v in prior_fields_raw.items()}

    # The total's numbers, always without the soil sink (totals.py),
    # whichever of the two total variables its map shows.
    totals = total_numbers(viz, files, grid, area_m2)
    sector_fields: dict[str, np.ndarray] = {}
    scalars: dict[str, dict[str, float | None]] = {}
    for s in cfg.sectors:
        assert s.imi_var is not None  # enforced by validator
        if s.imi_var in post_fields:
            sector_fields[s.id] = post_fields[s.imi_var]
        stem = imi_var_to_csv_stem(s.imi_var)
        if s.imi_var in (TOTAL_VAR, TOTAL_EXCL_SOIL_SINK_VAR):
            scalars[s.id] = {
                "prior": totals.prior,
                "posterior": totals.posterior,
                "ens_min": totals.ens_min,
                "ens_max": totals.ens_max,
            }
        else:
            scalars[s.id] = {
                "prior": _scalar(viz, f"{stem}Prior"),
                "posterior": _scalar(viz, f"{stem}Posterior"),
                "ens_min": _scalar(viz, f"{stem}_ensMin"),
                "ens_max": _scalar(viz, f"{stem}_ensMax"),
            }

    kernel = read_scalar_field(files.avkern, "A", grid)
    scale = read_scalar_field(files.scale, "ScaleFactor", grid)
    delta_emis = None
    delta_var = next((v for v in (DELTA_EMISSIONS_VAR, TOTAL_VAR) if v in post_fields and v in prior_fields), None)
    if delta_var is not None:
        # Already in kg km⁻² h⁻¹ (both operands converted above).
        delta_emis = (post_fields[delta_var] - prior_fields[delta_var]).astype(np.float32)
    delta_xch4 = read_delta_xch4(files.obs_regridded, grid)
    obs_count = read_obs_count(files.obs_regridded, grid)
    tropomi_xch4 = read_satellite_xch4(files.obs_regridded, grid)

    # Average kernel scalar — mean over cells that have a value.
    avg_kernel: float | None = None
    if kernel is not None:
        finite = np.isfinite(kernel)
        if finite.any():
            avg_kernel = float(np.nanmean(kernel[finite]))

    diag_scalars: dict[str, float | None] = {
        "dofs": _scalar(viz, "DOFS"),
        "n_obs": _scalar(viz, "NumSuperObservations"),
        "bias_prior": _scalar(viz, "BiasPrior"),
        "bias_posterior": _scalar(viz, "BiasPosterior"),
        "rmse_prior": _scalar(viz, "RMSEPrior"),
        "rmse_posterior": _scalar(viz, "RMSEPosterior"),
        "avg_kernel": avg_kernel,
        "prior_mt": totals.prior,
        "posterior_mt": totals.posterior,
    }

    return PeriodPayload(
        idx=period.idx,
        number=period.number,
        label=period.label,
        start=(viz or {}).get("StartDate") if viz else None,
        end=(viz or {}).get("EndDate") if viz else None,
        sector_fields=sector_fields,
        diag_fields={
            "kernel": kernel,
            "scale": scale,
            "delta_emissions": delta_emis,
            "delta_xch4": delta_xch4,
            "obs_count": obs_count,
            "tropomi_xch4": tropomi_xch4,
        },
        scalars=scalars,
        diag_scalars=diag_scalars,
        totals=totals,
        headline=headline_totals(viz),
    )


def _sector_bound(values: np.ndarray, max_percentile: float) -> dict[str, float]:
    """Colorbar bounds for one sector from its (already masked) finite values.

    ``min`` is the true minimum; ``max`` is the ``max_percentile`` percentile so
    a lone hot cell doesn't stretch the whole linear scale. ``max_percentile``
    of 100 reproduces the plain global maximum."""
    if values.size == 0:
        return {"min": 0.0, "max": 0.0}
    lo = float(np.min(values))
    hi = float(np.percentile(values, max_percentile)) if max_percentile < 100 else float(np.max(values))
    return {"min": lo, "max": hi}


def _mask_data_bbox(mask: np.ndarray, grid: GridMeta, pad_cells: float = 0.5) -> list[float] | None:
    """Tightest [west, south, east, north] enclosing the non-zero mask cells.

    Returns ``None`` if the mask is entirely empty. ``pad_cells`` adds a half
    cell margin so cell *edges* (not centers) sit at the bbox edges, matching
    how the BitmapLayer paints each cell."""
    idx = mask_index_bbox(mask)
    if idx is None:
        return None
    r0, r1, c0, c1 = idx
    pad_lat = abs(grid.dlat) * pad_cells
    pad_lon = abs(grid.dlon) * pad_cells
    south = float(grid.lat[r0]) - pad_lat
    north = float(grid.lat[r1]) + pad_lat
    west = float(grid.lon[c0]) - pad_lon
    east = float(grid.lon[c1]) + pad_lon
    return [west, south, east, north]


def _crop_payload(payload: PeriodPayload, r0: int, r1: int, c0: int, c1: int) -> PeriodPayload:
    """Return a copy of ``payload`` with all 2-D fields cropped to the given
    (inclusive) row/col window."""
    return replace(
        payload,
        sector_fields={
            k: np.ascontiguousarray(v[r0 : r1 + 1, c0 : c1 + 1])
            for k, v in payload.sector_fields.items()
        },
        diag_fields={
            k: (None if v is None else np.ascontiguousarray(v[r0 : r1 + 1, c0 : c1 + 1]))
            for k, v in payload.diag_fields.items()
        },
    )


def _crop_grid(grid: GridMeta, r0: int, r1: int, c0: int, c1: int) -> GridMeta:
    """Return a new GridMeta describing only the (r0..r1, c0..c1) window."""
    return GridMeta(
        cols=c1 - c0 + 1,
        rows=r1 - r0 + 1,
        lat=grid.lat[r0 : r1 + 1].copy(),
        lon=grid.lon[c0 : c1 + 1].copy(),
        dlat=grid.dlat,
        dlon=grid.dlon,
    )


def _global_mask(
    payloads: list[PeriodPayload], state_vector_mask: np.ndarray | None, grid: GridMeta
) -> np.ndarray:
    """Land/run mask. Prefer state_vector.nc; fall back to "any cell that ever
    had a positive emissions value in any period" (all zero when no sector
    field was read at all).
    """
    if state_vector_mask is not None:
        return state_vector_mask
    mask = np.zeros((grid.rows, grid.cols), dtype=bool)
    for p in payloads:
        for field in p.sector_fields.values():
            mask |= np.isfinite(field) & (field > 0)
    return mask.astype(np.uint8)


def _pack_period_binary(payload: PeriodPayload, sectors: list[SectorConfig], rows: int, cols: int) -> tuple[bytes, list[dict]]:
    """Serialize one period to a single Float32 binary blob.

    Returns (bytes, fields_metadata) where fields_metadata lists each field's
    offset (in float32 elements) and length, so the frontend can carve the
    blob apart without making any grid-size assumptions.
    """
    pieces: list[np.ndarray] = []
    fields_meta: list[dict] = []
    offset_elems = 0
    cells = rows * cols

    def _emit(name: str, kind: str, arr: np.ndarray | None) -> None:
        nonlocal offset_elems
        if arr is None:
            data = np.full(cells, np.nan, dtype=np.float32)
        else:
            data = np.ascontiguousarray(arr.astype(np.float32, copy=False)).reshape(cells)
        pieces.append(data)
        fields_meta.append({
            "name": name,
            "kind": kind,
            "offset": offset_elems,
            "length": cells,
        })
        offset_elems += cells

    for s in sectors:
        _emit(s.id, "sector", payload.sector_fields.get(s.id))
    for key, _desc in FIELD_LAYOUT_DIAGNOSTICS:
        _emit(key, "diagnostic", payload.diag_fields.get(key))

    blob = np.concatenate(pieces).tobytes()
    return blob, fields_meta


# The format of data/manifest.json and the period files. The web app reads
# the same number from web/src/data/format.json (a test checks that the two
# agree) and refuses any other.
MANIFEST_VERSION = 1

# The file a web build writes into web/dist/ (web/vite.config.ts) to mark the
# folder as a built web app: its name, the manifest version it reads, and
# the files it consists of. A copy goes into every dashboard.
APP_MARKER = "imi-dashboard-app.json"
APP_NAME = "imi-dashboard-web"

# What the converter writes at the top of a dashboard folder, besides the
# web app's own files (those its marker lists). index.html and assets/ are
# the web app's (assets/ also holds the custom logo); data/ and README.md
# are written from the input. Anything else in the folder (a .git/, a CNAME
# for GitHub Pages, notes) is never touched.
OWNED_ENTRIES = ("index.html", "assets", "data", "README.md", APP_MARKER)


def _read_json(path: Path) -> object | None:
    """The parsed content of a JSON file, or None if it is unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _is_dashboard_manifest(manifest: object) -> bool:
    """Whether a parsed manifest.json was written by this converter: it names
    imi-dashboard in its provenance record."""
    if not isinstance(manifest, dict):
        return False
    prov = manifest.get("provenance")
    converter = prov.get("converter") if isinstance(prov, dict) else None
    return isinstance(converter, dict) and converter.get("name") == "imi-dashboard"


def _check_output_folder(input_folder: Path, output_folder: Path) -> None:
    """Refuse an output folder that convert must not write into.

    convert writes ``index.html``, ``assets/``, ``data/`` and ``README.md``
    into the folder and replaces them on a later run, but never touches
    anything else there. That is safe for a new or empty folder and for a
    dashboard it wrote earlier (its ``data/manifest.json`` names
    imi-dashboard as its converter), and for nothing else: not a folder
    holding other files (a web site's own index.html would be overwritten),
    and not the input folder or a folder that contains it. Checked before
    any period is read, with or without ``force``."""
    out = output_folder.resolve()
    inp = input_folder.resolve()
    if out == inp or out in inp.parents:
        raise ValueError(
            f"output folder {output_folder} is the input folder or contains it; "
            "choose a separate folder for the dashboard"
        )
    if out.exists() and not out.is_dir():
        raise ValueError(f"output folder {output_folder} exists and is not a folder")
    if not (out.is_dir() and any(out.iterdir())):
        return
    manifest = out / "data" / "manifest.json"
    if not manifest.is_file():
        raise ValueError(
            f"output folder {output_folder} is not empty and holds no dashboard "
            "(no data/manifest.json); choose a new or empty folder. If a "
            "conversion into it was interrupted before it finished, delete "
            "the folder and convert again"
        )
    if not _is_dashboard_manifest(_read_json(manifest)):
        raise ValueError(
            f"output folder {output_folder} holds a data/manifest.json that "
            "cannot be read or was not written by imi-dashboard (it does not "
            "name imi-dashboard as its converter; early development versions "
            "did not record it); choose another folder, or delete this one if "
            "it is a dashboard you can convert again"
        )


def _remove(path: Path) -> None:
    """Delete a file, a symbolic link (not its target) or a folder tree."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _clear_for_full_rebuild(out_root: Path, *, keep_app: bool) -> None:
    """Remove what a full conversion rewrites, and nothing else.

    Removes the web app (unless ``keep_app``: no new one is copied, so the
    dashboard keeps its own), ``README.md`` and everything in ``data/``
    except ``manifest.json``, which stays until the new one replaces it
    (``_write_manifest`` writes it last, in one step), so that the folder is
    still recognised as a dashboard if this run is interrupted. Entries the
    converter does not write are kept, and listed at -v."""
    out_root.mkdir(parents=True, exist_ok=True)
    if not keep_app:
        _remove_web_app(out_root)
    _remove(out_root / "README.md")
    data_dir = out_root / "data"
    if data_dir.is_dir() and not data_dir.is_symlink():
        for entry in data_dir.iterdir():
            if entry.name != "manifest.json":
                _remove(entry)
    app_top = {f.split("/", 1)[0] for f in _marker_files(out_root)}
    kept = sorted(e.name for e in out_root.iterdir() if e.name not in OWNED_ENTRIES and e.name not in app_top)
    if kept:
        log.info("kept %d entr%s of %s that imi-dashboard does not write: %s",
                 len(kept), "y" if len(kept) == 1 else "ies", out_root, ", ".join(kept))


def _load_existing_manifest(out_root: Path) -> dict | None:
    """The manifest of the dashboard already in ``out_root``, or None if
    there is none, or if it is unreadable or not of the shape this converter
    writes (a full conversion then replaces it)."""
    m = _read_json(out_root / "data" / "manifest.json")
    if not _is_dashboard_manifest(m):
        return None
    assert isinstance(m, dict)
    fields, periods = m.get("fields"), m.get("periods")
    if not (isinstance(fields, list) and all(isinstance(f, dict) and isinstance(f.get("name"), str) for f in fields)):
        return None
    if not (isinstance(periods, list) and all(isinstance(p, dict) and isinstance(p.get("label"), str) for p in periods)):
        return None
    return m


def _grid_matches(existing_grid: dict, grid: GridMeta) -> bool:
    """True when a stored manifest grid is identical to a freshly-computed one.

    Compares shape, cell size and bbox so a changed inversion domain (or a
    region/crop change) correctly forces a full rebuild rather than silently
    reusing mismatched binaries."""
    fresh = grid.to_dict()
    for key in ("rows", "cols", "dlat", "dlon"):
        if existing_grid.get(key) != fresh.get(key):
            return False
    eb = existing_grid.get("bbox")
    fb = fresh.get("bbox")
    if not eb or not fb or len(eb) != len(fb):
        return False
    return all(abs(float(a) - float(b)) <= 1e-9 for a, b in zip(eb, fb))


def _period_fingerprint(period: PeriodInput, exclude: Path | None = None) -> str:
    """Short hash of a period's input files, stored with its manifest entry.

    Hashes the relative path, size and modification time (ns) of every file
    under the period's folder. An incremental run reuses a period binary only
    when this is unchanged, so a replaced, restored, deleted or added input
    file triggers a rebuild even when its timestamp is older than the binary
    (``cp -p``, ``rsync -a`` and ``tar x`` keep the original times).

    Files under ``exclude`` (the output folder) are left out: a dashboard
    written inside a single-period input folder would otherwise change that
    period's fingerprint on every run, and the period would be reconverted
    each time."""
    skip: tuple[str, ...] | None = None
    if exclude is not None:
        try:
            skip = exclude.resolve().relative_to(period.folder.resolve()).parts
        except ValueError:
            skip = None   # the output is not inside this period's folder
    h = hashlib.sha1()
    for f in sorted(p for p in period.folder.rglob("*") if p.is_file()):
        rel_path = f.relative_to(period.folder)
        if skip and rel_path.parts[: len(skip)] == skip:
            continue
        st = f.stat()
        h.update(f"{rel_path.as_posix()}\0{st.st_size}\0{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def _source_record(input_folder: Path, cfg: Config) -> dict:
    """What produced a dashboard, stored as ``manifest["source"]``.

    ``input`` is a hash of the resolved input folder path (the path itself
    would show the converting machine's directory layout to everyone the
    dashboard is sent to); ``sectorVars`` maps each sector id to the netCDF
    variable it is read from, and ``deltaEmissionsVar`` names the variable
    the posterior − prior emissions map is the difference of; and
    ``converter`` is the release of the converter that wrote the dashboard
    (``_converter_release``), so that a release that changes how periods
    are converted (a reader or unit fix) reconverts the periods an update
    would otherwise reuse. A change in any of them forces a full rebuild."""
    return {
        "input": hashlib.sha1(str(input_folder.resolve()).encode()).hexdigest()[:16],
        "converter": _converter_release(),
        "sectorVars": {s.id: s.imi_var for s in cfg.sectors},
        "deltaEmissionsVar": DELTA_EMISSIONS_VAR,
        "totalNumbers": TOTAL_NUMBERS,
    }


def _converter_release(version: str = __version__) -> str:
    """The release part of a converter version: ``0.1.1`` for ``0.1.1`` and
    for the development builds that lead up to it (``0.1.1.dev3+g1a2b3c4``,
    as setuptools-scm numbers the commits after the ``v0.1.0`` tag). Only a
    new release changes it, so that rebuilding a development install after
    each commit does not reconvert every period; ``--rebuild`` does that."""
    m = re.match(r"\d+(?:\.\d+)*", version)
    return m.group(0) if m else version


def _utc_now() -> str:
    """The current UTC time in ISO 8601, to the whole second, with a ``Z``
    for UTC: ``2026-11-04T10:15:30Z``. A function of its own so that tests
    can fix the clock."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _provenance(input_folder: Path) -> dict:
    """What built a dashboard, stored as ``manifest["provenance"]`` for
    whoever receives it: the converter's name and version, the UTC time of
    this run (rewritten by every run, incremental ones included) and the
    input folder's name. Only the name, not the path, which would show the
    converting machine's directory layout and user names.

    The IMI version is not recorded: IMI writes it nowhere in the files the
    converter reads. ``run_imi.sh`` appends it as a comment to the copy of
    its config file in the run directory, then overwrites that copy with
    the plain config when the run ends."""
    return {
        "converter": {"name": "imi-dashboard", "version": __version__},
        "convertedAt": _utc_now(),
        "input": input_folder.resolve().name,
    }


def _read_period_fields(
    bin_path: Path, fields_meta: list[dict], names: list[str], rows: int, cols: int
) -> dict[str, np.ndarray]:
    """Decode the named fields (sector or diagnostic) back out of a period binary.

    Cheap (a flat float32 read + slicing) compared to re-reading and
    regridding the source netCDF — this is what makes incremental rebuilds
    fast: reused periods are scanned from their bins, not rebuilt."""
    flat = np.fromfile(bin_path, dtype=np.float32)
    by_name = {f["name"]: f for f in fields_meta}
    out: dict[str, np.ndarray] = {}
    for sid in names:
        f = by_name.get(sid)
        if f is None:
            continue
        seg = flat[f["offset"]: f["offset"] + f["length"]]
        if seg.size != rows * cols:
            continue
        out[sid] = seg.reshape(rows, cols)
    return out


def _scan_bins_for_mask(
    periods_dir: Path,
    entries: list[dict],
    fields_meta: list[dict],
    sector_ids: list[str],
    rows: int,
    cols: int,
) -> np.ndarray:
    """Fallback land mask: any cell that ever held a positive emission value in
    any period (used only when there's no state-vector mask to lean on)."""
    mask = np.zeros((rows, cols), dtype=bool)
    for e in entries:
        bin_path = periods_dir / Path(e["file"]).name
        fields = _read_period_fields(bin_path, fields_meta, sector_ids, rows, cols)
        for arr in fields.values():
            mask |= np.isfinite(arr) & (arr > 0)
    return mask.astype(np.uint8)


def _scan_bins_for_bounds(
    periods_dir: Path,
    entries: list[dict],
    fields_meta: list[dict],
    sector_ids: list[str],
    rows: int,
    cols: int,
    mask_bool: np.ndarray,
    max_percentile: float,
) -> dict[str, dict[str, float]]:
    """Per-sector colorbar bounds by reading every period binary in ``entries``.

    Only cells inside ``mask_bool`` (the active land/ROI mask) contribute, so
    buffer-element artifacts can't drag the bounds negative. Mirrors the
    full-conversion bounds loop in ``convert``."""
    vals_by_sector: dict[str, list[np.ndarray]] = {sid: [] for sid in sector_ids}
    for e in entries:
        bin_path = periods_dir / Path(e["file"]).name
        fields = _read_period_fields(bin_path, fields_meta, sector_ids, rows, cols)
        for sid, arr in fields.items():
            vals_by_sector[sid].append(arr[np.isfinite(arr) & mask_bool])
    bounds: dict[str, dict[str, float]] = {}
    for sid in sector_ids:
        vals = vals_by_sector[sid]
        stacked = np.concatenate(vals) if vals else np.empty(0, dtype=np.float32)
        bounds[sid] = _sector_bound(stacked, max_percentile)
    return bounds


def _diag_bound_from_minmax(mn: float, mx: float) -> dict[str, float]:
    return {"min": float(mn), "max": float(mx)}


def _diag_bounds_from_payloads(
    payloads: list[PeriodPayload], mask_bool: np.ndarray
) -> dict[str, dict[str, float]]:
    """Global min/max per diagnostic field across all periods (masked, finite).

    These freeze the diagnostic-map colorscales so the same color means the
    same value in every period. Fields with no finite data anywhere are
    omitted; the frontend then falls back to per-period autoscaling."""
    bounds: dict[str, dict[str, float]] = {}
    for key, _desc in FIELD_LAYOUT_DIAGNOSTICS:
        mn = np.inf
        mx = -np.inf
        for p in payloads:
            arr = p.diag_fields.get(key)
            if arr is None:
                continue
            vals = arr[np.isfinite(arr) & mask_bool]
            if vals.size:
                mn = min(mn, float(vals.min()))
                mx = max(mx, float(vals.max()))
        if np.isfinite(mn) and np.isfinite(mx):
            bounds[key] = _diag_bound_from_minmax(mn, mx)
    return bounds


def _scan_bins_for_diag_bounds(
    periods_dir: Path,
    entries: list[dict],
    fields_meta: list[dict],
    rows: int,
    cols: int,
    mask_bool: np.ndarray,
) -> dict[str, dict[str, float]]:
    """Incremental-path counterpart of :func:`_diag_bounds_from_payloads`,
    reading the diagnostic fields back out of the on-disk period binaries."""
    diag_keys = [k for k, _ in FIELD_LAYOUT_DIAGNOSTICS]
    mn: dict[str, float] = {k: np.inf for k in diag_keys}
    mx: dict[str, float] = {k: -np.inf for k in diag_keys}
    for e in entries:
        bin_path = periods_dir / Path(e["file"]).name
        fields = _read_period_fields(bin_path, fields_meta, diag_keys, rows, cols)
        for k, arr in fields.items():
            vals = arr[np.isfinite(arr) & mask_bool]
            if vals.size:
                mn[k] = min(mn[k], float(vals.min()))
                mx[k] = max(mx[k], float(vals.max()))
    return {
        k: _diag_bound_from_minmax(mn[k], mx[k])
        for k in diag_keys
        if np.isfinite(mn[k]) and np.isfinite(mx[k])
    }


# Where to look for the app bundle when --app-bundle is not given, in order: the
# repository's own Vite build (a clone or editable install), then a copy
# shipped inside the package (``imi_dashboard/bundle/``, populated from
# ``web/dist/`` before building a wheel).
REPO_BUNDLE_DIR = Path(__file__).resolve().parents[3] / "web" / "dist"
PACKAGE_BUNDLE_DIR = Path(__file__).resolve().parent / "bundle"

# The files `npm run build` reads, relative to web/ (directories are searched
# recursively). Any of them modified after web/dist/index.html was written
# means the clone's build predates its source.
WEB_BUILD_INPUTS = (
    "src",
    "public",
    "index.html",
    "package.json",
    "package-lock.json",
    "tsconfig.json",
    "vite.config.ts",
)


class StaleBundleWarning(UserWarning):
    """The repository's web/dist/ is older than the web app's source files."""


class IncompletePeriodWarning(UserWarning):
    """A period lacks posterior.nc or prior.nc and is left out of the dashboard."""


class MissingStatisticsWarning(UserWarning):
    """A period lacks viz_notebook_statistics.csv, so it has no dates,
    emission totals or scalar diagnostics in the dashboard."""


class MissingSectorVariableWarning(UserWarning):
    """A configured sector's imi_var is not a variable of posterior.nc, so
    the sector's map is empty."""


class MissingBundleWarning(UserWarning):
    """No web app was found to copy, so an updated dashboard keeps the web
    app it already holds."""


class RegionFallbackWarning(UserWarning):
    """``region: roi`` was asked for but the region of interest could not be
    isolated, so more of the inversion grid is kept than was asked for."""


def _newest_build_input(web_dir: Path) -> tuple[Path, float] | None:
    """Return the most recently modified build input under `web_dir` and its
    mtime, or None if none of WEB_BUILD_INPUTS exists."""
    newest: tuple[Path, float] | None = None
    for name in WEB_BUILD_INPUTS:
        path = web_dir / name
        candidates: Iterator[Path]
        if path.is_dir():
            candidates = (p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            candidates = iter((path,))
        else:
            continue
        for p in candidates:
            mtime = p.stat().st_mtime
            if newest is None or mtime > newest[1]:
                newest = (p, mtime)
    return newest


def _warn_if_repo_bundle_stale(bundle_dir: Path) -> None:
    """Warn when `bundle_dir` is the clone's web/dist/ and a web source file
    was modified after it was built (an edit, a `git pull` or a branch
    switch), so the dashboard would ship an out-of-date web app. Bundles from
    anywhere else (the packaged copy, an arbitrary --app-bundle) are not checked:
    they have no source next to them to compare against."""
    if bundle_dir.resolve() != REPO_BUNDLE_DIR.resolve():
        return
    index = bundle_dir / "index.html"
    if not index.is_file():
        return
    web_dir = REPO_BUNDLE_DIR.parent
    newest = _newest_build_input(web_dir)
    if newest is None or newest[1] <= index.stat().st_mtime:
        return
    changed = newest[0].relative_to(web_dir.parent).as_posix()
    warnings.warn(
        f"web/dist/ is older than {changed}: this dashboard gets the app bundle "
        "as it was at the last build, without the later source changes. "
        "Rebuild it with `npm --prefix web run build` (./build.sh does this) "
        "and re-run `imi-dashboard convert`.",
        StaleBundleWarning,
        stacklevel=2,
    )


@dataclass(frozen=True)
class WebApp:
    """A built web app to copy into a dashboard: the folder holding it and
    the files its marker lists (relative POSIX paths)."""
    folder: Path
    files: tuple[str, ...]


def _app_file_problem(name: object) -> str | None:
    """Why a path listed in a web app's marker is not acceptable, or None.
    The web app may write only its own files: relative paths that stay in
    the folder, outside data/, and not the dashboard's README.md."""
    if not isinstance(name, str) or not name:
        return "an entry that is not a file name"
    parts = PurePosixPath(name).parts
    if name.startswith("/") or "\\" in name or ":" in parts[0] or ".." in parts:
        return f"{name!r}, which leaves the folder"
    if parts[0] == "data" or name in ("README.md", APP_MARKER):
        return f"{name!r}, which the converter writes itself"
    return None


def _marker_files(folder: Path) -> tuple[str, ...]:
    """The files listed by the web app marker in ``folder`` that are safe to
    delete or replace there, or () without a readable marker."""
    marker = _read_json(folder / APP_MARKER)
    files = marker.get("files") if isinstance(marker, dict) else None
    if not isinstance(files, list):
        return ()
    return tuple(f for f in files if _app_file_problem(f) is None)


def _web_app(folder: Path) -> WebApp:
    """The built web app in ``folder``, checked; a ValueError says what is
    wrong with it. A web app is a folder holding the marker that the web
    build writes (APP_MARKER), for this converter's manifest version, and
    every file the marker lists. Only the listed files are copied, so
    pointing --app-bundle at a folder holding anything else copies nothing
    else."""
    marker_path = folder / APP_MARKER
    if not marker_path.is_file():
        raise ValueError(
            f"{folder} is not an app bundle (it has no {APP_MARKER}, which "
            "`npm --prefix web run build` writes into web/dist/); point "
            "--app-bundle at web/dist/ after building it, or at a dashboard "
            "written by this version of imi-dashboard"
        )
    marker = _read_json(marker_path)
    if not isinstance(marker, dict) or marker.get("app") != APP_NAME or not isinstance(marker.get("files"), list):
        raise ValueError(f"{marker_path} cannot be read or is not an imi-dashboard app bundle marker")
    version = marker.get("manifestVersion")
    if version != MANIFEST_VERSION:
        raise ValueError(
            f"the app bundle in {folder} reads data format version {version}, "
            f"and this converter writes version {MANIFEST_VERSION}; use the app "
            "bundle built from the same release as the converter"
        )
    files: list[str] = marker["files"]
    for name in files:
        problem = _app_file_problem(name)
        if problem is not None:
            raise ValueError(f"{marker_path} lists {problem}")
        if not (folder / name).is_file():
            raise ValueError(f"the app bundle in {folder} is incomplete: {name}, listed in its {APP_MARKER}, is missing")
    if "index.html" not in files:
        raise ValueError(f"the app bundle in {folder} has no index.html in its {APP_MARKER}")
    return WebApp(folder=folder, files=tuple(files))


def _running_from_clone() -> bool:
    """Whether the converter runs from a repository clone (an editable
    install), which has a web/ folder to build the web app from."""
    return (REPO_BUNDLE_DIR.parent / "package.json").is_file()


def _no_web_app_message(output_folder: Path) -> str:
    """The error for a conversion that has no app bundle to copy, with the
    remedy that applies to this installation first: in a clone, building
    web/; otherwise installing the release wheel. The developer option
    --app-bundle comes last."""
    if _running_from_clone():
        first = (
            f"build it in this clone with `npm --prefix web run build` "
            f"(./build.sh does this), which writes {REPO_BUNDLE_DIR}"
        )
    else:
        first = "install imi-dashboard from a release wheel, which includes it"
    return (
        "this installation of imi-dashboard has no app bundle (the dashboard's "
        f"pages: index.html and assets/) to copy into {output_folder}, and that "
        f"folder holds none to keep: {first}. Developers can also pass "
        "--app-bundle FOLDER (a built web/dist/, or a dashboard written by this "
        "version)"
    )


def _find_web_app(bundle_dir: Path | None, output_folder: Path) -> WebApp | None:
    """The web app this conversion copies, or None when there is none to copy
    but the output folder already holds a web app it can keep.

    ``bundle_dir`` (--app-bundle) is checked by :func:`_web_app` and must not
    overlap the output folder. Without it: the clone's web/dist/ when it
    exists (checked, so an old or broken build is an error rather than
    silently replaced by another copy), else the copy packaged in the wheel
    (with a warning when running from a clone, where it may be older than
    the web source). With neither, an update keeps the output's own web app
    if it is a valid one, with a warning; otherwise it is an error. Called
    before any period is read, so the error comes before the work."""
    if bundle_dir is not None:
        b, o = bundle_dir.resolve(), output_folder.resolve()
        if b == o or o in b.parents or b in o.parents:
            raise ValueError(
                f"the app bundle folder {bundle_dir} and the output folder "
                f"{output_folder} overlap; copy the app bundle elsewhere first"
            )
        app = _web_app(bundle_dir)
    elif REPO_BUNDLE_DIR.is_dir():
        app = _web_app(REPO_BUNDLE_DIR)
    elif PACKAGE_BUNDLE_DIR.is_dir():
        if _running_from_clone():
            warnings.warn(
                f"{REPO_BUNDLE_DIR} is not built, so the app bundle packaged in "
                f"{PACKAGE_BUNDLE_DIR} is used; it may be older than the web "
                "source. Build web/ with `npm --prefix web run build`, or delete "
                "that folder",
                StaleBundleWarning,
                stacklevel=3,
            )
        app = _web_app(PACKAGE_BUNDLE_DIR)
    else:
        try:
            _web_app(output_folder)
        except ValueError:
            raise ValueError(_no_web_app_message(output_folder)) from None
        warnings.warn(
            f"no app bundle to copy, so {output_folder} keeps the one it holds, "
            "which this run does not update",
            MissingBundleWarning,
            stacklevel=3,
        )
        return None
    _warn_if_repo_bundle_stale(app.folder)
    return app


def _remove_web_app(out_root: Path) -> None:
    """Delete the web app in a dashboard folder: index.html, assets/ (with the
    custom logo, which _emit_config writes again), the marker, and any other
    file the marker lists."""
    for name in _marker_files(out_root):
        _remove(out_root / name)
    for name in ("index.html", "assets", APP_MARKER):
        _remove(out_root / name)


def _copy_web_app(out_root: Path, app: WebApp | None) -> None:
    """Replace the dashboard's web app with ``app``'s listed files and its
    marker; with None, keep the web app the dashboard has. Removing the old
    one first means files of an earlier build (assets/index-<hash>.js) do not
    pile up."""
    if app is None:
        return
    _remove_web_app(out_root)
    for name in app.files:
        dst = out_root / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(app.folder / name, dst)
    shutil.copy2(app.folder / APP_MARKER, out_root / APP_MARKER)
    log.info("app bundle: copied from %s", app.folder)


def _logo_name(src: Path) -> str:
    """Fixed name the custom logo is copied to under assets/, keeping its
    extension, so it can never replace a file of the web bundle (such as the
    Terra Arc mark, assets/logo_small.png)."""
    return f"custom-logo{src.suffix.lower()}"


def _check_logo(cfg: Config) -> None:
    """A custom logo that the header will show must exist."""
    if cfg.header_brand == "custom" and cfg.logo and not cfg.logo.is_file():
        raise ValueError(
            f"logo {cfg.logo} does not exist or is not a file "
            "(a relative path in the config file is read from the config file's folder)"
        )


def _resolved_config_json(cfg: Config) -> dict:
    return {
        "areaName": cfg.area_name,
        "displayName": cfg.display_name,
        "headerBrand": cfg.header_brand,
        "logoUrl": (
            f"./assets/{_logo_name(cfg.logo)}"
            if cfg.logo and cfg.header_brand == "custom" else None
        ),
        "brandName": cfg.brand_name,
        "brandTagline": cfg.brand_tagline,
        "sectors": [
            {
                "id": s.id,
                "label": s.label,
                "short": s.short,
                "color": s.color,
                "primary": s.primary,
            }
            for s in cfg.sectors
        ],
        "map": {
            "ramp": cfg.map.ramp,
            "scale": cfg.map.scale,
            "maxPercentile": cfg.map.max_percentile,
            "colorbarTransparentBg": cfg.map.colorbar_transparent_bg,
            **cfg.map.resolved_tiles(),
        },
        "chart": {
            "defaultStyle": cfg.chart.default_style,
            "defaultSpeed": cfg.chart.default_speed,
        },
        "text": cfg.text.resolved(area_name=cfg.area_name, overview_heading_fallback=cfg.display_name),
    }


_PERIOD_FILE_FMT = "{:04d}.bin"


_README_TEMPLATE = Path(__file__).resolve().parent / "templates" / "README.md"


def _write_readme(output_folder: Path, cfg: Config) -> None:
    """Write an end-user README.md into the generated dashboard root.

    A static template ships with the package; the only substitution is the
    dashboard's display name so the heading names the area/run. Written on
    both the full and incremental build paths so every dashboard carries it.
    """
    try:
        template = _README_TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return  # template missing from the install — don't fail the conversion
    rendered = template.replace("{display_name}", cfg.display_name)
    (output_folder / "README.md").write_text(rendered, encoding="utf-8")


def _emit_config(output_folder: Path, cfg: Config) -> None:
    """Write data/config.json and copy the custom logo (if any). Shared by the
    full and incremental build paths."""
    data_dir = output_folder / "data"
    if cfg.logo and cfg.header_brand == "custom":
        # Existence was checked by convert (_check_logo) before any
        # output was written. load_config has already resolved a relative
        # path against the config file's folder.
        assets_dir = output_folder / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg.logo, assets_dir / _logo_name(cfg.logo))
    (data_dir / "config.json").write_text(
        json.dumps(_resolved_config_json(cfg), indent=2, allow_nan=False), encoding="utf-8"
    )


def _period_entry(payload: PeriodPayload, file_name: str, fingerprint: str) -> dict:
    """The manifest entry of a period just converted. Shared by the full and
    incremental build paths, so both write the same keys."""
    return {
        "idx": payload.idx,
        "number": payload.number,
        "label": payload.label,
        "start": payload.start,
        "end": payload.end,
        "file": f"periods/{file_name}",
        "inputs": fingerprint,
        "sectorScalars": payload.scalars,
        "diagScalars": payload.diag_scalars,
    }


def _write_manifest(
    data_dir: Path,
    *,
    mode: str,
    region: str,
    grid: GridMeta,
    land_mask: np.ndarray,
    provenance: dict,
    source: dict,
    total_def: dict | None,
    fields: list[dict],
    sector_bounds: dict,
    diag_bounds: dict,
    periods: list[dict],
) -> None:
    """Write data/manifest.json. Shared by the full and incremental build
    paths, so both write the same keys."""
    manifest = {
        "version": MANIFEST_VERSION,
        "provenance": provenance,
        "mode": mode,
        "region": region,
        "grid": grid.to_dict(),
        "globals": {
            "file": "globals.bin",
            "dtype": "uint8",
            "shape": [grid.rows, grid.cols],
            # Tightest bbox enclosing the active mask; the frontend prefers
            # this over the (often wider) full grid bbox so the camera
            # frames the actual inversion area.
            "dataBbox": _mask_data_bbox(land_mask, grid),
        },
        "source": source,
        # Whether the total sector's numbers and map include the soil sink;
        # absent without a total sector.
        **({"totalDefinition": total_def} if total_def is not None else {}),
        "fields": fields,
        "sectorBounds": sector_bounds,
        "diagBounds": diag_bounds,
        "periods": periods,
    }
    # Written to a temporary file and moved into place in one step, so that
    # an interrupted run leaves the previous manifest, not half of a new one.
    tmp = data_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(tmp, data_dir / "manifest.json")


def _reuse_blocker(
    prev: dict | None, period: PeriodInput, bin_path: Path, expected_bytes: int, fingerprint: str
) -> str | None:
    """Why an already-converted period binary cannot be reused, or None if it
    can. The checks, in order: the period is in the old manifest, at the same
    index, its binary exists, has the size the field layout and grid require,
    and its input files are unchanged (same :func:`_period_fingerprint`)."""
    if prev is None:
        return "new period"
    if prev.get("idx") != period.idx:
        return f"its index changed from {prev.get('idx')} to {period.idx}"
    if not bin_path.exists():
        return f"{bin_path.name} is missing"
    size = bin_path.stat().st_size
    if size != expected_bytes:
        return f"{bin_path.name} has {size} bytes, the layout needs {expected_bytes}"
    if prev.get("inputs") != fingerprint:
        return "its input files changed"
    return None


class ConversionCancelled(Exception):
    """The ``confirm`` callback of ``convert`` declined; nothing was changed."""


def _unreadable_problem(period: PeriodInput, fingerprint: str, already_read: dict[str, object]) -> str | None:
    """Why a period's required netCDF files cannot be read, or None. Not
    checked again for a period the existing dashboard holds with the same
    input fingerprint: its files were read then and have not changed."""
    if already_read.get(period.label) == fingerprint:
        return None
    for name in REQUIRED_FILES:
        problem = netcdf_read_problem(period.netcdf_dir / name)
        if problem is not None:
            return f"{name}: {problem}"
    return None


def _removal_notice(existing: dict | None, source: dict, discovery: Discovery, output_folder: Path) -> str | None:
    """What this conversion would remove from the dashboard in
    ``output_folder`` without writing it again, as a sentence, or None.

    That is the dashboard as a whole when it was converted from another input
    folder (the recorded input differs), and otherwise the periods it holds
    that are not among this run's periods (their folders were removed from
    the input, or are now skipped as incomplete or unreadable). Reconverting
    the same periods (--rebuild, a changed config, region or converter
    release) removes nothing that is not written again, and needs no
    confirmation."""
    if existing is None:
        return None
    old_labels = [e["label"] for e in existing["periods"]]
    old_source = existing.get("source")
    old_input = old_source.get("input") if isinstance(old_source, dict) else None
    if old_input is not None and old_input != source["input"]:
        prov = existing.get("provenance") or {}
        return (
            f"{output_folder} holds a dashboard of {len(old_labels)} period(s) "
            f"converted from another input folder ({prov.get('input', 'name not recorded')!r}, "
            f"on {prov.get('convertedAt', 'an unrecorded date')}); this conversion replaces it"
        )
    current = {p.label for p in discovery.periods}
    dropped = [label for label in old_labels if label not in current]
    if not dropped:
        return None
    shown = ", ".join(dropped[:12]) + (f", … ({len(dropped)} in all)" if len(dropped) > 12 else "")
    return (
        f"{len(dropped)} period(s) of the dashboard in {output_folder} are no "
        f"longer in the input, or are now skipped, and would be removed from it: {shown}"
    )


def _full_rebuild_reason(
    *,
    force: bool,
    existing: dict | None,
    output_folder: Path,
    region: str,
    final_grid: GridMeta | None,
    expected_field_names: list[str],
    source: dict,
) -> str | None:
    """Why ``convert`` must reconvert every period rather than update the
    dashboard already in ``output_folder``, or None if it can update it."""
    if force:
        return "--rebuild given"
    if existing is None:
        return f"no readable data/manifest.json in {output_folder}"
    old_source = existing.get("source")
    if not isinstance(old_source, dict):
        return "the existing dashboard does not record its input (written by an older converter)"
    if old_source.get("input") != source["input"]:
        return "the existing dashboard was converted from a different input folder"
    if old_source.get("converter") != source["converter"]:
        old = old_source.get("converter")
        return (
            f"the existing dashboard was converted by imi-dashboard "
            f"{old or 'before ' + source['converter']}, this is {source['converter']}, "
            "which may convert periods differently"
        )
    if final_grid is None:
        return (
            "with region 'roi' and no state-vector mask, the crop window is "
            "known only after reading every period"
        )
    old_region = existing.get("region")
    if old_region != region:
        return f"the existing dashboard has region {old_region!r}, this run {region!r}"
    old_fields = [f["name"] for f in existing.get("fields", [])]
    if old_fields != expected_field_names:
        removed = [f for f in old_fields if f not in expected_field_names]
        added = [f for f in expected_field_names if f not in old_fields]
        detail = "; ".join(
            part for part in (
                f"removed {', '.join(removed)}" if removed else "",
                f"added {', '.join(added)}" if added else "",
            ) if part
        ) or "same fields in another order"
        return f"the field layout differs from the existing dashboard's ({detail})"
    old_vars = old_source.get("sectorVars") or {}
    changed = [sid for sid, var in source["sectorVars"].items() if old_vars.get(sid) != var]
    if changed:
        return (
            "the netCDF variable (imi_var) of sector(s) "
            f"{', '.join(changed)} differs from the existing dashboard's"
        )
    if old_source.get("deltaEmissionsVar") != source["deltaEmissionsVar"]:
        return (
            "the posterior − prior emissions map is computed from "
            f"{source['deltaEmissionsVar']}, the existing dashboard's from "
            f"{old_source.get('deltaEmissionsVar') or TOTAL_VAR}"
        )
    if old_source.get("totalNumbers") != source["totalNumbers"]:
        return (
            "the total's numbers are now read without the soil sink; the existing "
            "dashboard took them from the statistics CSV's PriorEmissions and "
            "PosteriorEmissions"
        )
    if not _grid_matches(existing.get("grid", {}), final_grid):
        old = existing.get("grid", {})
        return (
            f"the grid differs from the existing dashboard's "
            f"({old.get('rows')}x{old.get('cols')} before, "
            f"{final_grid.rows}x{final_grid.cols} now, or a different extent)"
        )
    return None


def _convert_incremental(
    *,
    discovery: Discovery,
    cfg: Config,
    grid: GridMeta,
    area_m2: np.ndarray | None,
    sv_mask: np.ndarray | None,
    region: str,
    final_grid: GridMeta,
    roi_window: tuple[int, int, int, int] | None,
    existing: dict,
    output_folder: Path,
    web_app: WebApp | None,
    provenance: dict,
    source: dict,
    fingerprints: dict[int, str],
    on_period: PeriodCallback | None = None,
) -> dict:
    """Reuse already-converted period binaries; rebuild only new/changed ones.

    Preconditions (checked by ``convert``): a compatible manifest exists at
    ``output_folder`` (same region, grid and field layout). ``final_grid`` is
    the grid the output already uses (full domain, or the roi crop window).
    """
    data_dir = output_folder / "data"
    periods_dir = data_dir / "periods"
    periods_dir.mkdir(parents=True, exist_ok=True)

    fields_meta: list[dict] = existing["fields"]
    expected_names = [f["name"] for f in fields_meta]
    sector_ids = [s.id for s in cfg.sectors]
    existing_by_label = {e["label"]: e for e in existing.get("periods", [])}

    # Every period binary is exactly ``nfields × rows × cols`` Float32s laid out
    # back-to-back at the offsets recorded in ``fields_meta``. A bin that doesn't
    # match this size was written under a different grid/field layout (e.g. an
    # interrupted or pre-region build whose manifest was later overwritten). The
    # frontend would slice it at the *current* offsets, so every field after the
    # first reads from the wrong place — values look low/displaced and later
    # fields come out sheared (the classic "striped" map). Reusing such a bin is
    # never correct, so size-check it and rebuild on any mismatch.
    expected_bin_bytes = len(fields_meta) * final_grid.rows * final_grid.cols * 4

    n_periods = len(discovery.periods)
    position = {p.label: i for i, p in enumerate(discovery.periods, 1)}
    reused_entries: list[dict] = []
    to_build: list[tuple[PeriodInput, str]] = []
    done = 0
    for p in discovery.periods:
        prev = existing_by_label.get(p.label)
        bin_path = periods_dir / _PERIOD_FILE_FMT.format(p.idx)
        why_rebuild = _reuse_blocker(prev, p, bin_path, expected_bin_bytes, fingerprints[p.idx])
        if why_rebuild is None:
            assert prev is not None  # _reuse_blocker returns a reason when it is None
            log.debug("[%d/%d] %s: reused %s", position[p.label], n_periods, p.label, bin_path.name)
            done += 1
            if on_period is not None:
                on_period(done, n_periods, p.label)
            entry = dict(prev)
            entry["file"] = f"periods/{bin_path.name}"
            # Older manifests predate the folder-number field; refresh it from
            # the (re-discovered) input either way.
            entry["number"] = p.number
            reused_entries.append(entry)
        else:
            to_build.append((p, why_rebuild))
    log.info(
        "incremental update of %s: %d of %d period(s) reused, %d to convert",
        output_folder, len(reused_entries), n_periods, len(to_build),
    )

    new_entries: list[dict] = []
    built: list[PeriodPayload] = []
    for p, why_rebuild in to_build:
        t0 = time.perf_counter()
        payload = _build_period(p, cfg, grid, area_m2)
        if region == "roi" and roi_window is not None:
            payload = _crop_payload(payload, *roi_window)
        blob, fmeta = _pack_period_binary(payload, cfg.sectors, final_grid.rows, final_grid.cols)
        if [f["name"] for f in fmeta] != expected_names:
            raise RuntimeError("internal error: per-period field layout drifted during incremental rebuild")
        out_path = periods_dir / _PERIOD_FILE_FMT.format(payload.idx)
        out_path.write_bytes(blob)
        log.info(
            "[%d/%d] %s: converted (%s; %.1f s)",
            position[p.label], n_periods, p.label, why_rebuild, time.perf_counter() - t0,
        )
        done += 1
        if on_period is not None:
            on_period(done, n_periods, p.label)
        new_entries.append(_period_entry(payload, out_path.name, fingerprints[payload.idx]))
        built.append(payload)
    # Reused periods were checked when they were converted.
    _report_totals(built)

    all_entries = sorted(reused_entries + new_entries, key=lambda e: e["idx"])

    # When the period set shrank (a folder removed, or a period now skipped as
    # incomplete), the bins at the vanished trailing indices are no longer
    # referenced: remove them so the output holds only the periods in the
    # manifest.
    live_bins = {Path(e["file"]).name for e in all_entries}
    for stale in periods_dir.glob("*.bin"):
        if stale.name not in live_bins:
            log.info("removed %s: its period is no longer in the input", stale.name)
            stale.unlink()

    # Resolve the active land mask first — bounds are masked by it (below) so
    # buffer-element negatives don't leak into the colorbar range. Prefer the
    # state-vector mask; otherwise derive the fallback mask from the bins.
    if sv_mask is not None:
        if region == "roi" and roi_window is not None:
            r0, r1, c0, c1 = roi_window
            land_mask = np.ascontiguousarray(sv_mask[r0 : r1 + 1, c0 : c1 + 1])
        else:
            land_mask = sv_mask
    else:
        land_mask = _scan_bins_for_mask(
            periods_dir, all_entries, fields_meta, sector_ids,
            final_grid.rows, final_grid.cols,
        )

    # Bounds always recomputed from the on-disk bins (reused + new), restricted
    # to active cells and using the configured percentile for the max.
    sector_bounds = _scan_bins_for_bounds(
        periods_dir,
        all_entries,
        fields_meta,
        sector_ids,
        final_grid.rows,
        final_grid.cols,
        land_mask.astype(bool),
        cfg.map.max_percentile,
    )
    diag_bounds = _scan_bins_for_diag_bounds(
        periods_dir,
        all_entries,
        fields_meta,
        final_grid.rows,
        final_grid.cols,
        land_mask.astype(bool),
    )

    _copy_web_app(output_folder, web_app)
    _emit_config(output_folder, cfg)
    _write_readme(output_folder, cfg)
    (data_dir / "globals.bin").write_bytes(np.ascontiguousarray(land_mask).tobytes())

    _write_manifest(
        data_dir,
        mode=discovery.mode,
        region=region,
        grid=final_grid,
        land_mask=land_mask,
        provenance=provenance,
        source=source,
        total_def=total_definition(cfg, discovery),
        fields=fields_meta,
        sector_bounds=sector_bounds,
        diag_bounds=diag_bounds,
        periods=all_entries,
    )

    return {
        "mode": discovery.mode,
        "period_count": len(all_entries),
        "rows": final_grid.rows,
        "cols": final_grid.cols,
        "output": str(output_folder),
        "reused": len(reused_entries),
        "rebuilt": len(new_entries),
    }


def convert(
    input_folder: Path,
    output_folder: Path,
    cfg: Config,
    *,
    bundle_dir: Path | None = None,
    region: str | None = None,
    force: bool = False,
    on_period: PeriodCallback | None = None,
    confirm: Callable[[str], bool] | None = None,
) -> dict:
    """Run a complete conversion. Returns a summary dict (also useful for tests).

    ``region``:
        ``None``      — use ``cfg.region`` (default).
        ``"domain"``  — keep the full IMI inversion grid (preserves
                        the buffer elements around the region of interest).
        ``"roi"``     — crop the grid + every gridded field to the tightest
                        bbox enclosing the active mask. Smaller files; the
                        dashboard and any downstream consumers see only the
                        region of interest.

    ``force``:
        When False (default) and a compatible dashboard already exists in
        ``output_folder``, periods whose inputs are unchanged are *reused*
        rather than re-read from netCDF — only new or modified periods are
        converted. This keeps a long-running, growing timeseries cheap to
        re-export. Set True to reconvert every period and rewrite the
        dashboard's files (entries of the folder the converter does not
        write are kept either way).

    ``on_period``:
        Called as ``on_period(done, total, label)`` once per period, with
        ``done`` running 1…total: after each period is read on a full
        conversion, and after each period is reused or rebuilt on an
        incremental update.

    ``confirm``:
        Called as ``confirm(message)`` before the conversion removes data
        from the dashboard already in ``output_folder`` that it will not
        write again (:func:`_removal_notice`): periods no longer in the
        input, or a dashboard of another input folder. The conversion goes
        ahead when it returns True, and raises ``ConversionCancelled``,
        having changed nothing, when it returns False. None goes ahead
        without asking (the command line always passes one).
    """
    if region is None:
        region = cfg.region
    if region not in ("domain", "roi"):
        raise ValueError(f"region must be 'domain' or 'roi', got {region!r}")
    _check_output_folder(input_folder, output_folder)
    _check_logo(cfg)
    web_app = _find_web_app(bundle_dir, output_folder)

    discovery: Discovery = discover(input_folder)
    if not discovery.periods:
        raise ValueError(f"no periods discovered in {input_folder}")
    # A period whose IMI notebook failed part-way may lack the emission
    # fields. Leave it out (it then shows as a gap, like a missing folder)
    # rather than abort the whole conversion; fail only if nothing is left.
    discovery, skipped = split_incomplete(discovery)
    if skipped:
        warnings.warn(
            f"skipping {len(skipped)} incomplete period(s), shown as gaps: "
            f"{describe_skipped(skipped)}",
            IncompletePeriodWarning,
            stacklevel=2,
        )
    # Loaded with --rebuild too: it is what _removal_notice compares against.
    existing = _load_existing_manifest(output_folder)
    # Taken before any period is read, so an input modified while this run
    # is converting it is picked up by the next run.
    fingerprint_of = {p.label: _period_fingerprint(p, exclude=output_folder) for p in discovery.periods}
    # A required file that cannot be read (typically posterior.nc truncated
    # while IMI is still writing it) leaves its period out, as a gap, like a
    # missing one. Checked only for periods whose files changed since the
    # dashboard in the output folder was written: the others were read then.
    already_read = {e["label"]: e.get("inputs") for e in existing["periods"]} if existing else {}
    discovery, unreadable = split_unreadable(
        discovery, lambda p: _unreadable_problem(p, fingerprint_of[p.label], already_read)
    )
    if unreadable:
        warnings.warn(
            f"skipping {len(unreadable)} period(s) whose files cannot be read, "
            "shown as gaps: " + "; ".join(f"{p.label} ({why})" for p, why in unreadable),
            IncompletePeriodWarning,
            stacklevel=2,
        )
    fingerprints = {p.idx: fingerprint_of[p.label] for p in discovery.periods}
    no_stats = [
        p.label for p in discovery.periods
        if not (p.folder / "viz_notebook_statistics.csv").exists()
    ]
    if no_stats:
        shown = ", ".join(no_stats[:12]) + (f", … ({len(no_stats)} in all)" if len(no_stats) > 12 else "")
        warnings.warn(
            f"{len(no_stats)} period(s) have no viz_notebook_statistics.csv, so "
            "the dashboard shows no dates, emission totals or scalar "
            f"diagnostics for them: {shown}",
            MissingStatisticsWarning,
            stacklevel=2,
        )

    log.info(
        "found a %s run in %s: %d period(s) to convert",
        discovery.mode, input_folder, len(discovery.periods),
    )

    cfg = fill_defaults_from_data(cfg, discovery)
    log.info("sectors: %s", ", ".join(s.id for s in cfg.sectors) or "(none)")

    grid = open_emissions_grid(discovery.periods[0].netcdf_dir / "posterior.nc")
    area_m2 = read_area(discovery.periods[0].netcdf_dir / "posterior.nc", grid)
    log.info(
        "grid: %d rows x %d columns, read from %s",
        grid.rows, grid.cols, discovery.periods[0].netcdf_dir / "posterior.nc",
    )
    with xr.open_dataset(discovery.periods[0].netcdf_dir / "posterior.nc") as ds:
        absent = [f"{s.id} ({s.imi_var})" for s in cfg.sectors if s.imi_var not in ds.variables]
    if absent:
        warnings.warn(
            f"sector variable(s) not found in {discovery.periods[0].netcdf_dir / 'posterior.nc'}, "
            f"so their maps are empty: {', '.join(absent)}; check imi_var in the config",
            MissingSectorVariableWarning,
            stacklevel=2,
        )
    state_vector_path = discovery.periods[0].netcdf_dir / "state_vector.nc"
    sv_path = state_vector_path if state_vector_path.exists() else None

    # The "active" mask depends on region. For "domain" it's every in-run cell
    # (buffers included); for "roi" it's the region of interest with the buffer
    # elements dropped. Both feed the same crop/mask machinery below — the only
    # difference is which cells count as part of the run.
    if region == "roi":
        buffers = read_buffer_regions(sv_path, grid)
        if buffers is None:
            # No state vector to classify against — fall back to the plain
            # in-run mask (same as domain). The emissions-derived fallback in
            # _global_mask still applies when that's None too.
            warnings.warn(
                f"region 'roi': {state_vector_path} is missing or has no "
                "StateVector variable, so buffer elements cannot be told apart "
                "from the region of interest and are kept",
                RegionFallbackWarning,
                stacklevel=2,
            )
            sv_mask = read_state_vector_mask(sv_path, grid)
        else:
            sv_mask = buffers.roi_mask.astype(np.uint8)
            log.info(
                "region roi: %d of the %d state-vector elements are buffer "
                "elements and are dropped",
                len(buffers.buffer_ids), buffers.n_elements,
            )
    else:
        sv_mask = read_state_vector_mask(sv_path, grid)
        log.info("region domain: every cell of the inversion grid is kept, buffer elements included")

    # ── Incremental reuse decision ──────────────────────────────────────
    # Reuse is only attempted when the final grid is knowable without building
    # every period: the full domain (always) or a roi crop whose window is
    # fixed by the state-vector mask. Anything else (roi with no state vector)
    # falls through to a full rebuild.
    final_grid_preview: GridMeta | None = None
    roi_window_preview: tuple[int, int, int, int] | None = None
    if region == "domain":
        final_grid_preview = grid
    elif sv_mask is not None:
        win = mask_index_bbox(sv_mask)
        if win is not None:
            roi_window_preview = win
            final_grid_preview = _crop_grid(grid, *win)

    expected_field_names = [s.id for s in cfg.sectors] + [k for k, _ in FIELD_LAYOUT_DIAGNOSTICS]
    source = _source_record(input_folder, cfg)
    provenance = _provenance(input_folder)
    notice = _removal_notice(existing, source, discovery, output_folder)
    if notice is not None and confirm is not None and not confirm(notice):
        raise ConversionCancelled(f"cancelled; {output_folder} was not changed")
    rebuild_reason = _full_rebuild_reason(
        force=force,
        existing=existing,
        output_folder=output_folder,
        region=region,
        final_grid=final_grid_preview,
        expected_field_names=expected_field_names,
        source=source,
    )
    if rebuild_reason is None:
        assert existing is not None and final_grid_preview is not None
        return _convert_incremental(
            discovery=discovery,
            cfg=cfg,
            grid=grid,
            area_m2=area_m2,
            sv_mask=sv_mask,
            region=region,
            final_grid=final_grid_preview,
            roi_window=roi_window_preview,
            existing=existing,
            output_folder=output_folder,
            web_app=web_app,
            provenance=provenance,
            source=source,
            fingerprints=fingerprints,
            on_period=on_period,
        )

    log.info("full conversion: %s", rebuild_reason)
    payloads: list[PeriodPayload] = []
    for i, p in enumerate(discovery.periods, 1):
        t0 = time.perf_counter()
        payloads.append(_build_period(p, cfg, grid, area_m2))
        # Only read so far: the binaries are written below, once every period
        # is in (with region roi and no state-vector mask, the crop window
        # depends on all of them).
        log.info(
            "[%d/%d] %s: read (%.1f s)",
            i, len(discovery.periods), p.label, time.perf_counter() - t0,
        )
        if on_period is not None:
            on_period(i, len(discovery.periods), p.label)
    _report_totals(payloads)
    t_write = time.perf_counter()
    land_mask = _global_mask(payloads, sv_mask, grid)

    # Crop down to the region of interest before computing colorbar bounds
    # and serializing — that way the binary blobs, the manifest grid, and
    # the bounds are all consistent with the cropped view the user wanted.
    if region == "roi":
        idx_bbox = mask_index_bbox(land_mask)
        if idx_bbox is None:
            # Empty mask — nothing to crop to. Fall back to full domain.
            warnings.warn(
                "region 'roi': no cell is in the region of interest (the "
                "state-vector mask and the emission fields are empty), so the "
                "full inversion grid is kept",
                RegionFallbackWarning,
                stacklevel=2,
            )
        else:
            r0, r1, c0, c1 = idx_bbox
            grid = _crop_grid(grid, r0, r1, c0, c1)
            land_mask = np.ascontiguousarray(land_mask[r0 : r1 + 1, c0 : c1 + 1])
            payloads = [_crop_payload(p, r0, r1, c0, c1) for p in payloads]

    # Per-sector global colorbar bounds across all periods (NaN-safe). Only
    # active (land/ROI) cells count: buffer elements ringing the ROI often
    # carry large negative posterior values, and letting them in dragged the
    # linear scale far below anything inside the ROI.
    mask_bool = land_mask.astype(bool)
    sector_bounds: dict[str, dict[str, float]] = {}
    for s in cfg.sectors:
        vals: list[np.ndarray] = []
        for payload in payloads:
            arr = payload.sector_fields.get(s.id)
            if arr is not None:
                vals.append(arr[np.isfinite(arr) & mask_bool])
        stacked = np.concatenate(vals) if vals else np.empty(0, dtype=np.float32)
        sector_bounds[s.id] = _sector_bound(stacked, cfg.map.max_percentile)

    # Global per-diagnostic-field bounds so diagnostic-map colorscales are
    # fixed across the whole timeseries (comparable colors period-to-period).
    diag_bounds = _diag_bounds_from_payloads(payloads, mask_bool)

    _clear_for_full_rebuild(output_folder, keep_app=web_app is None)
    _copy_web_app(output_folder, web_app)

    data_dir = output_folder / "data"
    periods_dir = data_dir / "periods"
    periods_dir.mkdir(parents=True)

    # Resolved config.json (sectors, branding, text — frontend reads at boot).
    _emit_config(output_folder, cfg)

    # End-user README explaining how to run/host the generated dashboard.
    _write_readme(output_folder, cfg)

    # globals.bin — uint8 land mask, row-major, shape (rows, cols).
    (data_dir / "globals.bin").write_bytes(np.ascontiguousarray(land_mask).tobytes())

    # Per-period binaries + manifest.
    period_entries: list[dict] = []
    fields_meta_reference: list[dict] = []
    for payload in payloads:
        blob, fields_meta = _pack_period_binary(payload, cfg.sectors, grid.rows, grid.cols)
        out_path = periods_dir / _PERIOD_FILE_FMT.format(payload.idx)
        out_path.write_bytes(blob)
        if not fields_meta_reference:
            fields_meta_reference = fields_meta
        elif fields_meta != fields_meta_reference:
            raise RuntimeError("internal error: per-period field layout drifted")
        period_entries.append(_period_entry(payload, out_path.name, fingerprints[payload.idx]))

    _write_manifest(
        data_dir,
        mode=discovery.mode,
        region=region,
        grid=grid,
        land_mask=land_mask,
        provenance=provenance,
        source=source,
        total_def=total_definition(cfg, discovery),
        fields=fields_meta_reference,
        sector_bounds=sector_bounds,
        diag_bounds=diag_bounds,
        periods=period_entries,
    )

    log.info(
        "wrote %d period file(s), manifest and colour-bar bounds (%.1f s)",
        len(period_entries), time.perf_counter() - t_write,
    )

    return {
        "mode": discovery.mode,
        "period_count": len(payloads),
        "rows": grid.rows,
        "cols": grid.cols,
        "output": str(output_folder),
    }
