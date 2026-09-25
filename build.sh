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
[ -f data/species.json ] || python3 extract.py      # decomp -> data/*.json
[ -f data/encounters.json ] || python3 build_graph.py

# tour.py is the slow stage (minutes, dominated by the post-game route) and its
# output depends only on map geometry + gating, never on the damage/party model.
# LGMAX_REUSE_ROUTE=1 reuses an existing data/route.json to skip it while iterating
# on the engine; leave it unset (the default) whenever tour.py, world.py or any
# routing input changed, so the route is recomputed.
if [ -n "${LGMAX_REUSE_ROUTE:-}" ] && [ -f data/route.json ]; then
  echo "reusing existing data/route.json (LGMAX_REUSE_ROUTE set)"
else
  python3 tour.py       # the completionist route: every item, trainer, catch
fi
python3 sections.py     # solves every section, battles in ROUTE order
python3 training.py     # grind calculator: fewest turns/level per party mon
python3 optimize.py     # per-encounter rankings (reads commitments.json)
python3 economy.py      # money: prize income vs Game Corner / stone costs
python3 render_maps.py  # map PNGs from the decomp's tilesets (incremental)
python3 compact.py      # array-encoded payload

python3 - <<'PY'
tpl = open("app_template.html").read()
open("app.html", "w").write(tpl.replace("__PAYLOAD__", open("data/payload.json").read()))
print("app.html written")
PY

python3 verify.py       # guard rails; all must pass
