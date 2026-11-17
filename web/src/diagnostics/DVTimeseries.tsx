import { useEffect, useMemo, useRef, useState } from "react";

import { periodNumber, type PeriodEntry } from "../data/types";
import { parseDate, formatDate } from "../util/text";

type DiagKey =
  | "dofs"
  | "n_obs"
  | "bias_prior"
  | "bias_posterior"
  | "rmse_prior"
  | "rmse_posterior"
  | "avg_kernel";

interface Props {
  label: string;
  hint: string;
  dataKey: DiagKey;
  /** Optional companion series (e.g. the prior counterpart of a posterior
   *  statistic) drawn dashed/muted on the same axis. */
  secondaryKey?: DiagKey;
  secondaryLabel?: string;
  /** When set, plot a single derived series combining `dataKey` and
   *  `secondaryKey` instead of two lines:
   *   - "diff"          → dataKey − secondaryKey
   *   - "magnitudeDiff" → |dataKey| − |secondaryKey|  (for signed quantities
   *                       like bias, where what matters is the magnitude)
   *  A zero reference line is drawn so it reads at a glance whether the
   *  statistic shrank (negative = improved) or grew. */
  derive?: "diff" | "magnitudeDiff";
  periods: PeriodEntry[];
  periodIdx: number;
  setPeriodIdx: (i: number) => void;
  /** Format the value as a rounded integer (e.g. for n_obs). */
  integer?: boolean;
  /** Strictly-positive quantities (DOFS, N obs, ⟨A⟩): never extend the y
   *  axis below zero. */
  nonNegative?: boolean;
  /** Unit shown after the current value, e.g. "ppb". */
  unit?: string;
}

function shortLabel(p: PeriodEntry): string {
  if (!parseDate(p.start)) return p.label ?? String(p.idx + 1);
  return formatDate(p.start, false);
}

function fmt(v: number | null | undefined, integer?: boolean): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (integer) return Math.round(v).toLocaleString();
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(3);
}

function extractVals(periods: PeriodEntry[], key: DiagKey): number[] {
  return periods.map((p) => {
    const v = (p.diagScalars as unknown as Record<string, number | null>)[key];
    return v == null ? NaN : v;
  });
}

