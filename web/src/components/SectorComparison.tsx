import type { PeriodEntry, SectorMeta } from "../data/types";
import { cssColorToRgb } from "../util/color";
import { formatMt, withLabel } from "../util/text";

interface Props {
  // Reads scalars from the manifest entry (in memory) rather than the loaded
  // per-period binary, so the bars don't blank while the .bin downloads.
  entry: PeriodEntry | undefined;
  sector: string;
  sectors: SectorMeta[];
  /** Soil-sink label of the total sector's numbers, or null (see Scalars). */
  totalLabel: string | null;
}

export function SectorComparison({ entry, sector, sectors, totalLabel }: Props) {
  const meta = sectors.find((s) => s.id === sector) ?? sectors[0];
  const scalars = entry?.sectorScalars[sector];
  const prior = scalars?.prior;
  const posterior = scalars?.posterior;
  const haveBoth = prior != null && posterior != null && Number.isFinite(prior) && Number.isFinite(posterior);
  const dpct = haveBoth && prior !== 0 ? ((posterior - prior) / Math.abs(prior)) * 100 : null;
  const direction = dpct != null ? (dpct >= 0 ? "above" : "below") : null;
  const absDpct = dpct != null ? Math.abs(dpct).toFixed(0) : null;
  // Bars are as long as the value's magnitude, so a sink (a sector whose
  // values are negative, such as the soil absorption) is drawn too; its
  // value label carries the minus sign.
  const max = haveBoth ? Math.max(Math.abs(prior), Math.abs(posterior)) * 1.15 : 1;
  const rgb = cssColorToRgb(meta?.color ?? "#00A79D");

  return (
    <div className="sc">
      <div className="sc-hd">
        <div className="overline">
          <span className="sector-dot" style={{ background: meta?.color ?? "var(--brand-primary)" }} />
          {withLabel("Bottom-up inventory vs inversion estimate", sector === "total" ? totalLabel : null)}
        </div>
        {haveBoth && dpct != null && (
          <div className="sc-sub">
            The inversion places{" "}
            <b>{meta?.label.toLowerCase() ?? sector}</b> emissions{" "}
            <b>
              {absDpct}% {direction}
            </b>{" "}
            the bottom-up inventory for this period.
          </div>
        )}
      </div>
      <div className="sc-bars">
        <Bar label="Bottom-up inventory" value={prior} max={max} color="var(--neutral-400)" />
        <Bar label="Inversion estimate" value={posterior} max={max} fillRgb={rgb} isPosterior />
      </div>
    </div>
  );
}

interface BarProps {
  label: string;
  value: number | null | undefined;
  max: number;
  color?: string;
  fillRgb?: string;
  isPosterior?: boolean;
}

function Bar({ label, value, max, color, fillRgb, isPosterior }: BarProps) {
  const ok = value != null && Number.isFinite(value) && max > 0;
  const w = ok ? Math.min(100, (Math.abs(value) / max) * 100) : 0;
  return (
    <div className={"sc-bar-row" + (isPosterior ? " sc-bar-row-posterior" : "")}>
      <div className="sc-bar-lbl">{label}</div>
      <div className="sc-bar-track">
        <div
          className="sc-bar-fill"
          style={{
            width: `${w}%`,
            background: fillRgb ? `rgba(${fillRgb}, 0.85)` : color,
          }}
        />
        <div className="sc-bar-val">
          {ok ? formatMt(value) : "—"} <span className="sc-bar-unit">Mt CH₄/yr</span>
        </div>
      </div>
    </div>
  );
}
