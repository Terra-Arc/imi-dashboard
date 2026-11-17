import type { DataAPI } from "../data/loader";
import { periodNumber, type PeriodData, type PeriodEntry } from "../data/types";
import { applyTemplate, formatDate, formatEndDate } from "../util/text";
import { SPEEDS } from "../components/ChartSettings";
import { DVMap } from "./DVMap";
import { basemapFromConfig } from "../map/basemap";
import { DVTimeseries } from "./DVTimeseries";

interface Props {
  api: DataAPI;
  period: PeriodData | undefined;
  periodIdx: number;
  setPeriodIdx: (i: number) => void;
  playing: boolean;
  setPlaying: (p: boolean) => void;
  speed: number;
  setSpeed: (s: number) => void;
  rampName: string;
  isSinglePeriod: boolean;
  templateVars: Record<string, string>;
  /** Why the period's maps are absent, if they are (see MapPanel). */
  loadState: "loading" | "failed" | null;
}

interface SnapshotProps {
  label: string;
  hint: string;
  value: string;
  unit?: string;
}

function fmtVal(v: number | null | undefined, integer?: boolean, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (integer) return Math.round(v).toLocaleString();
  const a = Math.abs(v);
  if (a >= 100) return v.toFixed(0);
  if (a >= 10) return v.toFixed(1);
  if (a >= 1) return v.toFixed(digits);
  return v.toFixed(digits + 1);
}

/** Posterior − prior. Null unless both operands are finite. */
function diff(post: number | null | undefined, prior: number | null | undefined): number | null {
  if (post == null || prior == null || !Number.isFinite(post) || !Number.isFinite(prior)) return null;
  return post - prior;
}

/** |Posterior| − |prior| — the magnitude change for signed quantities (bias). */
function absDiff(post: number | null | undefined, prior: number | null | undefined): number | null {
  const d = diff(post, prior);
  if (d == null) return null;
  return Math.abs(post as number) - Math.abs(prior as number);
}

function DVSnapshot({ label, hint, value, unit }: SnapshotProps) {
  return (
    <div className="dv-card dv-snapshot">
      <div className="dv-card-hd">
        <div className="dv-card-text">
          <div className="dv-card-label">{label}</div>
          <div className="dv-card-hint">{hint}</div>
        </div>
      </div>
      <div className="dv-snapshot-val mono">
        {value}
        {unit && value !== "—" && <span className="dv-card-unit"> {unit}</span>}
      </div>
    </div>
  );
}

interface HeaderProps {
  periodIdx: number;
  setPeriodIdx: (i: number) => void;
  playing: boolean;
  setPlaying: (p: boolean) => void;
  speed: number;
  setSpeed: (s: number) => void;
  isSinglePeriod: boolean;
  periods: PeriodEntry[];
}

function DVHeader({ periodIdx, setPeriodIdx, playing, setPlaying, speed, setSpeed, isSinglePeriod, periods }: HeaderProps) {
  const p = periods[periodIdx];
  const atStart = periodIdx <= 0;
  const atEnd = periodIdx >= periods.length - 1;
  return (
    <div className="dv-hd panel">
      {!isSinglePeriod && (
        <div className="dv-hd-buttons">
          <button
            className="ts-play"
            onClick={() => setPlaying(!playing)}
            aria-label={playing ? "Pause" : "Play"}
          >
            {playing ? (
              <svg width="14" height="14" viewBox="0 0 14 14">
                <rect x="3" y="2" width="3" height="10" rx="0.5" fill="currentColor" />
                <rect x="8" y="2" width="3" height="10" rx="0.5" fill="currentColor" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 14 14">
                <path d="M3 2 L12 7 L3 12 Z" fill="currentColor" />
              </svg>
            )}
          </button>
          <button
            className="ts-step"
            onClick={() => {
              setPlaying(false);
              setPeriodIdx(Math.max(0, periodIdx - 1));
            }}
            disabled={atStart}
            aria-label="Previous period"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M8 2 L4 6 L8 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button
            className="ts-step"
            onClick={() => {
              setPlaying(false);
              setPeriodIdx(Math.min(periods.length - 1, periodIdx + 1));
            }}
            disabled={atEnd}
            aria-label="Next period"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M4 2 L8 6 L4 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <div className="ts-speed">
            <span className="ts-speed-lbl">Speed</span>
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
          </div>
        </div>
      )}
      <div className="dv-hd-date">
        {formatDate(p?.start)}
        <span className="period-nav-arr">→</span>
        {formatEndDate(p?.end, false)}
      </div>
      {!isSinglePeriod && (
        <div className="dv-hd-meta">
          {/* Folder numbering — jumps across gaps when periods are missing. */}
          <b>{p ? periodNumber(p) : periodIdx + 1}</b>
          <span className="period-nav-of">
            {" "}of {periods.length ? periodNumber(periods[periods.length - 1]) : periods.length}
          </span>
        </div>
      )}
    </div>
  );
}

