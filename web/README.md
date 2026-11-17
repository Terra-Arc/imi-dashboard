# IMI Dashboard — web app

React + TypeScript app that reads the `data/` folder written by
`imi-dashboard` (`tools/imi-dashboard/`).

## Prerequisites

Node.js ≥ 20.19 or ≥ 22.12, with npm (Vite 8's requirement). `.nvmrc` pins
LTS 22; `fnm use` / `nvm use` in this folder picks it up.

## Develop

```
cd web
npm install
npm run dev
```

The dev server needs data in `public/data/`: link (or copy) the `data/`
folder of a generated dashboard, e.g. one from `./build.sh`. From the
repository root:

```
ln -s ../../dashboards/single/data web/public/data               # bash
cmd /c mklink /D web\public\data ..\..\dashboards\single\data    # PowerShell (admin or Developer Mode)
```

`web/public/data` is gitignored.

**Remove the link before `npm run build`**: Vite copies `public/` into
`dist/`, and the next conversion would then fail with
`error: [Errno 17] File exists: '<output>/data/periods'`. Use
`rm web/public/data` (bash) or `rmdir web\public\data` (Windows; removes only
the link).

## Build

```
npm run build       # writes web/dist/
```

`imi-dashboard convert --app-bundle web/dist <input> <output>` copies the
build into a dashboard.

## What's where

```
src/
  main.tsx                      entry point
  App.tsx                       composition root: state, view routing, play loop
  tokens.css                    design tokens: colours, type, spacing, shadows, motion
  styles.css                    layout and component styles
  data/
    types.ts                    manifest / config / period shapes, in lockstep with imi_dashboard/pack.py
    loader.ts                   fetch, LRU cache, useDataAPI / useImiPeriod hooks
  util/
    text.ts                     {placeholder} substitution, date formatting
    color.ts                    colour helpers
  components/                   Header, Footer, LayoutToggle, ChartSettings, PanelHeader, Scalars,
                                SectorBar (SectorPill buttons with sparklines), SectorComparison, Diagnostics
  map/
    MapPanel.tsx                overview map: MapLibre GL base map + deck.gl BitmapLayer
    Colorbar.tsx, MapSettings.tsx, PeriodNav.tsx   colour bar, scale/ramp/limits popover, period navigation
    ramps.ts, rasterize.ts      colour ramps; field → canvas
  chart/TimeseriesChart.tsx     SVG chart (line / area / candle / bar, uncertainty band, playhead, click-to-seek)
  diagnostics/
    DiagnosticsView.tsx         diagnostics page
    DVMap.tsx                   one spatial diagnostic panel
    DVTimeseries.tsx            scalar diagnostic charts
```
