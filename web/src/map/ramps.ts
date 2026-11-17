// Color ramps. Each takes t ∈ [0,1] and returns [r,g,b] in 0–255.
//
// Overview maps use *standard* perceptually-uniform / single-hue colormaps
// only (Reds, Viridis, Magma, Plasma) — no bespoke "theme" ramps. Stops are
// sampled from the canonical matplotlib colormaps so the dashboard reads like
// any other scientific figure. The lone non-overview ramp is `divergent`
// (blue→white→red), used internally by the diagnostics view for fields with a
// meaningful center.

export type RampFn = (t: number) => [number, number, number];

export interface RampOption {
  id: string;
  label: string;
}

// Selectable colormaps for the overview map. Standard scientific colormaps
// only — keep this list in sync with `RampName` in data/types.ts and the
// `ramp` Literal in imi_dashboard/config.py.
export const RAMP_OPTIONS: RampOption[] = [
  { id: "reds",    label: "Reds" },
  { id: "viridis", label: "Viridis" },
  { id: "magma",   label: "Magma" },
  { id: "plasma",  label: "Plasma" },
];

// Selectable colormaps for *diverging* diagnostic fields (meaningful center).
// Standard matplotlib diverging maps, all symmetric around t=0.5.
export const DIVERGENT_RAMP_OPTIONS: RampOption[] = [
  { id: "divergent", label: "Blue–Red" },
  { id: "coolwarm",  label: "Coolwarm" },
  { id: "puor",      label: "Orange–Purple" },
];

function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

function rampFromStops(stops: [number, number, number][]): RampFn {
  return (t: number) => {
    t = clamp01(t);
    const seg = t * (stops.length - 1);
    const i = Math.floor(seg);
    const f = seg - i;
    const a = stops[i];
    const b = stops[Math.min(stops.length - 1, i + 1)];
    return [
      a[0] + (b[0] - a[0]) * f,
      a[1] + (b[1] - a[1]) * f,
      a[2] + (b[2] - a[2]) * f,
    ];
  };
}

export const RAMPS: Record<string, RampFn> = {
  viridis: rampFromStops([
    [68, 1, 84],
    [59, 82, 139],
    [33, 144, 141],
    [94, 201, 98],
    [253, 231, 37],
  ]),
  magma: rampFromStops([
    [0, 0, 4],
    [80, 18, 123],
    [183, 55, 121],
    [251, 137, 97],
    [252, 253, 191],
  ]),
  plasma: rampFromStops([
    [13, 8, 135],
    [126, 3, 168],
    [204, 71, 120],
    [248, 149, 64],
    [240, 249, 33],
  ]),
  // Single-hue sequential (matplotlib "Reds"): pale → deep red.
  reds: rampFromStops([
    [255, 245, 240],
    [252, 187, 161],
    [251, 106, 74],
    [203, 24, 29],
    [103, 0, 13],
  ]),
  // Symmetric diverging ramp: blue → white → red. Used for fields with a
  // meaningful center (e.g. the posterior scaling factor, where 1 = "no
  // change", <1 = blue, >1 = red). The middle stop lands on t=0.5.
  divergent: rampFromStops([
    [43, 108, 176],   // blue (low end)
    [247, 250, 251],  // near-white (center)
    [232, 49, 42],    // red (high end)
  ]),
  // matplotlib "coolwarm": muted blue → light gray → muted red.
  coolwarm: rampFromStops([
    [59, 76, 192],
    [124, 159, 249],
    [221, 221, 221],
    [234, 125, 98],
    [180, 4, 38],
  ]),
  // matplotlib "PuOr": deep orange → near-white → deep purple.
  puor: rampFromStops([
    [127, 59, 8],
    [224, 130, 20],
    [247, 247, 247],
    [128, 115, 172],
    [45, 0, 75],
  ]),
};

/** Build a CSS linear-gradient string for the ramp's swatch. */
export function rampGradient(name: string, segments = 12): string {
  const ramp = RAMPS[name] ?? RAMPS.viridis;
  const stops: string[] = [];
  for (let i = 0; i < segments; i += 1) {
    const t = i / (segments - 1);
    const [r, g, b] = ramp(t);
    stops.push(`rgb(${r | 0},${g | 0},${b | 0}) ${(t * 100).toFixed(1)}%`);
  }
  return `linear-gradient(90deg, ${stops.join(",")})`;
}
