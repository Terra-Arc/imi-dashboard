# Running the dashboard as part of an IMI run

Where `imi-dashboard convert` fits in an
[Integrated Methane Inversion](https://github.com/geoschem/integrated_methane_inversion)
(IMI) run, and a proposed optional IMI step that builds the dashboard. File
references are to IMI `main` at commit `889ed03` (August 2026; latest release
`imi-2.3.1`).

**In short:** build the dashboard right after the IMI's visualization
notebook, which writes everything the converter reads. The IMI's inversion
code needs no change.

## What the IMI writes, and when

`run_posterior()` (`src/components/posterior_component/posterior.sh`) ends by
calling `run_notebooks` (`src/components/inversion_component/inversion.sh`),
which executes `visualization_notebook.ipynb`: once in standard mode, once per
period in Kalman-filter mode (`KalmanMode: true`; `run_period()` in
`src/components/kalman_component/kalman.sh`). Its last cell,
`export_visualization_outputs()` (`src/inversion_scripts/utils.py`), writes:

| File | Contents | Used for |
|---|---|---|
| `viz_notebook_statistics.csv` | prior and posterior totals, sector totals, DOFS, bias, RMSE, ensemble range | totals, scalar diagnostics, ensemble range |
| `ensemble_statistics.csv` | one row per ensemble member | not used |
| `netCDF/prior.nc`, `netCDF/posterior.nc` | emissions by sector | emission maps (**required**) |
| `netCDF/scale.nc` | posterior scale factors | scale-factor map |
| `netCDF/avkern.nc` | averaging-kernel diagonal | averaging-kernel map |
| `netCDF/obs_ds.nc` | observations and simulated columns at observation locations | not used |
| `netCDF/obs_ds_regridded.nc` | the same, on the model grid | observation and XCH4 maps |
| `netCDF/state_vector.nc` | the state vector | domain mask, buffer / region-of-interest split |

The output folder is the notebook's `plot_save_path` (`<run>` is
`${OutputPath}/${RunName}`):

| Mode | Notebook runs in | Output folder | Read as |
|---|---|---|---|
| Standard | `<run>/inversion/` | `<run>/inversion/output/` | single period |
| Kalman filter | `<run>/kf_inversions/period<N>/` | `<run>/kf_inversions/output/period_<N>_plots/` | timeseries |

Consequences:

- **No posterior step, no input.** In standard mode `DoPosterior: false` skips
  the notebook. Kalman mode requires `DoJacobian`, `DoInversion` and
  `DoPosterior`, so it always writes the input.
- **IMI 2.1.0 or later** (or development code from November 2024 on). Releases
  before 2.0.1 write no `netCDF/` folder; 2.0.1 is untested.
- **A Kalman run can be converted as it goes:** a re-run converts only new or
  changed periods.

## Option 1: convert after the run (no IMI change)

```bash
imi-dashboard convert <run>/inversion/output     <dashboard>   # standard mode
imi-dashboard convert <run>/kf_inversions/output <dashboard>   # Kalman filter mode
```

