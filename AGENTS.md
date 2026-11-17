# AGENTS.md

Instructions for AI coding agents working in this repository. Human
contributors will find the same material, in longer form, in `README.md`,
`docs/guide.md` (the user guide) and `CONTRIBUTING.md`.

## What this repository is

The **IMI Dashboard**: a static web dashboard of methane emissions estimated by
an [Integrated Methane Inversion](https://imi.seas.harvard.edu/) (IMI) run. It
has two parts:

1. **`tools/imi-dashboard/`**: a Python CLI (`imi-dashboard`) that turns a folder
   of IMI outputs (one period, or a timeseries of periods) plus an optional
   YAML config into a static site folder.
2. **`web/`**: a Vite + React 18 + TypeScript app. It is built once into a
   bundle (`web/dist/`), which the converter copies into every site it writes.

## Layout

```
├── tools/imi-dashboard/        the converter package and its tests
├── web/                        the web app source
├── sample_files/               small real IMI outputs, used as test fixtures
├── examples/config.example.yaml   annotated config for the Romania sample
├── build.sh                    rebuild the web bundle and the sample dashboards
├── dashboards/                 generated dashboards (gitignored)
└── local_data/                 your own full IMI datasets (gitignored)
```

### Converter (`tools/imi-dashboard/imi_dashboard/`)

- `cli.py`: the typer CLI, one `imi-dashboard` command with subcommands:
  `convert <input> <output> [--config FILE] [--region domain|roi]
  [--rebuild] [-y|--yes] [--app-bundle FOLDER]`;
  `info <input> [--config FILE] [--json]`;
  `serve <dashboard> [--port PORT] [--bind ADDRESS] [--open]`; and
  `init-config [<input>] [-o FILE|-] [--overwrite]`, which
  writes the commented template `templates/config.yaml`, filled by
  `init_config.py` with the run's detected values when given an input.
  Every command also takes the four output options `-v|-vv`, `-q`,
  `--log-style auto|screen|log` and `--log-file FILE`, with help built
  from the shared `_verbose_help`, `_quiet_help`, `LOG_STYLE_HELP` and
  `LOG_FILE_HELP`. Option values have explicit placeholders (`FILE`,
  `FOLDER`, `PORT`, ...), arguments explicit metavars. A relative output
  folder is taken from the current directory (there is no `--root`). Each
  command's first docstring sentence is its one-line summary in the command
  list (keep it under about 60 characters, or click's plain format cuts it
  off); its exit statuses and examples are in an `epilog`, printed line by
  line in both help formats by `_Command`. Console
  output goes through the `imi_dashboard` logger, set up per command by
  `_console()`, which also turns expected input and config errors into one
  `error:` line. stderr is written in one of two styles: `screen` (bare
  progress lines, coloured `warning:`/`error:` prefixes through a rich
  console, and a progress bar on a terminal) or `log` (every line
  `<UTC time> imi-dashboard <level>: <message>`); `auto` picks screen when
  stderr is a terminal, and `IMI_DASHBOARD_LOG_STYLE` sets the default.
  The `-v`/`-q` level is on the stderr handler, not on the logger, so that
  `--log-file` can append everything at DEBUG. rich is imported lazily,
  like the converter. Usage errors (click's, raised while the command line
  is parsed, and the commands' own `typer.BadParameter`) are printed by
  `_CommandGroup` through the same handlers, as `error: <message>` plus the
  usage line, with exit status 2, not as click's `Error:` or typer's box;
  click's `UsageError` is taken from `typer.BadParameter`'s ancestry,
  because typer ≥ 0.26 carries its own copy of click. `main()` switches off
  typer's rich help and tracebacks in the log style. The progress bar is fed by `pack.convert`'s `on_period`
  callback (`on_period(done, total, label)`, once per period on both the
  full and the incremental path); `pack.py` knows nothing of styles or
  rich. Modules log progress with `logging.getLogger(__name__)` and report
  problems with `warnings.warn`. There is no bare `imi-dashboard <input>
  <output>` form.
- `info.py`: `describe()` runs the discovery steps of `convert` (layout, gaps,
  dates, grid, buffer split, data-derived defaults, missing files) without
  converting, and returns them with the filled-in config (`RunInfo.config`);
  `format_info()` prints them. For a folder that is a generated
  dashboard (`is_dashboard()`: it holds `data/manifest.json`),
  `format_dashboard_info()` describes it from its manifest instead
  (provenance, mode, period count, grid, region). `run_info_json()` and
  `dashboard_info_json()` give the same as plain dicts for `info --json`
  (strict JSON: non-finite numbers become null); their keys are documented
  in `docs/guide.md` and versioned by `INFO_JSON_VERSION`, so a change to
  them needs the guide's table and, for a renamed or removed key, a new
  version.
- `init_config.py`: what `init-config` writes. `render_generic()` is the
  template with its examples; `render_for_run(RunInfo)` swaps the template's
  marked blocks (`#%header`/`#%sectors` … `#%end`, and the one-line
  `#%region`) for the run's header, sectors (one aligned flow-mapping row
  each) and buffer/roi note, and sets the title and branding lines to the
  run's values (empty where none). The `text:` block (`#%text`) is written
  by `_text_block` in both forms, every key resolved from the cadence and
  grouped by `TEXT_GROUPS`. In the file, `##` lines explain and `# ` lines
  are settings, never mixed; every setting stays commented, so the file
  loads as `Config()`, and uncommenting every setting reproduces what
  `convert` uses. `check_input()` refuses a YAML name, a file or a
  dashboard as the input with a hint.
- `serve.py`: a thin `http.server` wrapper for previewing a dashboard: fixed
  `Content-Type` per extension, `Cache-Control: no-store`, a check that the
  folder is a dashboard, request lines through the `imi_dashboard` logger
  (failed requests as warnings, the others at info, shown with `-v`), and
  `is_loopback`, which decides whether `serve` adds its second warning
  (reachable from the network) to the development-server one.
- `discover.py`: detects single-period vs. timeseries input from the folder
  shape; for a folder that is neither, the error names any runs one level
  down (a folder of runs given instead of a run).
- `readers.py`: one function per IMI file type (xarray/pandas, returning numpy
  arrays and dicts), including the state-vector mask and the classification of
  state-vector elements into buffer elements and region of interest
  (`identify_buffer_elements`), and `csv_number`, a statistics-CSV column as
  a finite float. Code for older IMI layouts lives here and in `totals.py`
  only, tagged `# compat: IMI < …`.
  `ensemble_statistics.csv` is not read; the ensemble range comes from
  columns of `viz_notebook_statistics.csv`. An optional file that cannot be
  read is treated as missing, with an `UnreadableFileWarning`;
  `netcdf_read_problem` is the check `convert` runs on each period's
  `posterior.nc` and `prior.nc` (a period failing it is skipped, through
  `discover.split_unreadable`).
- `grid.py`: `GridMeta`, the grid read from netCDF coordinate variables, the
  `align_to_grid` reindexing helper, and `mask_index_bbox`, the cell-index
  window enclosing a mask (the `--region roi` crop).
- `defaults.py`: the config defaults taken from the first complete period
  (`fill_defaults_from_data`: sector list, cadence, and the `total`
  sector's variable when the config names none, `total_sector_var`: the
  total without the soil sink, `EmisCH4_Total_ExclSoilAbs`, unless
  `posterior.nc` lacks it; `total_definition`, the manifest's
  `totalDefinition` record of whether the total's numbers and map include
  the sink, shown as labels by the web app), used by `convert` and
  `info`. Separate from `config.py` because it reads netCDF (xarray), which
  the CLI must not import at start-up.
- `totals.py`: the total sector's numbers (prior, posterior, ensemble
  range), always without the soil sink: `total_numbers` reads the
  statistics CSV's `TotalExclSoilAbsPrior`/`TotalExclSoilAbsPosterior`
  (`TotalExclSoilAbsorb…` in IMI 2.1.0 and 2.2.0), never its headline
  `PriorEmissions`/`PosteriorEmissions` (in Kalman mode IMI writes the
  prior there with the sink and the posterior without it); for a CSV of an
  IMI development version before 2.1.0, which has neither pair, it sums
  `EmisCH4_Total_ExclSoilAbs` over the region of interest, after checking
  that the same sum of `posterior.nc` reproduces the CSV.
  `_ensemble_range` moves IMI 2.2.1's ensemble range, summed with the
  sink, off it: such a run is recognised by `EnsemblePosterior_Mean`
  equalling `TotalPosterior` (the posterior is the ensemble mean, from
  2.2.1 on). `headline_totals` classifies the headline pair against the
  other columns, and `report_totals` warns once per conversion about a
  layout it does not know (`UnknownTotalsLayoutWarning`: a change in
  IMI), periods shown with the headline pair, or ensemble ranges it could
  not correct (`EnsembleRangeLeftOutWarning`).
  `manifest.source.totalNumbers` records the definition, so a dashboard
  converted before it is reconverted in full.