const SPATIAL_PANELS: {
  key: string;
  label: string;
  caption: string;
  ramp: string;
  diverging?: boolean;
  vmin?: number;
  vmax?: number;
  unit?: string;
}[] = [
  // First row: observation-driven panels (kernel, sampling density, raw TROPOMI).
  {
    key: "kernel",
    label: "Averaging kernel diagonal",
    caption: "Where the inversion is observationally constrained",
    ramp: "viridis",
  },
  {
    key: "obs_count",
    label: "TROPOMI observation count",
    caption: "Sampling density per cell over the period",
    ramp: "magma",
    unit: "obs/cell",
  },
  {
    key: "tropomi_xch4",
    label: "Mean TROPOMI XCH4",
    caption: "Period-averaged satellite column methane",
    ramp: "viridis",
    unit: "ppb",
  },
  // Second row: inversion-adjustment panels.
  {
    key: "scale",
    label: "Posterior scaling factor",
    caption: "Multiplicative correction to the prior emissions field",
    // Diverging around 1 (no change): 0 = blue, 1 = white, 2 = red.
    ramp: "divergent",
    vmin: 0,
    vmax: 2,
    unit: "× prior",
  },
  {
    key: "delta_emissions",
    label: "Posterior − prior emissions",
    caption: "Where the inversion adjusts the prior",
    // Diverging field (negatives = the inversion lowered the prior): standard
    // blue→white→red ramp centred on zero.
    ramp: "divergent",
    diverging: true,
    unit: "kg CH₄ km⁻² h⁻¹",
  },
  {
    key: "delta_xch4",
    label: "ΔXCH4 (posterior − prior)",
    caption: "Modelled change in column methane",
    ramp: "divergent",
    diverging: true,
    unit: "ppb",
  },
];

