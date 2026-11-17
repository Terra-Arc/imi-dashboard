"""The total sector's numbers: prior, posterior and ensemble range, always
without the soil sink (the methane oxidised by soils, which the IMI stores
as negative emissions in ``EmisCH4_SoilAbsorb``).

IMI's statistics CSV (``viz_notebook_statistics.csv``) has two pairs of
totals that matter here:

- the headline ``PriorEmissions`` / ``PosteriorEmissions``, which its
  visualization notebook sums from whichever variable it picked as "the
  total". In Kalman mode it picks ``EmisCH4_Total`` (with the sink) for the
  prior and ``EmisCH4_Total_ExclSoilAbs`` (without it) for the posterior,
  so the headline pair mixes the two definitions (IMI < 2.3 sums
  ``EmisCH4_Total`` for both); and
- ``TotalExclSoilAbsPrior`` / ``TotalExclSoilAbsPosterior`` (IMI ≥ 2.2.1;
  IMI 2.1.0 and 2.2.0 name them ``TotalExclSoilAbsorbPrior`` /
  ``TotalExclSoilAbsorbPosterior``), the same sums of the variable without
  the sink on both sides, whatever the mode.

So the converter reads the second pair, and does not depend on how the
notebook fills the first. For an older CSV without it, the totals are
summed from the maps (``prior.nc`` and ``posterior.nc``) over the region of
interest, the way the IMI sums them; only if that is not possible are the
headline columns shown, with a warning.

The ensemble range (``EnsemblePosterior_Min``/``_Max``, IMI ≥ 2.2.1) is
summed without the sink from IMI 2.3.0 on, but with it in IMI 2.2.1
(``_ensemble_range``). From 2.2.1 on the posterior is the mean of the
ensemble's scale factors, and a total is linear in them, so
``EnsemblePosterior_Mean`` equals the posterior total of whichever
definition the ensemble was summed with: that tells the two apart, and a
range with the sink is moved by the sink, which is the same in every
member (IMI 2.2.1 cannot optimise it).

``headline_totals`` classifies how the headline pair relates to the others,
so that a layout this converter does not know (a change in IMI) is reported
rather than passed over.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

from .readers import (
    TOTAL_EXCL_SOIL_SINK_VAR,
    TOTAL_VAR,
    PeriodFiles,
    csv_number,
    read_buffer_regions,
    read_emissions,
)

if TYPE_CHECKING:
    from .grid import GridMeta

log = logging.getLogger(__name__)

# The statistics CSV's columns.
HEADLINE_PRIOR = "PriorEmissions"
HEADLINE_POSTERIOR = "PosteriorEmissions"
PRIOR_WITHOUT_SINK = "TotalExclSoilAbsPrior"
POSTERIOR_WITHOUT_SINK = "TotalExclSoilAbsPosterior"
# compat: IMI < 2.2.1 (2.1.0 and 2.2.0 name the same pair this way)
PRIOR_WITHOUT_SINK_ABSORB = "TotalExclSoilAbsorbPrior"
POSTERIOR_WITHOUT_SINK_ABSORB = "TotalExclSoilAbsorbPosterior"
WITHOUT_SINK_PAIRS = (
    (PRIOR_WITHOUT_SINK, POSTERIOR_WITHOUT_SINK),
    (PRIOR_WITHOUT_SINK_ABSORB, POSTERIOR_WITHOUT_SINK_ABSORB),
)
PRIOR_WITH_SINK = "TotalPrior"
POSTERIOR_WITH_SINK = "TotalPosterior"
ENSEMBLE_MIN = "EnsemblePosterior_Min"
ENSEMBLE_MAX = "EnsemblePosterior_Max"
ENSEMBLE_MEAN = "EnsemblePosterior_Mean"
SINK_ENSEMBLE_MIN = "SoilAbsorb_ensMin"
SINK_ENSEMBLE_MAX = "SoilAbsorb_ensMax"

# Recorded in manifest.source: a dashboard whose source lacks it, or holds
# another value, took its totals from elsewhere and is reconverted in full.
# "-2": the ensemble range of IMI 2.2.1 runs is moved off the soil sink.
TOTAL_NUMBERS = "without-soil-sink-2"

# Two CSV values are the same number when they agree to this relative
# tolerance (they are sums of the same field, written with full precision).
_SAME = 1e-6
# Seconds in the IMI's year: its sum_total_emissions converts kg/s to Tg/yr
# with 365-day years.
_SECONDS_PER_YEAR = 86400 * 365
# A sum of the maps reproduces a CSV total when they agree to this relative
# tolerance (the maps are read as float32).
_REPRODUCED = 1e-5


class HeadlineTotals(Enum):
    """How the CSV's ``PriorEmissions``/``PosteriorEmissions`` relate to its
    totals with and without the soil sink."""

    NO_SPLIT = "no TotalExclSoilAbs columns (IMI before 2.1.0)"
    WITHOUT_SINK = "both without the soil sink"
    WITH_SINK = "both with the soil sink (IMI < 2.3, or OptimizeSoil)"
    PRIOR_WITH_SINK = "prior with the soil sink, posterior without it (IMI's Kalman mode)"
    UNKNOWN = "neither total with nor without the soil sink"


class TotalSource(Enum):
    """Where a period's total numbers came from."""

    CSV = "csv"            # TotalExclSoilAbs(orb)Prior / ...Posterior
    MAPS = "maps"          # summed from prior.nc and posterior.nc (IMI before 2.1.0)
    HEADLINE = "headline"  # PriorEmissions / PosteriorEmissions, sink not known
    NONE = "none"          # no statistics CSV