`imi-dashboard info <folder>` checks the input first; `init-config` writes a
starting config; [Converting a run](guide.md#converting-a-run) lists every
option.

To view it, run `imi-dashboard serve <dashboard> --open`, or copy the folder
to a static host ([Viewing and sharing a
dashboard](guide.md#viewing-and-sharing-a-dashboard)). On a cluster: run
`imi-dashboard serve <dashboard> --port 8000`, forward the port with
`ssh -L 8000:localhost:8000 <user>@<login-node>` and open
<http://localhost:8000>. On a compute node, add `--bind 0.0.0.0` and forward to
the node (`-L 8000:<node>:8000`), if the cluster allows it.

## Option 2: an optional dashboard step inside the IMI (proposed)

A new component, one shell file like the IMI's other stages:

```bash
# src/components/dashboard_component/dashboard.sh
# Description: Build the IMI dashboard from the visualization notebook's export
# Usage:
#   run_dashboard
run_dashboard() {
    printf "\n=== BUILDING DASHBOARD ===\n"
    if "$KalmanMode"; then
        DashboardInput=${RunDirs}/kf_inversions/output
    else
        DashboardInput=${RunDirs}/inversion/output
    fi
    dashboard_args=()
    if [[ -n "${DashboardConfig:-}" ]]; then
        dashboard_args+=(--config "$DashboardConfig")
    fi
    ${DashboardCommand:-imi-dashboard} convert "$DashboardInput" \
        "${RunDirs}/dashboard" "${dashboard_args[@]}" ||
        printf "\nWARNING: dashboard build failed; the IMI run continues.\n"
    printf "\n=== DONE BUILDING DASHBOARD ===\n"
}
```

sourced in `run_imi.sh` with the other components and called in
`run_posterior()` right after `run_notebooks`:

```bash
    # convert vizualization notebooks to html
    run_notebooks

    if [[ "${MakeDashboard:-false}" == "true" ]]; then
        run_dashboard
    fi
```

with three optional config keys, added to the optional rules in
`src/utilities/sanitize_input_yaml.py` and to the IMI documentation:

| Key | Default | Meaning |
|---|---|---|
| `MakeDashboard` | `false` | build the dashboard after each notebook run |
| `DashboardConfig` | empty | dashboard YAML config; without it, defaults come from the data |
| `DashboardCommand` | `imi-dashboard` | the command, for an installation outside the IMI's environment |

### Design choices

- **After `run_notebooks`, not at the end of `run_imi.sh`**, so a Kalman run
  has a dashboard after every period, and because `run_imi.sh`'s top-level
  `trap 'imi_failed $LINENO' ERR` would end the run on a failure there. Inside
  a function the trap does not apply (the IMI does not `set -E`).
- **Not inside the notebook**, so it can be switched off, use its own Python
  and be re-run alone.
- **Non-fatal:** `|| printf` turns a failure into a warning.
- **Absolute output path**, because the IMI changes directory between steps.
- **`[[ … == "true" ]]`** (as `kalman.sh` does for `AutoAdvanceFirstPeriod`)
  treats a missing key as false, and keeps a value like `1`, which the
  validator accepts as a boolean, from being run as a command.
- **No `--yes`:** in a batch job, `convert` refuses to remove periods that
  left the input, and the IMI run carries on. Add `--yes` to `dashboard_args`
  to allow removal.

### When a period's notebook fails

The ERR trap does not reach into `run_notebooks`, so the IMI continues. A
missing period folder, or one with a missing or unreadable `posterior.nc` or
`prior.nc`, appears as a gap. With no usable period, the conversion fails.

### Logging

IMI jobs write all output to `imi_output.log` (`#SBATCH -o "imi_output.log"`).
There the converter writes timestamped plain lines, warnings and errors only.
Add `--log-file "${RunDirs}/dashboard.log"` to `convert` for a full,
appended record. Command-line errors appear only in `imi_output.log`.

### Installation

A plain pip package. It needs Python ≥ 3.11, numpy ≥ 1.26, xarray ≥ 2024.1,
netCDF4 ≥ 1.6.2, pandas ≥ 2.1 and PyYAML ≥ 6, plus pydantic, typer and rich,
which the IMI does not use.

| IMI environment | Python | Compatible |
|---|---|---|
| `envs/Harvard-Cannon/imi_env.yml`, `envs/MIT-Engaging/imi_env.yml`, `envs/MSU-HPC_Orion/imi_env.yml`, the containers (`resources/containers/{al2,ubuntu}/base-image/install-scripts/imi_env.yml`) | 3.11.6 | yes (numpy 1.26.4, xarray 2024.6.0, netCDF4 1.7.1, pandas 2.2.2, PyYAML 6.0.1) |
| `envs/NASA-Pleiades/pip_requirements.txt` | not pinned | no: numpy 1.24.4, xarray 2023.8.0 |
| `envs/aws/conda_env.yml` (not the container) | 3.9.7 | no |

- **Into the IMI's environment** (`pip install` while it is active; in the
  containers, a line in the install script). In the compatible environments
  this adds pydantic, typer and rich and changes no pinned version. Not in the
  others: pip would upgrade numpy and xarray (Pleiades) or fail (Python 3.9).
- **In its own virtual environment**, with `DashboardCommand` pointing to it
  (e.g. `/path/to/dashboard-venv/bin/imi-dashboard`). Works anywhere with
  Python ≥ 3.11 and leaves the IMI's environment untouched.

Use the release wheel ([Install](../README.md#install)): it includes the
dashboard's pages, so no Node.js is needed on the cluster.

## Option 3: an agreed interface (to discuss with the IMI developers)

`export_visualization_outputs()` is already the de facto interface: file
names, CSV columns, the `netCDF/` folder and the `period_<N>_plots` name. Its
path is a string in a notebook cell, so renaming it would break the converter.
A documented, versioned interface (e.g. a format version in the statistics
CSV) would let both sides change independently.

## Particular configurations

### Dynamic clustering in Kalman mode

With `DynamicKFClustering: true` the IMI builds a new state vector each
period, but `update_sv_clusters()`
(`src/components/statevector_component/aggregation.py`) re-clusters only the
region of interest and keeps the buffer elements, so the domain and the
buffer / region-of-interest split do not change. The converter takes both from
the first usable period; `imi-dashboard info` prints that period's element
count.

### Delivering dashboards through S3

The IMI's S3 upload (`S3Upload: true`) archives the paths listed in the config,
so listing `<run>/dashboard` includes the dashboard. At the commit above,
though, `src/utilities/s3_upload.py` reads `OutputFiles` while the validator
and the documentation require `S3UploadFiles`; unless both are set, the upload
fails with a `KeyError`.
