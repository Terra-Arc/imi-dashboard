import type { ReactNode } from "react";
import { periodNumber, type PeriodEntry } from "../data/types";
import { formatDate, formatEndDate } from "../util/text";

interface Props {
  periods: PeriodEntry[];
  periodIdx: number;
  setPeriodIdx?: (i: number) => void;
  isSinglePeriod: boolean;
  children?: ReactNode;
}

export function PeriodNav({ periods, periodIdx, setPeriodIdx, isSinglePeriod, children }: Props) {
  const p = periods[periodIdx];
  if (!p) return null;
  const atStart = periodIdx <= 0;
  const atEnd = periodIdx >= periods.length - 1;
  return (
    <div className="period-nav">
      {!isSinglePeriod && setPeriodIdx && (
        <div className="period-nav-actions">
          <button
            className="period-nav-btn"
            onClick={() => setPeriodIdx(Math.max(0, periodIdx - 1))}
            disabled={atStart}
            aria-label="Previous period"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M8 2 L4 6 L8 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button
            className="period-nav-btn"
            onClick={() => setPeriodIdx(Math.min(periods.length - 1, periodIdx + 1))}
            disabled={atEnd}
            aria-label="Next period"
          >
            <svg width="12" height="12" viewBox="0 0 12 12">
              <path d="M4 2 L8 6 L4 10" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        </div>
      )}
      <div className="period-nav-date">
        {formatDate(p.start)}
        <span className="period-nav-arr">→</span>
        {/* IMI's EndDate is exclusive; the last day of the period is shown. */}
        {formatEndDate(p.end, false)}
      </div>
      {!isSinglePeriod && (
        <div className="period-nav-meta">
          {/* Folder numbering — jumps across gaps when periods are missing
              on disk, instead of renumbering to a dense 1…N counter. */}
          <b>{periodNumber(p)}</b>{" "}
          <span className="period-nav-of">of {periodNumber(periods[periods.length - 1])}</span>
        </div>
      )}
      {children && <div className="period-nav-extras">{children}</div>}
    </div>
  );
}
