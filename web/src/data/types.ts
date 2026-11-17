// Data shapes shared between the converter (writes manifest.json) and the
// frontend (reads it). Field names mirror imi_dashboard/pack.py exactly — any
// drift between the two halves shows up as a TypeScript error at the read
// site.

export type Cadence = "weekly" | "monthly" | "biweekly" | "daily" | "annual" | "custom";

export interface SectorMeta {
  id: string;
  label: string;
  short: string;
  color: string;
  primary: boolean;
}

export interface ResolvedText {
  cadence: Cadence;
  cadence_singular: string;
  cadence_plural: string;
  heading_context: string;
  overview_heading: string;
  diagnostics_heading: string;
  heading_context_timeseries: string;
  heading_context_single: string;
  heading_context_diagnostics: string;
  subtitle_timeseries: string;
  subtitle_single: string;
  subtitle_diagnostics_timeseries: string;
  subtitle_diagnostics_single: string;
  chart_title_split: string;
  chart_subtitle_split: string;
  chart_title_chart_first: string;
  chart_subtitle_chart_first: string;
  speed_unit: string;
  diagnostics_spatial_label: string;
  diagnostics_scalar_label: string;
  footer_attribution: string;
  footer_disclaimer: string;
  footer_version: string;
}

export interface SiteConfig {
  /** Null when the config names no area; titles then omit it. */
  areaName: string | null;
  displayName: string;
  headerBrand: "terra" | "custom";
  logoUrl: string | null;
  brandName: string | null;
  brandTagline: string | null;
  sectors: SectorMeta[];
  map: {
    ramp: "viridis" | "magma" | "plasma" | "reds";
    scale: "linear" | "log";
    /** Percentile used by the converter to pick the colorbar max (documented;
     *  bounds are already baked into manifest.sectorBounds). */
    maxPercentile?: number;
    /** Draw the on-map colorbar over a translucent background (default true). */
    colorbarTransparentBg?: boolean;
    /** Base-map tile address template; null for no base map; absent in a
     *  config.json written before the key existed (then OpenStreetMap). */
    tiles?: string | null;
    tilesAttribution?: string | null;
    tilesMaxZoom?: number;
    tileSize?: number;
    /** MapLibre's compact attribution control (default true). */
    attributionCompact?: boolean;
  };
  chart: { defaultStyle: "line" | "area" | "candle" | "bar"; defaultSpeed: number };
  text: ResolvedText;
}

export interface GridMeta {
  cols: number;
  rows: number;
  lat: number[];
  lon: number[];
  dlat: number;
  dlon: number;
  bbox: [number, number, number, number]; // [west, south, east, north]
}

export type FieldKind = "sector" | "diagnostic";

export interface FieldDescriptor {
  name: string;       // sector id ("total", "oil", …) or diagnostic key ("kernel", "scale", "delta_emissions", "delta_xch4", "obs_count")
  kind: FieldKind;
  offset: number;     // element offset into the Float32 blob
  length: number;     // element count = grid.cols * grid.rows
}

export interface PeriodScalars {
  prior: number | null;
  posterior: number | null;
  ens_min: number | null;
  ens_max: number | null;
}

export interface PeriodDiagScalars {
  dofs: number | null;
  n_obs: number | null;
  bias_prior: number | null;
  bias_posterior: number | null;
  rmse_prior: number | null;
  rmse_posterior: number | null;
  avg_kernel: number | null;
  prior_mt: number | null;
  posterior_mt: number | null;
}

export interface PeriodEntry {
  idx: number;
  /** Folder number parsed from period_<N>_plots — non-contiguous when periods
   *  are missing on disk. Absent in pre-gap-aware manifests (see
   *  {@link periodNumber}). */
  number?: number;
  label: string;
  start: string | null;   // ISO YYYY-MM-DD
  end: string | null;
  file: string;           // path relative to data/, e.g. "periods/0000.bin"
  sectorScalars: Record<string, PeriodScalars>;
  diagScalars: PeriodDiagScalars;
}

/** What built a dashboard (manifest.provenance), written by every
 *  conversion. Absent in dashboards from converters that predate it. */
export interface Provenance {
  converter: { name: string; version: string };
  /** UTC time of the conversion, ISO 8601 to the second: "2026-11-04T10:15:30Z". */
  convertedAt: string;
  /** Name (not path) of the IMI run folder converted. */
  input: string;
}

/** Whether the total sector's numbers (the statistics CSV's totals and
 *  ensemble range) and its map include the soil sink (manifest.totalDefinition).
 *  null: not known. The two can differ. */
export interface TotalDefinition {
  numbersIncludeSoilSink: boolean | null;
  mapIncludesSoilSink: boolean | null;
}

export interface Manifest {
  /** Version of the manifest format, not of the converter. */
  version: number;
  provenance?: Provenance;
  mode: "single" | "timeseries";
  /** Spatial extent the converter wrote: "domain" (full grid) or "roi"
   *  (cropped to the mask bbox). Used by the converter for incremental
   *  rebuilds; the frontend doesn't depend on it. */
  region?: "domain" | "roi";
  grid: GridMeta;
  globals: {
    file: string;
    dtype: "uint8";
    shape: [number, number];
    /** Tightest [west, south, east, north] enclosing the active mask cells.
     *  Null when the mask is empty (e.g. a fallback build). */
    dataBbox: [number, number, number, number] | null;
  };
  fields: FieldDescriptor[];
  sectorBounds: Record<string, { min: number; max: number }>;
  /** Global min/max per diagnostic field across all periods, so diagnostic
   *  map colorscales stay fixed over the timeseries. Fields with no data are
   *  omitted; absent in older manifests (frontend falls back to per-period
   *  autoscale). */
  diagBounds?: Record<string, { min: number; max: number }>;
  /** Absent without a total sector, and in dashboards from converters that
   *  predate it (no label is shown then). */
  totalDefinition?: TotalDefinition;
  periods: PeriodEntry[];
}

/** A period's folder number: explicit `number` when the manifest carries it,
 *  else parsed from the label ("period_205" → 205), else the array index. */
export function periodNumber(p: PeriodEntry): number {
  if (p.number != null) return p.number;
  const m = /(\d+)\s*$/.exec(p.label ?? "");
  if (m) return parseInt(m[1], 10);
  return p.idx;
}

/** Decoded per-period data — the value the data loader hands to render code. */
export interface PeriodData {
  entry: PeriodEntry;
  /** Map of field name → Float32Array of length rows*cols. NaN where the
   * source was missing. */
  fields: Record<string, Float32Array>;
}
