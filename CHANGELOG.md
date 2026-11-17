# Changelog

All notable changes to this project are recorded here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-11-17

The first public release.

### Added

- **`imi-dashboard`**, a command-line converter from the outputs of an
  [Integrated Methane Inversion](https://imi.seas.harvard.edu/) (IMI) run to a
  static web dashboard. Reads IMI 2.1.0 and later (and development code from
  November 2024 on), single inversions or Kalman-filter timeseries. Commands:
  - `convert` builds or updates a dashboard; a re-run converts only new or
    changed periods, never deletes files it did not write, and asks before
    removing periods that left the input.
  - `info` describes a run or a dashboard, as text or JSON (`--json`).
  - `init-config` writes a commented config with the run's detected values.
  - `serve` previews a dashboard locally.
- **The dashboard**: per-period emission maps by sector over a configurable
  base map; posterior and prior totals with the ensemble range; a timeseries
  chart (line, area, candle, bar) with playback, or a prior/posterior
  comparison for a single inversion; a diagnostics page with six maps and the
  scalar diagnostics (DOFS, super-observations, bias, RMSE). The total
  excludes the soil sink, and says so. Period data loads on demand.
- **Optional YAML config** for title, wording, sectors, branding, colour scale
  and base map; without it, sectors and wording come from the run.
- **Buffer-element detection** from the state vector; maps cropped to the
  region of interest (`--region roi`, default) or showing the whole domain
  (`--region domain`).
- **Console output** for terminals (progress bar, coloured warnings) and logs
  (timestamped lines), with `-v`, `-q`, `--log-style` and `--log-file` on
  every command.
- **Two sample runs** in `sample_files/`: the IMI team's Permian Basin
  quick-start (IMI 2.3.1, one period) and three periods of a Romania run (IMI
  development version, one period missing on purpose; for testing and
  demonstration only).

[Unreleased]: https://github.com/Terra-Arc/imi-dashboard/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Terra-Arc/imi-dashboard/releases/tag/v0.1.0
