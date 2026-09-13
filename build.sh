#!/usr/bin/env bash
# Full pipeline: decomp -> analysis -> a single self-contained page.
# Takes 6-8 minutes. Run it detached if your shell has a timeout.
set -euo pipefail
cd "$(dirname "$0")"

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

python3 sections.py     # writes wildLoad / tmSupply / commitments, solves every section
python3 optimize.py     # per-encounter rankings (reads commitments.json)
python3 compact.py      # array-encoded payload

python3 - <<'PY'
tpl = open("app_template.html").read()
open("app.html", "w").write(tpl.replace("__PAYLOAD__", open("data/payload.json").read()))
print("app.html written")
PY

python3 verify.py       # 27 guard rails; all must pass
