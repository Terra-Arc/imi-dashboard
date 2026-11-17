import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import { attributionControl, basemapStyle, type Basemap } from "./basemap";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { BitmapLayer } from "@deck.gl/layers";

import type { GridMeta, PeriodData, PeriodEntry, SectorMeta } from "../data/types";
import { RAMPS } from "./ramps";
import { rasterizeFieldToCanvas } from "./rasterize";
import { Colorbar } from "./Colorbar";
import { MapSettings } from "./MapSettings";
import { PeriodNav } from "./PeriodNav";
import { withLabel } from "../util/text";

import "maplibre-gl/dist/maplibre-gl.css";

interface Props {
  period: PeriodData | undefined;
  sector: string;
  rampName: string;
  setRampName: (r: string) => void;
  scale: "linear" | "log";
  setScale: (s: "linear" | "log") => void;
  /** Manual colorbar overrides keyed by sector id (see App state). */
  colorLimits: Record<string, { min: number; max: number }>;
  setColorLimit: (sectorId: string, limits: { min: number; max: number } | null) => void;
  grid: GridMeta;
  bounds: Record<string, { min: number; max: number }>;
  sectors: SectorMeta[];
  isSinglePeriod: boolean;
  setPeriodIdx?: (idx: number) => void;
  globalsMask: Uint8Array;
  periods: PeriodEntry[];
  periodIdx: number;
  /** Tight [W, S, E, N] of the active mask area; if null the full grid
   *  bbox is used for the camera fit. */
  dataBbox: [number, number, number, number] | null;
  /** Draw the on-map colorbar over a translucent background (config-driven). */
  colorbarTransparentBg: boolean;
  /** The base map (tiles, attribution), from the config. */
  basemap: Basemap;
  /** Why the current period's maps are absent, if they are: still loading,
   *  or the fetch failed. Null once the period is loaded. */
  loadState: "loading" | "failed" | null;
  /** Soil-sink label of the total sector's map ("excluding the soil sink"),
   *  or null when the dashboard does not record it. */
  totalMapLabel: string | null;
}

// The base map (basemap.ts) is best-effort: without network access the tiles
// fail to load and the plain background stays visible; `map.tiles: none`
// leaves it out on purpose. How strongly the tiles are faded on this map:
const BASEMAP_LOOK = { opacity: 0.55, saturation: -0.35 };

