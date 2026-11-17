"""``imi-dashboard info``: describe an IMI run folder without converting it.

Runs the same discovery steps as ``convert`` (folder layout, the defaults
taken from the first period, the buffer/ROI split of the state vector) and
reports what it finds, so problems show up before a long conversion.

Pointed at a generated dashboard instead (a folder holding
``data/manifest.json``), it describes the dashboard from its manifest: what
built it and when, its layout, grid and region.

``run_info_json`` and ``dashboard_info_json`` give the same descriptions as
plain JSON-ready dicts for ``info --json``; their keys are documented in
docs/guide.md ("info --json") and versioned by ``INFO_JSON_VERSION``.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Config
from .defaults import fill_defaults_from_data, first_period_totals, period_days, soil_sink_text, total_definition
from .discover import REQUIRED_FILES, PeriodInput, discover, split_incomplete
from .grid import GridMeta, mask_index_bbox
from .readers import PeriodFiles, open_emissions_grid, read_buffer_regions, read_viz_stats
from .totals import HeadlineTotals, TotalSource, headline_totals

# Optional per-period inputs, with what the dashboard loses without them
# (mirrors the input table in docs/guide.md). ensemble_statistics.csv is not
# listed: the converter does not use it (the uncertainty band comes from the
# ensemble columns of viz_notebook_statistics.csv; see ENSEMBLE_COLUMNS).
OPTIONAL_FILES = {
    "viz_csv": ("viz_notebook_statistics.csv", "emission totals and scalar diagnostics"),
    "state_vector": ("state_vector.nc", "buffer detection for --region roi"),
    "avkern": ("avkern.nc", "averaging-kernel map"),
    "scale": ("scale.nc", "scale-factor map"),
    "obs_regridded": ("obs_ds_regridded.nc", "observation and satellite XCH4 maps"),
}

# The total's ensemble minimum and maximum in viz_notebook_statistics.csv,
# which give the uncertainty band and the "Ensemble range" tile.
ENSEMBLE_COLUMNS = ("EnsemblePosterior_Min", "EnsemblePosterior_Max")

log = logging.getLogger(__name__)

# The ``schemaVersion`` of ``info --json``: raised when a key is renamed or
# removed or its meaning changes, not when a key is added.
INFO_JSON_VERSION = 1


@dataclass
class PeriodInfo:
    """One period folder found in the input, complete or not."""

    label: str                          # "period_<N>", or "single"
    number: int                         # N of period_<N>_plots (0 for single)
    missing_required: tuple[str, ...]   # REQUIRED_FILES absent: convert skips the period
    missing_optional: tuple[str, ...]   # OPTIONAL_FILES names absent
    # From the period's viz_notebook_statistics.csv; read only when
    # describe() is asked for the period dates (None otherwise, and when
    # the CSV or the date is missing).
    start: str | None = None
    end: str | None = None


@dataclass
class RunInfo:
    folder: Path
    mode: str
    period_numbers: list[int]           # folder numbers N of period_<N>_plots (0 for single)
    missing_numbers: list[int]          # gaps between the first and last number
    start: str | None                   # StartDate of the first period
    end: str | None                     # EndDate of the last period
    period_days: float | None           # length of the first period
    cadence: str
    cadence_from_config: bool
    sectors: list[str]
    sectors_from_config: bool
    grid: GridMeta                      # full inversion domain
    reference_label: str = ""           # period whose state vector and defaults are read
    n_elements: int | None = None       # state-vector elements (None: no state_vector.nc)
    n_buffers: int | None = None
    roi_shape: tuple[int, int] | None = None     # (rows, cols) after the roi crop
    # ENSEMBLE_COLUMNS hold values in the reference period's statistics CSV
    # (None: that period has no CSV)
    has_ensemble: bool | None = None
    # Whether the total sector's numbers and map include the soil sink
    # (defaults.total_definition; None: the sectors have no total)
    total_definition: dict | None = None
    # Where the reference period's total numbers come from, and how its
    # statistics CSV's headline totals relate to them (totals.py)
    total_source: TotalSource | None = None
    total_headline: HeadlineTotals | None = None
    # The CSV columns the total's numbers are read from (source CSV).
    total_columns: tuple[str, str] | None = None
    # file name -> period numbers lacking it; `convert` skips periods that
    # lack a required file (they show as gaps)
    missing_required: dict[str, list[int]] = field(default_factory=dict)
    missing_optional: dict[str, list[int]] = field(default_factory=dict)
    # The config with its data-derived defaults filled in, as `convert`
    # would use it (fill_defaults_from_data); init-config writes it out.
    config: Config = field(default_factory=Config)
    # Every period folder found, in folder-number order (info --json).
    periods: list[PeriodInfo] = field(default_factory=list)


def _absent_files(p: PeriodInput) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The required and the optional input files absent from period ``p``
    (the statistics CSV is looked for in the period's folder, the others in
    its netCDF/ folder, as PeriodFiles.discover does)."""
    required = tuple(f for f in REQUIRED_FILES if not (p.netcdf_dir / f).is_file())
    optional = tuple(
        name for name, _ in OPTIONAL_FILES.values()
        if not ((p.folder if name.endswith(".csv") else p.netcdf_dir) / name).exists()
    )
    return required, optional