@dataclass(frozen=True)
class TotalNumbers:
    """A period's total emissions in Tg/yr (= Mt/yr), for the total sector."""

    prior: float | None
    posterior: float | None
    ens_min: float | None
    ens_max: float | None
    source: TotalSource
    # Why the maps could not give the totals (source HEADLINE only).
    maps_problem: str | None = None
    # The CSV columns the prior and posterior were read from (source CSV).
    columns: tuple[str, str] | None = None
    # The soil sink taken out of an ensemble range the IMI summed with it
    # (IMI 2.2.1), in Tg/yr; None when the range needed no correction.
    ensemble_sink_removed: float | None = None
    # Why an ensemble range that includes the sink could not be corrected
    # (it is then left out).
    ensemble_problem: str | None = None

    @property
    def includes_soil_sink(self) -> bool | None:
        """False when the numbers are known to leave the sink out; None when
        they are IMI's headline columns (or there are none)."""
        return False if self.source in (TotalSource.CSV, TotalSource.MAPS) else None


def headline_totals(viz: dict | None) -> HeadlineTotals | None:
    """Classify the CSV's headline pair against its totals with and without
    the soil sink; None without a CSV.

    When the sink is zero the two definitions coincide and the row counts as
    ``WITHOUT_SINK``.
    """
    if viz is None:
        return None
    found = _without_sink(viz)
    if found is None:
        return HeadlineTotals.NO_SPLIT
    prior_without, posterior_without, _ = found

    def kinds(value: float | None, without: float, with_: float | None) -> set[str]:
        out: set[str] = set()
        if value is None:
            return out
        if math.isclose(value, without, rel_tol=_SAME):
            out.add("without")
        if with_ is not None and math.isclose(value, with_, rel_tol=_SAME):
            out.add("with")
        return out

    prior = kinds(csv_number(viz, HEADLINE_PRIOR), prior_without, csv_number(viz, PRIOR_WITH_SINK))
    posterior = kinds(csv_number(viz, HEADLINE_POSTERIOR), posterior_without, csv_number(viz, POSTERIOR_WITH_SINK))
    if "without" in prior and "without" in posterior:
        return HeadlineTotals.WITHOUT_SINK
    if "with" in prior and "with" in posterior:
        return HeadlineTotals.WITH_SINK
    if "with" in prior and "without" in posterior:
        return HeadlineTotals.PRIOR_WITH_SINK
    return HeadlineTotals.UNKNOWN


