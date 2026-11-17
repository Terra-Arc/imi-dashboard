// Resolve any CSS color string (hex, named, var(--x), color-function, …) to
// an "r, g, b" tuple suitable for `rgba(var(--sec-rgb), a)`. The design CSS
// uses this pattern extensively for sector-tinted hover/active states, but
// the converter only ships the raw color string per sector. We let the
// browser do the parsing once and cache the result.

const cache = new Map<string, string>();
let probe: HTMLDivElement | null = null;

function getProbe(): HTMLDivElement {
  if (probe) return probe;
  probe = document.createElement("div");
  probe.style.position = "absolute";
  probe.style.visibility = "hidden";
  probe.style.pointerEvents = "none";
  document.body.appendChild(probe);
  return probe;
}

/** "#F47B20" → "244, 123, 32". "var(--brand-primary)" → "0, 167, 157", etc.
 *  Returns "0, 0, 0" if the color cannot be resolved (shouldn't happen for
 *  any valid CSS color). */
export function cssColorToRgb(css: string): string {
  const cached = cache.get(css);
  if (cached !== undefined) return cached;
  const el = getProbe();
  el.style.color = "";
  el.style.color = css;
  const computed = getComputedStyle(el).color;
  // computed is "rgb(R, G, B)" or "rgba(R, G, B, A)".
  const m = computed.match(/rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i);
  const tuple = m ? `${m[1]}, ${m[2]}, ${m[3]}` : "0, 0, 0";
  cache.set(css, tuple);
  return tuple;
}
