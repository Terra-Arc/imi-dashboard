import { useEffect, useMemo, useState } from "react";
import { useDataAPI, useImiPeriod } from "./data/loader";
import { Header } from "./components/Header";
import { Footer } from "./components/Footer";
import { SectorBar } from "./components/SectorBar";
import { Scalars } from "./components/Scalars";
import { SectorComparison } from "./components/SectorComparison";
import { Diagnostics } from "./components/Diagnostics";
import { PanelHeader } from "./components/PanelHeader";
import { ChartSettings, nearestSpeed } from "./components/ChartSettings";
import { MapPanel } from "./map/MapPanel";
import { TimeseriesChart } from "./chart/TimeseriesChart";
import { DiagnosticsView } from "./diagnostics/DiagnosticsView";
import { applyTemplate, formatDate, formatEndDate, soilSinkLabel, withLabel, yearSpan } from "./util/text";
import { RAMP_OPTIONS } from "./map/ramps";
import { basemapFromConfig } from "./map/basemap";
import type { PeriodEntry } from "./data/types";

const DEFAULT_RAMP = "reds";
/** Normalize a configured ramp to a known overview colormap. Guards against
 *  stale configs that still name a retired ramp (e.g. "terra"/"arc"). */
function normalizeRamp(ramp: string): string {
  return RAMP_OPTIONS.some((r) => r.id === ramp) ? ramp : DEFAULT_RAMP;
}

/** Whether any period has an ensemble range (the ensemble min/max columns
 *  of viz_notebook_statistics.csv) for any sector. */
function runHasEnsemble(periods: PeriodEntry[]): boolean {
  return periods.some((p) =>
    Object.values(p.sectorScalars).some((s) => s.ens_min != null && s.ens_max != null),
  );
}

type Layout = "split" | "chart-first";
type View = "overview" | "diagnostics";
type ChartStyle = "line" | "area" | "candle" | "bar";
type Scale = "linear" | "log";

