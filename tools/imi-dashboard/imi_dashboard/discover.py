"""Detect whether an input folder is a single-period or a timeseries run."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal


Mode = Literal["single", "timeseries"]

_PERIOD_DIR_RE = re.compile(r"^period_(\d+)_plots$")

# Files every period's netCDF/ must hold to be converted. A period without
# them (typically one whose IMI visualization notebook failed part-way) is
# skipped, see `split_incomplete`.
REQUIRED_FILES = ("posterior.nc", "prior.nc")


@dataclass(frozen=True)
class PeriodInput:
    """One inversion period on disk."""

    idx: int                 # 0-based position in the timeseries (always 0 for single)
    number: int              # folder number parsed from period_<N>_plots (0 for single);
                             # non-contiguous when periods are missing on disk
    label: str               # display label, e.g. "period_110" or "single"
    folder: Path             # the period's root folder containing netCDF/ + CSVs + PNGs
    netcdf_dir: Path         # always folder / "netCDF"


@dataclass(frozen=True)
class Discovery:
    mode: Mode
    periods: list[PeriodInput]


def discover(input_folder: Path) -> Discovery:
    """Inspect an input folder and return the discovered periods.

    Detection rules:
    - If the folder itself contains a ``netCDF/`` subdir → single-period run.
    - Otherwise, scan for ``period_<N>_plots/`` subdirs, sort by N → timeseries.
    - Anything else is an error.
    """
    if not input_folder.is_dir():
        raise FileNotFoundError(f"input folder does not exist: {input_folder}")

    direct_netcdf = input_folder / "netCDF"
    if direct_netcdf.is_dir():
        return Discovery(
            mode="single",
            periods=[PeriodInput(
                idx=0,
                number=0,
                label="single",
                folder=input_folder,
                netcdf_dir=direct_netcdf,
            )],
        )

    period_dirs: list[tuple[int, Path]] = []
    for child in input_folder.iterdir():
        if not child.is_dir():
            continue
        m = _PERIOD_DIR_RE.match(child.name)
        if m and (child / "netCDF").is_dir():
            period_dirs.append((int(m.group(1)), child))

    if not period_dirs:
        raise ValueError(
            f"{input_folder}: not a single-period folder (no netCDF/ subdir) "
            f"and no period_<N>_plots/ subdirs found" + _runs_below_hint(input_folder)
        )

    period_dirs.sort(key=lambda t: t[0])
    return Discovery(
        mode="timeseries",
        periods=[
            PeriodInput(
                idx=i,
                number=n,
                label=f"period_{n}",
                folder=path,
                netcdf_dir=path / "netCDF",
            )
            for i, (n, path) in enumerate(period_dirs)
        ],
    )


def _looks_like_run(folder: Path) -> bool:
    """A single-period run (netCDF/) or a timeseries (a period_<N>_plots/netCDF/)."""
    if (folder / "netCDF").is_dir():
        return True
    return any(
        child.is_dir() and _PERIOD_DIR_RE.match(child.name) and (child / "netCDF").is_dir()
        for child in folder.iterdir()
    )


def _runs_below_hint(folder: Path, limit: int = 5) -> str:
    """``; it holds IMI runs: a, b`` for a folder whose subfolders are runs
    (a folder of runs given instead of one of them), else ``""``."""
    try:
        runs = sorted(c for c in folder.iterdir() if c.is_dir() and _looks_like_run(c))
    except OSError:
        return ""
    if not runs:
        return ""
    shown = ", ".join(str(r) for r in runs[:limit])
    more = f" and {len(runs) - limit} more" if len(runs) > limit else ""
    return f"; it holds IMI runs, pass one of them: {shown}{more}"


@dataclass(frozen=True)
class SkippedPeriod:
    """A discovered period left out because it lacks a required file."""

    period: PeriodInput
    missing: tuple[str, ...]     # names from REQUIRED_FILES absent from netCDF/


def split_incomplete(discovery: Discovery) -> tuple[Discovery, list[SkippedPeriod]]:
    """Separate the periods that lack a file in ``REQUIRED_FILES``.

    Returns the discovery restricted to complete periods, re-indexed so that
    ``idx`` is again a contiguous 0-based range (the folder ``number`` is kept,
    so a skipped period shows as a gap in the numbering, like a missing
    folder), together with the skipped periods. Raises ``ValueError`` if no
    period is complete.
    """
    complete: list[PeriodInput] = []
    skipped: list[SkippedPeriod] = []
    for p in discovery.periods:
        missing = tuple(f for f in REQUIRED_FILES if not (p.netcdf_dir / f).is_file())
        if missing:
            skipped.append(SkippedPeriod(period=p, missing=missing))
        else:
            complete.append(replace(p, idx=len(complete)))
    if not complete:
        where = ", ".join(f"{s.period.label} ({', '.join(s.missing)})" for s in skipped)
        raise ValueError(
            f"no period has both {' and '.join(REQUIRED_FILES)}; missing in: {where}"
        )
    return Discovery(mode=discovery.mode, periods=complete), skipped


def split_unreadable(
    discovery: Discovery, problem: Callable[[PeriodInput], str | None]
) -> tuple[Discovery, list[tuple[PeriodInput, str]]]:
    """Separate the periods for which ``problem`` names a reason they cannot
    be read (a required file truncated while IMI was still writing it, for
    one), re-indexed like ``split_incomplete``. Returns the discovery of the
    remaining periods and the (period, reason) pairs left out. Raises
    ``ValueError`` if no period remains."""
    kept: list[PeriodInput] = []
    left_out: list[tuple[PeriodInput, str]] = []
    for p in discovery.periods:
        why = problem(p)
        if why is None:
            kept.append(replace(p, idx=len(kept)))
        else:
            left_out.append((p, why))
    if not kept:
        where = "; ".join(f"{p.label} ({why})" for p, why in left_out)
        raise ValueError(f"no period can be read: {where}")
    return Discovery(mode=discovery.mode, periods=kept), left_out


def describe_skipped(skipped: list[SkippedPeriod]) -> str:
    """One-line, human-readable list of skipped periods and what they lack."""
    return "; ".join(
        f"{s.period.label} (no {' or '.join(s.missing)})" for s in skipped
    )
