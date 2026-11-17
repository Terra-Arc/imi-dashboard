import { useMemo } from "react";
import { RAMPS, type RampFn } from "./ramps";
import { computeLogRange, linearRange } from "./rasterize";

interface Props {
  rampName: string;
  vmin: number;
  vmax: number;
  isLog: boolean;
  /** Log scale: manual minimum (see computeLogRange). */
  logMin?: number;
  /** Diverging fields carry meaningful negatives; the linear tick range must
   * not be clamped at 0 (mirrors the rasterizer). */
  diverging?: boolean;
  sectorLabel?: string;
  sectorColor?: string;
  /** Inline variant: renders in normal flow (not as a map overlay) and drops
   * the title row — used under the small diagnostic maps, which already carry
   * their own header. */
  inline?: boolean;
  /** Optional units string shown at the right of the strip (inline variant). */
  unit?: string;
  /** Overlay variant only: render over a translucent (vs solid) background so
   *  map values behind the legend stay visible. */
  translucentBg?: boolean;
}

const TICK_COUNT = 5;

function fmtTick(t: number): string {
  if (!Number.isFinite(t)) return "—";
  const a = Math.abs(t);
  if (a === 0) return "0";
  if (a >= 100) return Math.round(t).toString();
  if (a >= 10) return t.toFixed(0);
  if (a >= 1) return t.toFixed(1);
  if (a >= 0.1) return t.toFixed(1);
  if (a >= 0.01) return t.toFixed(2);
  return t.toExponential(1);
}

/** Log-scale ticks: the powers of ten inside [floor, ceiling], thinned to
 *  at most TICK_COUNT. When fewer than two fall inside (a range under about
 *  one decade), the two ends are labelled instead. Positions are fractions
 *  of the bar's width. */
export function logTicks(floor: number, ceiling: number): { ticks: number[]; positions: number[]; powers: boolean } {
  const lMin = Math.log10(floor);
  const lMax = Math.log10(ceiling);
  const span = lMax - lMin;
  const eps = 1e-9;
  const exps: number[] = [];
  for (let e = Math.ceil(lMin - eps); e <= Math.floor(lMax + eps); e += 1) exps.push(e);
  if (exps.length < 2 || !(span > 0)) {
    return { ticks: [floor, ceiling], positions: [0, 1], powers: false };
  }
  const stride = Math.ceil(exps.length / TICK_COUNT);
  const kept = exps.filter((_, i) => i % stride === 0);
  return {
    ticks: kept.map((e) => 10 ** e),
    positions: kept.map((e) => Math.min(1, Math.max(0, (e - lMin) / span))),
    powers: true,
  };
}

/** Label for a power of ten: "0.01", "1", "1000"; "1e-7" below 1e-6. */
function fmtPow10(t: number): string {
  return String(Number(t.toPrecision(1)));
}

function buildLinearGradient(ramp: RampFn): string {
  const stops: string[] = [];
  const N = 24;
  for (let i = 0; i < N; i += 1) {
    const t = i / (N - 1);
    const [r, g, b] = ramp(t);
    stops.push(`rgb(${r | 0},${g | 0},${b | 0}) ${(t * 100).toFixed(1)}%`);
  }
  return `linear-gradient(90deg, ${stops.join(",")})`;
}

export function Colorbar({ rampName, vmin, vmax, isLog, logMin, diverging, sectorLabel, sectorColor, inline, unit, translucentBg }: Props) {
  const ramp = RAMPS[rampName] ?? RAMPS.viridis;
  const { gradient, labels, positions } = useMemo(() => {
    const grad = buildLinearGradient(ramp);
    let lbl: string[];
    let pos: number[];
    if (isLog) {
      // Mirror the rasterizer's log mapping exactly so colorbar ticks land
      // on the same colors the map paints.
      const { floor, ceiling } = computeLogRange(vmax, logMin);
      const lt = logTicks(floor, ceiling);
      lbl = lt.ticks.map(lt.powers ? fmtPow10 : fmtTick);
      pos = lt.positions;
    } else {
      // Mirror the rasterizer's linear mapping, for the same reason.
      const { lo, hi } = linearRange(vmin, vmax, diverging);
      const tk: number[] = [];
      for (let i = 0; i < TICK_COUNT; i += 1) {
        tk.push(lo + ((hi - lo) * i) / (TICK_COUNT - 1));
      }
      pos = tk.map((t) => (t - lo) / (hi - lo));
      lbl = tk.map(fmtTick);
      // Values below 0 are painted in the lowest colour (the rasterizer
      // clamps them); say so where the data has any.
      if (!diverging && vmin < 0 && lo === 0) lbl[0] = "≤0";
    }
    return { gradient: grad, labels: lbl, positions: pos };
  }, [ramp, vmin, vmax, isLog, logMin, diverging]);

  return (
    <div className={"cbar" + (inline ? " cbar-inline" : translucentBg ? " cbar-translucent" : "")}>
      {inline ? (
        // Always render the unit line (blank when there's no unit) so the
        // strips line up across maps that do and don't carry units.
        <div className="cbar-unit">{unit || " "}</div>
      ) : (
        <div className="cbar-head">
          {sectorColor && <span className="sector-dot" style={{ background: sectorColor }} />}
          <span className="cbar-title">{sectorLabel ?? "Methane flux"}</span>
          {isLog && <span className="cbar-scale">log</span>}
        </div>
      )}
      <div className="cbar-strip" style={{ background: gradient }} />
      <div className="cbar-ticks">
        {labels.map((label, i) => (
          <span
            key={i}
            className="cbar-tick"
            style={{ left: `${(positions[i] * 100).toFixed(2)}%` }}
          >
            <span className="cbar-tick-mark" />
            <span className="cbar-tick-label">{label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