def describe(input_folder: Path, cfg: Config | None = None, *, period_dates: bool = False) -> RunInfo:
    """Run the discovery steps of ``convert`` on ``input_folder``. With
    ``period_dates``, also read every period's statistics CSV for its dates
    (``RunInfo.periods``); otherwise only the first and last complete
    periods' are read."""
    cfg = cfg or Config()
    discovery = discover(input_folder)
    periods = discovery.periods
    numbers = [p.number for p in periods]
    missing_numbers = sorted(set(range(numbers[0], numbers[-1] + 1)) - set(numbers))

    missing_required: dict[str, list[int]] = {}
    missing_optional: dict[str, list[int]] = {}
    files_by_period: dict[int, PeriodFiles] = {}
    # The same rule as `convert`: periods lacking a required file are left
    # out, and the checks below read the first and last complete periods.
    # Raises ValueError when no period is complete.
    complete, skipped = split_incomplete(discovery)
    for sp in skipped:
        for name in sp.missing:
            missing_required.setdefault(name, []).append(sp.period.number)
    for p in complete.periods:
        files = PeriodFiles.discover(p.netcdf_dir)
        files_by_period[p.number] = files
        for attr, (name, _) in OPTIONAL_FILES.items():
            if getattr(files, attr) is None:
                missing_optional.setdefault(name, []).append(p.number)

    first, last = complete.periods[0], complete.periods[-1]
    first_files = files_by_period[first.number]
    last_viz_csv = files_by_period[last.number].viz_csv
    # Shown with -v: the files the description below is read from.
    for path in dict.fromkeys((first_files.viz_csv, last_viz_csv, first_files.posterior, first_files.state_vector)):
        if path is not None:
            log.info("reading %s", path)
    viz_first = read_viz_stats(first_files.viz_csv)
    viz_last = read_viz_stats(last_viz_csv) if last_viz_csv != first_files.viz_csv else viz_first

    period_infos: list[PeriodInfo] = []
    for p in periods:
        required, optional = _absent_files(p)
        period_infos.append(PeriodInfo(label=p.label, number=p.number, missing_required=required, missing_optional=optional))
    if period_dates:
        with_csv = [(pi, p) for pi, p in zip(period_infos, periods) if "viz_notebook_statistics.csv" not in pi.missing_optional]
        log.info("reading the dates of %d period(s) from their viz_notebook_statistics.csv", len(with_csv))
        known = {first_files.viz_csv: viz_first, last_viz_csv: viz_last}
        for pi, p in with_csv:
            csv = p.folder / "viz_notebook_statistics.csv"
            log.debug("reading %s", csv)
            viz = known[csv] if csv in known else read_viz_stats(csv)
            if viz:
                pi.start = str(viz["StartDate"]) if viz.get("StartDate") else None
                pi.end = str(viz["EndDate"]) if viz.get("EndDate") else None

    filled = fill_defaults_from_data(cfg, complete)
    assert filled.text.cadence is not None  # fill_defaults_from_data always sets it
    grid = open_emissions_grid(first_files.posterior)
    totals = first_period_totals(complete)
    info = RunInfo(
        folder=input_folder,
        mode=discovery.mode,
        period_numbers=numbers,
        missing_numbers=missing_numbers,
        start=str(viz_first["StartDate"]) if viz_first and viz_first.get("StartDate") else None,
        end=str(viz_last["EndDate"]) if viz_last and viz_last.get("EndDate") else None,
        period_days=period_days(viz_first),
        cadence=filled.text.cadence,
        cadence_from_config=cfg.text.cadence is not None,
        sectors=[s.id for s in filled.sectors],
        sectors_from_config=bool(cfg.sectors),
        grid=grid,
        reference_label=first.label,
        has_ensemble=(
            all(viz_first.get(c) is not None for c in ENSEMBLE_COLUMNS) if viz_first is not None else None
        ),
        total_definition=total_definition(filled, complete, totals),
        total_source=totals.source,
        total_headline=headline_totals(viz_first),
        total_columns=totals.columns,
        missing_required=missing_required,
        missing_optional=missing_optional,
        config=filled,
        periods=period_infos,
    )

    buffers = read_buffer_regions(first_files.state_vector, grid)
    if buffers is not None:
        info.n_elements = buffers.n_elements
        info.n_buffers = buffers.n_buffers
        window = mask_index_bbox(buffers.roi_mask)
        if window is not None:
            r0, r1, c0, c1 = window
            info.roi_shape = (r1 - r0 + 1, c1 - c0 + 1)
    return info


