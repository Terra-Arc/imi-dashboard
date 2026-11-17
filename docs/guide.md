# User guide

What a dashboard shows, which IMI files it reads, and how to convert, view and
share it. Installation and a quick start are in the [README](../README.md).

## What the dashboard shows

### State vector, buffer elements and region of interest

An IMI inversion estimates one emission scale factor (posterior ÷ prior) per
*state-vector element*, a group of grid cells; `state_vector.nc` gives each
cell its element number. Inside the *region of interest* (ROI), the area the
inversion is for, elements are single cells. Around it, the IMI adds larger
*buffer elements* that take up emissions from outside, so they are not
attributed to sources inside. Older IMI versions build a few large clusters
(Romania sample: 8, of 233–545 cells); IMI 2.3.x builds rings of small
elements (Permian sample: 33, of 2–9 cells).

No IMI file labels the buffer elements, so the converter infers them. It tries
every split of the element numbers into a lower part (ROI) and a higher part
(buffer), and accepts a split when the smallest buffer element has at least
1.5 × the cells of the largest ROI element and no ROI cell lies on the edge of
the rectangle bounding the inversion. The accepted split with the largest size
ratio wins (Permian 2.0, Romania 233). If none is accepted, it warns and treats
every element as ROI.

`--region roi` (default) maps only the ROI cells, on a grid cropped to them;
`--region domain` maps every cell. The emission totals do not depend on this:
they come from the IMI's statistics file.

### The overview page

Everything refers to the selected *sector* and *period*. A period is the time
span one inversion covers (a month in both samples); a timeseries has one
`period_<N>_plots/` folder per period. Change period with the map's arrows,
the play button, or by clicking the chart.

- **Sector bar.** One button per sector: its posterior total for the period
  (Mt CH₄/yr) and, for a timeseries, a sparkline across periods. Sectors with
  `primary: false` are behind "+N more".