export function DVTimeseries({
  label,
  hint,
  dataKey,
  secondaryKey,
  secondaryLabel,
  derive,
  periods,
  periodIdx,
  setPeriodIdx,
  integer,
  nonNegative,
  unit,
}: Props) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [w, setW] = useState(380);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) setW(Math.max(160, e.contentRect.width));
    });
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const rawVals = useMemo(() => extractVals(periods, dataKey), [periods, dataKey]);
  const rawVals2 = useMemo(
    () => (secondaryKey ? extractVals(periods, secondaryKey) : null),
    [periods, secondaryKey],
  );

  // In `derive` mode the two source series collapse into one plotted line
  // (their difference); otherwise the primary plots solid and the secondary,
  // when present, plots dashed alongside it.
  const vals = useMemo(() => {
    if (!derive || !rawVals2) return rawVals;
    return rawVals.map((v, i) => {
      const b = rawVals2[i];
      if (!Number.isFinite(v) || !Number.isFinite(b)) return NaN;
      return derive === "magnitudeDiff" ? Math.abs(v) - Math.abs(b) : v - b;
    });
  }, [rawVals, rawVals2, derive]);
  const vals2 = derive ? null : rawVals2;

  const stats = useMemo(() => {
    const finite = [...vals, ...(vals2 ?? [])].filter((v) => Number.isFinite(v));
    if (finite.length === 0) return { yMin: 0, yMax: 1 };
    // Keep the zero baseline in frame for difference plots so the sign reads.
    if (derive) finite.push(0);
    const vMin = Math.min(...finite);
    const vMax = Math.max(...finite);
    const range = vMax - vMin || Math.abs(vMax) || 1;
    let yMin = vMin - range * 0.15;
    if (nonNegative) yMin = Math.max(0, yMin);
    return { yMin, yMax: vMax + range * 0.15 };
  }, [vals, vals2, nonNegative, derive]);

  const h = 120;
  const PAD = { t: 8, r: 14, b: 22, l: 44 };
  const innerW = Math.max(20, w - PAD.l - PAD.r);
  const innerH = Math.max(20, h - PAD.t - PAD.b);

  const N = periods.length;
  // X position follows the folder number, so missing periods leave a
  // proportional horizontal gap rather than being silently compacted.
  const numbers = useMemo(() => periods.map(periodNumber), [periods]);
  const n0 = numbers[0] ?? 0;
  const span = Math.max(1, (numbers[N - 1] ?? n0) - n0);
  const xAt = (i: number) =>
    N <= 1 ? PAD.l + innerW / 2 : PAD.l + ((numbers[i] - n0) / span) * innerW;
  const yAt = (v: number) => PAD.t + (1 - (v - stats.yMin) / (stats.yMax - stats.yMin)) * innerH;

  const pathFor = (series: number[]): string => {
    let d = "";
    let pen = false;
    series.forEach((v, i) => {
      if (!Number.isFinite(v)) {
        pen = false;
        return;
      }
      // Drop the pen across folder-number gaps so the line doesn't bridge
      // missing periods.
      if (i > 0 && numbers[i] - numbers[i - 1] > 1) pen = false;
      d += `${pen ? "L" : "M"}${xAt(i).toFixed(1)},${yAt(v).toFixed(1)} `;
      pen = true;
    });
    return d;
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const linePath = useMemo(() => pathFor(vals), [vals, numbers, innerW, innerH, stats]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const linePath2 = useMemo(() => (vals2 ? pathFor(vals2) : ""), [vals2, numbers, innerW, innerH, stats]);

  const onPointer = (e: React.PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const xRel = ((e.clientX - rect.left) / rect.width) * w - PAD.l;
    const t = Math.max(0, Math.min(1, xRel / innerW));
    // Nearest existing period by folder number (plotted x's are sparse when
    // periods are missing).
    const target = n0 + t * span;
    let best = 0;
    for (let i = 1; i < N; i += 1) {
      if (Math.abs(numbers[i] - target) < Math.abs(numbers[best] - target)) best = i;
    }
    setPeriodIdx(best);
  };

  const curr = vals[periodIdx];
  const curr2 = vals2 ? vals2[periodIdx] : null;
  const yTicks = [stats.yMin, (stats.yMin + stats.yMax) / 2, stats.yMax];

  return (
    <div className="dv-card" ref={wrapRef}>
      <div className="dv-card-hd">
        <div className="dv-card-text">
          <div className="dv-card-label">{label}</div>
          <div className="dv-card-hint">{hint}</div>
        </div>
        <div className="dv-card-vals">
          <div className="dv-card-val mono">
            {fmt(curr, integer)}
            {unit && Number.isFinite(curr) && <span className="dv-card-unit"> {unit}</span>}
          </div>
          {secondaryKey && (
            <div className="dv-card-val-sub mono">
              {secondaryLabel ?? "prior"} {fmt(curr2, integer)}
            </div>
          )}
        </div>
      </div>
      <svg
        ref={svgRef}
        className="dv-card-chart"
        viewBox={`0 0 ${w} ${h}`}
        width="100%"
        height={h}
        onPointerDown={(e) => {
          setDragging(true);
          onPointer(e);
          e.currentTarget.setPointerCapture(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (dragging) onPointer(e);
        }}
        onPointerUp={() => setDragging(false)}
        onPointerCancel={() => setDragging(false)}
      >
        {yTicks.map((v, i) => (
          <g key={`yg${i}`}>
            <line
              x1={PAD.l}
              x2={w - PAD.r}
              y1={yAt(v)}
              y2={yAt(v)}
              stroke="rgba(184,206,221,0.45)"
              strokeDasharray={i === 1 ? "" : "2 3"}
            />
            <text
              x={PAD.l - 6}
              y={yAt(v) + 3.5}
              textAnchor="end"
              style={{ fontFamily: "var(--font-mono)" }}
              fontSize="10"
              fill="#5E7A8A"
            >
              {fmt(v, integer)}
            </text>
          </g>
        ))}
        {derive && stats.yMin < 0 && stats.yMax > 0 && (
          <line
            x1={PAD.l}
            x2={w - PAD.r}
            y1={yAt(0)}
            y2={yAt(0)}
            stroke="rgba(94,122,138,0.7)"
            strokeWidth="1"
          />
        )}
        {linePath2 && (
          <path
            d={linePath2}
            fill="none"
            stroke="#8AA3B3"
            strokeWidth="1.4"
            strokeDasharray="4 3"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        )}
        <path
          d={linePath}
          fill="none"
          stroke="var(--neutral-700)"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {vals.map((v, i) =>
          Number.isFinite(v) ? (
            <circle
              key={`d${i}`}
              cx={xAt(i)}
              cy={yAt(v)}
              r="1.6"
              fill="var(--neutral-700)"
              opacity={i === periodIdx ? 0 : 0.7}
            />
          ) : null,
        )}
        <line
          x1={xAt(periodIdx)}
          x2={xAt(periodIdx)}
          y1={PAD.t}
          y2={PAD.t + innerH}
          stroke="var(--neutral-900)"
          strokeWidth="1.4"
          strokeDasharray="2 3"
        />
        {vals2 && Number.isFinite(curr2 ?? NaN) && (
          <circle
            cx={xAt(periodIdx)}
            cy={yAt(curr2 as number)}
            r="3.4"
            fill="#8AA3B3"
            stroke="#fff"
            strokeWidth="1.4"
          />
        )}
        {Number.isFinite(curr) && (
          <circle
            cx={xAt(periodIdx)}
            cy={yAt(curr)}
            r="4.5"
            fill="var(--neutral-900)"
            stroke="#fff"
            strokeWidth="1.6"
          />
        )}
        <text
          x={PAD.l}
          y={h - 4}
          textAnchor="start"
          style={{ fontFamily: "var(--font-body)" }}
          fontSize="10"
          fill="#5E7A8A"
        >
          {shortLabel(periods[0])}
        </text>
        <text
          x={w - PAD.r}
          y={h - 4}
          textAnchor="end"
          style={{ fontFamily: "var(--font-body)" }}
          fontSize="10"
          fill="#5E7A8A"
        >
          {shortLabel(periods[N - 1])}
        </text>
      </svg>
    </div>
  );
}
