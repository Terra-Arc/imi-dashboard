import { periodNumber, type PeriodEntry, type SectorMeta } from "../data/types";
import { cssColorToRgb } from "../util/color";
import { formatMt, withLabel } from "../util/text";

interface Props {
  // Scalars come straight from manifest.json (already in memory), not from the
  // per-period binary, so the numbers update the instant the period changes
  // instead of blanking out while the .bin downloads.
  entry: PeriodEntry | undefined;
  sector: string;
  sectors: SectorMeta[];
  /** False when no period of the run has an ensemble range. */
  hasEnsemble: boolean;
  /** "excluding the soil sink" or "including the soil sink" for the total
   *  sector's numbers, or null when the dashboard does not record it. */
  totalLabel: string | null;
  /** True for a timeseries, which the dashboard takes to be one Kalman-mode
   *  IMI run (see docs/guide.md). */
  timeseries: boolean;
}

export function Scalars({ entry, sector, sectors, hasEnsemble, totalLabel, timeseries }: Props) {
  const meta = sectors.find((s) => s.id === sector) ?? sectors[0];
  const scalars = entry?.sectorScalars[sector];
  const posterior = scalars?.posterior ?? null;
  const prior = scalars?.prior ?? null;
  const ensMin = scalars?.ens_min ?? null;
  const ensMax = scalars?.ens_max ?? null;

  const totalScalars = entry?.sectorScalars["total"];
  const totalPosterior = totalScalars?.posterior ?? null;

  // Share of total only meaningful for non-total sectors.
  const share = sector !== "total" && posterior != null && totalPosterior && totalPosterior !== 0
    ? (posterior / totalPosterior) * 100
    : null;

  const labelPrefix = sector === "total" ? "Total" : meta?.label ?? sector;
  // Only the total sector's own numbers carry the soil-sink label.
  const ownLabel = sector === "total" ? totalLabel : null;
  // In Kalman mode IMI starts period 1 from the inventory and every later
  // period from the previous period's posterior, moved part of the way back
  // toward the inventory (IMI's NudgeFactor). The period number, not the
  // index, decides: a run whose period 1 is missing on disk still starts
  // its first period shown from a previous posterior.
  const priorSource = timeseries && entry && periodNumber(entry) > 1
    ? "From the previous period's estimate"
    : "Bottom-up inventory";
  const rgb = cssColorToRgb(meta?.color ?? "#00A79D");

  return (
    <div className="scalars scalars-big">
      <Stat
        label={`${labelPrefix} methane emissions`}
        value={formatMt(posterior)}
        unit="Mt CH₄/yr"
        subtext={withLabel("Posterior estimate", ownLabel)}
        primary
        accentRgb={rgb}
      />
      {share != null ? (
        <Stat
          label="Share of total"
          value={share.toFixed(1)}
          unit="%"
          subtext={withLabel(`Total ${formatMt(totalPosterior)} Mt CH₄/yr`, totalLabel)}
        />
      ) : (
        <Stat
          label="Prior estimate"
          value={formatMt(prior)}
          unit="Mt CH₄/yr"
          subtext={withLabel(priorSource, ownLabel)}
        />
      )}
      {hasEnsemble ? (
        <Stat
          label="Ensemble range"
          value={ensMin != null && ensMax != null ? `${formatMt(ensMin)}–${formatMt(ensMax)}` : "—"}
          unit="Mt CH₄/yr"
          subtext={withLabel("Min – max across the run ensemble", ownLabel)}
        />
      ) : (
        <Stat label="Ensemble range" value="—" subtext="This run has no ensemble" />
      )}
    </div>
  );
}

interface StatProps {
  label: string;
  value: string;
  unit?: string;
  subtext?: string;
  primary?: boolean;
  accentRgb?: string;
}

function Stat({ label, value, unit, subtext, primary, accentRgb }: StatProps) {
  return (
    <div className={"stat" + (primary ? " stat-primary" : "")}>
      <div className="stat-label">
        {accentRgb && <span className="stat-dot" style={{ background: `rgb(${accentRgb})` }} />}
        {label}
      </div>
      <div className="stat-row">
        <div className="stat-value">
          <span className="stat-num">{value}</span>
          {unit && <span className="stat-unit">{unit}</span>}
        </div>
      </div>
      {subtext && <div className="stat-sub">{subtext}</div>}
    </div>
  );
}