def total_numbers(
    viz: dict | None,
    files: PeriodFiles,
    grid: GridMeta,
    area_m2: np.ndarray | None,
) -> TotalNumbers:
    """The total's prior, posterior and ensemble range, without the soil sink.

    From the CSV's ``TotalExclSoilAbsPrior``/``TotalExclSoilAbsPosterior``
    (or their IMI 2.1.0/2.2.0 names) and ensemble columns when it has them,
    the ensemble range without the sink (``_ensemble_range``). For an older
    CSV without them, the sums over the region of interest of
    ``EmisCH4_Total_ExclSoilAbs`` in ``prior.nc`` and ``posterior.nc``
    (``_map_totals``), with no ensemble range (those CSVs have none).
    Failing that, the headline columns. No numbers without a CSV.
    """
    if viz is None:
        return TotalNumbers(None, None, None, None, TotalSource.NONE)
    found = _without_sink(viz)
    if found is not None:
        prior, posterior, columns = found
        ens_min, ens_max, removed, problem = _ensemble_range(viz, posterior)
        return TotalNumbers(
            prior, posterior, ens_min, ens_max, TotalSource.CSV,
            columns=columns, ensemble_sink_removed=removed, ensemble_problem=problem,
        )
    # compat: IMI before 2.1.0 (its development versions)
    # Older CSVs have only the headline pair, which includes the sink; the
    # maps give the totals without it.
    sums, problem = _map_totals(viz, files, grid, area_m2)
    if sums is not None:
        return TotalNumbers(sums[0], sums[1], None, None, TotalSource.MAPS)
    return TotalNumbers(
        csv_number(viz, HEADLINE_PRIOR),
        csv_number(viz, HEADLINE_POSTERIOR),
        csv_number(viz, ENSEMBLE_MIN),
        csv_number(viz, ENSEMBLE_MAX),
        TotalSource.HEADLINE,
        maps_problem=problem,
    )


def _without_sink(viz: dict) -> tuple[float, float, tuple[str, str]] | None:
    """The CSV's prior and posterior totals without the soil sink and the
    columns they were read from, under the name of IMI ≥ 2.2.1 or that of
    IMI 2.1.0/2.2.0; None when the CSV has neither pair."""
    for prior_column, posterior_column in WITHOUT_SINK_PAIRS:
        prior, posterior = csv_number(viz, prior_column), csv_number(viz, posterior_column)
        if prior is not None and posterior is not None:
            return prior, posterior, (prior_column, posterior_column)
    return None


def _ensemble_range(
    viz: dict, posterior_without: float,
) -> tuple[float | None, float | None, float | None, str | None]:
    """The ensemble range without the soil sink: ``(min, max, sink removed,
    problem)``.

    The IMI writes the range without the sink from 2.3.0 on, and with it in
    2.2.1. The posterior is the mean of the ensemble's scale factors in
    both, and a total is linear in them, so ``EnsemblePosterior_Mean``
    equals the posterior total of the definition the ensemble was summed
    with: ``TotalExclSoilAbsPosterior`` (no change) or ``TotalPosterior``
    (the range includes the sink). The sink is the same in every member
    (IMI 2.2.1 cannot optimise it; ``SoilAbsorb_ensMin`` equals
    ``SoilAbsorb_ensMax``), so it is ``TotalPosterior −
    TotalExclSoilAbsPosterior`` and is taken off both ends. If the members'
    sinks differ, the range cannot be corrected and is left out. When the
    mean matches neither total (or a column is missing), the range is
    taken as written.
    """
    ens_min, ens_max = csv_number(viz, ENSEMBLE_MIN), csv_number(viz, ENSEMBLE_MAX)
    mean, posterior_with = csv_number(viz, ENSEMBLE_MEAN), csv_number(viz, POSTERIOR_WITH_SINK)
    if ens_min is None or ens_max is None or mean is None or posterior_with is None:
        return ens_min, ens_max, None, None
    if math.isclose(mean, posterior_without, rel_tol=_SAME) or not math.isclose(mean, posterior_with, rel_tol=_SAME):
        return ens_min, ens_max, None, None
    # compat: IMI 2.2.1 (its ensemble range includes the soil sink)
    sink_min, sink_max = csv_number(viz, SINK_ENSEMBLE_MIN), csv_number(viz, SINK_ENSEMBLE_MAX)
    if sink_min is not None and sink_max is not None and not math.isclose(sink_min, sink_max, rel_tol=_SAME):
        return None, None, None, (
            f"the soil sink differs between ensemble members ({SINK_ENSEMBLE_MIN} {sink_min:.6g}, "
            f"{SINK_ENSEMBLE_MAX} {sink_max:.6g} Tg/yr)"
        )
    sink = posterior_with - posterior_without
    return ens_min - sink, ens_max - sink, sink, None


