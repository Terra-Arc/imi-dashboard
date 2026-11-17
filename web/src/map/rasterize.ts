// Convert a Float32 emissions field on the manifest grid into an RGBA canvas
// suitable as input to deck.gl's BitmapLayer.
//
// The data array is row-major with rows ordered south → north (lat ascending),
// matching xarray's coordinate order. BitmapLayer expects images in pixel
// space with row 0 at the top of the world (north), so this function flips
// rows while writing.

import type { RampFn } from "./ramps";

export interface RasterizeOptions {
  field: Float32Array;
  mask: Uint8Array | null;
  cols: number;
  rows: number;
  vmin: number;
  vmax: number;
  ramp: RampFn;
  isLog: boolean;
  /** Log scale only: the value at the low end of the colour range, used
   *  when it is above 0 and below vmax (a manual minimum). Otherwise the
   *  range spans three decades below vmax. */
  logMin?: number;
  /** Diverging fields (e.g. posterior−prior) carry meaningful negatives and
   * pass a symmetric [-m, m] range; for them the vmin→0 clamp below must be
   * skipped or zero no longer lands at the ramp's center. */
  diverging?: boolean;
  /** Alpha for cells inside the mask. Cells outside (mask=0) are transparent. */
  alpha?: number;
}

/** Shared (rasterizer + colorbar) log mapping: from `logMin` when it is
 *  in (0, vmax), else 3 decades below vmax, up to vmax. Values below the
 *  floor, including zero and negative values, get the floor's colour.
 *
 * Used to be ad-hoc in both places, which made colorbar tick positions
 * disagree with the actual pixel colors. The mapping is now one function
 * so the two views stay in lockstep. */
export interface LogRange {
  floor: number;   // value at colorbar 0
  ceiling: number; // value at colorbar 1
  lMin: number;    // = log10(floor)
  lMax: number;    // = log10(ceiling)
}

export function computeLogRange(vmax: number, logMin?: number, decades = 3): LogRange {
  const safeMax = Math.max(vmax, 1e-12);
  const ceiling = safeMax;
  const floor =
    logMin != null && logMin > 0 && logMin < safeMax ? logMin : safeMax / Math.pow(10, decades);
  return {
    floor,
    ceiling,
    lMin: Math.log10(floor),
    lMax: Math.log10(ceiling),
  };
}

/** Shared (rasterizer + colorbar) linear mapping: the values at the two
 *  ends of the colour range. Emission fluxes are clamped at 0 from below
 *  (their negatives are numerical artifacts of the inversion, painted in
 *  the lowest colour). Not clamped: diverging fields, which pass a
 *  symmetric [-m, m] range so 0 stays at the ramp's centre, and a field
 *  whose maximum is not above 0 (a sink such as the soil absorption, all
 *  of whose values are negative), whose range the clamp would collapse to
 *  nothing. */
export function linearRange(vmin: number, vmax: number, diverging = false): { lo: number; hi: number } {
  const lo = diverging || vmax <= 0 ? vmin : Math.max(0, vmin);
  return { lo, hi: Math.max(vmax, lo + 1e-12) };
}

/** Smallest power of two ≥ n. */
function nextPow2(n: number): number {
  let p = 1;
  while (p < n) p *= 2;
  return p;
}

export function rasterizeFieldToCanvas(opts: RasterizeOptions): HTMLCanvasElement {
  const { field, mask, cols, rows, vmin, vmax, ramp, isLog, logMin, diverging = false, alpha = 235 } = opts;

  const log = computeLogRange(vmax, logMin);
  const { lo: linMin, hi: linMax } = linearRange(vmin, vmax, diverging);
  const linRange = linMax - linMin;

  // One texel per grid cell, north-up. Built at native grid size first.
  const cellImg = new ImageData(cols, rows);
  for (let r = 0; r < rows; r += 1) {
    // Source row in data space (lat ascending) → destination row in image
    // space (north-up). Image y=0 is north (max lat), so flip vertically.
    const srcRow = rows - 1 - r;
    for (let c = 0; c < cols; c += 1) {
      const srcIdx = srcRow * cols + c;
      const dstIdx = (r * cols + c) * 4;
      const inside = !mask || mask[srcIdx];
      const v = field[srcIdx];
      if (!inside || !Number.isFinite(v)) {
        cellImg.data[dstIdx + 3] = 0;
        continue;
      }
      let t: number;
      if (isLog) {
        const safe = Math.max(log.floor, v);
        t = (Math.log10(safe) - log.lMin) / (log.lMax - log.lMin);
      } else {
        t = (v - linMin) / linRange;
      }
      const [rC, gC, bC] = ramp(t);
      cellImg.data[dstIdx] = rC | 0;
      cellImg.data[dstIdx + 1] = gC | 0;
      cellImg.data[dstIdx + 2] = bC | 0;
      cellImg.data[dstIdx + 3] = alpha;
    }
  }

  // deck.gl's BitmapLayer always allocates a full mipmap chain and calls
  // generateMipmap on the texture it builds from this canvas. For a small
  // *non-power-of-two* texture (e.g. an ROI crop of 38×38) Firefox's WebGL
  // mishandles that NPOT mipmap path and samples the base level at a drifting
  // row stride — the data field renders sheared/repeated on a diagonal even
  // though the pixels are correct (Chromium/Edge tolerate the same texture).
  // Emitting a power-of-two canvas sidesteps it: NPOT mipmaps are never built.
  // The grid stays crisp because both this upscale and the layer sample with
  // nearest filtering. The square POT texture maps [0,1]² onto the (possibly
  // rectangular) bitmap bounds exactly as the native-size canvas did, so the
  // geographic placement is unchanged.
  const tex = Math.min(2048, Math.max(256, nextPow2(Math.max(cols, rows) * 8)));
  const canvas = document.createElement("canvas");
  canvas.width = tex;
  canvas.height = tex;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  // Stage the native-size cells on a scratch canvas, then nearest-upscale.
  const src = document.createElement("canvas");
  src.width = cols;
  src.height = rows;
  src.getContext("2d")?.putImageData(cellImg, 0, 0);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(src, 0, 0, cols, rows, 0, 0, tex, tex);
  return canvas;
}
