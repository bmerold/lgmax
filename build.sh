#!/usr/bin/env bash
# Full pipeline: decomp -> analysis -> a single self-contained page.
# Takes 6-8 minutes. Run it detached if your shell has a timeout.
set -euo pipefail
cd "$(dirname "$0")"

# Reproducible builds: pin the hash seed so set/dict iteration (a few greedy
# tie-breaks in the solver) is stable run to run, and the parallel solve is
# byte-identical to a serial one. LGMAX_WORKERS caps the per-starter pool.
export PYTHONHASHSEED=0

: "${LGMAX_POKEFIRERED:=$HOME/pokefirered}"
export LGMAX_POKEFIRERED
[ -d "$LGMAX_POKEFIRERED/src/data/pokemon" ] || {
  echo "Cannot find the pokefirered decompilation at $LGMAX_POKEFIRERED" >&2
  echo "  git clone https://github.com/pret/pokefirered ~/pokefirered" >&2
  echo "  (or set LGMAX_POKEFIRERED to wherever it lives)" >&2
  exit 1
}

mkdir -p data
[ -f data/species.json ] || python3 extract.py      # decomp -> data/*.json (version-agnostic)

# One game, two versions. extract.py tags every wild table FireRed/LeafGreen/Both;
# the rest of the pipeline picks a version with LGMAX_VERSION (lg|fr) and drops the
# other's exclusives, so the route, teams, trades, Game Corner and Dex all differ.
# Build each version through compact.py, snapshot its payload, then merge_versions.py
# folds the two into one payload (shared map art kept single-copy). LGMAX_VERSIONS
# overrides the set to build (e.g. "lg" to rebuild LeafGreen alone).
: "${LGMAX_VERSIONS:=lg fr}"
for V in $LGMAX_VERSIONS; do
  echo "===================== building $V ====================="
  export LGMAX_VERSION="$V"
  python3 build_graph.py  # wild graph for this version (filters the other's tables)
  # tour.py is the slow stage; its route is version-specific (catch stops differ).
  # LGMAX_REUSE_ROUTE=1 reuses data/route_<v>.json when iterating on the engine only.
  if [ -n "${LGMAX_REUSE_ROUTE:-}" ] && [ -f "data/route_${V}.json" ]; then
    cp "data/route_${V}.json" data/route.json
    echo "reusing data/route_${V}.json (LGMAX_REUSE_ROUTE set)"
  else
    python3 tour.py
    cp data/route.json "data/route_${V}.json"
  fi
  python3 sections.py     # solves every section, battles in ROUTE order
  python3 training.py     # grind calculator (version-keyed input hash)
  python3 optimize.py     # per-encounter rankings
  python3 economy.py      # money: prize income vs Game Corner / stone costs
  python3 render_maps.py  # map PNGs (incremental; this version's used maps)
  python3 compact.py      # array-encoded payload for this version
  cp data/payload.json "data/payload_${V}.json"
  python3 verify.py       # guard rails for this version; all must print OK
done
unset LGMAX_VERSION

python3 merge_versions.py  # data/payload_*.json -> one data/payload.json

python3 - <<'PY'
tpl = open("app_template.html").read()
open("app.html", "w").write(tpl.replace("__PAYLOAD__", open("data/payload.json").read()))
print("app.html written")
PY