def _numbers(nums: list[int], limit: int = 12) -> str:
    shown = ", ".join(str(n) for n in nums[:limit])
    return shown + (f", … ({len(nums)} in all)" if len(nums) > limit else "")


def format_info(info: RunInfo) -> str:
    g = info.grid
    n = len(info.period_numbers)
    if info.mode == "single":
        layout = "single period"
    else:
        layout = (
            f"timeseries, {n} period{'s' if n != 1 else ''} numbered "
            f"{info.period_numbers[0]}–{info.period_numbers[-1]}"
        )
        if info.missing_numbers:
            layout += f" (missing: {_numbers(info.missing_numbers)})"
    days = f"{info.period_days:g} days" if info.period_days is not None else "unknown length"
    cadence_note = "from the config" if info.cadence_from_config else "inferred; text.cadence overrides it"
    rows = [
        ("IMI run", str(info.folder)),
        ("Layout", layout),
        ("Dates", f"{info.start or '?'} to {info.end or '?'}"),
        ("Period", f"{days}, cadence {info.cadence} ({cadence_note})"),
        (
            "Grid",
            f"{g.rows} x {g.cols} cells (rows x cols), {g.dlat:g}° lat x {g.dlon:g}° lon; "
            f"cell centres lat {g.lat_min:.8g} to {g.lat_max:.8g}, lon {g.lon_min:.8g} to {g.lon_max:.8g}",
        ),
    ]
    # In a timeseries the state vector (and the defaults) come from the first
    # complete period; with dynamic clustering the element count varies.
    sv_source = f" (in {info.reference_label})" if info.mode == "timeseries" else ""
    if info.n_elements is None or info.n_buffers is None:
        rows.append(("State vector", f"no state_vector.nc{sv_source}: buffer elements cannot be identified"))
    else:
        rows.append((
            "State vector",
            f"{info.n_elements} elements{sv_source}: {info.n_elements - info.n_buffers} region of interest, "
            f"{info.n_buffers} buffer",
        ))
        if info.roi_shape:
            rows.append(("Region roi", f"{info.roi_shape[0]} x {info.roi_shape[1]} cells after cropping to the region of interest"))
    if info.has_ensemble is not None:
        rows.append((
            "Ensemble",
            f"minimum and maximum of the total in viz_notebook_statistics.csv{sv_source}: uncertainty band shown"
            if info.has_ensemble
            else f"no ensemble minimum and maximum in viz_notebook_statistics.csv{sv_source}: no uncertainty band",
        ))
    source = "from the config" if info.sectors_from_config else "detected"
    rows.append(("Sectors", f"{len(info.sectors)} {source}: {', '.join(info.sectors)}"))
    if info.total_definition is not None:
        where = f" in {info.reference_label}" if info.mode == "timeseries" else ""
        rows.append(("Total", _total_text(info.total_definition, where, info.total_source, info.total_headline, info.total_columns)))
    for name, nums in info.missing_required.items():
        rows.append(("Skipped", f"{name} missing in period(s) {_numbers(nums)}; convert leaves them out (shown as gaps)"))
    if not info.missing_optional:
        rows.append(("Files", "all optional files present in every period"))
    for name, nums in info.missing_optional.items():
        what = next(desc for fname, desc in OPTIONAL_FILES.values() if fname == name)
        where = "every period" if len(nums) == n else f"period(s) {_numbers(nums)}"
        rows.append(("Missing", f"{name} in {where} (no {what})"))
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)