- **Emission map.** Posterior emissions in kg CH₄ km⁻² h⁻¹ (converted from the
  IMI's kg m⁻² s⁻¹). The colour bar runs from the minimum to the 98th
  percentile (`map.max_percentile`) of the sector's shown cells over all
  periods.
- **Totals.** The posterior total; its share of the total posterior (for the
  Total sector: the prior total instead, subtitled "Bottom-up inventory" for a
  single period and for period 1, "From the previous period's estimate" for
  later periods, see [A timeseries is one Kalman-mode
  run](#a-timeseries-is-one-kalman-mode-run)); and the *ensemble range*, the
  minimum and maximum posterior total across the run's ensemble (inversions of
  the same period with different error settings), from
  `viz_notebook_statistics.csv`. Without an ensemble: "This run has no
  ensemble".
- **Timeseries chart** (timeseries only). The posterior total per period, as
  line, area, candle or bars; the band or whiskers show the ensemble range.
  Missing periods leave a gap.
- **Sector comparison** (single period only). Prior and posterior totals as
  bars, with the change in %.
- **Quick diagnostics** (folded). DOFS, number of super-observations, and bias
  and RMSE with prior and with posterior emissions (see the diagnostics page).

### What "Total" means

The Total sector **excludes the soil sink**, the methane oxidised by soils,
which the IMI stores as negative emissions in `EmisCH4_SoilAbsorb`. Its numbers
are `TotalExclSoilAbsPrior` and `TotalExclSoilAbsPosterior` from
`viz_notebook_statistics.csv` (`TotalExclSoilAbsorb…` in IMI 2.1.0 and 2.2.0);
its map is `EmisCH4_Total_ExclSoilAbs`, so the map summed over the ROI gives
the number shown. Permian sample: prior 0.409 Mt/yr; with the sink, 0.385.

The CSV's headline pair `PriorEmissions` / `PosteriorEmissions` is not used. In
Kalman mode the IMI writes the prior there *with* the sink and the posterior
*without* it, so their difference contains the whole sink (Permian: +56 %
instead of +47 %). `convert` warns if the pair matches neither definition,
which would mean the IMI changed its statistics file.

Exceptions:

- **IMI development versions before 2.1.0** (e.g. the Romania sample) write
  only the headline pair, both with the sink. The converter sums
  `EmisCH4_Total_ExclSoilAbs` of `prior.nc` and `posterior.nc` over the ROI
  itself (365-day years, as the IMI does), after checking that summing
  `posterior.nc` the same way reproduces the CSV's `PosteriorEmissions`. If the
  check fails or a file is missing, the headline pair is shown, unlabelled,
  with a warning. Romania, period 1: prior 1.317 and posterior 0.949 Mt/yr,
  against the CSV's 1.300 and 0.931 with the sink. These runs, and those of
  IMI 2.1.0 and 2.2.0, have no ensemble range.
- **IMI 2.2.1** writes the ensemble range *with* the sink. The converter
  recognises such a run by `EnsemblePosterior_Mean` equalling `TotalPosterior`
  (from 2.3.0 on it equals `TotalExclSoilAbsPosterior`), and shifts both ends
  by the posterior's sink (`TotalPosterior` − `TotalExclSoilAbsPosterior`),
  reported with `-v`. If the sink differs between members
  (`SoilAbsorb_ensMin` ≠ `SoilAbsorb_ensMax`), the range is left out with a
  warning. If the mean matches neither total, the range is shown as written.
- **No `EmisCH4_Total_ExclSoilAbs`** in `posterior.nc`: the map shows
  `EmisCH4_Total`, with a warning.

The dashboard labels the definition: the Totals tiles, the sector-comparison
heading and the chart title say "excluding the soil sink"; the map caption
says "excluding" or "including" according to the map's variable (no label for
a variable other than the two totals). `imi-dashboard info` reports it on its
`Total` line. Dashboards from earlier converters show no label.

### The diagnostics page

Six maps of the selected period, each with its own colour bar:

| Map | Content | Unit |
|---|---|---|
| Averaging kernel diagonal | from `avkern.nc`: the sensitivity of each cell's posterior to the true emissions, 0 (prior only) to 1 (observations only) | none |
| TROPOMI observation count | `observation_count` from `obs_ds_regridded.nc` | observations per cell |
| Mean TROPOMI XCH4 | period-mean column-averaged dry-air mole fraction of methane, from `obs_ds_regridded.nc`; blank without observations | ppb |
| Posterior scaling factor | from `scale.nc`; colour bar 0–2, centred on 1 (no change) | × prior |
| Posterior − prior emissions | `EmisCH4_Total_ExclSoilAbs`, posterior minus prior (equal to the difference including the sink unless the run set `OptimizeSoil`) | kg CH₄ km⁻² h⁻¹ |
| ΔXCH4 (posterior − prior) | `diff_satellite_posterior − diff_satellite_prior` from `obs_ds_regridded.nc`; blank without observations | ppb |

Below the maps are the scalar diagnostics from `viz_notebook_statistics.csv`
(cards for a single period, small charts for a timeseries):

- **DOFS**, degrees of freedom for signal: the number of independent pieces of
  information the observations supply.
- **Super-observations**: TROPOMI observations aggregated onto the model grid,
  as counted by the IMI.
- **Mean averaging-kernel diagonal** over the cells with a value.
- **Bias and RMSE** of GEOS-Chem against the observations (ppb), with prior and
  with posterior emissions, and their difference (for the bias, of absolute
  values; negative means the posterior fits better).

## Input layout

Supported: IMI 2.1.0 and later, and IMI development code from November 2024
(when the `netCDF/` export was added) on. Releases before 2.0.1 have no
`netCDF/` folder and cannot be converted; 2.0.1 writes the same files but is
untested. The tests use IMI 2.3.1 and a pre-2.1.0 development version (see
[`sample_files/README.md`](../sample_files/README.md)).

The IMI's visualization notebook writes the input after each posterior step,
to `<run>/inversion/output/` (single inversion) or `<run>/kf_inversions/output/`
(Kalman filter); see [Running it with the IMI](imi-integration.md).

```
my_run/                    single period
  netCDF/
    posterior.nc, prior.nc, scale.nc, avkern.nc, state_vector.nc, ...
  viz_notebook_statistics.csv

my_run/                    timeseries
  period_1_plots/
    netCDF/ ...
    viz_notebook_statistics.csv
  period_2_plots/
    ...
```

Each period needs `netCDF/posterior.nc` and `netCDF/prior.nc`. A period
lacking either, or with one that cannot be read (typically a `posterior.nc`
still being written), is skipped with a warning and shown as a gap; the
conversion fails only if no period is left. The other files are optional; one
that cannot be read (empty, truncated, a date not `YYYYMMDD`) is treated as
missing, with a warning.

| File | Supplies |
|---|---|
| `viz_notebook_statistics.csv` | dates, prior and posterior totals per sector, scalar diagnostics, ensemble minimum and maximum (if the run has an ensemble) |
| `state_vector.nc` | the buffer elements, for `--region roi`; without it the full domain is shown |
| `avkern.nc`, `scale.nc` | the averaging-kernel and scale-factor maps |
| `obs_ds_regridded.nc` | the observation-count, XCH4 and ΔXCH4 maps |

`ensemble_statistics.csv` is not used. Period folders are ordered by `N`; gaps
in the numbering appear as gaps in the charts.

### A timeseries is one Kalman-mode run

The dashboard assumes a timeseries is one IMI run in Kalman-filter mode.
Nothing in the input says so, so this is not checked.

In that mode, period 1's prior is the bottom-up inventory, and period `N`'s
is built from the posterior of period `N − 1`. The IMI's `prepare_sf.py`
computes, per cell, `NudgeFactor` × inventory + (1 − `NudgeFactor`) ×
previous posterior (negative posteriors set to zero; default `NudgeFactor`
0.1), rescales it so its ROI total equals the previous posterior's, and resets
the buffer elements to the inventory. The resulting scale factors are applied
to period `N`'s own inventory, so its prior need not equal the previous
posterior (Romania: period 1's posterior 0.949 Mt/yr, period 2's prior 0.918).
The dashboard does not read the IMI config, so it cannot show `NudgeFactor`.

Consequences: every period numbered above 1 (by folder name, even if
`period_1_plots/` is missing) has its prior subtitled "From the previous
period's estimate", and the default header subtitle says "Kalman-filter
inversion" (override with `text.subtitle_timeseries`). A folder of independent
per-period inversions converts without error but is mislabelled; convert each
period as a single-period run instead.

## Converting a run

```bash
imi-dashboard info INPUT_FOLDER                       # optional: check the run
imi-dashboard init-config INPUT_FOLDER                # optional: a config with the run's values
imi-dashboard convert INPUT_FOLDER OUTPUT_FOLDER [--config FILE]
```

- **`INPUT_FOLDER`**: an IMI run folder; single period or timeseries is
  detected.
- **`OUTPUT_FOLDER`**: a new or empty folder, or a dashboard written earlier
  (one with a `data/manifest.json` naming `imi-dashboard`). Any other folder,
  and the input folder or one containing it, is refused. `convert` writes
  `index.html`, `assets/`, `data/`, `README.md` and `imi-dashboard-app.json`
  and leaves everything else alone (e.g. `.git/`, `CNAME`).
- **`--config FILE`**: optional YAML config; see [Configuration](#configuration).

| Option | Effect |
|---|---|
| `--region domain\|roi` | `roi` (default) drops the buffer elements and crops the grid; `domain` keeps the whole inversion domain. Also `region:` in the config. |
| `--rebuild` | Reconvert every period, not only new or changed ones. |
| `-y`, `--yes` | Do not ask before removing periods or replacing a dashboard of another input (see [Updating a dashboard](#updating-a-dashboard)). |
| `--app-bundle FOLDER` | Developer option. The dashboard's pages (`index.html`, `assets/`) to copy: a built `web/dist/`, or a dashboard from the same `imi-dashboard` version. Default: the clone's `web/dist/`, then the copy in the installed package. Without either, an update keeps the existing pages (with a warning) and a new dashboard is an error. See [Building a wheel by hand](../CONTRIBUTING.md#building-a-wheel-by-hand). |

### Checking a run with `info`

`info` reads a run without converting it and prints its layout (with gaps),
dates, grid, buffer elements, the ROI-cropped grid size, the sectors and
*cadence* `convert` would use (those of `--config`, if given), missing files
per period, and periods `convert` will skip. The cadence is the period length
as a word (`daily`, `weekly`, `biweekly`, `monthly`, `annual`, `custom`); it
sets wording such as "Monthly timeseries".

Given a dashboard folder, `info` prints when it was converted (UTC), the
converter version, the input folder's name (not its path), layout, number of
periods, grid and region. Dashboards from older converters show "not
recorded" for the first three. The IMI version is not available: the IMI
writes it into none of the files read. The dashboard's footer shows the
conversion date and converter version.

### Updating a dashboard

Re-running `convert` into the same folder reconverts only new and changed
periods. A period counts as changed when its *fingerprint* changes: a hash of
the relative path, size and modification time (to the nanosecond) of every
file in its folder. Times are compared for equality, so a file replaced by an
older copy (`cp -p`, `rsync -a`, `tar x`), added or deleted is noticed; only a
replacement with identical name, size and time is not.

Every period is reconverted with `--rebuild`, and when the input folder, the
converter release, the region, the grid, the sector list or a sector's
`imi_var` changed, or with `region: roi` and no `state_vector.nc`.

A skipped period is not recorded. Once its files are present and readable,
the next run adds it and reconverts the periods after it.

`convert` asks before removing data: periods no longer in the input, or a
dashboard converted from another input folder. Without a terminal (cron, batch
jobs) it refuses, with exit status 1 and nothing changed, unless `--yes` is
given.

### Examples

```bash
# first conversion and preview
imi-dashboard info run/
imi-dashboard init-config run/ -o dashboard.yaml      # then edit it
imi-dashboard convert run/ site/ --config dashboard.yaml
imi-dashboard serve site/ --open

# update after new periods: the same command
imi-dashboard convert run/ site/ --config dashboard.yaml

# scheduled update: errors only on stderr, full log in a file
imi-dashboard convert /data/run/ /srv/www/site/ --config /data/dashboard.yaml -q --log-file /data/convert.log \
    || echo "dashboard update failed with status $?" >&2
```

### info --json

`imi-dashboard info INPUT_FOLDER --json` prints one JSON object on stdout;
warnings and errors stay on stderr. Non-finite numbers are `null`, dates are
ISO 8601 (`2023-01-01`), paths are as given. `schemaVersion` is raised when a
key is renamed, removed or changes meaning; new keys may appear without it.
Unknown values are `null`.

For an IMI run (`"kind": "run"`):

| Key | Meaning |
|---|---|
| `schemaVersion` | `1` |
| `kind` | `"run"` |
| `path` | the input folder |
| `mode` | `"single"` or `"timeseries"` |
| `periods` | one object per period folder, skipped ones included: `label` (`period_<N>` or `single`), `number` (`N`; `null` for a single period), `start`, `end` (from the CSV), `skipped`, `skipReason` (e.g. `"missing posterior.nc"`), `missingFiles` |
| `gaps` | missing folder numbers (`[3]` for the Romania sample) |
| `start`, `end` | start of the first and end of the last complete period |
| `periodDays` | length of the first complete period, in days |
| `cadence`, `cadenceSource` | the cadence, and `"config"` or `"inferred"` |
| `grid` | from the first complete period's `posterior.nc`: `rows`, `cols`, `dlat`, `dlon` (degrees), and the outermost cell centres `latMin`, `latMax`, `lonMin`, `lonMax` |
| `region` | the config's `region`, else `"roi"` |
| `stateVector` | `period`, `elements`, `roiElements`, `bufferElements`; `null` without `state_vector.nc` |
| `roiGrid` | `rows`, `cols` after cropping; `null` without `state_vector.nc` |
| `ensemble` | whether the CSV has the ensemble range; `null` without a CSV |
| `sectors`, `sectorsSource` | `id`, `imiVar`, `label` per sector, and `"config"` or `"detected"` |
| `total` | `numbersIncludeSoilSink`, `mapIncludesSoilSink`; `null` without a total sector |

`skipped` covers missing required files only; `info` does not test whether
they can be read.

For a dashboard (`"kind": "dashboard"`), from its `data/manifest.json`:

| Key | Meaning |
|---|---|
| `schemaVersion`, `kind`, `path` | `1`, `"dashboard"`, the folder |
| `provenance` | `converter` (`name`, `version`), `convertedAt` (UTC), `input` (folder name); `null` if not recorded |
| `mode` | `"single"` or `"timeseries"` |
| `periodCount` | number of periods held |
| `periods` | `label`, `number`, `start`, `end` per period |
| `grid` | as for a run; the cropped grid with `region` `roi` |
| `region` | `"roi"` or `"domain"` |
| `total` | as for a run; `null` if not recorded |

### Windows and HDF5 plugins

On Windows, `imi-dashboard` sets `HDF5_PLUGIN_PATH` to empty unless it is
already set: the HDF5 plugins bundled with netCDF4 ≥ 1.7.4 otherwise make a
conversion about 50 × slower, and IMI files need none. Set the variable
yourself to use your own plugins.

## Viewing and sharing a dashboard

A dashboard must be served over HTTP; opened from disk (`file://`) it shows
only a note saying so.

```bash
imi-dashboard serve DASHBOARD [--port PORT] [--bind ADDRESS] [--open]
```

This is a preview server, not for hosting. It sends fixed `Content-Type`s
(a wrong Windows registry entry for `.js` otherwise blanks the page), disables
caching, and prints failed requests (status ≥ 400) as warnings; `-v` prints
every request. `--port 0` picks a free port. `--bind 0.0.0.0` makes it
reachable from your network, with a warning.

Any static server works too: `python -m http.server -d <folder> 8000`. To
share, upload the folder to a static host (GitHub Pages, Netlify, S3, …) with
`index.html` at its top; any URL path works. Each dashboard includes a
`README.md` for its recipients.

The viewer's browser downloads the base map from OpenStreetMap, or the tile
server the config names (see [The base map](#the-base-map)). Offline, the data
is drawn on a plain background. The fonts are bundled.

### What a dashboard folder contains

| File | Content |
|---|---|
| `index.html`, `assets/` | the dashboard's pages (HTML, JavaScript, CSS, fonts, logo, third-party licences); with `header_brand: custom`, also `assets/custom-logo.<ext>` |
| `imi-dashboard-app.json` | the list of those files and the data format they read |
| `README.md` | how to view and host it, headed with `display_name` |
| `data/config.json` | the config's display settings: names, branding, sectors, map and chart settings, text |
| `data/manifest.json` | provenance (converter name and version, UTC time, input folder *name*); a hash of the input folder's absolute path; the converter release; each sector's netCDF variable; the grid's cell centres; per period: label, folder number, dates, input fingerprint, sector totals with ensemble range, scalar diagnostics; the colour-bar limits |
| `data/globals.bin` | one byte per cell: 1 if the maps show it |
| `data/periods/NNNN.bin` | per period: every sector's posterior map and the six diagnostic maps, as 32-bit floats |

The hashes cannot be reversed, but anyone who knows a candidate path can test
it against them.

Dashboards include third-party code and fonts under MIT, ISC, BSD-2-Clause,
BSD-3-Clause and SIL Open Font licences (`assets/THIRD-PARTY-LICENSES.md`).
The default footer attribution (`text.footer_attribution`) includes "Contains
modified Copernicus Sentinel data {years}", which the Copernicus terms require
wherever Sentinel-5P-derived data is shown; keep it in your own attribution.

## Configuration

The config file sets branding, sectors, region and text. The file and every
key are optional. Without one:

- **Sectors**: every `EmisCH4_*` variable in the first complete period's
  `posterior.nc` that has Prior/Posterior columns in its CSV (all of them if
  there is no CSV), except `EmisCH4_Total_ExclSoilAbs`. Standard sectors come
  first in a fixed order (total, oil, gas, coal, livestock, landfills,
  wastewater, rice, other anthropogenic, wetlands, biomass burning,
  reservoirs, lakes, seeps, termites, soil absorption); others follow
  alphabetically, labelled with their name minus the prefix, behind "+N more".
- **Cadence**, from the first period's length in days: 1 daily, 6–8 weekly,
  13–15 biweekly, 28–31 monthly, 365–366 annual, else `custom` (generic
  "period" wording).
- **Titles** name no area ("Monthly methane emissions"); region `roi`; Terra
  Arc branding.

### Starting a config from your run

```bash
imi-dashboard init-config [INPUT_FOLDER] [-o FILE] [--overwrite]
```

writes a config listing every setting with the value your run gets without a
config: detected sectors (label, colour, primary flag), cadence, every text
string, and how many cells `region: roi` keeps. Lines starting `##` explain;
lines starting `# ` are settings.

```yaml
## This run: 8 of its 359 state-vector elements (in period_1) are buffer
## elements. roi keeps 18 x 29 of the domain's 60 x 63 cells.
# region: roi                   # roi | domain
```

Every setting is commented out, so the file changes nothing as written. To
change one, remove its `# ` and edit it, together with its section line
(`map:`, `text:`); settings left commented keep following the data. To edit
sectors, uncomment the whole `sectors:` list; a sector removed from it is left
out.

- `-o FILE` (default `dashboard.yaml`); `-o -` prints to stdout.
- `--overwrite` replaces an existing file.
- Without `INPUT_FOLDER`, the file has illustrative examples instead of your
  run's values.

### The keys

```yaml
area_name: Romania      # the area, named in the titles
display_name: "Methane emissions over Romania"   # tab title, heading, README title

region: roi             # roi = region of interest only | domain = full inversion domain

header_brand: terra     # terra = Terra Arc | custom = your logo/name; the footer credits the software
# logo: logo.png        # with custom; a relative path is relative to the config file
# brand_name: My Lab    # with custom
# brand_tagline: Methane monitoring   # with custom

sectors:                # ordered; each becomes a button
  - id: total           # a standard id needs nothing else: label, color,
  - id: coal            # imi_var and primary take their standard values
    label: Hard coal    # ... unless given
  - id: flaring         # any other id needs imi_var, label and color
    imi_var: EmisCH4_Flaring
    label: Flaring
    color: "#C2410C"
  - {id: venting, imi_var: EmisCH4_Venting, label: Venting, color: "#B45309"}   # one-line form
  # listing sectors replaces the detected list; unlisted sectors are dropped

map:
  ramp: reds            # viridis | magma | plasma | reds
  scale: linear         # linear | log
  # max_percentile: 98  # upper end of the colour bar
  # tiles: https://tiles.example.org/{z}/{x}/{y}.png   # base map; default OpenStreetMap; none = no base map
  # tiles_attribution: "© Example Tiles"                # required with other tiles

text:
  cadence: monthly      # daily | weekly | biweekly | monthly | annual | custom
```

Sector keys: `primary: true` gives the sector its own button (others are
behind "+N more"); `color` is any CSS colour or a token such as
`var(--arc-orange)`; `short` is a shorter label, stored but not shown by the
current app. [`examples/config.example.yaml`](../examples/config.example.yaml)
is the Romania sample's config.

### The text keys

Every `text:` key defaults to a string derived from `cadence` (and
`area_name`); `init-config INPUT_FOLDER` writes them all.

| Key | Where it appears |
|---|---|
| `cadence` | the period word everywhere |
| `cadence_singular`, `cadence_plural` | the period word ("month", "months") |
| `heading_context` | first part of the small line above each page heading (default "Integrated Methane Inversion") |
| `heading_context_timeseries`, `heading_context_single`, `heading_context_diagnostics` | its second part: timeseries overview, single-period overview, diagnostics page |
| `overview_heading` | the overview heading, if not `display_name` |
| `diagnostics_heading` | the diagnostics heading |
| `subtitle_timeseries`, `subtitle_single` | the line under the overview heading |
| `subtitle_diagnostics_timeseries`, `subtitle_diagnostics_single` | the line under the diagnostics heading |
| `chart_title_split`, `chart_subtitle_split` | chart title and subtitle, Split layout (map beside chart) |
| `chart_title_chart_first`, `chart_subtitle_chart_first` | the same, Chart-first layout |
| `speed_unit` | hint under the playback speed |
| `diagnostics_spatial_label`, `diagnostics_scalar_label` | labels above the diagnostic maps and the scalar diagnostics |
| `footer_attribution`, `footer_disclaimer` | footer paragraphs; `""` removes one |
| `footer_version` | the footer's version line |

Placeholders in `subtitle_*`, `chart_*` and `footer_attribution`:

| Placeholder | Becomes |
|---|---|
| `{count}` | number of periods |
| `{date}` | start date of the selected period |
| `{date_start}`, `{date_end}` | start of the first period, last day of the last |
| `{years}` | calendar years covered ("2023", "2018–2025") |
| `{cadence_singular}`, `{cadence_plural}` | the period word |

Changing `sectors` (adding, removing, reordering, or a sector's `imi_var`) or
`region` reconverts every period. `map.max_percentile` only recomputes the
colour-bar limits. Other keys only rewrite `data/config.json`, which is quick.

### The base map

Map tiles come from OpenStreetMap's `tile.openstreetmap.org` by default. Its
[tile usage policy](https://operations.osmfoundation.org/policies/tiles/)
allows normal interactive use without guarantee and may block heavy users.
For a high-traffic dashboard, set `map.tiles` to another provider and
`map.tiles_attribution` to its attribution. `map.tiles: none` draws the data
on a plain background with no outside requests. `map.tiles_max_zoom` and
`map.tile_size` (256 or 512) describe the provider's tiles.

The attribution appears in MapLibre's compact control, which collapses once
the map is dragged. OpenStreetMap asks that it not be hidden;
`map.attribution_compact: false` keeps it visible.

## Console output

Results go to stdout: `convert` a summary line with the elapsed time
(`converted timeseries run · 3 period(s) · grid 18x29 -> romania (14.2 s)`),
`info` its description, `serve` its address, `init-config` a `wrote …` line
(or the config, with `-o -`). Warnings, errors and progress go to stderr.
Every command takes:

| Option | Effect on stderr |
|---|---|
| `-v`, `--verbose` | progress: for `convert`, the input found, grid, dropped buffer elements, full or incremental conversion and why, one line per period with its time, the web bundle copied; for `info` and `init-config`, the files read; for `serve`, every request |
| `-vv` | also debug detail and the Python traceback of an error |
| `-q`, `--quiet` | errors only. `convert` and `init-config` also drop their stdout line |
| `--log-style auto\|screen\|log` | line format (below); default from `IMI_DASHBOARD_LOG_STYLE`, else `auto` |
| `--log-file FILE` | also append every message, at `-vv` detail and in log style, to `FILE`, whatever `-v`/`-q` say; each run starts with the version and command and ends with the result |

Styles:

- **screen** (for a terminal): plain progress lines, `warning:`/`error:`/
  `debug:` prefixes in colour (none if `NO_COLOR` is set), and a progress bar
  during `convert`.
- **log** (for files, cron mail, Slurm output): every line is
  `<UTC time> imi-dashboard <level>: <message>`; continuation lines are
  indented four spaces; no colour or progress bar.

  ```
  2026-11-04T10:15:30Z imi-dashboard info: [2/3] period_2: read (0.7 s)
  2026-11-04T10:15:31Z imi-dashboard warning: no web bundle (none found in …)
  ```

`auto` picks screen when stderr is a terminal, log otherwise.

Input and config errors print `error: …` (plus one indented line per invalid
config key) and exit with status 1; `-vv` adds the traceback. Usage errors
(`--region bogus`, a missing folder, an unknown option, `-v` with `-q`) print
`error: …`, the usage line and a pointer to `--help`, and exit with status 2.

The log file misses usage errors (found before it is opened) and text that C
libraries such as HDF5 write directly to stderr. To capture everything, use
`… 2>&1 | tee run.log`.

### Exit status

0 on success, warnings included; 1 on an error; 2 on a usage error; 130 when
interrupted with Ctrl+C during work.

| Command | 0 | 1 |
|---|---|---|
| `convert` | dashboard written or updated | error in input, config, output folder or log file; removal refused without a terminal and `--yes`; confirmation declined or interrupted (dashboard unchanged in these cases) |
| `info` | description printed, even with skipped periods | not a run or a dashboard, no complete period, invalid config, unreadable file |
| `serve` | stopped with Ctrl+C | not a dashboard, port in use, address unavailable |
| `init-config` | config written | output exists (without `--overwrite`) or is a folder, input not a run, no complete period |
