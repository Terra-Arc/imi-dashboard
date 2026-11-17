"""The total's numbers, always without the soil sink (totals.py).

IMI's statistics CSV carries the total twice. The headline pair
(PriorEmissions, PosteriorEmissions) is summed from whichever variable the
visualization notebook picked; in Kalman mode it picks the total with the
soil sink for the prior and without it for the posterior. The
TotalExclSoilAbsPrior/Posterior pair is the total without the sink on both
sides in every mode. The converter reads the second pair, so the tests below
check that the dashboard's numbers are the same whichever way the headline
pair is filled: as IMI fills it today in single-period runs (the Permian
sample as published), in Kalman mode, with OptimizeSoil, and after a fix
of IMI's Kalman branch, which would make it equal to the published sample.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import pytest

from imi_dashboard.readers import PeriodFiles, open_emissions_grid, read_area, read_viz_stats
from imi_dashboard.totals import (
    EnsembleRangeLeftOutWarning,
    HeadlineTotals,
    TotalMayIncludeSoilSinkWarning,
    TotalSource,
    UnknownTotalsLayoutWarning,
    headline_totals,
    report_totals,
    total_numbers,
)

from .conftest import SINGLE, TIMESERIES

# The Permian sample's CSV (IMI 2.3.1, single period), Tg/yr.
PRIOR_WITHOUT, POSTERIOR_WITHOUT = 0.4087327477516389, 0.602334359307776
PRIOR_WITH, POSTERIOR_WITH = 0.3853649695308958, 0.578966569306703
# As written in the CSV. pandas 3.1 parses ENS_MIN exactly; older releases'
# default parser lands one unit in the last place lower, so compare read
# values with a tolerance.
ENS_MIN, ENS_MAX = 0.49386518631926585, 0.7823478220013
# The soil sink in the posterior: TotalPosterior - TotalExclSoilAbsPosterior.
SINK = POSTERIOR_WITH - POSTERIOR_WITHOUT  # -0.023368


def _permian(**changes: float | None) -> dict:
    viz = read_viz_stats(SINGLE / "viz_notebook_statistics.csv")
    assert viz is not None
    assert (viz["PriorEmissions"], viz["PosteriorEmissions"]) == (PRIOR_WITHOUT, POSTERIOR_WITHOUT)
    assert (viz["TotalPrior"], viz["TotalPosterior"]) == (PRIOR_WITH, POSTERIOR_WITH)
    for key, value in changes.items():
        if value is None:
            viz.pop(key)
        else:
            viz[key] = value
    return viz


# The ways IMI fills the headline pair, as variants of the Permian CSV.
LAYOUTS = {
    # Single-period runs, and Kalman runs once IMI's notebook is fixed.
    "published": ({}, HeadlineTotals.WITHOUT_SINK),
    # Kalman mode today: prior with the sink, posterior without it.
    "kalman": ({"PriorEmissions": PRIOR_WITH}, HeadlineTotals.PRIOR_WITH_SINK),
    # OptimizeSoil: both with the sink (the ensemble range still without).
    "optimize_soil": (
        {"PriorEmissions": PRIOR_WITH, "PosteriorEmissions": POSTERIOR_WITH}, HeadlineTotals.WITH_SINK,
    ),
    # Anything else: a change in IMI this converter does not know.
    "unknown": ({"PriorEmissions": 0.5}, HeadlineTotals.UNKNOWN),
}


def _period(folder: Path):
    files = PeriodFiles.discover(folder / "netCDF")
    grid = open_emissions_grid(files.posterior)
    return files, grid, read_area(files.posterior, grid)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_headline_totals_are_classified(layout):
    changes, expected = LAYOUTS[layout]
    assert headline_totals(_permian(**changes)) is expected


def test_headline_totals_without_the_split_columns_or_a_csv():
    assert headline_totals(_permian(TotalExclSoilAbsPrior=None, TotalExclSoilAbsPosterior=None)) is HeadlineTotals.NO_SPLIT
    assert headline_totals(None) is None


@pytest.mark.parametrize("layout", LAYOUTS)
def test_total_numbers_do_not_depend_on_the_headline_columns(layout):
    """Whichever way the headline pair is filled, the total's numbers are
    TotalExclSoilAbsPrior/Posterior and the ensemble range: a fix of IMI's
    Kalman branch changes nothing on the dashboard."""
    numbers = total_numbers(_permian(**LAYOUTS[layout][0]), *_period(SINGLE))
    assert numbers.source is TotalSource.CSV
    assert (numbers.prior, numbers.posterior) == (PRIOR_WITHOUT, POSTERIOR_WITHOUT)
    assert (numbers.ens_min, numbers.ens_max) == pytest.approx((ENS_MIN, ENS_MAX), abs=1e-12)
    assert numbers.ensemble_sink_removed is None
    assert numbers.columns == ("TotalExclSoilAbsPrior", "TotalExclSoilAbsPosterior")
    assert numbers.includes_soil_sink is False


def _imi_2_2_0() -> dict:
    """The Permian CSV laid out as IMI 2.1.0 and 2.2.0 write it: the total
    without the sink is named TotalExclSoilAbsorbPrior/Posterior, the
    headline pair is the total with the sink, and there is no ensemble."""
    viz = _permian(
        TotalExclSoilAbsPrior=None, TotalExclSoilAbsPosterior=None,
        PriorEmissions=PRIOR_WITH, PosteriorEmissions=POSTERIOR_WITH,
        EnsemblePosterior_Min=None, EnsemblePosterior_Max=None, EnsemblePosterior_Mean=None,
    )
    viz["TotalExclSoilAbsorbPrior"], viz["TotalExclSoilAbsorbPosterior"] = PRIOR_WITHOUT, POSTERIOR_WITHOUT
    return viz


def test_imi_2_2_0_total_is_read_under_its_older_name():
    viz = _imi_2_2_0()
    assert headline_totals(viz) is HeadlineTotals.WITH_SINK
    numbers = total_numbers(viz, *_period(SINGLE))
    assert numbers.source is TotalSource.CSV
    assert numbers.columns == ("TotalExclSoilAbsorbPrior", "TotalExclSoilAbsorbPosterior")
    assert (numbers.prior, numbers.posterior) == (PRIOR_WITHOUT, POSTERIOR_WITHOUT)
    assert (numbers.ens_min, numbers.ens_max) == (None, None)
    assert numbers.includes_soil_sink is False


def _imi_2_2_1(**changes: float | None) -> dict:
    """The Permian CSV as IMI 2.2.1 would write it: the headline pair and
    the ensemble are summed with the sink, so the ensemble mean (the
    posterior is the mean of the members' scale factors) equals
    TotalPosterior, and the range is lower by the sink, 0.023368 Tg/yr."""
    return _permian(**{
        "PriorEmissions": PRIOR_WITH, "PosteriorEmissions": POSTERIOR_WITH,
        "EnsemblePosterior_Mean": POSTERIOR_WITH,
        "EnsemblePosterior_Min": ENS_MIN + SINK, "EnsemblePosterior_Max": ENS_MAX + SINK,
        **changes,
    })


def test_imi_2_2_1_ensemble_range_is_moved_off_the_soil_sink():
    numbers = total_numbers(_imi_2_2_1(), *_period(SINGLE))
    assert numbers.source is TotalSource.CSV
    assert (numbers.prior, numbers.posterior) == (PRIOR_WITHOUT, POSTERIOR_WITHOUT)
    assert numbers.ensemble_sink_removed == pytest.approx(-0.023367790001073, abs=1e-12)
    assert numbers.ens_min == pytest.approx(ENS_MIN, abs=1e-12)
    assert numbers.ens_max == pytest.approx(ENS_MAX, abs=1e-12)
    assert numbers.ens_min < numbers.posterior < numbers.ens_max


def test_ensemble_range_is_left_out_when_the_members_sinks_differ():
    numbers = total_numbers(_imi_2_2_1(SoilAbsorb_ensMin=-0.03), *_period(SINGLE))
    assert (numbers.ens_min, numbers.ens_max, numbers.ensemble_sink_removed) == (None, None, None)
    assert numbers.ensemble_problem is not None and "differs between ensemble members" in numbers.ensemble_problem


def test_ensemble_range_is_kept_when_its_mean_matches_neither_total():
    """A mean that equals neither posterior total (e.g. members excluded
    from it) does not say how the range was summed: it is shown as written."""
    numbers = total_numbers(_imi_2_2_1(EnsemblePosterior_Mean=0.59), *_period(SINGLE))
    assert (numbers.ens_min, numbers.ens_max) == (ENS_MIN + SINK, ENS_MAX + SINK)
    assert numbers.ensemble_sink_removed is None


def test_older_csv_totals_are_summed_from_the_maps():
    """The Romania CSV (older IMI) has only the headline pair, which includes
    the soil sink (period 1: prior 1.299558, posterior 0.931382 Tg/yr). The
    totals without it are the sums of EmisCH4_Total_ExclSoilAbs over the
    region of interest: those values plus the sink, 0.017264 Tg/yr."""
    folder = TIMESERIES / "period_1_plots"
    viz = read_viz_stats(folder / "viz_notebook_statistics.csv")
    assert viz is not None and "TotalExclSoilAbsPrior" not in viz
    numbers = total_numbers(viz, *_period(folder))
    assert numbers.source is TotalSource.MAPS
    assert numbers.prior == pytest.approx(1.316822, abs=1e-6)
    assert numbers.posterior == pytest.approx(0.948646, abs=1e-6)
    assert numbers.prior - viz["PriorEmissions"] == pytest.approx(0.017264, abs=1e-6)
    assert numbers.posterior - viz["PosteriorEmissions"] == pytest.approx(0.017264, abs=1e-6)
    assert (numbers.ens_min, numbers.ens_max) == (None, None)
    assert numbers.includes_soil_sink is False


def test_map_sums_are_not_used_when_they_do_not_reproduce_the_csv():
    """If summing posterior.nc does not reproduce the CSV's posterior (here
    the CSV value is changed by 10 %), the region or the cell areas differ
    from the IMI's, and the headline pair is shown instead."""
    folder = TIMESERIES / "period_1_plots"
    viz = read_viz_stats(folder / "viz_notebook_statistics.csv")
    assert viz is not None
    viz["PosteriorEmissions"] *= 1.1
    numbers = total_numbers(viz, *_period(folder))
    assert numbers.source is TotalSource.HEADLINE
    assert (numbers.prior, numbers.posterior) == (viz["PriorEmissions"], viz["PosteriorEmissions"])
    assert numbers.includes_soil_sink is None
    assert numbers.maps_problem is not None and "does not reproduce its PosteriorEmissions" in numbers.maps_problem