def _total_text(
    definition: dict,
    where: str | None = None,
    source: TotalSource | None = None,
    headline: HeadlineTotals | None = None,
    columns: tuple[str, str] | None = None,
) -> str:
    """``numbers excluding the soil sink, map excluding the soil sink``. For
    a run (``where`` given, ``source`` and ``headline`` from its reference
    period), where the numbers come from is said too, and that the CSV's
    headline totals were set aside when they do not agree with each other."""
    numbers_text = soil_sink_text(definition.get("numbersIncludeSoilSink"))
    csv = f"viz_notebook_statistics.csv{where}"
    if where is not None:
        if source is TotalSource.CSV:
            prior_column, posterior_column = columns or ("TotalExclSoilAbsPrior", "TotalExclSoilAbsPosterior")
            numbers_text += f" ({prior_column} and {posterior_column} of {csv})"
        elif source is TotalSource.MAPS:
            numbers_text += f" (summed from prior.nc and posterior.nc{where}: {csv} has no TotalExclSoilAbs columns)"
        elif source is TotalSource.HEADLINE:
            numbers_text = f"possibly including the soil sink (PriorEmissions and PosteriorEmissions of {csv})"
        else:
            numbers_text = f"no {csv}"
    text = f"numbers {numbers_text}, map {soil_sink_text(definition.get('mapIncludesSoilSink'))}"
    if headline is HeadlineTotals.PRIOR_WITH_SINK:
        text += "; the CSV's PriorEmissions includes the soil sink and its PosteriorEmissions does not (IMI's Kalman mode), so neither is used"
    elif headline is HeadlineTotals.UNKNOWN:
        text += "; the CSV's PriorEmissions and PosteriorEmissions match none of its other totals (not used)"
    return text


# ── generated dashboards ─────────────────────────────────────────────────

REGION_TEXT = {
    "domain": "domain (the complete inversion region, buffer elements included)",
    "roi": "roi (the region of interest only, buffer elements dropped)",
}


def is_dashboard(folder: Path) -> bool:
    """True for a folder written by ``convert`` (it holds data/manifest.json)."""
    return (folder / "data" / "manifest.json").is_file()


def _read_manifest(folder: Path) -> Any:
    """The dashboard's data/manifest.json. Raises ValueError when it is not
    valid JSON (json's JSONDecodeError is a ValueError) or not an object."""
    path = folder / "data" / "manifest.json"
    log.info("reading %s", path)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError(f"{path} does not hold a JSON object")
    return manifest


def format_dashboard_info(folder: Path) -> str:
    """Describe a generated dashboard from its data/manifest.json.

    Raises ValueError when the manifest is not valid JSON."""
    manifest = _read_manifest(folder)
    rows: list[tuple[str, str]] = [("Dashboard", str(folder))]
    prov = manifest.get("provenance")
    if isinstance(prov, dict):
        conv = prov.get("converter") or {}
        rows += [
            ("Converted", f"{prov.get('convertedAt', '?')} (UTC)"),
            ("Converter", f"{conv.get('name', '?')} {conv.get('version', '?')}"),
            ("Input", f"{prov.get('input', '?')} (the IMI run folder's name)"),
        ]
    else:
        rows.append(("Provenance", "not recorded (written by an older imi-dashboard, which did not record it)"))
    n = len(manifest.get("periods") or [])
    rows.append(("Layout", "single period" if manifest.get("mode") == "single" else f"timeseries, {n} period{'s' if n != 1 else ''}"))
    g = manifest.get("grid") or {}
    grid = f"{g.get('rows', '?')} x {g.get('cols', '?')} cells (rows x cols)"
    if isinstance(g.get("dlat"), (int, float)) and isinstance(g.get("dlon"), (int, float)):
        grid += f", {g['dlat']:g}° lat x {g['dlon']:g}° lon"
    rows.append(("Grid", grid))
    region = manifest.get("region")
    rows.append(("Region", REGION_TEXT.get(region, str(region) if region else "not recorded")))
    total_def = manifest.get("totalDefinition")
    if isinstance(total_def, dict):
        rows.append(("Total", _total_text(total_def)))
    width = max(len(label) for label, _ in rows)
    return "\n".join(f"{label:<{width}}  {value}" for label, value in rows)


# ── info --json ──────────────────────────────────────────────────────────


