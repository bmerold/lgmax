#!/usr/bin/env bash
# Full pipeline: decomp -> analysis -> a single self-contained page.
# Takes 10-15 minutes (less when the wild tables and grind are unchanged). Run it detached if your shell has a timeout.
#
#   ./build.sh            fresh build (clears checkpoints, runs every step)
#   ./build.sh --resume   pick up after the last step that finished (LGMAX_RESUME=1 too)
#   LGMAX_TRAINING=1 ./build.sh   force the grind simulator (training.py) to re-run
#
# Pausing: stop the build any way you like (Ctrl-C, kill). Every step writes a
# checkpoint when it finishes, so `--resume` skips straight to the step that was
# interrupted and re-runs only that one and what follows. (Ctrl-Z / SIGSTOP still
# suspends in place, and SIGCONT carries on.) Resume trusts the checkpoints: if you
# edit code a finished step depends on, do a fresh build instead.
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

[ "${1:-}" = "--resume" ] && LGMAX_RESUME=1

mkdir -p data
[ -f data/species.json ] || python3 extract.py      # decomp -> data/*.json (version-agnostic)

# ---- checkpoints -------------------------------------------------------------
# One marker per finished step under data/.ckpt/ (data/ is gitignored). The
# stages hand off through shared files (data/route.json, sections.json,
# payload.json, ...) that the next version overwrites, and the build runs
# versions in order, so "every step before the first missing marker finished
# last" is exactly the state those shared files are in -- resume needs nothing
# more. A step killed mid-write never gets its marker, so it re-runs whole.
CKPT=data/.ckpt
if [ -n "${LGMAX_RESUME:-}" ]; then
  echo "resuming: $(ls "$CKPT" 2>/dev/null | wc -l | tr -d ' ') step(s) already done"
else
  rm -rf "$CKPT"
fi
mkdir -p "$CKPT"

step() {   # step <name> <command...>: run unless its checkpoint exists
  local name="$1"; shift
  if [ -f "$CKPT/$name" ]; then
    echo "[skip] $name (checkpoint)"
    return 0
  fi
  echo "[run ] $name"
  "$@"
  touch "$CKPT/$name"
}

route_step() {   # tour.py is the slow stage; LGMAX_REUSE_ROUTE=1 reuses the last route
  if [ -n "${LGMAX_REUSE_ROUTE:-}" ] && [ -f "data/route_${V}.json" ]; then
    cp "data/route_${V}.json" data/route.json
    echo "reusing data/route_${V}.json (LGMAX_REUSE_ROUTE set)"
  else
    python3 tour.py
    cp data/route.json "data/route_${V}.json"
  fi
}

# The grind simulator (training.py) runs ONCE for both versions, before the
# per-version loop: its fights come from the version-free wild matchup tables
# (wild_matchups.py), and a version is only a filter (which areas exist, when a
# species can first be held, which are trades). Both steps skip themselves when
# their inputs are unchanged; LGMAX_TRAINING=1 forces the grind to re-run.
# sections.py prices training from data/training_<v>.json, so this comes first.
training_all() {
  if [ -n "${LGMAX_TRAINING:-}" ]; then
    LGMAX_FORCE_TRAINING=1 python3 training.py
  else
    python3 training.py
  fi
}
# The downstream stages read data/training.json; restore this version's copy.
training_step() {
  cp "data/training_${V}.json" data/training.json
}

snapshot() { cp "$1" "$2"; }

# The League-sets analysis (league_sets.py) reuses its last result when nothing it
# reads has changed; LGMAX_LEAGUE=0 skips it (the app's League tab then shows the
# last result, if any).
league_step() {
  if [ "${LGMAX_LEAGUE:-1}" = "0" ]; then
    echo "league sets: skipped (LGMAX_LEAGUE=0)"
  else
    python3 league_sets.py
  fi
}

# One game, two versions. extract.py tags every wild table FireRed/LeafGreen/Both;
# the rest of the pipeline picks a version with LGMAX_VERSION (lg|fr) and drops the
# other's exclusives, so the route, teams, trades, Game Corner and Dex all differ.
# Build each version through compact.py, snapshot its payload, then merge_versions.py
# folds the two into one payload (shared map art kept single-copy). LGMAX_VERSIONS
# overrides the set to build (e.g. "lg" to rebuild LeafGreen alone).
: "${LGMAX_VERSIONS:=lg fr}"
step "0.wild"     python3 wild_matchups.py   # wild matchup tables (version-free)
step "0.training" training_all               # grind itineraries, both versions
for V in $LGMAX_VERSIONS; do
  echo "===================== building $V ====================="
  export LGMAX_VERSION="$V"
  step "$V.1.graph"    python3 build_graph.py   # wild graph (filters the other version's tables)
  step "$V.2.route"    route_step               # route is version-specific (catch stops differ)
  step "$V.2b.evotiming" python3 evo_timing.py # which evolutions to hold back for a move
  step "$V.3.sections" python3 sections.py      # solves every section, battles in ROUTE order
  step "$V.3b.league"  league_step              # League-clearing sixes (cached; ~20 min when inputs change)
  step "$V.4.training" training_step            # this version's grind itineraries
  step "$V.5.optimize" python3 optimize.py      # per-encounter rankings
  step "$V.6.economy"  python3 economy.py       # money: prize income vs Game Corner / stone costs
  step "$V.7.maps"     python3 render_maps.py   # map PNGs (incremental; this version's used maps)
  step "$V.8.compact"  python3 compact.py       # array-encoded payload for this version
  step "$V.9.snapshot" snapshot data/payload.json "data/payload_${V}.json"
  step "$V.10.verify"  python3 verify.py        # guard rails for this version; all must print OK
done
unset LGMAX_VERSION

python3 merge_versions.py  # data/payload_*.json -> one data/payload.json

python3 - <<'PY'
tpl = open("app_template.html").read()
open("app.html", "w").write(tpl.replace("__PAYLOAD__", open("data/payload.json").read()))
print("app.html written")
PY
