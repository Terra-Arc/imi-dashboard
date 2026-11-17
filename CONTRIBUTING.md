# Contributing

## Setup

You need Python ≥ 3.11 and Node.js ≥ 20.19 or ≥ 22.12 (Vite 8's requirement;
`web/.nvmrc` pins the development version).

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -e "tools/imi-dashboard[dev]"
npm --prefix web install
npm --prefix web run build           # or ./build.sh
```

The editable install finds the clone's `web/dist/` by itself. `web/dist/` is
not in git: rebuild it after changing or pulling `web/`. The converter warns
when a web source file (`web/src/`, `web/public/`, `web/index.html`,
`package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`) is
newer than `web/dist/index.html`; the check uses modification times only.

[`web/README.md`](web/README.md) covers the web app's development server and
source layout.

## Project layout

```
imi-dashboard/
├── tools/imi-dashboard/   the Python converter (CLI + library)
├── web/                   the React dashboard source
├── dashboards/            generated dashboards (output of build.sh; gitignored)
├── sample_files/          small IMI outputs (Permian Basin, Romania), the test fixtures
├── local_data/            full IMI datasets (gitignored)
├── examples/              annotated example config (Romania)
└── docs/                  user guide, IMI integration, screenshot
```

## Sample dashboards and `build.sh`

`./build.sh` (bash; on Windows, Git Bash or WSL) rebuilds the web app and
converts the samples: Permian Basin to `dashboards/single`, Romania to
`dashboards/romania` (with `examples/config.example.yaml`). It activates
`.venv` if present. Options: `--skip-build`, `--rebuild` (reconvert every
period), `--region domain`.

```bash
./build.sh
imi-dashboard serve dashboards/romania --open
```

To build your own data too, list it in `local_data/build.entries`, one
`input:output-name[:config]` per line, paths relative to the repository root
(`#` comments allowed; missing inputs are skipped with a warning).

Without bash:

```bash
imi-dashboard convert sample_files/single_period dashboards/single
imi-dashboard convert sample_files/timeseries    dashboards/romania --config examples/config.example.yaml
```

## Checks

CI runs these on every push and pull request; run them before opening one:

```bash
pytest tools/imi-dashboard/tests/
mypy --config-file tools/imi-dashboard/pyproject.toml tools/imi-dashboard/imi_dashboard
npm --prefix web run build           # tsc --noEmit, then the production build
```

`npm --prefix web run typecheck` runs only the TypeScript check.

CI also runs the converter tests with the lowest dependency versions
`tools/imi-dashboard/pyproject.toml` allows (Python 3.11). If that fails,
make the code work with the older version or raise the minimum, with the
reason in the comment above the list. Locally, with
[uv](https://docs.astral.sh/uv/):

```bash
uv venv -p 3.11 .venv-lowest
VIRTUAL_ENV=.venv-lowest uv pip install --resolution lowest-direct -e "tools/imi-dashboard[dev]"
.venv-lowest/bin/python -m pytest tools/imi-dashboard/tests/
```

### Browser tests

`tools/imi-dashboard/tests/test_browser.py` converts both samples with the
built web app, serves them and checks in headless Chromium
([Playwright](https://playwright.dev/python/)) what a reader sees: titles,
dates, totals, sector switching, period navigation, playback, diagnostics,
the map, and the note for maps that fail to load. Setup, once:

```bash
pip install -e "tools/imi-dashboard[dev,browser]"
playwright install --with-deps chromium   # --with-deps installs system libraries via apt (sudo)
```

They run within `pytest tools/imi-dashboard/tests/` (about 40 s more). Without
the `browser` extra, Chromium or a built `web/dist/` they are skipped; with a
`web/dist/` older than the source they fail. In CI they run in their own job,
where a missing prerequisite is a failure.

The browser stays offline: map tiles are blank, any other outside request
fails the test. To watch or record a run:

```bash
pytest tools/imi-dashboard/tests/test_browser.py --headed --slowmo 500
pytest tools/imi-dashboard/tests/test_browser.py --tracing retain-on-failure   # playwright show-trace test-results/<test>/trace.zip
```

## What a change should include

- **A test for converter changes**, under `tools/imi-dashboard/tests/`,
  asserting concrete values from `sample_files/` (described in
  `sample_files/README.md`).
- **No hard-coded grid figures or period counts**: the converter reads them
  from the netCDF coordinates, the web app from `manifest.json`.
- **A `CHANGELOG.md` entry** under `## [Unreleased]` (Added / Changed / Fixed).
- **No inversion results that are not yours to publish.** Keep them in
  `local_data/` and `dashboards/`, both gitignored.

## AI coding agents

`AGENTS.md` holds the instructions for AI coding agents. Keep personal
instructions out of it, in a gitignored `CLAUDE.local.md` or your tool's
user-level file. Claude Code reads `AGENTS.md` only when there is no
`CLAUDE.md` or `CLAUDE.local.md`, so start a `CLAUDE.local.md` with
`@AGENTS.md`.

Agents follow these files as instructions, so review changes to `AGENTS.md`
and `.github/` (CI workflows) like build scripts; `.github/CODEOWNERS` assigns
both to the maintainer. Before running an agent on someone else's pull
request, read its changes to these files.

## Building a wheel by hand

A regular (non-editable) install cannot see `web/dist/`. Copy the built web
app into the package first, removing stale copies setuptools would reuse:

```bash
npm --prefix web install && npm --prefix web run build
rm -rf tools/imi-dashboard/build tools/imi-dashboard/imi_dashboard/bundle
mkdir -p tools/imi-dashboard/imi_dashboard/bundle
cp -r web/dist/. tools/imi-dashboard/imi_dashboard/bundle/
pip wheel --no-deps tools/imi-dashboard -w wheels/      # or: pip install tools/imi-dashboard
```

Such a wheel lacks `LICENSE` and `NOTICE`, which the release workflow adds.

Alternatively, pass `--app-bundle path/to/web/dist` on every run. The *app
bundle* (the built web app) is recognised by the `imi-dashboard-app.json` the
build writes (plugin in `web/vite.config.ts`). It lists the files to copy and
the data format read (`web/src/data/format.json`, which must equal
`MANIFEST_VERSION` in `pack.py`; a test checks this). A copied bundle is a
snapshot: only the clone's own `web/dist/` is checked for staleness. Rebuild
and reinstall to update.

## Licence of contributions

The project is under the Apache License 2.0; by its section 5, a contribution
is offered under the same licence. Submit only work you may license this way,
and no data you may not redistribute.

## Making a release

setuptools-scm takes the version from the latest `v<version>` tag: `v0.2.0`
gives `0.2.0` on the tagged commit and e.g. `0.2.1.dev3+g1a2b3c4` three
commits later. An editable install keeps the version it was installed with.

1. Move the `[Unreleased]` entries of `CHANGELOG.md` under the new version and
   commit.
2. Tag and push:

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

`.github/workflows/release.yml` then builds the web app into the package,
builds the wheel and sdist, installs the wheel in a clean environment (without
the clone's `web/dist/`), and checks that `imi-dashboard --version` matches the
tag and that converting `sample_files/single_period` produces `index.html`,
`assets/` and `data/manifest.json`. If both pass, it creates a **draft**
release with notes from the merged pull requests; edit and publish it by hand.
Tags such as `v0.2.0rc1` are marked as pre-releases.

If a check fails, nothing is released: delete the tag
(`git push --delete origin v0.2.0 && git tag -d v0.2.0`), fix, and tag again.
