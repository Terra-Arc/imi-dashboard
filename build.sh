#!/usr/bin/env bash
# Rebuild the web bundle and the bundled IMI sample dashboards.
#
# Run this whenever you touch web/src or tools/imi-dashboard and want fresh
# dashboards/single/ and dashboards/romania/ (plus any local extra entries,
# see below) to open or serve. By default imi-dashboard updates an existing
# output incrementally (only new/changed periods are reconverted); pass
# --rebuild to reconvert every period from scratch.
#
# Usage:
#     ./build.sh                  # build bundle + dashboards (region=roi)
#     ./build.sh --region domain  # full inversion domain (keep buffer cells)
#     ./build.sh --skip-build     # skip `vite build` (handy when only the
#                                 # converter changed)
#     ./build.sh --rebuild        # full reconvert (no incremental reuse)
#     ./build.sh --yes            # do not ask before removing periods that
#                                 # left the input (passed on to convert)
#
# Extra dashboards of your own data: list them in local_data/build.entries
# (gitignored), one input:output-name[:config] entry per line, paths relative
# to the repository root; blank lines and # comments are ignored, and an
# entry whose input folder is absent is skipped.
#
# Prereqs (one-time):
#     npm  --prefix web install
#     python -m venv .venv && source .venv/bin/activate
#     pip install -e tools/imi-dashboard
set -euo pipefail

cd "$(dirname "$0")"

REGION="roi"
DO_BUILD=1
REBUILD=0
YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)     REGION="$2"; shift 2 ;;
    --region=*)   REGION="${1#*=}"; shift ;;
    --skip-build) DO_BUILD=0; shift ;;
    --rebuild)    REBUILD=1; shift ;;
    --yes|-y)     YES=1; shift ;;
    -h|--help)
      sed -n '2,27p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Activate the venv if it exists and isn't already active. Falls back to
# whatever `imi-dashboard` is on $PATH (useful in CI).
if [[ -z "${VIRTUAL_ENV:-}" && -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if ! command -v imi-dashboard >/dev/null 2>&1; then
  echo "imi-dashboard not on PATH — install with: pip install -e tools/imi-dashboard" >&2
  exit 1
fi

if (( DO_BUILD )); then
  echo "==> Building web bundle (vite build)"
  npm --prefix web run build
else
  echo "==> Skipping vite build (--skip-build)"
fi

# Each input:output-name[:config] entry. No config = defaults taken from the
# IMI output (sectors, cadence). The Permian sample uses those defaults; the
# Romania sample uses the worked example config.
ENTRIES=(
  "sample_files/single_period:single"
  "sample_files/timeseries:romania:examples/config.example.yaml"
)

# Optional extra entries, in the same format, from a gitignored file.
EXTRA_ENTRIES="local_data/build.entries"
if [[ -f "$EXTRA_ENTRIES" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"                          # CRLF line endings
    line="${line%%#*}"                            # comments
    line="${line#"${line%%[![:space:]]*}"}"       # leading blanks
    line="${line%"${line##*[![:space:]]}"}"       # trailing blanks
    if [[ -n "$line" ]]; then
      ENTRIES+=("$line")
    fi
  done < "$EXTRA_ENTRIES"
fi

# One argument array per conversion, with the optional arguments appended,
# so that no possibly-empty array is expanded (bash 3.2 with `set -u` treats
# "${empty[@]}" as an unbound variable).
for entry in "${ENTRIES[@]}"; do
  IFS=: read -r src name config <<< "$entry"
  if [[ ! -d "$src" ]]; then
    echo "==> Skipping dashboards/$name — input folder $src not found" >&2
    continue
  fi
  args=(convert "$src" "dashboards/$name" --region "$REGION")
  if [[ -n "$config" && ! -f "$config" ]]; then
    # Not a fallback to the default config: that would publish the
    # dashboard with the wrong title, sectors and labels.
    echo "error: config $config of the entry for dashboards/$name not found" >&2
    exit 1
  fi
  if [[ -n "$config" ]]; then
    args+=(--config "$config")
  fi
  if (( REBUILD )); then
    args+=(--rebuild)
  fi
  if (( YES )); then
    args+=(--yes)
  fi
  echo "==> Converting $src → dashboards/$name (region=$REGION${config:+, config=$config})"
  imi-dashboard "${args[@]}"
done

echo
echo "Done. Preview a dashboard, e.g.:"
echo "    imi-dashboard serve dashboards/romania --open"