- `pack.py`: the pipeline. Packs per-period Float32 rasters into
  `data/periods/0000.bin`, `0001.bin`, … (the period index, four digits),
  writes `manifest.json`, `globals.bin` and `config.json`, copies the web
  bundle, writes the dashboard's `README.md` from `templates/README.md`, and
  supports incremental re-conversion. `manifest.provenance` records what
  built the dashboard, for its readers: `converter` (name and
  `__version__`), `convertedAt` (UTC time of the run, from `_utc_now()`, in
  the form `2026-11-04T10:15:30Z`, rewritten by incremental runs too) and
  `input` (the input folder's name, never its path); there is no IMI
  version, because IMI writes it into no file the converter reads.
  `manifest.source` holds a hash of the
  input folder path and each sector's `imi_var` (a change forces a full
  rebuild); each period entry's `inputs` holds a hash of the names, sizes and
  modification times of the files in its folder (a change reconverts that
  period). The full and the incremental paths build period entries and the
  manifest through the same helpers (`_period_entry`, `_write_manifest`,
  which writes the manifest last and atomically). `manifest.source` also
  holds the converter's release (`_converter_release`), so a new release
  reconverts every period. `convert` refuses an output folder that holds
  files but no `data/manifest.json` naming imi-dashboard as its converter,
  or that is or contains the input folder. It only ever deletes what it
  writes (`OWNED_ENTRIES` and the files listed in the dashboard's app
  bundle marker); other entries of the folder are kept. Before it removes periods
  that left the input, or replaces a dashboard of another input folder, it
  calls the `confirm` callback (`_removal_notice`); the CLI asks on a
  terminal and otherwise requires `--yes`. The app bundle (the built web
  app that `--app-bundle`, a developer option, names) is found and checked
  before any period is read (`_find_web_app`): a folder counts as one only
  with the `imi-dashboard-app.json` marker that the web build writes, for
  the converter's `MANIFEST_VERSION`, and only the files it lists are
  copied.
