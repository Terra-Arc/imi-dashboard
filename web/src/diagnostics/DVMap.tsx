import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { Map as MapLibreMap } from "maplibre-gl";
import { attributionControl, basemapStyle, type Basemap } from "../map/basemap";
import { MapboxOverlay } from "@deck.gl/mapbox";
import { BitmapLayer } from "@deck.gl/layers";

import type { GridMeta, PeriodData } from "../data/types";
import { DIVERGENT_RAMP_OPTIONS, RAMP_OPTIONS, RAMPS } from "../map/ramps";
import { rasterizeFieldToCanvas } from "../map/rasterize";
import { Colorbar } from "../map/Colorbar";
import { MapSettings } from "../map/MapSettings";
import { PeriodLoadNote } from "../map/MapPanel";

import "maplibre-gl/dist/maplibre-gl.css";

interface Props {
  label: string;
  caption: string;
  period: PeriodData | undefined;
  fieldKey: string;
  rampName: string;
  /** True for fields like δ where 0 is the natural center (symmetric bounds). */
  diverging?: boolean;
  /** Optional explicit bounds; otherwise from globalBounds, else the current field. */
  vmin?: number;
  vmax?: number;
  /** Units label shown on the colorbar (e.g. "× prior", "obs/cell"). */
  unit?: string;
  /** Cross-period min/max from manifest.diagBounds — fixes the colorscale
   *  over the whole timeseries so periods are directly comparable. */
  globalBounds?: { min: number; max: number } | null;
  grid: GridMeta;
  globalsMask: Uint8Array;
  /** Tight [W, S, E, N] of the active mask; falls back to the grid bbox. */
  dataBbox?: [number, number, number, number] | null;
  /** Why the period's maps are absent, if they are (see MapPanel). */
  loadState: "loading" | "failed" | null;
  /** The base map (tiles, attribution), from the config. */
  basemap: Basemap;
}

const BASEMAP_LOOK = { opacity: 0.45, saturation: -0.5 };

function deriveBounds(field: Float32Array, mask: Uint8Array | null, diverging: boolean): { min: number; max: number } {
  let mn = Infinity;
  let mx = -Infinity;
  for (let i = 0; i < field.length; i += 1) {
    if (mask && !mask[i]) continue;
    const v = field[i];
    if (!Number.isFinite(v)) continue;
    if (v < mn) mn = v;
    if (v > mx) mx = v;
  }
  if (!Number.isFinite(mn) || !Number.isFinite(mx)) return { min: 0, max: 1 };
  if (diverging) {
    const m = Math.max(Math.abs(mn), Math.abs(mx), 1e-9);
    return { min: -m, max: m };
  }
  if (mn === mx) return { min: mn, max: mn + 1 };
  return { min: mn, max: mx };
}

