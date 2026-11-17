import { existsSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

const OUT_DIR = "dist";

// The file that marks a folder as a built web app for the converter. The
// converter refuses an --app-bundle folder without it, copies exactly the files
// it lists, and refuses one whose manifestVersion is not the one it writes.
const APP_MARKER = "imi-dashboard-app.json";

/** Write APP_MARKER into the build's output folder once every file is in
 *  place (the files copied from public/ included, which is why the folder is
 *  listed from disk rather than from the bundle Rollup reports). */
function appMarker(): Plugin {
  return {
    name: "imi-dashboard-app-marker",
    apply: "build",
    closeBundle() {
      const root = join(import.meta.dirname, OUT_DIR);
      const files = readdirSync(root, { recursive: true, withFileTypes: true })
        .filter((e) => e.isFile())
        .map((e) => relative(root, join(e.parentPath, e.name)).split(sep).join("/"))
        .filter((f) => f !== APP_MARKER)
        .sort();
      const format = JSON.parse(readFileSync(join(import.meta.dirname, "src/data/format.json"), "utf-8"));
      const marker = { app: "imi-dashboard-web", manifestVersion: format.manifestVersion, files };
      writeFileSync(join(root, APP_MARKER), JSON.stringify(marker, null, 2) + "\n");
    },
  };
}

const THIRD_PARTY_LICENSES = "assets/THIRD-PARTY-LICENSES.md";

// Packages whose published build already contains their dependencies' code,
// so that the bundler sees one module and build.license lists only the
// package itself. MapLibre's dist/ inlines kdbush, earcut, geojson-vt,
// tiny-sdf, pbf and the rest of its runtime dependencies.
const PREBUNDLED = ["maplibre-gl"];

type PackageJson = {
  name: string;
  version: string;
  license?: string;
  author?: string | { name?: string };
  dependencies?: Record<string, string>;
};

/** The folder of package `name` as Node resolves it from `from`, or null. */
function packageDir(name: string, from: string): string | null {
  for (let dir = from; ; dir = dirname(dir)) {
    const candidate = join(dir, "node_modules", name);
    if (existsSync(join(candidate, "package.json"))) return candidate;
    if (dirname(dir) === dir) return null;
  }
}

/** The runtime dependencies of `name`, transitively, without type-only
 *  packages, each with its package.json and folder. */
function dependencyClosure(name: string, from: string): [PackageJson, string][] {
  const root = packageDir(name, from);
  if (root === null) return [];
  const seen = new Map<string, PackageJson>();
  const queue = Object.keys(readPackage(root).dependencies ?? {}).map((d) => [d, root] as const);
  while (queue.length > 0) {
    const [dep, parent] = queue.shift()!;
    if (dep.startsWith("@types/")) continue;
    const dir = packageDir(dep, parent);
    if (dir === null || seen.has(dir)) continue;
    const pkg = readPackage(dir);
    seen.set(dir, pkg);
    for (const d of Object.keys(pkg.dependencies ?? {})) queue.push([d, dir]);
  }
  return [...seen].map(([dir, pkg]) => [pkg, dir]);
}

function readPackage(dir: string): PackageJson {
  return JSON.parse(readFileSync(join(dir, "package.json"), "utf-8"));
}

/** The licence text a package ships, or a line naming its licence and
 *  author when it ships none. */
function licenceText(pkg: PackageJson, dir: string): string {
  const file = readdirSync(dir).find((f) => /^licen[cs]e(\.(md|txt))?$/i.test(f));
  if (file) return readFileSync(join(dir, file), "utf-8").trim();
  const author = typeof pkg.author === "string" ? pkg.author : pkg.author?.name;
  return `${pkg.license ?? "Licence not stated"}${author ? `, ${author}` : ""} (no licence file in the package; see its source).`;
}

/** Add to THIRD_PARTY_LICENSES the licences of the code that PREBUNDLED
 *  packages carry inside their own build, in the same layout as the
 *  entries build.license writes. */
function prebundledLicences(): Plugin {
  return {
    name: "imi-dashboard-prebundled-licences",
    apply: "build",
    enforce: "post",
    generateBundle: {
      order: "post",
      handler(_options, bundle) {
        const asset = bundle[THIRD_PARTY_LICENSES];
        if (!asset || asset.type !== "asset") {
          this.error(`${THIRD_PARTY_LICENSES} was not generated; check build.license`);
        }
        const sections = PREBUNDLED.flatMap((host) =>
          dependencyClosure(host, import.meta.dirname)
            .sort(([a], [b]) => a.name.localeCompare(b.name))
            .map(([pkg, dir]) =>
              `## ${pkg.name} - ${pkg.version} (${pkg.license ?? "unknown"}), inside ${host}\n\n${licenceText(pkg, dir)}\n`,
            ),
        );
        asset.source = `${String(asset.source).trimEnd()}\n\n${sections.join("\n")}`;
      },
    },
  };
}

export default defineConfig({
  // Relative asset paths, so a dashboard works at any URL path: at a static
  // host's root, under a subpath, or in any folder. (Opening index.html from
  // file:// still does not work: browsers block the data fetches there.)
  base: "./",
  plugins: [react(), prebundledLicences(), appMarker()],
  build: {
    outDir: OUT_DIR,
    sourcemap: false,
    target: "es2020",
    // One file with the licence of every bundled dependency (React, deck.gl,
    // MapLibre, the fonts' SIL OFL), shipped in every dashboard's assets/;
    // prebundledLicences adds the code inside MapLibre's own build.
    license: { fileName: THIRD_PARTY_LICENSES },
  },
  server: {
    port: 5173,
  },
});