- `config.py`: the pydantic schema for the config file (the file and every key
  are optional), the standard sector table (`DEFAULT_SECTOR_TABLE`), `default_sectors`,
  `cadence_from_days`, and `FIELD_LAYOUT_DIAGNOSTICS`, the diagnostic maps
  packed after the sector fields in each period file (here so that a sector id
  equal to one of them can be refused).

Output site: `index.html`, `README.md`, `imi-dashboard-app.json`, `assets/` (the copied bundle) and
`data/{config.json, manifest.json, globals.bin, periods/0000.bin, …}`.

### Web app (`web/src/`)

- `main.tsx`: the entry module. It imports the fonts (the
  `@fontsource-variable` packages, so no request goes to Google Fonts) and
  gives MapLibre the URL of its worker script (`setWorkerUrl`), which
  MapLibre 6 loads as a separate file that the bundler would not otherwise
  place where MapLibre looks for it.
- `App.tsx`: composition root; owns the period index, playback, view, layout,
  sector, chart style, colour scale and ramp.
- `data/`: the lazy two-tier loader (`globals.bin` once, per-period rasters on
  demand, held in an LRU cache) and the shared types.
  `data/format.json` holds the manifest version the app reads, which must
  equal `MANIFEST_VERSION` in `pack.py`. `web/vite.config.ts` writes the
  `imi-dashboard-app.json` marker into `web/dist/` after each build.
- `map/`: MapLibre GL + deck.gl `BitmapLayer` map, colour bar, map settings,
  period navigation, colour ramps, rasterisation, and `basemap.ts`, the base
  map (tile source, attribution control) shared by the overview and the
  diagnostic maps, from the config's `map.tiles*` keys.
- `chart/TimeseriesChart.tsx`: the SVG timeseries chart.
- `diagnostics/`: the diagnostics view, its spatial panels (`DVMap`) and the
  scalar timeseries (`DVTimeseries`).
- `components/`: header, footer and the smaller panels.
- `tokens.css`, `styles.css`: the design tokens and styles.

The layout is chosen from the data: one period gives the single-period layout
(map, scalars and sector comparison, no chart or playback); more than one
gives the timeseries layout.