export function DVMap({
  label,
  caption,
  period,
  fieldKey,
  rampName: defaultRampName,
  diverging = false,
  vmin,
  vmax,
  unit,
  globalBounds,
  grid,
  globalsMask,
  dataBbox,
  loadState,
  basemap,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const overlayRef = useRef<MapboxOverlay | null>(null);
  const [ready, setReady] = useState(false);

  // Per-panel view controls, mirroring the overview map's settings popover.
  const [scale, setScale] = useState<"linear" | "log">("linear");
  const [rampName, setRampName] = useState(defaultRampName);
  const [override, setOverride] = useState<{ min: number; max: number } | null>(null);
  // Fields painted with a diverging ramp (δ fields centred on 0, the scale
  // factor centred on 1) offer the divergent colormap set and no log toggle.
  const usesDivergentRamps = DIVERGENT_RAMP_OPTIONS.some((o) => o.id === defaultRampName);
  const isLog = !usesDivergentRamps && scale === "log";

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

  const cameraBounds = useMemo<[number, number, number, number]>(() => {
    return dataBbox ?? bitmapBounds;
  }, [dataBbox, bitmapBounds]);

  // Bounds are computed in render (not inside the layer effect) so the
  // colorbar and the painted raster share the exact same min/max.
  // Auto resolution order: explicit panel bounds (e.g. scale factor's 0–2) →
  // cross-period manifest bounds (fixed over the timeseries; symmetrized for
  // diverging fields) → per-period derivation (legacy manifests only).
  const field = period?.fields[fieldKey];
  const autoBounds = useMemo(() => {
    if (vmin != null && vmax != null) return { min: vmin, max: vmax };
    if (globalBounds) {
      if (diverging) {
        const m = Math.max(Math.abs(globalBounds.min), Math.abs(globalBounds.max), 1e-9);
        return { min: -m, max: m };
      }
      return { min: globalBounds.min, max: globalBounds.max };
    }
    if (!field) return { min: 0, max: 1 };
    return deriveBounds(field, globalsMask ?? null, diverging);
  }, [field, globalsMask, diverging, vmin, vmax, globalBounds]);
  const bounds = override ?? autoBounds;
  // Log scale: a manual minimum above 0 sets the low end (see MapPanel).
  const logMin = override?.min;

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: basemapStyle(basemap, BASEMAP_LOOK),
      bounds: [
        [cameraBounds[0], cameraBounds[1]],
        [cameraBounds[2], cameraBounds[3]],
      ],
      fitBoundsOptions: { padding: 8 },
      attributionControl: attributionControl(basemap),
      interactive: false,
    });
    // Added at once, not on `load` (which waits for the basemap tiles); see
    // MapPanel.
    const overlay = new MapboxOverlay({ layers: [] });
    map.addControl(overlay as unknown as maplibregl.IControl);
    overlayRef.current = overlay;
    mapRef.current = map;
    setReady(true);
    return () => {
      overlayRef.current = null;
      mapRef.current?.remove();
      mapRef.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!ready) return;
    mapRef.current?.fitBounds(
      [
        [cameraBounds[0], cameraBounds[1]],
        [cameraBounds[2], cameraBounds[3]],
      ],
      { padding: 8, duration: 0 },
    );
  }, [ready, cameraBounds]);

  useEffect(() => {
    if (!ready || !overlayRef.current) return;
    if (!field) {
      overlayRef.current.setProps({ layers: [] });
      return;
    }
    const canvas = rasterizeFieldToCanvas({
      field,
      mask: globalsMask ?? null,
      cols: grid.cols,
      rows: grid.rows,
      vmin: bounds.min,
      vmax: bounds.max,
      ramp: RAMPS[rampName] ?? RAMPS.viridis,
      isLog,
      logMin,
      diverging,
    });
    const layer = new BitmapLayer({
      id: `${fieldKey}-${period?.entry.idx ?? 0}-${rampName}`,
      bounds: bitmapBounds,
      image: canvas,
      // Grid rows are evenly spaced in latitude (see MapPanel).
      _imageCoordinateSystem: "lnglat",
      opacity: 0.9,
      pickable: false,
      textureParameters: {
        minFilter: "nearest",
        magFilter: "nearest",
      },
    });
    overlayRef.current.setProps({ layers: [layer] });
  }, [ready, period, field, fieldKey, rampName, isLog, diverging, bounds.min, bounds.max, grid.cols, grid.rows, globalsMask, bitmapBounds, logMin]);

  return (
    <div className="dv-map">
      <div className="dv-map-hd">
        <div className="dv-map-hd-text">
          <div className="dv-map-label">{label}</div>
          <div className="dv-map-sub">{caption}</div>
        </div>
        <MapSettings
          scale={scale}
          setScale={setScale}
          rampName={rampName}
          setRampName={setRampName}
          rampOptions={usesDivergentRamps ? DIVERGENT_RAMP_OPTIONS : RAMP_OPTIONS}
          showScale={!usesDivergentRamps}
          vmin={bounds.min}
          vmax={bounds.max}
          autoMin={autoBounds.min}
          autoMax={autoBounds.max}
          isManual={!!override}
          onSetLimits={(min, max) => setOverride({ min, max })}
          onResetLimits={() => setOverride(null)}
        />
      </div>
      <div className="dv-map-stage">
        <div ref={containerRef} className="map-canvas-host" />
        <PeriodLoadNote state={loadState} />
      </div>
      <Colorbar
        rampName={rampName}
        vmin={bounds.min}
        vmax={bounds.max}
        isLog={isLog}
        logMin={logMin}
        diverging={diverging}
        inline
        unit={unit}
      />
    </div>
  );
}
