import { useEffect, useRef, useState } from "react";
import type { Manifest, PeriodData, PeriodEntry, SiteConfig } from "./types";
// The data format this web app reads; the converter writes the same
// number (pack.MANIFEST_VERSION, checked by a test).
import format from "./format.json";

const DATA_ROOT = "data";

/** Tiny LRU cache keyed by period index. */
class PeriodCache {
  private order: number[] = [];
  private map = new Map<number, PeriodData>();
  constructor(private capacity: number) {}

  get(idx: number): PeriodData | undefined {
    const v = this.map.get(idx);
    if (v) {
      const i = this.order.indexOf(idx);
      if (i >= 0) this.order.splice(i, 1);
      this.order.push(idx);
    }
    return v;
  }

  set(idx: number, data: PeriodData): void {
    if (this.map.has(idx)) {
      const i = this.order.indexOf(idx);
      if (i >= 0) this.order.splice(i, 1);
    }
    this.map.set(idx, data);
    this.order.push(idx);
    while (this.order.length > this.capacity) {
      const evict = this.order.shift();
      if (evict !== undefined) this.map.delete(evict);
    }
  }

  has(idx: number): boolean {
    return this.map.has(idx);
  }
}

export async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${DATA_ROOT}/${path}`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`fetch ${path} failed: ${res.status}`);
  return (await res.json()) as T;
}

export async function fetchConfig(): Promise<SiteConfig> {
  return fetchJSON<SiteConfig>("config.json");
}

export async function fetchManifest(): Promise<Manifest> {
  return fetchJSON<Manifest>("manifest.json");
}

// The binaries keep their names when a dashboard is re-converted (periods/
// are named by position), so they are revalidated with the server like the
// JSON files rather than taken from the browser cache unchecked. Repeat
// visits to a period are served from PeriodCache without a request.
export async function fetchGlobalsMask(manifest: Manifest): Promise<Uint8Array> {
  const res = await fetch(`${DATA_ROOT}/${manifest.globals.file}`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`fetch globals failed: ${res.status}`);
  return new Uint8Array(await res.arrayBuffer());
}

async function fetchPeriod(manifest: Manifest, entry: PeriodEntry): Promise<PeriodData> {
  const res = await fetch(`${DATA_ROOT}/${entry.file}`, { cache: "no-cache" });
  if (!res.ok) throw new Error(`fetch period ${entry.idx} failed: ${res.status}`);
  const buf = await res.arrayBuffer();
  // A truncated or stale file would otherwise be sliced at the manifest's
  // offsets and show displaced or blank cells; refuse it instead.
  const expected = manifest.fields.length * manifest.grid.rows * manifest.grid.cols * 4;
  if (buf.byteLength !== expected) {
    throw new Error(
      `period ${entry.idx} (${entry.file}) has ${buf.byteLength} bytes, the manifest needs ${expected}`,
    );
  }
  const all = new Float32Array(buf);
  const fields: Record<string, Float32Array> = {};
  for (const f of manifest.fields) {
    fields[f.name] = all.subarray(f.offset, f.offset + f.length);
  }
  return { entry, fields };
}

export interface DataAPI {
  manifest: Manifest;
  config: SiteConfig;
  globalsMask: Uint8Array;
  /** Returns the period synchronously if cached, else triggers a fetch and
   * returns `undefined`. The caller subscribes via `useImiData`. */
  getPeriod(idx: number): PeriodData | undefined;
  /** Force-prefetch a period (used to warm the LRU around the current idx). */
  prefetch(idx: number): Promise<PeriodData> | null;
}

export function createDataAPI(
  manifest: Manifest,
  config: SiteConfig,
  globalsMask: Uint8Array,
  capacity = 32,
): DataAPI {
  const cache = new PeriodCache(capacity);
  const inflight = new Map<number, Promise<PeriodData>>();

  async function _load(entry: PeriodEntry): Promise<PeriodData> {
    const existing = cache.get(entry.idx);
    if (existing) return existing;
    const flying = inflight.get(entry.idx);
    if (flying) return flying;
    // The request leaves `inflight` whether it succeeds or fails, so a failed
    // period is fetched again on the next visit instead of returning the
    // same rejected promise forever.
    const p = fetchPeriod(manifest, entry).then(
      (data) => {
        cache.set(entry.idx, data);
        inflight.delete(entry.idx);
        return data;
      },
      (err: unknown) => {
        inflight.delete(entry.idx);
        throw err;
      },
    );
    inflight.set(entry.idx, p);
    return p;
  }

  return {
    manifest,
    config,
    globalsMask,
    getPeriod(idx: number) {
      return cache.get(idx);
    },
    prefetch(idx: number) {
      if (idx < 0 || idx >= manifest.periods.length) return null;
      const entry = manifest.periods[idx];
      return _load(entry);
    },
  };
}

/** React hook: load API once on mount. Returns api+error so the UI can
 * tell "still loading" from "load failed" (otherwise a missing data/ folder
 * looks indistinguishable from a slow fetch). */
export function useDataAPI(): { api: DataAPI | null; error: string | null } {
  const [api, setApi] = useState<DataAPI | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const [config, manifest] = await Promise.all([fetchConfig(), fetchManifest()]);
      if (manifest.version !== format.manifestVersion) {
        throw new Error(
          `manifest.json has format version ${String(manifest.version)}; this web app reads version ${format.manifestVersion} (re-convert the dashboard)`,
        );
      }
      const mask = await fetchGlobalsMask(manifest);
      const cells = manifest.grid.rows * manifest.grid.cols;
      if (mask.length !== cells) {
        throw new Error(
          `${manifest.globals.file} has ${mask.length} bytes, the ${manifest.grid.rows} x ${manifest.grid.cols} grid needs ${cells}`,
        );
      }
      if (cancelled) return;
      setApi(createDataAPI(manifest, config, mask));
    })().catch((err) => {
      console.error("data load failed", err);
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return { api, error };
}

/** React hook: returns the period if loaded, otherwise triggers a fetch and
 * prefetches neighbours. `failed` is true when the fetch of the current
 * period failed; it is retried the next time the period is shown. */
export function useImiPeriod(
  api: DataAPI | null,
  periodIdx: number,
  prefetchWindow = 2,
): { period: PeriodData | undefined; failed: boolean } {
  const [, force] = useState(0);
  const [failedIdx, setFailedIdx] = useState<number | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    if (!api) return;
    seq.current += 1;
    const mySeq = seq.current;
    setFailedIdx(null);
    const cached = api.getPeriod(periodIdx);
    if (!cached) {
      api.prefetch(periodIdx)?.then(
        () => {
          if (mySeq === seq.current) force((n) => n + 1);
        },
        (err: unknown) => {
          console.error(`period ${periodIdx} failed to load`, err);
          if (mySeq === seq.current) setFailedIdx(periodIdx);
        },
      );
    }
    // A failed prefetch needs no handling here: the period is fetched again
    // when it is shown.
    const ignore = () => {};
    for (let d = 1; d <= prefetchWindow; d += 1) {
      api.prefetch(periodIdx + d)?.catch(ignore);
      api.prefetch(periodIdx - d)?.catch(ignore);
    }
  }, [api, periodIdx, prefetchWindow]);
  const period = api?.getPeriod(periodIdx);
  return { period, failed: !period && failedIdx === periodIdx };
}
