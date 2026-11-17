import type { MapOptions, StyleSpecification } from "maplibre-gl";
import type { SiteConfig } from "../data/types";

/** The base map under the data layer, from config.json's `map` (the
 *  converter's `map.tiles*` and `map.attribution_compact` keys). */
export interface Basemap {
  /** Raster tile address template, or null for no base map. */
  tiles: string | null;
  attribution: string | null;
  maxZoom: number;
  tileSize: number;
  attributionCompact: boolean;
}

const OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTRIBUTION = "© OpenStreetMap contributors";
const BACKGROUND = "#F4F7FA";

/** The configured base map. A config.json without the keys (written before
 *  they existed) gets the converter's defaults: OpenStreetMap, compact. */
export function basemapFromConfig(map: SiteConfig["map"]): Basemap {
  const tiles = map.tiles === undefined ? OSM_TILES : map.tiles;
  return {
    tiles,
    attribution: tiles === null ? null : (map.tilesAttribution ?? OSM_ATTRIBUTION),
    maxZoom: map.tilesMaxZoom ?? 19,
    tileSize: map.tileSize ?? 256,
    attributionCompact: map.attributionCompact ?? true,
  };
}

/** MapLibre style: the plain background, then the tiles (if any) faded and
 *  desaturated so the data layer stands out. The overview and the diagnostic
 *  maps use different amounts of both. */
export function basemapStyle(
  b: Basemap,
  look: { opacity: number; saturation: number },
): StyleSpecification {
  const background = { id: "bg", type: "background" as const, paint: { "background-color": BACKGROUND } };
  if (b.tiles === null) return { version: 8, sources: {}, layers: [background] };
  return {
    version: 8,
    sources: {
      basemap: {
        type: "raster",
        tiles: [b.tiles],
        tileSize: b.tileSize,
        attribution: b.attribution ?? undefined,
        maxzoom: b.maxZoom,
      },
    },
    layers: [
      background,
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        paint: { "raster-opacity": look.opacity, "raster-saturation": look.saturation },
      },
    ],
  };
}

/** The attribution control: none without a base map (nothing to credit),
 *  otherwise compact or full as configured. Tile providers' terms, the OSM
 *  tile usage policy among them, require the attribution on every map. */
export function attributionControl(b: Basemap): MapOptions["attributionControl"] {
  if (b.tiles === null) return false;
  return { compact: b.attributionCompact };
}
