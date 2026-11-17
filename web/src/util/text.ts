/** Substitute {placeholder} tokens in a template string with values. */
export function applyTemplate(
  template: string,
  vars: Record<string, string | number | null | undefined>,
): string {
  return template.replace(/\{(\w+)\}/g, (_, key) => {
    const v = vars[key];
    return v === undefined || v === null ? "" : String(v);
  });
}

const DAY_MS = 86_400_000;

/** Parse a calendar date, ISO "YYYY-MM-DD" or compact "YYYYMMDD", as
 *  midnight UTC. Dates are always formatted in UTC too (see formatDate), so
 *  the day shown does not depend on the viewer's time zone. */
export function parseDate(iso: string | null | undefined): Date | null {
  if (!iso) return null;
  const s = /^\d{8}$/.test(iso) ? `${iso.slice(0, 4)}-${iso.slice(4, 6)}-${iso.slice(6, 8)}` : iso;
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(s) ? s + "T00:00:00Z" : s);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** Format a calendar date as "Jun 15, 2024" (or "Jun 15" without the year).
 *  Empty input gives "—"; an unparseable string is returned unchanged. */
export function formatDate(iso: string | null | undefined, withYear = true): string {
  if (!iso) return "—";
  const d = parseDate(iso);
  if (!d) return iso;
  return d.toLocaleDateString("en-US", {
    day: "numeric",
    month: "short",
    year: withYear ? "numeric" : undefined,
    timeZone: "UTC",
  });
}

/** Format a period's end. IMI's EndDate is exclusive (the day after the
 *  period's last day), so the date shown is EndDate minus one day. */
export function formatEndDate(iso: string | null | undefined, withYear = true): string {
  const d = parseDate(iso);
  if (!d) return formatDate(iso, withYear);
  return formatDate(new Date(d.getTime() - DAY_MS).toISOString().slice(0, 10), withYear);
}

/** The calendar years a run covers, from the first period's start to the
 *  last period's exclusive end: "2023", or "2018–2025". Empty when the
 *  dates are missing. */
export function yearSpan(start: string | null | undefined, endExclusive: string | null | undefined): string {
  const first = yearOf(start);
  const end = parseDate(endExclusive);
  const last = end ? new Date(end.getTime() - DAY_MS).getUTCFullYear() : first;
  if (first === null || last === null) return "";
  return first === last ? String(first) : `${first}–${last}`;
}

/** Calendar year of a date (UTC), or null when undatable. */
export function yearOf(iso: string | null | undefined): number | null {
  return parseDate(iso)?.getUTCFullYear() ?? null;
}

/** Format an emission total (Mt CH₄/yr) to three significant figures, so
 *  small sectors keep their digits ("0.00405") instead of rounding to
 *  "0.00". Values from 1000 up are rounded to an integer; values below 1e-4
 *  use exponent notation ("2.75e-7"). */
export function formatMt(v: number | null | undefined): string {
  if (v == null || !Number.isFinite(v)) return "—";
  if (v === 0) return "0";
  if (Math.abs(v) < 1e-4) return v.toExponential(2);
  if (Math.abs(v) >= 1000) return Math.round(v).toString();
  // Round first, then pick the decimals from the rounded value, so 0.9996
  // reads "1.00" rather than "1.000".
  const r = Number(v.toPrecision(3));
  const decimals = Math.max(0, 2 - Math.floor(Math.log10(Math.abs(r))));
  return r.toFixed(decimals);
}

/** "including the soil sink" / "excluding the soil sink" for a total whose
 *  definition is recorded (manifest.totalDefinition), else null (no label). */
export function soilSinkLabel(includes: boolean | null | undefined): string | null {
  if (includes == null) return null;
  return includes ? "including the soil sink" : "excluding the soil sink";
}

/** `text` followed by ", <label>" when there is a label. */
export function withLabel(text: string, label: string | null): string {
  return label ? `${text}, ${label}` : text;
}
