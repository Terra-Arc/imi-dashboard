import { useEffect, useRef, useState } from "react";

type Style = "line" | "area" | "candle" | "bar";

interface Props {
  chartStyle: Style;
  setChartStyle: (s: Style) => void;
  speed: number;
  setSpeed: (s: number) => void;
  speedUnit: string;
}

const STYLE_OPTIONS: { id: Style; label: string }[] = [
  { id: "line", label: "Line" },
  { id: "area", label: "Area" },
  { id: "candle", label: "Candle" },
  { id: "bar", label: "Bars" },
];

/** Playback speeds offered (periods per second). */
export const SPEEDS = [1, 3, 6];

/** The offered speed closest to `s`, for a configured default_speed that is
 *  not one of them (the menus could not show it as selected). */
export function nearestSpeed(s: number): number {
  return SPEEDS.reduce((best, v) => (Math.abs(v - s) < Math.abs(best - s) ? v : best), SPEEDS[0]);
}

function StylePreview({ id }: { id: Style }) {
  const stroke = "var(--brand-primary)";
  const W = 56;
  const H = 18;
  if (id === "line" || id === "area") {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        {id === "area" && (
          <path d="M0,14 L8,9 L16,12 L24,5 L32,8 L40,3 L48,7 L56,4 L56,18 L0,18 Z" fill={stroke} fillOpacity="0.22" />
        )}
        <path
          d="M0,14 L8,9 L16,12 L24,5 L32,8 L40,3 L48,7 L56,4"
          fill="none"
          stroke={stroke}
          strokeWidth="1.4"
          strokeLinejoin="round"
        />
        <circle cx="0" cy="14" r="1.4" fill={stroke} />
        <circle cx="16" cy="12" r="1.4" fill={stroke} />
        <circle cx="32" cy="8" r="1.4" fill={stroke} />
        <circle cx="48" cy="7" r="1.4" fill={stroke} />
      </svg>
    );
  }
  if (id === "candle") {
    return (
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
        {[[6, 3, 15], [16, 5, 14], [26, 2, 13], [36, 6, 12], [46, 4, 10]].map(([x, y1, y2], i) => (
          <g key={i}>
            <line x1={x} x2={x} y1={y1} y2={y2} stroke="rgba(0,167,157,0.5)" strokeWidth="1.2" strokeLinecap="round" />
            <line x1={x - 3} x2={x + 3} y1={(y1 + y2) / 2} y2={(y1 + y2) / 2} stroke={stroke} strokeWidth="1.6" strokeLinecap="round" />
          </g>
        ))}
      </svg>
    );
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none">
      {[5, 9, 6, 11, 8, 13, 10, 15, 12].map((h, i) => (
        <rect key={i} x={i * 6 + 2} y={18 - h} width="4" height={h} fill={stroke} rx="0.5" />
      ))}
    </svg>
  );
}

export function ChartSettings({ chartStyle, setChartStyle, speed, setSpeed, speedUnit }: Props) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node | null;
      if (popRef.current && target && popRef.current.contains(target)) return;
      if (btnRef.current && target && btnRef.current.contains(target)) return;
      setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("mousedown", onDocClick);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDocClick);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="map-settings">
      <button
        ref={btnRef}
        className={"map-settings-trigger " + (open ? "is-open" : "")}
        onClick={() => setOpen((o) => !o)}
        aria-label="Chart options"
        aria-expanded={open}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
          <line x1="3" y1="4" x2="13" y2="4" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          <line x1="3" y1="8" x2="13" y2="8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          <line x1="3" y1="12" x2="13" y2="12" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          <circle cx="6" cy="4" r="1.8" fill="var(--white)" stroke="currentColor" strokeWidth="1.4" />
          <circle cx="10" cy="8" r="1.8" fill="var(--white)" stroke="currentColor" strokeWidth="1.4" />
          <circle cx="5" cy="12" r="1.8" fill="var(--white)" stroke="currentColor" strokeWidth="1.4" />
        </svg>
        <span>Chart options</span>
      </button>
      {open && (
        <div ref={popRef} className="map-settings-pop" role="dialog" aria-label="Chart options">
          <div className="map-settings-row">
            <div className="map-settings-lbl">Chart style</div>
            <div className="ramp-grid">
              {STYLE_OPTIONS.map((s) => (
                <button
                  key={s.id}
                  className={"ramp-swatch " + (s.id === chartStyle ? "is-active" : "")}
                  onClick={() => setChartStyle(s.id)}
                  aria-label={s.label}
                >
                  <span className="ramp-swatch-strip ramp-swatch-strip-chart">
                    <StylePreview id={s.id} />
                  </span>
                  <span className="ramp-swatch-label">{s.label}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="map-settings-sep" />
          <div className="map-settings-row">
            <div className="map-settings-lbl">Play speed</div>
            <div className="seg-ctl">
              {SPEEDS.map((s) => (
                <button
                  key={s}
                  className={"seg-btn " + (speed === s ? "is-active" : "")}
                  onClick={() => setSpeed(s)}
                >
                  {s}×
                </button>
              ))}
            </div>
            <div className="map-settings-hint">{speedUnit || "Periods advanced per second when playing."}</div>
          </div>
        </div>
      )}
    </div>
  );
}
