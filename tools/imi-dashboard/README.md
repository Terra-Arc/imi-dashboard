# imi-dashboard

Turns the outputs of an [Integrated Methane Inversion](https://imi.seas.harvard.edu/)
(IMI) run, one period or a timeseries, into a static web dashboard that any
static web host can serve. An optional YAML config sets branding, sectors and
wording. Reads IMI 2.1.0 and later.

## Install

Python 3.11 or later. Install the wheel from the
[releases page](https://github.com/Terra-Arc/imi-dashboard/releases) (no
Node.js needed):

```bash
pip install imi_dashboard-<version>-py3-none-any.whl
```

From source: see the project's [Install](https://github.com/Terra-Arc/imi-dashboard#install)
section.

## Usage

The input is `<run>/inversion/output/` (single inversion) or
`<run>/kf_inversions/output/` (Kalman filter).

```bash
imi-dashboard info        <input>                              # periods, gaps, grid, sectors, missing files
imi-dashboard init-config <input> [-o dashboard.yaml]          # optional: commented config with the run's values
imi-dashboard convert     <input> <output> [--config dashboard.yaml]
imi-dashboard serve       <output> --open                      # preview
```

`imi-dashboard <command> --help` lists options, exit statuses and examples.
Re-running `convert` into the same folder converts only new or changed
periods. `--region domain` keeps the buffer elements that the default `roi`
drops.

## Documentation

- [User guide](https://github.com/Terra-Arc/imi-dashboard/blob/main/docs/guide.md)
- [Running it with the IMI](https://github.com/Terra-Arc/imi-dashboard/blob/main/docs/imi-integration.md)
- [Changelog](https://github.com/Terra-Arc/imi-dashboard/blob/main/CHANGELOG.md)

Issues: <https://github.com/Terra-Arc/imi-dashboard/issues>
