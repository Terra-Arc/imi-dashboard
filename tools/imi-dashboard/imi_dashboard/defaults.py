"""The config defaults taken from the IMI output itself: the sector list and
the cadence. Used by ``convert`` (pack.py) and ``info``.

Kept out of config.py because it reads netCDF files (xarray), and config.py
is imported by the CLI at start-up, which must stay fast.
"""

from __future__ import annotations

import warnings
from datetime import date

import xarray as xr

from .config import Config, cadence_from_days, default_sectors
from .discover import Discovery
from .readers import (
    TOTAL_EXCL_SOIL_SINK_VAR,
    TOTAL_VAR,
    PeriodFiles,
    open_emissions_grid,
    read_area,
    read_viz_stats,
)
from .totals import TotalNumbers, total_numbers


class TotalIncludesSoilSinkWarning(UserWarning):
    """posterior.nc has no EmisCH4_Total_ExclSoilAbs, so the total map
    includes the soil sink while the total's numbers exclude it."""


def imi_var_to_csv_stem(imi_var: str) -> str:
    """``EmisCH4_Coal`` → ``Coal``; ``EmisCH4_BiomassBurn`` → ``BiomassBurn``."""
    return imi_var.replace("EmisCH4_", "", 1)


def available_sector_vars(files: PeriodFiles, viz: dict | None) -> list[str]:
    """``EmisCH4_*`` variables in posterior.nc that the viz CSV also reports.

    A sector without CSV columns would have no prior/posterior totals, so it
    is left out of the default list. ``EmisCH4_Total`` pairs with the CSV's
    PriorEmissions/PosteriorEmissions columns. Without a CSV, every
    ``EmisCH4_*`` variable counts.
    """
    with xr.open_dataset(files.posterior) as ds:
        nc_vars = [v for v in ds.data_vars if str(v).startswith("EmisCH4_")]
    if viz is None:
        return [str(v) for v in nc_vars]
    out = []
    for v in nc_vars:
        stem = imi_var_to_csv_stem(str(v))
        key = "PosteriorEmissions" if stem == "Total" else f"{stem}Posterior"
        if key in viz:
            out.append(str(v))
    return out


def total_sector_var(files: PeriodFiles) -> str:
    """The netCDF variable the ``total`` sector's map is read from when the
    config does not name one: ``EmisCH4_Total_ExclSoilAbs``, the total
    without the soil sink, like the total's numbers (totals.py). If
    posterior.nc lacks it, ``EmisCH4_Total``, which includes the sink, with
    a warning.
    """
    with xr.open_dataset(files.posterior) as ds:
        if TOTAL_EXCL_SOIL_SINK_VAR in ds.variables:
            return TOTAL_EXCL_SOIL_SINK_VAR
    warnings.warn(
        f"{files.posterior} has no {TOTAL_EXCL_SOIL_SINK_VAR}, so the total map and "
        f"the posterior − prior map show {TOTAL_VAR}, which includes any soil sink, "
        "while the total's numbers exclude it",
        TotalIncludesSoilSinkWarning,
        stacklevel=2,
    )
    return TOTAL_VAR


# Whether a total sector's map, read from this variable, includes the soil
# sink. Any other variable: not known.
_MAP_INCLUDES_SOIL_SINK = {TOTAL_EXCL_SOIL_SINK_VAR: False, TOTAL_VAR: True}


def first_period_totals(discovery: Discovery) -> TotalNumbers:
    """The total's numbers of the first period (totals.total_numbers), as
    ``convert`` reads them."""
    files = PeriodFiles.discover(discovery.periods[0].netcdf_dir)
    grid = open_emissions_grid(files.posterior)
    return total_numbers(read_viz_stats(files.viz_csv), files, grid, read_area(files.posterior, grid))


def total_definition(cfg: Config, discovery: Discovery, numbers: TotalNumbers | None = None) -> dict | None:
    """Whether the total sector's numbers and its map include the soil sink,
    stored as ``manifest["totalDefinition"]`` and shown as labels.

    ``numbersIncludeSoilSink`` describes the numbers and ensemble range of
    the first period (``numbers``, read when not given): false when they
    leave the sink out, None when they are the CSV's headline totals or
    there is no CSV. ``mapIncludesSoilSink`` describes the sector's variable
    (None for a variable other than the two totals); it is true after the
    ``TotalIncludesSoilSinkWarning`` fallback or with an explicit
    ``imi_var: EmisCH4_Total``. None when the config has no ``total``
    sector. Call after ``fill_defaults_from_data``.
    """
    total = next((s for s in cfg.sectors if s.id == "total"), None)
    if total is None:
        return None
    if numbers is None:
        numbers = first_period_totals(discovery)
    return {
        "numbersIncludeSoilSink": numbers.includes_soil_sink,
        "mapIncludesSoilSink": _MAP_INCLUDES_SOIL_SINK.get(str(total.imi_var)),
    }


def soil_sink_text(includes: bool | None) -> str:
    """``including the soil sink`` / ``excluding the soil sink`` / ``not known``."""
    if includes is None:
        return "not known"
    return "including the soil sink" if includes else "excluding the soil sink"


def period_days(viz: dict | None) -> float | None:
    """Length in days of the period a viz CSV row describes (EndDate −
    StartDate), or None if either date is missing or unreadable."""
    if not viz or not viz.get("StartDate") or not viz.get("EndDate"):
        return None
    try:
        start = date.fromisoformat(str(viz["StartDate"]))
        end = date.fromisoformat(str(viz["EndDate"]))
    except ValueError:
        return None
    return float((end - start).days)


def fill_defaults_from_data(cfg: Config, discovery: Discovery) -> Config:
    """Fill the config keys whose defaults come from the IMI output itself.

    Reads the first period only: the sector list (when the config lists
    none) from its posterior.nc and viz CSV, the variable of a ``total``
    sector that names none (``total_sector_var``), and the cadence (when
    ``text.cadence`` is unset) from its StartDate/EndDate. Kalman-mode
    periods all have the same length, so one period is enough.
    """
    unresolved = any(s.imi_var is None for s in cfg.sectors)
    if cfg.sectors and not unresolved and cfg.text.cadence is not None:
        return cfg
    files = PeriodFiles.discover(discovery.periods[0].netcdf_dir)
    viz = read_viz_stats(files.viz_csv)
    update: dict = {}
    sectors = cfg.sectors
    if not sectors:
        sectors = default_sectors(available_sector_vars(files, viz))
        if not sectors:
            raise ValueError(
                f"no EmisCH4_* sector variables found in {files.posterior}; "
                "list the sectors in the config"
            )
    if any(s.imi_var is None for s in sectors):
        total_var = total_sector_var(files)
        sectors = [s.model_copy(update={"imi_var": total_var}) if s.imi_var is None else s for s in sectors]
    if sectors is not cfg.sectors:
        update["sectors"] = sectors
    if cfg.text.cadence is None:
        update["text"] = cfg.text.model_copy(update={"cadence": cadence_from_days(period_days(viz))})
    return cfg.model_copy(update=update)