def _number(value: object) -> float | int | None:
    """A JSON number: ints as ints, finite floats as floats, anything else
    (NaN, infinity, a missing or non-numeric value) as None, so the output
    is strict JSON."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _grid_json(rows: object, cols: object, dlat: object, dlon: object, lat: object, lon: object) -> dict:
    def first_last(values: object) -> tuple[float | int | None, float | int | None]:
        if isinstance(values, (list, tuple)) and values:
            return _number(values[0]), _number(values[-1])
        return None, None

    lat_min, lat_max = first_last(lat)
    lon_min, lon_max = first_last(lon)
    return {
        "rows": _number(rows),
        "cols": _number(cols),
        "dlat": _number(dlat),
        "dlon": _number(dlon),
        "latMin": lat_min,
        "latMax": lat_max,
        "lonMin": lon_min,
        "lonMax": lon_max,
    }


def _total_json(definition: dict | None) -> dict | None:
    if not isinstance(definition, dict):
        return None
    return {
        "numbersIncludeSoilSink": definition.get("numbersIncludeSoilSink"),
        "mapIncludesSoilSink": definition.get("mapIncludesSoilSink"),
    }


def run_info_json(info: RunInfo) -> dict:
    """The description of an IMI run for ``info --json`` (docs/guide.md
    lists the keys). Expects ``info`` from ``describe(..., period_dates=True)``."""
    g = info.grid
    state_vector = None
    if info.n_elements is not None and info.n_buffers is not None:
        state_vector = {
            "period": info.reference_label,
            "elements": int(info.n_elements),
            "roiElements": int(info.n_elements - info.n_buffers),
            "bufferElements": int(info.n_buffers),
        }
    return {
        "schemaVersion": INFO_JSON_VERSION,
        "kind": "run",
        "path": str(info.folder),
        "mode": info.mode,
        "periods": [
            {
                "label": p.label,
                "number": p.number if info.mode == "timeseries" else None,
                "start": p.start,
                "end": p.end,
                "skipped": bool(p.missing_required),
                "skipReason": (
                    f"missing {' and '.join(p.missing_required)}" if p.missing_required else None
                ),
                "missingFiles": list(p.missing_required + p.missing_optional),
            }
            for p in info.periods
        ],
        "gaps": list(info.missing_numbers),
        "start": info.start,
        "end": info.end,
        "periodDays": _number(info.period_days),
        "cadence": info.cadence,
        "cadenceSource": "config" if info.cadence_from_config else "inferred",
        "grid": _grid_json(g.rows, g.cols, g.dlat, g.dlon, g.lat.tolist(), g.lon.tolist()),
        "region": info.config.region,
        "stateVector": state_vector,
        "roiGrid": {"rows": info.roi_shape[0], "cols": info.roi_shape[1]} if info.roi_shape else None,
        "ensemble": info.has_ensemble,
        "sectors": [
            {"id": s.id, "imiVar": s.imi_var, "label": s.label} for s in info.config.sectors
        ],
        "sectorsSource": "config" if info.sectors_from_config else "detected",
        "total": _total_json(info.total_definition),
    }


def dashboard_info_json(folder: Path) -> dict:
    """The description of a generated dashboard for ``info --json``, from
    its data/manifest.json (docs/guide.md lists the keys). Raises ValueError
    when the manifest is not valid JSON."""
    manifest = _read_manifest(folder)
    prov = manifest.get("provenance")
    provenance = None
    if isinstance(prov, dict):
        conv = prov["converter"] if isinstance(prov.get("converter"), dict) else {}
        provenance = {
            "converter": {"name": conv.get("name"), "version": conv.get("version")},
            "convertedAt": prov.get("convertedAt"),
            "input": prov.get("input"),
        }
    periods = [e for e in manifest.get("periods") or [] if isinstance(e, dict)]
    mode = manifest.get("mode")
    g = manifest["grid"] if isinstance(manifest.get("grid"), dict) else {}
    return {
        "schemaVersion": INFO_JSON_VERSION,
        "kind": "dashboard",
        "path": str(folder),
        "provenance": provenance,
        "mode": mode,
        "periodCount": len(periods),
        "periods": [
            {
                "label": e.get("label"),
                "number": e.get("number") if mode == "timeseries" else None,
                "start": e.get("start"),
                "end": e.get("end"),
            }
            for e in periods
        ],
        "grid": _grid_json(g.get("rows"), g.get("cols"), g.get("dlat"), g.get("dlon"), g.get("lat"), g.get("lon")),
        "region": manifest.get("region"),
        "total": _total_json(manifest.get("totalDefinition")),
    }