export function DiagnosticsView({
  api,
  period,
  periodIdx,
  setPeriodIdx,
  playing,
  setPlaying,
  speed,
  setSpeed,
  rampName: _rampName,
  isSinglePeriod,
  templateVars,
  loadState,
}: Props) {
  const periods = api.manifest.periods;
  const grid = api.manifest.grid;
  const basemap = basemapFromConfig(api.config.map);
  const text = api.config.text;
  // Snapshot scalars come from the manifest entry (in memory); only the
  // spatial DVMaps below need the per-period binary.
  const cur = periods[periodIdx]?.diagScalars;

  const curEntry = periods[periodIdx];
  const spatialLabel = isSinglePeriod
    ? text.diagnostics_spatial_label
    : `${text.diagnostics_spatial_label} for ${text.cadence_singular} ${curEntry ? periodNumber(curEntry) : periodIdx + 1}`;
  const scalarLabel = isSinglePeriod ? text.diagnostics_scalar_label : `${text.diagnostics_scalar_label} over time`;
  const dateMeta = isSinglePeriod
    ? formatDate(periods[0]?.start)
    : applyTemplate("{date_start} → {date_end}", templateVars);

  return (
    <div className="dv">
      <DVHeader
        periodIdx={periodIdx}
        setPeriodIdx={setPeriodIdx}
        playing={playing}
        setPlaying={setPlaying}
        speed={speed}
        setSpeed={setSpeed}
        isSinglePeriod={isSinglePeriod}
        periods={periods}
      />

      <div className="dv-section-lbl">
        <span>{spatialLabel}</span>
        <span className="dv-section-meta">{dateMeta}</span>
      </div>
      <div className="dv-maps-row">
        {SPATIAL_PANELS.map((panel) => (
          <DVMap
            key={panel.key}
            label={panel.label}
            caption={panel.caption}
            period={period}
            fieldKey={panel.key}
            rampName={panel.ramp}
            diverging={panel.diverging}
            vmin={panel.vmin}
            vmax={panel.vmax}
            unit={panel.unit}
            globalBounds={api.manifest.diagBounds?.[panel.key] ?? null}
            grid={grid}
            globalsMask={api.globalsMask}
            dataBbox={api.manifest.globals.dataBbox ?? null}
            loadState={loadState}
            basemap={basemap}
          />
        ))}
      </div>

      <div className="dv-section-lbl">
        <span>{scalarLabel}</span>
        {!isSinglePeriod && (
          <span className="dv-section-meta">Click any chart to jump to that {text.cadence_singular}</span>
        )}
      </div>

      {isSinglePeriod ? (
        <div className="dv-snapshot-grid">
          <DVSnapshot label="DOFS" hint="Degrees of freedom for signal" value={fmtVal(cur?.dofs)} />
          <DVSnapshot label="N obs" hint="TROPOMI super-obs assimilated" value={fmtVal(cur?.n_obs, true)} />
          <DVSnapshot label="⟨A⟩" hint="Mean averaging-kernel diagonal" value={fmtVal(cur?.avg_kernel)} />
          <DVSnapshot label="Bias (prior)" hint="Prior bias vs observations" value={fmtVal(cur?.bias_prior)} unit="ppb" />
          <DVSnapshot label="Bias (posterior)" hint="Posterior bias vs observations" value={fmtVal(cur?.bias_posterior)} unit="ppb" />
          <DVSnapshot
            label="Δ|Bias|"
            hint="Posterior − prior magnitude — negative means the bias shrank"
            value={fmtVal(absDiff(cur?.bias_posterior, cur?.bias_prior))}
            unit="ppb"
          />
          <DVSnapshot label="RMSE (prior)" hint="Prior RMSE vs observations" value={fmtVal(cur?.rmse_prior)} unit="ppb" />
          <DVSnapshot label="RMSE (posterior)" hint="Posterior RMSE vs observations" value={fmtVal(cur?.rmse_posterior)} unit="ppb" />
          <DVSnapshot
            label="ΔRMSE"
            hint="Posterior − prior — negative means the fit improved"
            value={fmtVal(diff(cur?.rmse_posterior, cur?.rmse_prior))}
            unit="ppb"
          />
        </div>
      ) : (
        <div className="dv-ts-grid">
          <DVTimeseries
            label="DOFS"
            hint="Degrees of freedom for signal"
            dataKey="dofs"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
            nonNegative
          />
          <DVTimeseries
            label="N obs"
            hint="TROPOMI super-obs assimilated"
            dataKey="n_obs"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
            integer
            nonNegative
          />
          <DVTimeseries
            label="⟨A⟩"
            hint="Mean averaging-kernel diagonal"
            dataKey="avg_kernel"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
            nonNegative
          />
          <DVTimeseries
            label="Bias (prior)"
            hint="Prior simulation vs observations"
            dataKey="bias_prior"
            unit="ppb"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
          />
          <DVTimeseries
            label="Bias (posterior)"
            hint="Posterior simulation vs observations"
            dataKey="bias_posterior"
            unit="ppb"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
          />
          <DVTimeseries
            label="Δ|Bias|"
            hint="Posterior − prior magnitude — negative means the bias shrank"
            dataKey="bias_posterior"
            unit="ppb"
            secondaryKey="bias_prior"
            derive="magnitudeDiff"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
          />
          <DVTimeseries
            label="RMSE (prior)"
            hint="Prior simulation vs observations"
            dataKey="rmse_prior"
            unit="ppb"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
            nonNegative
          />
          <DVTimeseries
            label="RMSE (posterior)"
            hint="Posterior simulation vs observations"
            dataKey="rmse_posterior"
            unit="ppb"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
            nonNegative
          />
          <DVTimeseries
            label="ΔRMSE"
            hint="Posterior − prior — negative means the fit improved"
            dataKey="rmse_posterior"
            unit="ppb"
            secondaryKey="rmse_prior"
            derive="diff"
            periods={periods}
            periodIdx={periodIdx}
            setPeriodIdx={setPeriodIdx}
          />
        </div>
      )}
    </div>
  );
}