def _map_totals(
    viz: dict,
    files: PeriodFiles,
    grid: GridMeta,
    area_m2: np.ndarray | None,
) -> tuple[tuple[float, float] | None, str | None]:
    """The sums of ``EmisCH4_Total_ExclSoilAbs`` in prior.nc and posterior.nc
    over the region of interest, times the cell areas, in Tg/yr, as the IMI
    sums its totals; or None and the reason they cannot be had.

    The region of interest is the state vector's, without its buffer
    elements (``read_buffer_regions``). The sums are used only if summing
    posterior.nc's total the same way, with or without the sink,
    reproduces the CSV's ``PosteriorEmissions``: that is the check that the
    region and the cell areas are the ones the IMI summed over.
    """
    if area_m2 is None:
        return None, "posterior.nc has no AREA variable"
    regions = read_buffer_regions(files.state_vector, grid)
    if regions is None:
        return None, "there is no state_vector.nc to take the region of interest from"
    headline = csv_number(viz, HEADLINE_POSTERIOR)
    if headline is None:
        return None, f"the statistics CSV has no {HEADLINE_POSTERIOR} to check the sums against"
    posterior = read_emissions(files.posterior, grid, [TOTAL_EXCL_SOIL_SINK_VAR, TOTAL_VAR])
    prior = read_emissions(files.prior, grid, [TOTAL_EXCL_SOIL_SINK_VAR])
    if TOTAL_EXCL_SOIL_SINK_VAR not in posterior or TOTAL_EXCL_SOIL_SINK_VAR not in prior:
        return None, f"prior.nc or posterior.nc has no {TOTAL_EXCL_SOIL_SINK_VAR}"
    roi = regions.roi_mask

    def tg_per_yr(field: np.ndarray) -> float:
        return float(np.nansum(field[roi].astype(np.float64) * area_m2[roi])) * _SECONDS_PER_YEAR / 1e9

    checks = {v: tg_per_yr(f) for v, f in posterior.items()}
    if not any(math.isclose(s, headline, rel_tol=_REPRODUCED) for s in checks.values()):
        sums = ", ".join(f"{v} {s:.6g}" for v, s in checks.items())
        return None, (
            f"summing posterior.nc over the region of interest ({sums} Tg/yr) does not "
            f"reproduce its {HEADLINE_POSTERIOR}, {headline:.6g} Tg/yr"
        )
    return (tg_per_yr(prior[TOTAL_EXCL_SOIL_SINK_VAR]), checks[TOTAL_EXCL_SOIL_SINK_VAR]), None


class UnknownTotalsLayoutWarning(UserWarning):
    """The CSV's PriorEmissions/PosteriorEmissions match neither its totals
    with nor without the soil sink: a layout this converter does not know."""


class TotalMayIncludeSoilSinkWarning(UserWarning):
    """No total without the soil sink could be had for some periods, so
    the CSV's headline totals are shown."""