A timeseries is taken to be one Kalman-mode IMI run, in which period 1's
prior is the inventory and each later period's prior is the previous
posterior nudged back toward it (IMI's `NudgeFactor`). Nothing in the input
says which mode made it, so this is an assumption, not a check: the prior
tile's subtitle (`Scalars.tsx`) depends on it, and so does the default
header subtitle ("Kalman-filter inversion", `config.py`). A folder of
independent per-period inversions is not supported as a timeseries
(docs/guide.md, "A timeseries is one Kalman-mode run").

## Commands

```bash
# one-time setup
python -m venv .venv && source .venv/bin/activate
pip install -e "tools/imi-dashboard[dev]"
npm --prefix web install
# optional: the browser tests (tests/test_browser.py)
pip install -e "tools/imi-dashboard[dev,browser]"
playwright install --with-deps chromium

# checks (CI runs the same three, the browser tests in a job of their own, and
# the converter tests again with the lowest dependency versions pyproject.toml
# allows; see CONTRIBUTING.md, "Checks")
pytest tools/imi-dashboard/tests/
mypy --config-file tools/imi-dashboard/pyproject.toml tools/imi-dashboard/imi_dashboard
npm --prefix web run build      # runs the TypeScript check (tsc --noEmit), then vite build

# rebuild the bundle and the sample dashboards under dashboards/
./build.sh                  # --skip-build if only the converter changed; --rebuild for a full reconvert;
                            # --region domain to keep the buffer elements

# describe a run, convert it by hand, then preview it
imi-dashboard info sample_files/timeseries
imi-dashboard init-config sample_files/timeseries -o -    # the config the run gets without one, commented
imi-dashboard convert sample_files/timeseries dashboards/romania --config examples/config.example.yaml
imi-dashboard serve dashboards/romania --port 5181
```

The converter copies whatever is in `web/dist/`, so after changing `web/src/`
run `npm --prefix web run build` (or `./build.sh`) before converting; the
converter warns when `web/dist/` is older than the web source.

## Conventions

- **Nothing about the grid or the number of periods is hard-coded.** The
  converter reads resolution, extent and cell counts from the netCDF coordinate
  variables; the web app reads them from `manifest.json`. Timeseries can run to
  hundreds of periods, which is why per-period data is loaded on demand and
  never bundled up front.
- **Converter changes come with a test** under `tools/imi-dashboard/tests/`,
  following the package's patterns (xarray readers, `GridMeta` alignment,
  pydantic config) rather than standalone scripts. Assert concrete values from
  the fixtures, not only that output exists. `tests/conftest.py` holds the
  sample paths, `invoke()` (runs the CLI in-process; prefer it to a
  subprocess) and an autouse fake web bundle, so conversions behave the same
  whether or not `web/dist/` is built.
- **`tests/test_browser.py` drives the real web app** (pytest-playwright,
  headless Chromium): it converts both samples with `--app-bundle web/dist`
  (bypassing the fake bundle), serves each with `serve.make_server(port=0)`
  in a thread, and asserts the text a reader sees (titles, dates, totals,
  period numbers, diagnostics values) against the fixtures. It keeps the
  browser offline (blank map tiles, any other outside request blocked; the
  fonts are bundled from the `@fontsource-variable` packages) and fails a
  test on a console error unless the test expects it
  (`browser_log.expect_errors`). Without the `browser`
  extra, Chromium or a built `web/dist/` it is skipped (failed when
  `IMI_DASHBOARD_REQUIRE_BROWSER` is set, as on CI); with a `web/dist/` older
  than the web source it fails, so rebuild before running it.
- **The fixtures in `sample_files/` are fixed.** `single_period/` is the IMI
  team's Permian Basin quick-start (IMI 2.3.1, buffer rings);
  `timeseries/` holds periods 1, 2 and 4 of a Romania run (older IMI:
  `tropomi` variable names, 8 buffer clusters, no ensemble columns), with
  period 3 missing on purpose to exercise the missing-period handling
  (`sample_files/README.md` describes both). The tests assert exact values
  from these files, so do not add, remove or renumber periods without
  updating them.
- **`web/src/tokens.css` and `styles.css` are the design contract**: colours,
  type (DM Sans, Inter, JetBrains Mono), spacing, shadows and motion. Reuse the
  tokens rather than introducing new values.
- **Record user-visible changes in `CHANGELOG.md`** under `## [Unreleased]`
  (Keep a Changelog: Added / Changed / Fixed).
- **Never commit inversion results that are not yours to publish.** Full
  datasets go in `local_data/`; generated dashboards go in `dashboards/`. Both
  are gitignored.
