import { useState } from "react";
import { periodNumber, type Manifest, type SectorMeta } from "../data/types";
import { cssColorToRgb } from "../util/color";
import { formatMt } from "../util/text";

interface Props {
  sectors: SectorMeta[];
  sector: string;
  setSector: (id: string) => void;
  manifest: Manifest;
  periodIdx: number;
  isSinglePeriod: boolean;
}

export function SectorBar({ sectors, sector, setSector, manifest, periodIdx, isSinglePeriod }: Props) {
  const [expanded, setExpanded] = useState(false);
  const primary = sectors.filter((s) => s.primary);
  const secondary = sectors.filter((s) => !s.primary);
  const hasSecondary = secondary.length > 0;

  function pick(id: string) {
    setSector(id);
    if (expanded) setExpanded(false);
  }

  const selectedSecondary = secondary.find((s) => s.id === sector);

  return (
    <div className={"sectorbar " + (expanded ? "is-expanded" : "")}>
      <div className="sectorbar-row sectorbar-row-primary">
        <div className="sectorbar-lbl">
          <svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden>
            <rect x="2" y="3" width="5" height="10" rx="1" stroke="currentColor" strokeWidth="1.4" />
            <rect x="9" y="6" width="5" height="7" rx="1" stroke="currentColor" strokeWidth="1.4" />
          </svg>
          View by source
        </div>
        <div className="sectorbar-pills" role="tablist">
          {primary.map((s) => (
            <SectorPill
              key={s.id}
              meta={s}
              active={s.id === sector}
              periodIdx={periodIdx}
              onClick={() => pick(s.id)}
              manifest={manifest}
              isSinglePeriod={isSinglePeriod}
            />
          ))}
          {!expanded && selectedSecondary && (
            <SectorPill
              key={selectedSecondary.id}
              meta={selectedSecondary}
              active
              periodIdx={periodIdx}
              onClick={() => pick(selectedSecondary.id)}
              manifest={manifest}
              isSinglePeriod={isSinglePeriod}
              secondary
            />
          )}
        </div>
        {hasSecondary && (
          <button
            className={"sector-more " + (expanded ? "is-expanded" : "")}
            onClick={() => setExpanded((e) => !e)}
            aria-expanded={expanded}
          >
            {expanded ? (
              <>
                <svg width="11" height="11" viewBox="0 0 12 12">
                  <path d="M3 7 L6 4 L9 7" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                Less
              </>
            ) : (
              <>
                +{secondary.length} more
                <svg width="11" height="11" viewBox="0 0 12 12">
                  <path d="M3 5 L6 8 L9 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </>
            )}
          </button>
        )}
      </div>

      {expanded && (
        <div className="sectorbar-row sectorbar-row-secondary">
          <div className="sectorbar-lbl sectorbar-lbl-secondary">More sources</div>
          <div className="sectorbar-pills sectorbar-pills-secondary" role="tablist">
            {secondary.map((s) => (
              <SectorPill
                key={s.id}
                meta={s}
                active={s.id === sector}
                periodIdx={periodIdx}
                onClick={() => pick(s.id)}
                manifest={manifest}
                isSinglePeriod={isSinglePeriod}
                secondary
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

interface PillProps {
  meta: SectorMeta;
  active: boolean;
  onClick: () => void;
  manifest: Manifest;
  periodIdx: number;
  isSinglePeriod: boolean;
  secondary?: boolean;
}

function SectorPill({ meta, active, onClick, manifest, periodIdx, isSinglePeriod, secondary }: PillProps) {
  const rgb = cssColorToRgb(meta.color);
  const curr = manifest.periods[periodIdx]?.sectorScalars[meta.id]?.posterior ?? null;
  const showSpark = !isSinglePeriod && manifest.periods.length > 1;

  let sparkEl: React.ReactNode = null;
  if (showSpark) {
    const vals = manifest.periods.map((p) => p.sectorScalars[meta.id]?.posterior ?? NaN);
    const finite = vals.filter((v) => Number.isFinite(v));
    if (finite.length >= 2) {
      // As in the timeseries chart: x follows the period's folder number, so
      // missing periods leave a gap, and the line breaks at missing values
      // and at gaps in the numbering instead of joining across them.
      const nums = manifest.periods.map(periodNumber);
      const n0 = nums[0];
      const nSpan = Math.max(1, nums[nums.length - 1] - n0);
      const lo = Math.min(0, ...finite);
      const range = Math.max(...finite) - lo || 1;
      const w = 56;
      const h = 16;
      const xAt = (i: number) => ((nums[i] - n0) / nSpan) * w;
      const yAt = (v: number) => h - ((v - lo) / range) * (h - 2) - 1;
      let path = "";
      vals.forEach((v, i) => {
        if (!Number.isFinite(v)) return;
        const joined = i > 0 && Number.isFinite(vals[i - 1]) && nums[i] - nums[i - 1] <= 1;
        path += (joined ? "L" : "M") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1) + " ";
      });
      const cur = vals[periodIdx];
      sparkEl = (
        <svg className="sector-pill-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
          <path d={path} fill="none" stroke={`rgba(${rgb},${active ? 0.95 : 0.55})`} strokeWidth="1.3" strokeLinejoin="round" strokeLinecap="round" />
          {Number.isFinite(cur) && (
            <circle cx={xAt(periodIdx)} cy={yAt(cur)} r="2" fill={meta.color} stroke="#fff" strokeWidth="1" />
          )}
        </svg>
      );
    }
  }

  return (
    <button
      role="tab"
      aria-selected={active}
      className={"sector-pill " + (active ? "is-active " : "") + (secondary ? "is-secondary" : "")}
      style={{
        ["--sec" as never]: meta.color,
        ["--sec-rgb" as never]: rgb,
      }}
      onClick={onClick}
    >
      <div className="sector-pill-top">
        <span className="sector-pill-dot" />
        <span className="sector-pill-label">{meta.label}</span>
      </div>
      <div className="sector-pill-bot">
        <span className="sector-pill-val">
          {formatMt(curr)}
          <span className="sector-pill-unit"> Mt/yr</span>
        </span>
        {sparkEl}
      </div>
    </button>
  );
}
