# Working in this repo

LeafGreen Maximizer: a Python pipeline that turns the pret/pokefirered decompilation into one
self-contained `app.html`. Read `README.md` for the domain and the pipeline table; this file is
the working contract for changing the code.

## Cardinal rules

1. **Every fact comes from the decompilation, never a wiki.** Base stats, learnsets, evolutions,
   trainer parties, encounter tables, map geometry, item scripts, mechanics — all parsed from
   `$LGMAX_POKEFIRERED` (default `~/pokefirered`). If the ROM disagrees with received wisdom, the
   ROM wins, and a comment should cite the source file (e.g. `src/data/…` or a map's `scripts.inc`).
2. **`verify.py` is the safety net — it must print all OKs after every rebuild.** It encodes 38
   executable invariants (route coverage/continuity, no illegal party, sight-line/aggro legality,
   move/TM legality, Moon-Stone accounting, …). A new guard is the right way to lock in any bug a
   playthrough uncovers. Never weaken a guard to make it pass; fix the cause.
3. **Change → rebuild → verify → then commit.** Most edits require the full pipeline. Run
   `./build.sh` (6–8 min; runs the stages below and ends with `verify.py`). Commit and push only
   when the user asks; deploy the live site with `./deploy.sh`.

## Pipeline order (build.sh)

`extract.py` → `build_graph.py` → `tour.py` → `sections.py` → `optimize.py` → `render_maps.py` →
`compact.py` → inject into `app_template.html` → `verify.py`. Each stage reads the previous stage's
JSON in `data/`. `tour.py` runs before `sections.py`, so `sections.py` may read `data/route.json`.

## Conventions the code already follows — keep them

- **Comments say *why*, and cite the decomp.** Never restate what the code does; explain the game
  mechanic or the source it encodes. This is the house style — match it.
- **Name for intent** (`steps_per_encounter`, `reach_tile`, `dex_evolutions`). Magic numbers get a
  named constant with a comment (`OFF_ROUTE_PENALTY`, `DIG_COST`).
- **One job per module** (the pipeline stages). Prefer a small pure helper over widening a hot
  function; the fragile bugs this session hid in the longest functions.

## Fragile spots — handle with care

- **The payload is positional arrays.** `compact.py` packs each record as a bare list
  (`[kind, what, map, x, y, …]`) and `app_template.html` reads it by index (`s[11]`, `s[13]`).
  This is an implicit contract across two files and two languages: when you add a field, append it
  at the **end** of the packer AND update every reader index, and re-check the field-map comment
  above each packer. Off-by-one here is silent.
- **`sections.py` is the largest module and does the most** (moveset choice, HM assignment, the
  section simulator, dex/wild plans). Add to it carefully; a new concern probably wants its own
  function, and the `assign_hms` loop mutates a shared `uncovered` list — guard every `.remove`.
- **`app_template.html` is authored, not generated.** `app.html`/`data/` are build outputs and are
  gitignored; commit source (`app_template.html`, the `.py` files), never the built artifacts.
- **The sync bundle shape is a second cross-file contract.** `app_template.html`'s `syncBundle`
  (`{v, done, play, starter, trading, ts}`) and `sync/worker.js`'s `merge` must agree. The rules
  that keep two devices from clobbering each other: `done` is always **unioned**, `play`/`starter`/
  `trading` are taken from the **newer `ts`**. Change one side and change the other. `syncTouch` is
  gated by `syncReady`/`syncApplying` so boot and merge-driven re-renders don't bump the clock —
  don't remove those guards.

## Quality workflow

- Before pushing a non-trivial change, run **`/code-review`** on the diff.
- Keep functions small and the `verify.py` guards green; those two habits carry most of the load.