class EnsembleRangeLeftOutWarning(UserWarning):
    """An ensemble range summed with the soil sink could not be moved off
    it, so it is not shown."""


def _labels(labels: list[str], limit: int = 6) -> str:
    shown = ", ".join(labels[:limit])
    return shown if len(labels) <= limit else f"{shown} and {len(labels) - limit} more"


def report_totals(checked: list[tuple[str, TotalNumbers, HeadlineTotals | None]]) -> None:
    """Report, once for all periods, what the per-period checks found:
    ``(label, numbers, headline)`` for each period converted.

    A warning for headline columns in an unknown layout, for periods shown
    with the headline columns and for ensemble ranges left out; a log line
    for the known IMI issues the converter corrects (Kalman mode's headline
    prior, IMI 2.2.1's ensemble range).
    """
    unknown = [label for label, _, h in checked if h is HeadlineTotals.UNKNOWN]
    if unknown:
        warnings.warn(
            f"in {len(unknown)} period(s) ({_labels(unknown)}), the statistics CSV's "
            f"{HEADLINE_PRIOR} and {HEADLINE_POSTERIOR} match neither its totals with the soil "
            f"sink ({PRIOR_WITH_SINK}, {POSTERIOR_WITH_SINK}) nor without it "
            f"({PRIOR_WITHOUT_SINK}, {POSTERIOR_WITHOUT_SINK}), a layout this version of "
            "imi-dashboard does not know (the IMI may have changed its statistics file). The "
            "dashboard shows the totals without the soil sink; check them, and whether a newer "
            "imi-dashboard is available",
            UnknownTotalsLayoutWarning,
            stacklevel=2,
        )
    mixed = [label for label, _, h in checked if h is HeadlineTotals.PRIOR_WITH_SINK]
    if mixed:
        log.info(
            "%d period(s): the statistics CSV's %s includes the soil sink and its %s does not "
            "(IMI's Kalman mode); the dashboard shows %s and %s instead",
            len(mixed), HEADLINE_PRIOR, HEADLINE_POSTERIOR, PRIOR_WITHOUT_SINK, POSTERIOR_WITHOUT_SINK,
        )
    headline = [(label, n.maps_problem) for label, n, _ in checked if n.source is TotalSource.HEADLINE]
    if headline:
        reasons = list(dict.fromkeys(p for _, p in headline if p))
        why = "; ".join(reasons[:3]) + (f"; and {len(reasons) - 3} more" if len(reasons) > 3 else "")
        warnings.warn(
            f"in {len(headline)} period(s) ({_labels([label for label, _ in headline])}), the "
            f"statistics CSV has no {PRIOR_WITHOUT_SINK} and {POSTERIOR_WITHOUT_SINK} and the "
            f"totals without the soil sink cannot be summed from the maps ({why}), "
            f"so the total's numbers are the CSV's {HEADLINE_PRIOR} and {HEADLINE_POSTERIOR}, "
            "which may include the soil sink",
            TotalMayIncludeSoilSinkWarning,
            stacklevel=2,
        )
    corrected = [label for label, n, _ in checked if n.ensemble_sink_removed is not None]
    if corrected:
        log.info(
            "%d period(s): the statistics CSV's ensemble range (%s, %s) includes the soil sink "
            "(IMI 2.2.1); the dashboard shows it without the sink, like the total",
            len(corrected), ENSEMBLE_MIN, ENSEMBLE_MAX,
        )
    dropped = [(label, n.ensemble_problem) for label, n, _ in checked if n.ensemble_problem]
    if dropped:
        warnings.warn(
            f"in {len(dropped)} period(s) ({_labels([label for label, _ in dropped])}), the "
            f"statistics CSV's ensemble range includes the soil sink and cannot be corrected "
            f"({dropped[0][1]}), so the dashboard shows no ensemble range there",
            EnsembleRangeLeftOutWarning,
            stacklevel=2,
        )
