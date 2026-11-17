import { useEffect, useMemo, useRef, useState } from "react";

import type { DataAPI } from "../data/loader";
import { periodNumber, type PeriodEntry } from "../data/types";
import { formatDate, formatMt, parseDate, yearOf } from "../util/text";

interface Props {
  api: DataAPI;
  periodIdx: number;
  setPeriodIdx: (i: number) => void;
  playing: boolean;
  setPlaying: (p: boolean) => void;
  chartStyle: "line" | "area" | "candle" | "bar";
  sector: string;
  height: number;
}

interface SeriesPoint {
  posterior: number;
  ensMin: number;
  ensMax: number;
  /** Symmetric uncertainty radius around posterior, in data units. */
  unc: number;
}

interface SeriesData {
  posterior: number[];
  ensMin: number[];
  ensMax: number[];
  /** Indices where posterior is null (skipped from plotting). */
  missing: Set<number>;
}

function extractSeries(periods: PeriodEntry[], sector: string): SeriesData {
  const posterior: number[] = [];
  const ensMin: number[] = [];
  const ensMax: number[] = [];
  const missing = new Set<number>();
  periods.forEach((p, i) => {
    const s = p.sectorScalars[sector];
    const post = s?.posterior;
    if (post == null) {
      posterior.push(NaN);
      ensMin.push(NaN);
      ensMax.push(NaN);
      missing.add(i);
      return;
    }
    posterior.push(post);
    // Fall back to symmetric ±0 band if ensemble bounds are absent.
    ensMin.push(s?.ens_min ?? post);
    ensMax.push(s?.ens_max ?? post);
  });
  return { posterior, ensMin, ensMax, missing };
}

function pointAt(i: number, series: SeriesData): SeriesPoint | null {
  if (series.missing.has(i)) return null;
  const post = series.posterior[i];
  const lo = Math.min(series.ensMin[i], post);
  const hi = Math.max(series.ensMax[i], post);
  return { posterior: post, ensMin: lo, ensMax: hi, unc: Math.max(post - lo, hi - post) };
}

function shortLabel(p: PeriodEntry): string {
  if (!parseDate(p.start)) return p.label ?? String(p.idx + 1);
  return formatDate(p.start, false);
}

export interface YScale {
  min: number;
  max: number;
  /** Tick values from min to max, `step` apart; 0 is always one of them. */
  ticks: number[];
  /** Tick labels: 0 reads "0", the others carry the step's decimals (or
   *  exponent notation, "3e-7", for steps below 1e-5). */
  labels: string[];
}

/** Y axis covering [dataMin, dataMax] and 0, with 10% headroom above the
 *  data (the tooltip sits at the top) and 5% below negative values. The tick
 *  step is 1, 2 or 5 × 10^k, chosen for about `intervals` intervals (3 to 6
 *  in practice), and the axis ends at the next multiple of the step. */
export function niceYScale(dataMin: number, dataMax: number, intervals = 4): YScale {
  let lo = Number.isFinite(dataMin) ? Math.min(0, dataMin) : 0;
  let hi = Number.isFinite(dataMax) ? Math.max(0, dataMax) : 0;
  if (!(hi > lo)) hi = lo + 1;
  const span = hi - lo;
  if (hi > 0) hi += 0.1 * span;
  if (lo < 0) lo -= 0.05 * span;
  const raw = (hi - lo) / intervals;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const norm = raw / mag;
  const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
  const kMin = Math.floor(lo / step + 1e-9);
  const kMax = Math.ceil(hi / step - 1e-9);
  const decimals = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  const ticks: number[] = [];
  const labels: string[] = [];
  for (let k = kMin; k <= kMax; k += 1) {
    ticks.push(k * step);
    const v = k * step;
    labels.push(k === 0 ? "0" : decimals > 5 ? Number(v.toPrecision(12)).toExponential() : v.toFixed(decimals));
  }
  return { min: kMin * step, max: kMax * step, ticks, labels };
}

