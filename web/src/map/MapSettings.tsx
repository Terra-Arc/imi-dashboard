import { useEffect, useRef, useState } from "react";
import { RAMP_OPTIONS, rampGradient, type RampOption } from "./ramps";

interface Props {
  scale: "linear" | "log";
  setScale: (s: "linear" | "log") => void;
  rampName: string;
  setRampName: (r: string) => void;
  /** Colormap choices to offer; defaults to the sequential overview set.
   *  Diverging diagnostic maps pass the divergent set instead. */
  rampOptions?: RampOption[];
  /** Hide the linear/log row (log is meaningless for diverging fields). */
  showScale?: boolean;
  /** Effective colorbar min/max currently in use (override or data-derived). */
  vmin: number;
  vmax: number;
  /** Data-derived bounds — shown as the placeholder / restored on reset. */
  autoMin: number;
  autoMax: number;
  /** True when a manual override is active for the current sector. */
  isManual: boolean;
  onSetLimits: (min: number, max: number) => void;
  onResetLimits: () => void;
}

/** Compact value formatting for the limit inputs — avoids dumping a float
 *  with 15 significant digits into the field. */
function fmtLimit(v: number): string {
  if (!Number.isFinite(v)) return "";
  const a = Math.abs(v);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return v.toExponential(2);
  // Trim trailing zeros from a fixed representation.
  return parseFloat(v.toPrecision(4)).toString();
}

export function MapSettings({
  scale,
  setScale,
  rampName,
  setRampName,
  rampOptions = RAMP_OPTIONS,
  showScale = true,
  vmin,
  vmax,
  autoMin,
  autoMax,
  isManual,
  onSetLimits,
  onResetLimits,
}: Props) {
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const popRef = useRef<HTMLDivElement | null>(null);

  // Local, editable copies of the limit fields. Re-sync whenever the
  // effective limits change (sector switch, reset, or a committed edit).
  const [minStr, setMinStr] = useState(() => fmtLimit(vmin));
  const [maxStr, setMaxStr] = useState(() => fmtLimit(vmax));
  useEffect(() => {
    setMinStr(fmtLimit(vmin));
    setMaxStr(fmtLimit(vmax));
  }, [vmin, vmax]);

  function commitLimits() {
    const mn = parseFloat(minStr);
    const mx = parseFloat(maxStr);
    if (!Number.isFinite(mn) || !Number.isFinite(mx) || mx <= mn) {
      // Invalid entry — snap the fields back to the effective values.
      setMinStr(fmtLimit(vmin));
      setMaxStr(fmtLimit(vmax));
      return;
    }
    onSetLimits(mn, mx);
  }

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      const target = e.target as Node;
      if (popRef.current?.contains(target)) return;
      if (btnRef.current?.contains(target)) return;
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
        aria-label="Map settings"
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
        <span>Map options</span>
      </button>
      {open && (
        <div ref={popRef} className="map-settings-pop" role="dialog" aria-label="Map options">
          {showScale && (
          <div className="map-settings-row">
            <div className="map-settings-lbl">Color scale</div>
            <div className="seg-ctl">
              <button
                className={"seg-btn " + (scale === "linear" ? "is-active" : "")}
                onClick={() => setScale("linear")}
              >
                Linear
              </button>
              <button
                className={"seg-btn " + (scale === "log" ? "is-active" : "")}
                onClick={() => setScale("log")}
              >
                Log
              </button>
            </div>
            <div className="map-settings-hint">
              {scale === "log"
                ? "Compresses high values to surface low-intensity emissions. Spans three decades below Max unless Min is above 0; lower values, including 0 and negatives, take the lowest colour."
                : "Equal value steps. Best for highlighting the strongest sources."}
            </div>
          </div>
          )}
          {showScale && <div className="map-settings-sep" />}
          <div className="map-settings-row">
            <div className="map-settings-lbl-row">
              <div className="map-settings-lbl">Color limits</div>
              <button
                className="map-settings-reset"
                onClick={onResetLimits}
                disabled={!isManual}
                title="Reset to data-derived limits"
              >
                Reset
              </button>
            </div>
            <div className="limit-ctl">
              <label className="limit-field">
                <span>Min</span>
                <input
                  type="number"
                  inputMode="decimal"
                  value={minStr}
                  onChange={(e) => setMinStr(e.target.value)}
                  onBlur={commitLimits}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  }}
                />
              </label>
              <label className="limit-field">
                <span>Max</span>
                <input
                  type="number"
                  inputMode="decimal"
                  value={maxStr}
                  onChange={(e) => setMaxStr(e.target.value)}
                  onBlur={commitLimits}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  }}
                />
              </label>
            </div>
            <div className="map-settings-hint">
              {isManual
                ? "Manual limits — fixed across every period for this variable."
                : `Auto from data (${fmtLimit(autoMin)} – ${fmtLimit(autoMax)}). Edit to fix the scale.`}
            </div>
          </div>
          <div className="map-settings-sep" />
          <div className="map-settings-row">
            <div className="map-settings-lbl">Colormap</div>
            <div className="ramp-grid">
              {rampOptions.map((r) => (
                <button
                  key={r.id}
                  className={"ramp-swatch " + (r.id === rampName ? "is-active" : "")}
                  onClick={() => setRampName(r.id)}
                  aria-label={r.label}
                >
                  <span className="ramp-swatch-strip" style={{ background: rampGradient(r.id) }} />
                  <span className="ramp-swatch-label">{r.label}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
