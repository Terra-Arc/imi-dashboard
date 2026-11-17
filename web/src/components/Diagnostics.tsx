import type { PeriodEntry } from "../data/types";

interface Props {
  // Diagnostic scalars live in the manifest entry (in memory), so this panel
  // stays populated as the period changes without waiting on the .bin fetch.
  entry: PeriodEntry | undefined;
  open: boolean;
  onToggle: () => void;
}

function fmt(n: number | null | undefined, digits = 2): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return n.toFixed(digits);
}

export function Diagnostics({ entry, open, onToggle }: Props) {
  const d = entry?.diagScalars;
  return (
    <section className={"diag " + (open ? "diag-open" : "")}>
      <button className="diag-trigger" onClick={onToggle} aria-expanded={open}>
        <span className="diag-trigger-lbl">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
            <path
              d="M3 8.5l3 3 7-7"
              stroke="currentColor"
              strokeWidth="1.5"
              strokeLinecap="round"
              strokeLinejoin="round"
              style={{ display: open ? "none" : "block" }}
            />
            <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="1.2" fill="none" />
          </svg>
          Quick diagnostics
          <span className="diag-meta">For technical reviewers · 6 variables</span>
        </span>
        <span className="diag-chevron" aria-hidden>
          <svg width="14" height="14" viewBox="0 0 14 14">
            <path d="M3 5l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="diag-body">
          <div className="diag-grid">
            <DiagStat label="DOFS" value={fmt(d?.dofs, 2)} hint="Degrees of freedom for signal" />
            <DiagStat
              label="N obs"
              value={d?.n_obs != null && Number.isFinite(d.n_obs) ? Math.round(d.n_obs).toLocaleString() : "—"}
              hint="TROPOMI super-obs assimilated"
            />
            <DiagStat label="Bias (prior)" value={fmt(d?.bias_prior, 2)} unit="ppb" hint="Prior bias vs observations" />
            <DiagStat label="Bias (post)" value={fmt(d?.bias_posterior, 2)} unit="ppb" hint="Posterior bias vs observations" />
            <DiagStat label="RMSE (prior)" value={fmt(d?.rmse_prior, 2)} unit="ppb" hint="Prior RMSE vs observations" />
            <DiagStat label="RMSE (post)" value={fmt(d?.rmse_posterior, 2)} unit="ppb" hint="Posterior RMSE vs observations" />
          </div>
        </div>
      )}
    </section>
  );
}

interface DiagStatProps {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
}

function DiagStat({ label, value, unit, hint }: DiagStatProps) {
  return (
    <div className="diag-stat">
      <div className="diag-stat-lbl">{label}</div>
      <div className="diag-stat-val mono">
        {value}
        {unit && value !== "—" && <span className="diag-stat-unit">{" " + unit}</span>}
      </div>
      {hint && <div className="diag-stat-hint">{hint}</div>}
    </div>
  );
}