export function TimeseriesChart({
  api,
  periodIdx,
  setPeriodIdx,
  playing: _playing,
  setPlaying,
  chartStyle,
  sector,
  height,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [w, setW] = useState(800);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(Math.max(200, e.contentRect.width));
    });
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const periods = api.manifest.periods;
  const sectorMeta = api.config.sectors.find((s) => s.id === sector) ?? api.config.sectors[0];
  const color = sectorMeta?.color ?? "var(--brand-primary)";

  const series = useMemo(() => extractSeries(periods, sector), [periods, sector]);

  const yScale = useMemo(() => {
    let lo = 0;
    let hi = 0;
    for (let i = 0; i < series.posterior.length; i += 1) {
      if (series.missing.has(i)) continue;
      lo = Math.min(lo, series.posterior[i], series.ensMin[i]);
      hi = Math.max(hi, series.posterior[i], series.ensMax[i]);
    }
    return niceYScale(lo, hi);
  }, [series]);
  const yMin = yScale.min;
  const yMax = yScale.max;

  const PAD = { t: 18, r: 18, b: 42, l: 52 };
  const innerW = Math.max(50, w - PAD.l - PAD.r);
  const innerH = Math.max(50, height - PAD.t - PAD.b);

  const N = periods.length;
  // X position follows the folder number, not the array index, so missing
  // periods open a proportional horizontal gap instead of being compacted.
  const numbers = useMemo(() => periods.map(periodNumber), [periods]);
  const n0 = numbers[0] ?? 0;
  const span = Math.max(1, (numbers[N - 1] ?? n0) - n0);
  const xAt = (i: number): number =>
    N <= 1 ? PAD.l + innerW / 2 : PAD.l + ((numbers[i] - n0) / span) * innerW;
  const yAt = (v: number): number => PAD.t + (1 - (v - yMin) / (yMax - yMin)) * innerH;
  // y of the value 0 (bars and areas start there) and of the plot's bottom
  // edge (x axis and labels), which differ when the series has negatives.
  const baselineY = yAt(0);
  const bottomY = PAD.t + innerH;

  /** Runs of consecutive present points: breaks at missing values and at
   *  folder-number gaps, so neither lines nor bands bridge a hole. */
  const runsFor = (missing: Set<number>): number[][] => {
    const runs: number[][] = [];
    let cur: number[] = [];
    for (let i = 0; i < N; i += 1) {
      if (missing.has(i)) {
        if (cur.length) runs.push(cur);
        cur = [];
        continue;
      }
      if (cur.length && numbers[i] - numbers[cur[cur.length - 1]] > 1) {
        runs.push(cur);
        cur = [];
      }
      cur.push(i);
    }
    if (cur.length) runs.push(cur);
    return runs;
  };

  const xTicks = useMemo(() => {
    if (N <= 1) return [0];
    const out: number[] = [];
    const stride = Math.max(1, Math.round(N / 6));
    for (let i = 0; i < N; i += stride) out.push(i);
    if (out[out.length - 1] !== N - 1) out.push(N - 1);
    return out;
  }, [N]);

  // Year to print beneath each x-tick: shown on the first tick and again
  // whenever the year changes, so multi-year timeseries are unambiguous
  // without repeating the year on every label.
  const xYearLabels = useMemo(() => {
    let prev: number | null = null;
    return xTicks.map((i) => {
      const y = yearOf(periods[i]?.start);
      if (y != null && y !== prev) {
        prev = y;
        return String(y);
      }
      return null;
    });
  }, [xTicks, periods]);

  const linePath = useMemo(() => {
    return runsFor(series.missing)
      .map((run) =>
        run
          .map((i, k) => `${k ? "L" : "M"}${xAt(i).toFixed(1)},${yAt(series.posterior[i]).toFixed(1)}`)
          .join(" "),
      )
      .join(" ");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [N, series, numbers, innerW, innerH, yMin, yMax]);

  const bandPath = useMemo(() => {
    if (series.missing.size === N) return "";
    // One closed ribbon per contiguous run, so the band breaks across gaps
    // just like the central line.
    const parts: string[] = [];
    for (const run of runsFor(series.missing)) {
      let top = "";
      let bot = "";
      for (const i of run) {
        const x = xAt(i);
        const hi = yAt(series.ensMax[i]);
        const lo = yAt(series.ensMin[i]);
        top += `${top ? "L" : "M"}${x.toFixed(1)},${hi.toFixed(1)} `;
        bot = `L${x.toFixed(1)},${lo.toFixed(1)} ${bot}`;
      }
      if (top) parts.push(`${top}${bot}Z`);
    }
    return parts.join(" ");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, N, numbers, innerW, innerH, yMin, yMax]);

  const areaPath = useMemo(() => {
    if (series.missing.size === N) return "";
    const parts: string[] = [];
    for (const run of runsFor(series.missing)) {
      const first = run[0];
      const last = run[run.length - 1];
      const d = run
        .map((i) => `L${xAt(i).toFixed(1)},${yAt(series.posterior[i]).toFixed(1)}`)
        .join(" ");
      parts.push(
        `M${xAt(first).toFixed(1)},${baselineY.toFixed(1)} ${d} L${xAt(last).toFixed(1)},${baselineY.toFixed(1)} Z`,
      );
    }
    return parts.join(" ");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, N, numbers, innerW, innerH, yMin, yMax, baselineY]);

  // Slot width derives from the folder-number span so bars keep a constant
  // on-screen width regardless of how many periods are missing.
  const barW = Math.max(2, (innerW / Math.max(span + 1, 1)) * 0.65);

  const sel = pointAt(periodIdx, series);
  const selX = xAt(periodIdx);
  const selY = sel ? yAt(sel.posterior) : baselineY;
  const selPct = sel && sel.posterior > 0 ? (sel.unc / sel.posterior) * 100 : 0;

  const onPointer = (e: React.PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const xRel = ((e.clientX - rect.left) / rect.width) * w - PAD.l;
    const t = Math.max(0, Math.min(1, xRel / innerW));
    // Nearest existing period by folder number — plotted x's are sparse when
    // periods are missing, so a linear-index inverse would land in the gap.
    const target = n0 + t * span;
    let idx = 0;
    for (let i = 1; i < N; i += 1) {
      if (Math.abs(numbers[i] - target) < Math.abs(numbers[idx] - target)) idx = i;
    }
    setPeriodIdx(idx);
  };

  const sectorLabel = sectorMeta?.label ?? sector;

  return (
    <div className="ts-wrap" ref={wrapRef}>
      <svg
        ref={svgRef}
        className="ts-svg"
        viewBox={`0 0 ${w} ${height}`}
        width={w}
        height={height}
        onPointerDown={(e) => {
          setDragging(true);
          setPlaying(false);
          onPointer(e);
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (dragging) onPointer(e);
        }}
        onPointerUp={() => setDragging(false)}
        onPointerCancel={() => setDragging(false)}
      >
        {/* Y grid + ticks */}
        {yScale.ticks.map((v, i) => (
          <g key={`y${i}`}>
            <line
              x1={PAD.l}
              x2={w - PAD.r}
              y1={yAt(v)}
              y2={yAt(v)}
              stroke="rgba(184,206,221,0.45)"
              strokeWidth="1"
              strokeDasharray={yScale.labels[i] === "0" ? "" : "3 4"}
            />
            <text
              x={PAD.l - 8}
              y={yAt(v) + 3.5}
              textAnchor="end"
              style={{ fontFamily: "var(--font-mono)" }}
              fontSize="10.5"
              fill="#5E7A8A"
            >
              {yScale.labels[i]}
            </text>
          </g>
        ))}

        {/* X baseline + ticks */}
        <line x1={PAD.l} x2={w - PAD.r} y1={bottomY} y2={bottomY} stroke="#B8CEDD" strokeWidth="1" />
        {xTicks.map((i, ti) => (
          <g key={`x${i}`}>
            <line
              x1={xAt(i)}
              x2={xAt(i)}
              y1={bottomY}
              y2={bottomY + 4}
              stroke="#B8CEDD"
              strokeWidth="1"
            />
            <text
              x={xAt(i)}
              y={bottomY + 17}
              textAnchor="middle"
              style={{ fontFamily: "var(--font-body)" }}
              fontSize="11"
              fill="#5E7A8A"
            >
              {shortLabel(periods[i])}
            </text>
            {xYearLabels[ti] && (
              <text
                x={xAt(i)}
                y={bottomY + 30}
                textAnchor="middle"
                style={{ fontFamily: "var(--font-mono)" }}
                fontSize="10"
                fontWeight="600"
                fill="#5E7A8A"
              >
                {xYearLabels[ti]}
              </text>
            )}
          </g>
        ))}

        {/* Y axis label */}
        <text
          x={PAD.l - 40}
          y={PAD.t + innerH / 2}
          style={{ fontFamily: "var(--font-body)" }}
          fontSize="10.5"
          fill="#5E7A8A"
          transform={`rotate(-90, ${PAD.l - 40}, ${PAD.t + innerH / 2})`}
          textAnchor="middle"
        >
          Mt CH₄/yr
        </text>

        {/* Series rendering by style */}
        {chartStyle === "bar" &&
          periods.map((_, i) => {
            const pt = pointAt(i, series);
            if (!pt) return null;
            const x = xAt(i) - barW / 2;
            const y = yAt(pt.posterior);
            const yHi = yAt(pt.ensMax);
            const yLo = yAt(pt.ensMin);
            const isSel = i === periodIdx;
            return (
              <g key={`b${i}`}>
                <rect
                  x={x}
                  y={Math.min(y, baselineY)}
                  width={barW}
                  height={Math.abs(baselineY - y)}
                  fill={color}
                  opacity={isSel ? 1 : 0.55}
                  rx="1.5"
                />
                <line
                  x1={xAt(i)}
                  x2={xAt(i)}
                  y1={yHi}
                  y2={yLo}
                  stroke={isSel ? "var(--neutral-900)" : "rgba(15,25,35,0.45)"}
                  strokeWidth="1.2"
                />
              </g>
            );
          })}

        {chartStyle === "candle" &&
          periods.map((_, i) => {
            const pt = pointAt(i, series);
            if (!pt) return null;
            const yHi = yAt(pt.ensMax);
            const yLo = yAt(pt.ensMin);
            const y = yAt(pt.posterior);
            const isSel = i === periodIdx;
            const cw = Math.max(4, barW * 0.4);
            return (
              <g key={`c${i}`}>
                <line
                  x1={xAt(i)}
                  x2={xAt(i)}
                  y1={yHi}
                  y2={yLo}
                  stroke={color}
                  strokeOpacity={0.55}
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
                <line
                  x1={xAt(i) - cw / 2}
                  x2={xAt(i) + cw / 2}
                  y1={y}
                  y2={y}
                  stroke={isSel ? "var(--neutral-900)" : color}
                  strokeWidth={isSel ? 2.5 : 1.8}
                  strokeLinecap="round"
                />
              </g>
            );
          })}

        {(chartStyle === "area" || chartStyle === "line") && (
          <>
            <path d={bandPath} fill={color} fillOpacity={0.16} />
            {chartStyle === "area" && (
              <>
                <path d={areaPath} fill="url(#ts-grad)" />
                <defs>
                  <linearGradient id="ts-grad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={color} stopOpacity="0.42" />
                    <stop offset="100%" stopColor={color} stopOpacity="0.05" />
                  </linearGradient>
                </defs>
              </>
            )}
            <path
              d={linePath}
              fill="none"
              stroke={color}
              strokeWidth="2"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {periods.map((_, i) => {
              const pt = pointAt(i, series);
              if (!pt) return null;
              return (
                <circle
                  key={`d${i}`}
                  cx={xAt(i)}
                  cy={yAt(pt.posterior)}
                  r="2"
                  fill={color}
                  stroke="#fff"
                  strokeWidth="1"
                />
              );
            })}
          </>
        )}

        {/* Playhead */}
        <line
          x1={selX}
          x2={selX}
          y1={PAD.t}
          y2={bottomY}
          stroke="var(--neutral-900)"
          strokeWidth="1.5"
          strokeDasharray="2 3"
        />
        {sel && (
          <circle cx={selX} cy={selY} r="6" fill="var(--neutral-900)" stroke="#fff" strokeWidth="2" />
        )}

        {/* Tooltip */}
        {sel && (
          <g
            transform={`translate(${Math.min(
              Math.max(selX, PAD.l + 80),
              w - PAD.r - 80,
            )} ${PAD.t})`}
          >
            <rect x="-80" y="-2" width="160" height="32" rx="6" fill="var(--neutral-900)" />
            <text
              x="0"
              y="11"
              textAnchor="middle"
              style={{ fontFamily: "var(--font-body)" }}
              fontSize="10.5"
              fill="rgba(255,255,255,0.65)"
            >
              {sectorLabel} · {shortLabel(periods[periodIdx])}
            </text>
            <text
              x="0"
              y="25"
              textAnchor="middle"
              style={{ fontFamily: "var(--font-display)" }}
              fontWeight="600"
              fontSize="13"
              fill="#fff"
            >
              {formatMt(sel.posterior)} Mt CH₄/yr{" "}
              {selPct > 0 && (
                <tspan fill="rgba(255,255,255,0.55)" fontWeight="400">
                  ±{selPct.toFixed(0)}%
                </tspan>
              )}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