def test_no_csv_no_numbers():
    numbers = total_numbers(None, *_period(SINGLE))
    assert numbers.source is TotalSource.NONE
    assert (numbers.prior, numbers.posterior, numbers.ens_min, numbers.ens_max) == (None, None, None, None)


def test_report_names_unknown_layouts_and_headline_periods(caplog):
    files, grid, area = _period(SINGLE)
    known = [(f"p{i}", total_numbers(v, files, grid, area), headline_totals(v))
             for i, v in enumerate([_permian(), _permian(PriorEmissions=PRIOR_WITH)])]
    # The known layouts give no warning; Kalman mode's is logged.
    with caplog.at_level(logging.INFO, logger="imi_dashboard"), warnings.catch_warnings():
        warnings.simplefilter("error")
        report_totals(known)
    assert "1 period(s): the statistics CSV's PriorEmissions includes the soil sink" in caplog.text

    unknown = _permian(PriorEmissions=0.5)
    with pytest.warns(UnknownTotalsLayoutWarning, match=r"in 1 period\(s\) \(p9\).*does not know"):
        report_totals(known + [("p9", total_numbers(unknown, files, grid, area), headline_totals(unknown))])

    folder = TIMESERIES / "period_1_plots"
    viz = read_viz_stats(folder / "viz_notebook_statistics.csv")
    assert viz is not None
    viz["PosteriorEmissions"] *= 1.1
    with pytest.warns(TotalMayIncludeSoilSinkWarning, match=r"in 1 period\(s\) \(period_1\).*may include the soil sink"):
        report_totals([("period_1", total_numbers(viz, *_period(folder)), headline_totals(viz))])


def test_report_names_corrected_and_left_out_ensemble_ranges(caplog):
    files, grid, area = _period(SINGLE)
    corrected = _imi_2_2_1()
    with caplog.at_level(logging.INFO, logger="imi_dashboard"), warnings.catch_warnings():
        warnings.simplefilter("error")
        report_totals([("p1", total_numbers(corrected, files, grid, area), headline_totals(corrected))])
    assert "1 period(s): the statistics CSV's ensemble range" in caplog.text
    assert "(IMI 2.2.1)" in caplog.text

    varying = _imi_2_2_1(SoilAbsorb_ensMin=-0.03)
    with pytest.warns(EnsembleRangeLeftOutWarning, match=r"in 1 period\(s\) \(p2\).*no ensemble range"):
        report_totals([("p2", total_numbers(varying, files, grid, area), headline_totals(varying))])