export function App() {
  const { api, error: loadError } = useDataAPI();

  const [periodIdx, setPeriodIdx] = useState(0);
  const [view, setView] = useState<View>("overview");
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(3);
  const [layout, setLayout] = useState<Layout>("split");
  const [sector, setSector] = useState<string>("total");
  const [chartStyle, setChartStyle] = useState<ChartStyle>("candle");
  const [scale, setScale] = useState<Scale>("linear");
  const [rampName, setRampName] = useState<string>(DEFAULT_RAMP);
  // Manual colorbar limits, keyed by sector id. When a sector has an entry
  // here it overrides the data-derived bounds and stays fixed across every
  // period of the timeseries. Cleared per-sector by the "Reset" control.
  const [colorLimits, setColorLimits] = useState<Record<string, { min: number; max: number }>>({});
  const [diagOpen, setDiagOpen] = useState(false);

  // After config loads, pull defaults from it.
  useEffect(() => {
    if (!api) return;
    setRampName(normalizeRamp(api.config.map.ramp));
    setScale(api.config.map.scale);
    // Without an ensemble the candle and bar styles have no range to draw,
    // so the default falls back to a line; every style stays selectable.
    const style = api.config.chart.defaultStyle;
    const ensemble = runHasEnsemble(api.manifest.periods);
    setChartStyle(!ensemble && (style === "candle" || style === "bar") ? "line" : style);
    setSpeed(nearestSpeed(api.config.chart.defaultSpeed));
    // Default sector = first listed.
    if (api.config.sectors.length > 0) {
      setSector(api.config.sectors[0].id);
    }
    // Land on the first period so the user sees the start of the timeseries
    // rather than its midpoint or end.
    setPeriodIdx(0);
    // The browser tab shows the dashboard's name (config display_name).
    document.title = api.config.displayName;
  }, [api]);

  const isSinglePeriod = !!api && api.manifest.periods.length <= 1;
  const hasEnsemble = useMemo(() => !!api && runHasEnsemble(api.manifest.periods), [api]);
  const onDiagView = view === "diagnostics";
  const periodCount = api?.manifest.periods.length ?? 0;

  // Play loop. Single source of truth for time, owned by App. Playback
  // stops on the last period; started from the last period, it restarts
  // from the first.
  useEffect(() => {
    if (!playing || !api || isSinglePeriod) return;
    const interval = 1000 / Math.max(1, speed);
    const id = window.setInterval(() => {
      setPeriodIdx((i) => {
        if (i >= periodCount - 1) return 0;
        const next = i + 1;
        if (next === periodCount - 1) setPlaying(false);
        return next;
      });
    }, interval);
    return () => window.clearInterval(id);
  }, [playing, speed, api, isSinglePeriod, periodCount]);

  // Keyboard nav (timeseries only).
  useEffect(() => {
    if (!api || isSinglePeriod) return;
    function onKey(e: KeyboardEvent) {
      // A key already handled by the element that has focus (MapLibre pans
      // on the arrow keys and calls preventDefault) is left alone, as are
      // keys typed into form fields, and Space on a focused button (which
      // clicks the button).
      if (e.defaultPrevented) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      if (e.key === " " && tag === "BUTTON") return;
      if (e.key === "ArrowRight") {
        setPlaying(false);
        setPeriodIdx((i) => Math.min(periodCount - 1, i + 1));
      } else if (e.key === "ArrowLeft") {
        setPlaying(false);
        setPeriodIdx((i) => Math.max(0, i - 1));
      } else if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [api, isSinglePeriod, periodCount]);

  const { period, failed: periodFailed } = useImiPeriod(api, periodIdx);
  const loadState: "loading" | "failed" | null = period ? null : periodFailed ? "failed" : "loading";

  // Hooks must run in the same order on every render, so compute tmplVars
  // before the api-loaded guard — not after, or the api=null → api=loaded
  // transition triggers "Rendered more hooks…" and blanks the page.
  const periodEntry = api?.manifest.periods[periodIdx];
  const text = api?.config.text;
  // Dates are formatted for display; date_end is the last day of the last
  // period (IMI's EndDate is exclusive, see formatEndDate).
  const dateVar = (iso: string | null | undefined, end = false) =>
    iso ? (end ? formatEndDate(iso) : formatDate(iso)) : "";
  const tmplVars = useMemo(() => ({
    count: String(periodCount),
    date: dateVar(periodEntry?.start),
    date_start: dateVar(api?.manifest.periods[0]?.start),
    date_end: dateVar(api?.manifest.periods[periodCount - 1]?.end, true),
    years: yearSpan(api?.manifest.periods[0]?.start, api?.manifest.periods[periodCount - 1]?.end),
    cadence_singular: text?.cadence_singular ?? "",
    cadence_plural: text?.cadence_plural ?? "",
  }), [api, periodCount, periodEntry, text]);

  if (loadError) {
    return (
      <div className="app-loading app-loading-error">
        <strong>Could not load the dashboard data.</strong>
        <p>
          The files in the dashboard&apos;s <code>data/</code> folder could not
          be read. If you opened <code>index.html</code> straight from disk,
          serve the folder over HTTP instead: the <code>README.md</code> in the
          dashboard folder explains how.
        </p>
        <p className="app-loading-detail">Error: {loadError}</p>
      </div>
    );
  }
  if (!api || !text) return <div className="app-loading">Loading IMI data…</div>;

  // Set (limits) or clear (null) the manual colorbar override for one sector.
  const setColorLimit = (sectorId: string, limits: { min: number; max: number } | null) => {
    setColorLimits((prev) => {
      if (limits === null) {
        if (!(sectorId in prev)) return prev;
        const next = { ...prev };
        delete next[sectorId];
        return next;
      }
      return { ...prev, [sectorId]: limits };
    });
  };

  // Whether the total sector's numbers and map include the soil sink, as
  // labels ("excluding the soil sink"); null when the dashboard does not say.
  const totalDef = api.manifest.totalDefinition;
  const totalLabel = soilSinkLabel(totalDef?.numbersIncludeSoilSink);
  const totalMapLabel = soilSinkLabel(totalDef?.mapIncludesSoilSink);
  // The chart shows the selected sector's numbers.
  const chartTitle = (template: string) =>
    withLabel(applyTemplate(template, tmplVars), sector === "total" ? totalLabel : null);

  // Map and chart receive the loaded period (may be undefined briefly).
  const basemap = basemapFromConfig(api.config.map);
  const mapProps = {
    period,
    sector,
    rampName,
    setRampName,
    scale,
    setScale,
    colorLimits,
    setColorLimit,
    grid: api.manifest.grid,
    bounds: api.manifest.sectorBounds,
    sectors: api.config.sectors,
    isSinglePeriod,
    setPeriodIdx: isSinglePeriod ? undefined : setPeriodIdx,
    globalsMask: api.globalsMask,
    periods: api.manifest.periods,
    periodIdx,
    dataBbox: api.manifest.globals.dataBbox ?? null,
    colorbarTransparentBg: api.config.map.colorbarTransparentBg ?? true,
    basemap,
    loadState,
    totalMapLabel,
  };

  // Chart-panel controls: a modest play/pause toggle next to the chart-options
  // popover. The chart is only shown in timeseries mode, so the play button
  // rides along with it (single-period dashboards have neither).
  const chartControls = (
    <div className="panel-hd-controls">
      <button
        className="ts-play"
        onClick={() => setPlaying(!playing)}
        aria-label={playing ? "Pause" : "Play"}
      >
        {playing ? (
          <svg width="14" height="14" viewBox="0 0 14 14">
            <rect x="3" y="2" width="3" height="10" rx="0.5" fill="currentColor" />
            <rect x="8" y="2" width="3" height="10" rx="0.5" fill="currentColor" />
          </svg>
        ) : (
          <svg width="14" height="14" viewBox="0 0 14 14">
            <path d="M3 2 L12 7 L3 12 Z" fill="currentColor" />
          </svg>
        )}
      </button>
      <ChartSettings
        chartStyle={chartStyle}
        setChartStyle={setChartStyle}
        speed={speed}
        setSpeed={setSpeed}
        speedUnit={text.speed_unit}
      />
    </div>
  );

  return (
    <div className={`app layout-${layout} ${isSinglePeriod ? "mode-single" : "mode-ts"} view-${view}`}>
      <Header
        config={api.config}
        view={view}
        setView={setView}
        layout={layout}
        setLayout={setLayout}
        showLayoutToggle={!onDiagView && !isSinglePeriod}
        isSinglePeriod={isSinglePeriod}
        templateVars={tmplVars}
      />

      <main className="main">
        {onDiagView ? (
          <DiagnosticsView
            api={api}
            period={period}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
            playing={playing}
            setPlaying={setPlaying}
            speed={speed}
            setSpeed={setSpeed}
            rampName={rampName}
            isSinglePeriod={isSinglePeriod}
            templateVars={tmplVars}
            loadState={loadState}
          />
        ) : (
          <>
            <SectorBar
              sectors={api.config.sectors}
              sector={sector}
              setSector={setSector}
              manifest={api.manifest}
              periodIdx={periodIdx}
              isSinglePeriod={isSinglePeriod}
            />

            {isSinglePeriod ? (
              <div className="grid-single">
                <section className="panel panel-map panel-map-hero">
                  <MapPanel {...mapProps} />
                </section>
                <section className="panel panel-side">
                  <div className="panel-side-inner">
                    <Scalars entry={periodEntry} sector={sector} sectors={api.config.sectors} hasEnsemble={hasEnsemble} totalLabel={totalLabel} timeseries={!isSinglePeriod} />
                    <SectorComparison entry={periodEntry} sector={sector} sectors={api.config.sectors} totalLabel={totalLabel} />
                  </div>
                </section>
              </div>
            ) : layout === "split" ? (
              <div className="grid-split">
                <section className="panel panel-map">
                  <MapPanel {...mapProps} />
                </section>
                <section className="panel panel-side">
                  <div className="panel-side-inner">
                    <Scalars entry={periodEntry} sector={sector} sectors={api.config.sectors} hasEnsemble={hasEnsemble} totalLabel={totalLabel} timeseries={!isSinglePeriod} />
                    <div className="ts-panel">
                      <PanelHeader
                        title={chartTitle(text.chart_title_split)}
                        subtitle={applyTemplate(text.chart_subtitle_split, tmplVars)}
                        right={chartControls}
                      />
                      <TimeseriesChart
                        api={api}
                        periodIdx={periodIdx}
                        setPeriodIdx={setPeriodIdx}
                        playing={playing}
                        setPlaying={setPlaying}
                        chartStyle={chartStyle}
                        sector={sector}
                        height={220}
                      />
                    </div>
                  </div>
                </section>
              </div>
            ) : (
              <div className="grid-stack">
                <section className="panel ts-panel ts-panel-hero">
                  <PanelHeader
                    title={chartTitle(text.chart_title_chart_first)}
                    subtitle={applyTemplate(text.chart_subtitle_chart_first, tmplVars)}
                    large
                    right={chartControls}
                  />
                  <TimeseriesChart
                    api={api}
                    periodIdx={periodIdx}
                    setPeriodIdx={setPeriodIdx}
                    playing={playing}
                    setPlaying={setPlaying}
                    chartStyle={chartStyle}
                    sector={sector}
                    height={280}
                  />
                </section>
                <section className="grid-cf-bottom">
                  <div className="panel panel-map">
                    <MapPanel {...mapProps} />
                  </div>
                  <div className="panel panel-side">
                    <Scalars entry={periodEntry} sector={sector} sectors={api.config.sectors} hasEnsemble={hasEnsemble} totalLabel={totalLabel} timeseries={!isSinglePeriod} />
                  </div>
                </section>
              </div>
            )}

            <Diagnostics
              entry={periodEntry}
              open={diagOpen}
              onToggle={() => setDiagOpen((s) => !s)}
            />
          </>
        )}
      </main>

      <Footer
        showTerraLogo={api.config.headerBrand !== "terra"}
        attribution={applyTemplate(text.footer_attribution, tmplVars)}
        disclaimer={text.footer_disclaimer}
        metaVersion={text.footer_version}
        provenance={api.manifest.provenance}
      />
    </div>
  );
}