export function MapPanel({
  period,
  sector,
  rampName,
  setRampName,
  scale,
  setScale,
  colorLimits,
  setColorLimit,
  grid,
  bounds,
  sectors,
  isSinglePeriod,
  setPeriodIdx,
  globalsMask,
  periods,
  periodIdx,
  dataBbox,
  colorbarTransparentBg,
  basemap,
  loadState,
  totalMapLabel,
}: Props) {
  const manifestDataBbox = dataBbox;
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const [mapReady, setMapReady] = useState(false);

  const sectorMeta = sectors.find((s) => s.id === sector) ?? sectors[0];
  // Data-derived bounds for the active sector. The manual override (if any)
  // wins and stays fixed across every period; `autoStats` is still kept so
  // the settings panel can prefill inputs and the Reset control can restore.
  const autoStats = bounds[sector] ?? { min: 0, max: 1 };
  const override = colorLimits[sector];
  const stats = override ?? autoStats;
  // A log scale cannot show a sector with no value above 0 (a sink such as
  // the soil absorption): every cell would get the floor's colour. Such a
  // sector is drawn on the linear scale, and its colour bar says so by
  // leaving out the "log" tag.
  const isLog = scale === "log" && stats.max > 0;
  // In log scale a manual minimum above 0 sets the low end of the colour
  // range; otherwise the range spans three decades below the maximum.
  const logMin = override?.min;

  // Bitmap bounds [west, south, east, north]. Pad by half a cell so the
  // texture aligns with cell centers rather than cell edges — the manifest
  // lat/lon arrays are centers, not corners.
  const bitmapBounds = useMemo<[number, number, number, number]>(() => {
    const halfLon = grid.dlon / 2;
    const halfLat = grid.dlat / 2;
    return [
      grid.bbox[0] - halfLon,
      grid.bbox[1] - halfLat,
      grid.bbox[2] + halfLon,
      grid.bbox[3] + halfLat,
    ];
  }, [grid]);

  // Camera bounds — prefer the converter-computed data bbox (tightly fitted
  // to the run's mask) over the wider full-grid bbox.
  const cameraBounds = useMemo<[number, number, number, number]>(() => {
    const db = manifestDataBbox;
    if (db) return db;
    return bitmapBounds;
  // bitmapBounds intentional — re-fit when grid changes.
  }, [manifestDataBbox, bitmapBounds]);

  // Initialise MapLibre once.
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: basemapStyle(basemap, BASEMAP_LOOK),
      bounds: [
        [cameraBounds[0], cameraBounds[1]],
        [cameraBounds[2], cameraBounds[3]],
      ],
      fitBoundsOptions: { padding: 16 },
      attributionControl: attributionControl(basemap),
      cooperativeGestures: false,
    });
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: false, showCompass: false }), "top-right");
    // The overlay is added straight away rather than on MapLibre's `load`
    // event, which waits until every basemap tile has loaded or failed. The
    // overlaid (non-interleaved) MapboxOverlay only needs the map's camera,
    // not its style, so the data can draw before the basemap arrives.
    const overlay = new MapboxOverlay({ layers: [] });
    // Cast: maplibre's addControl signature is structurally compatible.
    map.addControl(overlay as unknown as maplibregl.IControl);
    overlayRef.current = overlay;
    mapRef.current = map;
    setMapReady(true);
    return () => {
      overlayRef.current = null;
      mapRef.current?.remove();
      mapRef.current = null;
      setMapReady(false);
    };
    // Dependencies intentionally omitted — the map is created once and stays
    // around for the component's lifetime; bounds changes are handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Refit the camera when bounds change (new grid or new data bbox).
  useEffect(() => {
    if (!mapReady) return;
    mapRef.current?.fitBounds(
      [
        [cameraBounds[0], cameraBounds[1]],
        [cameraBounds[2], cameraBounds[3]],
      ],
      { padding: 16, duration: 0 },
    );
  }, [mapReady, cameraBounds]);

  // Re-render the BitmapLayer whenever the displayed period / sector / ramp /
  // scale changes. `mapReady` is a dep so the first paint happens whether
  // the period arrives before or after the map's `load` event fires.
  // Cells outside the land mask render as transparent.
  useEffect(() => {
    if (!mapReady || !overlayRef.current) return;
    const field = period?.fields[sector];
    if (!field) {
      overlayRef.current.setProps({ layers: [] });
      return;
    }
    const canvas = rasterizeFieldToCanvas({
      field,
      mask: globalsMask ?? null,
      cols: grid.cols,
      rows: grid.rows,
      vmin: stats.min,
      vmax: stats.max,
      ramp: RAMPS[rampName] ?? RAMPS.viridis,
      isLog,
      logMin,
    });
    const layer = new BitmapLayer({
      id: `methane-${sector}-${period?.entry.idx ?? 0}-${rampName}-${scale}`,
      bounds: bitmapBounds,
      image: canvas,
      // The grid's rows are evenly spaced in latitude, not in Web Mercator
      // y, so the image is mapped in longitude/latitude; the default
      // interpolates it linearly in Mercator and shifts rows north.
      _imageCoordinateSystem: "lnglat",
      opacity: 0.92,
      pickable: false,
      // Render each grid cell as a crisp rectangle instead of the linearly
      // interpolated default — this is gridded data, not a photograph.
      textureParameters: {
        minFilter: "nearest",
        magFilter: "nearest",
      },
    });
    overlayRef.current.setProps({ layers: [layer] });
  }, [mapReady, period, sector, rampName, scale, isLog, grid.cols, grid.rows, globalsMask, bitmapBounds, stats.min, stats.max, logMin]);

  return (
    <div className="map-panel">
      <PeriodNav
        periods={periods}
        periodIdx={periodIdx}
        setPeriodIdx={setPeriodIdx}
        isSinglePeriod={isSinglePeriod}
      >
        <MapSettings
          scale={scale}
          setScale={setScale}
          rampName={rampName}
          setRampName={setRampName}
          vmin={stats.min}
          vmax={stats.max}
          autoMin={autoStats.min}
          autoMax={autoStats.max}
          isManual={!!override}
          onSetLimits={(min, max) => setColorLimit(sector, { min, max })}
          onResetLimits={() => setColorLimit(sector, null)}
        />
      </PeriodNav>
      <div className="map-stage map-stage-live">
        <div ref={containerRef} className="map-canvas-host" />
        <PeriodLoadNote state={loadState} />
        <Colorbar
          rampName={rampName}
          vmin={stats.min}
          vmax={stats.max}
          isLog={isLog}
          logMin={logMin}
          sectorLabel={sectorMeta?.label}
          sectorColor={sectorMeta?.color}
          translucentBg={colorbarTransparentBg}
        />
      </div>
      <div className="map-caption">
        <span className="overline" style={{ color: "var(--neutral-500)" }}>
          {sectorMeta && <span className="sector-dot" style={{ background: sectorMeta.color }} />}
          {withLabel(`${sectorMeta?.label ?? sector} methane emissions`, sector === "total" ? totalMapLabel : null)}
        </span>
        <span className="map-units">kg CH₄ km⁻² h⁻¹</span>
      </div>
    </div>
  );
}

/** Note over a map whose period is not drawn: loading, or the fetch failed. */
export function PeriodLoadNote({ state }: { state: "loading" | "failed" | null }) {
  if (!state) return null;
  return state === "failed" ? (
    <div className="map-msg map-msg-error" role="status">
      Could not load the maps for this period. Change period to try again.
    </div>
  ) : (
    <div className="map-msg map-msg-loading" role="status">Loading maps…</div>
  );
}
