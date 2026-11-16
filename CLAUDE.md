# Working in this repo

LeafGreen Maximizer: a Python pipeline that turns the pret/pokefirered decompilation into one
self-contained `app.html`. Read `README.md` for the domain and the pipeline table; this file is
the working contract for changing the code.

## Cardinal rules

1. **Every fact comes from the decompilation, never a wiki.** Base stats, learnsets, evolutions,
   trainer parties, encounter tables, map geometry, item scripts, mechanics — all parsed from
   `$LGMAX_POKEFIRERED` (default `~/pokefirered`). If the ROM disagrees with received wisdom, the
   ROM wins, and a comment should cite the source file (e.g. `src/data/…` or a map's `scripts.inc`).
2. **`verify.py` is the safety net — it must print all OKs after every rebuild.** It encodes 90+
   executable invariants (route coverage/continuity, no illegal party, sight-line/aggro legality,
   Fly/Teleport/Dig legality,
   move/TM/HM legality, Moon-Stone accounting, prize-money/affordability, Teleport-carrier
   legality, double-battle flag round-trip and two-mon modeling, …). A new guard is the right way
   to lock in any bug a playthrough uncovers. Never
   weaken a guard to make it pass; fix the cause.
3. **Change → rebuild → verify → then commit.** Most edits require the full pipeline. Run
   `./build.sh` (10–15 min; runs the stages below and ends with `verify.py`). Commit and push only
   when the user asks; deploy the live site with `./deploy.sh`.

## Pipeline order (build.sh)

`extract.py` → (once, both versions) `wild_matchups.py` → `training.py` → per version: `build_graph.py` → `tour.py` → `evo_timing.py` → `sections.py` → `league_sets.py` → `optimize.py` → `economy.py` → `render_maps.py` →
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
- **Abilities: `abilities.py` is the registry; `engine.base_damage` owns the stat mults.** Every
  ability is a function in `abilities.py`, and the engine reads its effects through consumer helpers
  (`negates_damage`, `blocks_crit`, `accuracy_mult`, `immune_to_status`, …). The order-sensitive
  CalculateBaseDamage Attack/Defense mults (Huge/Pure Power, Hustle, Guts, Marvel Scale, Thick Fat,
  the Overgrow line) stay transcribed inline in `base_damage` — do **not** also route them through a
  helper, or they double-apply. New ability effect that fits the 1v1 model → add a consumer helper
  and call it from the engine; effect outside the model → still give the ability a documented
  `unmodeled` descriptor so the registry stays complete (`verify.py` checks that).
- **The per-starter solve runs in parallel, so its caches must not leak.**
  `sections.py` forks one worker per `(starter, mode)` solve (`_run_starters`; the
  three starters are independent — pass 1 compares them on even footing, pass 2
  gives each its own TM plan). The memoization that depends on that per-starter
  context — `_profile_cache`, `_threat_cache`, and optimize's move-pool/mon caches
  — is cleared at the top of every worker via `reset_solve_caches()`. Do **not**
  remove that reset or add a new solve cache that outlives one starter without
  clearing it there: a value cached under one starter must never be reused for
  another (this was a real bug the parallel port surfaced). Builds pin
  `PYTHONHASHSEED=0` and are byte-reproducible; `LGMAX_WORKERS` caps the pool and
  `LGMAX_REUSE_ROUTE=1` reuses `data/route.json` (skip `tour.py` only when routing
  inputs are unchanged).
- **The grind simulator runs once for both versions**, before the per-version loop
  (`sections.py` prices training from its output). Its fights come from the version-free wild
  tables; a version is only a filter (its areas, availability, trades), and an area both
  versions share is searched once. It writes `data/training_<v>.json` and skips itself when
  its inputs are unchanged; `LGMAX_TRAINING=1` forces a re-run. It must not read
  `build_graph` outputs from `data/` (they don't exist yet on a fresh build) — it gets them
  from a per-version subprocess.
- **Parties are chosen for least training** (`sections.OBJECTIVE`, default `"training"`;
  `LGMAX_OBJECTIVE=turns` restores fewest-turns). Each member is billed the grind turns
  (`training_cost.py`: obtain level from the map scripts, turns/level from the cached
  `data/training_<v>.json`) for the levels it is behind; a member fielded last section pays
  nothing. Pass 2 re-solves each run `LOOKAHEAD_PASSES` times, spreading a line's bill over the
  sections the previous solve used it in, and keeps the cheapest run. Who owes what lives in one
  place, `training_cost.Roster` (solver, guide and the verify guard's rule all match it). Faints
  stay a veto (pricing them in turns caused wipes); a stage that wipes is re-solved ranking by
  battles alone (`_solve_run`'s fallback). Pass 1 always runs the fewest-turns rule, so the
  one-time choices (fossil, Eevee, TM owners) never drift with the party objective.
- **Two approaches, one curve each.** The guide ships the maximalist solve (every section's
  best party, on the shared curve) and the **core approach** (`core.py`, sections.py pass 3,
  `sections.json["core"]["options"]`, payload `coreOptions`, the app's Approach picker + "Pick
  your six"). Each run offers up to `CORE_OPTIONS` distinct sixes that clear the League, picked
  backwards from it with every route HM learnable inside each and no legendary bird; each makes
  its own Eevee/fossil/Dojo choice (`core_commitments`) and is solved in full, and the player
  picks one (app `approach` = `"max"` or `"core:<i>"`, synced as an opaque string). A core is seated in every section once held (`solve_section(core=…)` — never pruned,
  swapped, or evicted for an HM carrier; it gives up a move instead). Its levels come from
  `core.CoreLedger` (trainer XP actually earned, pooled to the laggard), not the curve; a
  section the six can't clear at that level gets the cheaper of a specialist or a grind, and
  nothing else. Fly is required only once a core member can carry it. The user approved
  "one curve per approach"; the maximalist curve stays shared across all its combinations.
  `LGMAX_APPROACHES="max"` skips pass 3. The core guards live in verify.py
  ("the core approach" block); core runs also pass every party guard as `core/<mode>/<starter>`.
- **The matchup space is standalone (not in build.sh yet).** `matchups.py` tabulates every
  one-on-one fight (species × level × move vs every trainer Pokémon, plus the foe's four moves
  back) independent of version/starter/trades into `data/matchups.bin.gz` (~90 s). `mq.py`
  rebuilds `sections.profile`/`threat` from it — `python3 mq.py validate` must report 0
  mismatches after any engine change — and answers queries (`mq.py solo <species>`).
  The wild half (in build.sh, skipped when unchanged): `wild_matchups.py` →
  `data/wild_matchups.bin.gz` (~100–180 s), queried by
  `grind_space.py` (`validate` must report 0 mismatches). `training.py` reads its fights from
  it when present (it re-checks a sample against the engine first and refuses stale tables;
  `LGMAX_GRIND_ENGINE=1` forces the engine). Engine changes rebuild the wild tables (input hash).
  `best_grind` visits areas by an exact floor (1 turn per fight, no heal trips) and stops
  when none can win; keep that bound true if the time model changes.
- **The build checkpoints per step** (`data/.ckpt/<v>.<n>.<step>`). `./build.sh --resume`
  continues after the last finished step; a plain `./build.sh` starts fresh. Resume
  only when no code a finished step depends on has changed since.
- **The sync bundle shape is a second cross-file contract.** `app_template.html`'s `syncBundle`
  (`{v, done, play, starter, trading, approach, dex, ts}`) and `sync/worker.js`'s `merge` must agree. The
  rules that keep two devices from clobbering each other: `done` is always **unioned**, `dex`
  (manual Dex overrides, `{species: bool}`) is a **per-key union with the newer `ts` winning a
  conflict**, and `play`/`starter`/`trading`/`approach` are taken from the **newer `ts`**. Change one side and
  change the other — and adding a field means redeploying the Worker (`cd sync && npx wrangler
  deploy`), or the server drops it. `syncTouch` is gated by `syncReady`/`syncApplying` so boot and
  merge-driven re-renders don't bump the clock — don't remove those guards.

- **Evolution timing is opt-in per stage.** `evo_timing.py` decides which evolutions to hold
  back for a pre-evolution move (weighing the weaker form's boss fights against the move) and
  writes `data/evo_timing_<v>.json`; `progression.use_evo_timing()` adopts it. `sections.py`,
  `economy.py`, `compact.py` and `verify.py` call it; the route and the grind simulator must not
  (they run before the plan exists). Read LEVEL evolutions through `P.evolve_level`, never the raw
  `param`, or a held line evolves early. A held evolution is the line's source even where the
  evolved form is catchable (`full_availability`).
- **Moves a Pokémon learns itself are never TM-gated.** `optimize.own_moves` (level-up as it
  actually levelled, plus tutors) bypasses the one-copy TM owner rule; an Alakazam's level-up
  Psychic must not depend on who holds TM29. A pre-evolution learns its evolution-level moves
  before evolving (`_levelup_pool`'s `in_battle`).
- **Game Corner TMs are a store** (`tms.GAME_CORNER_STORE`): the prize room sets no flag, so
  Thunderbolt, Ice Beam, Flamethrower, Shadow Ball and Iron Tail are unlimited from Celadon;
  `economy.py` prices each copy.
- **The League tab** (`league_sets.py`, ~20 min/version, cached on an inputs hash;
  `LGMAX_FORCE_LEAGUE=1` reruns, `LGMAX_LEAGUE=0` skips) lists legal sixes that clear the Elite
  Four: a generous 1v1 screen (best learnable move, pre-evolution moves, birds allowed, one-copy TMs
  handed out once) then the full League sim raised until it clears. Payload `leagueSets` rows are
  positional (`[names, clearLevel, faints, turns, screenLevel, tms, flags]`, see compact.py) and
  read by index in `renderLeague`. It is League-only: a six there need not be route-viable.

## Quality workflow

- Before pushing a non-trivial change, run **`/code-review`** on the diff.
- Keep functions small and the `verify.py` guards green; those two habits carry most of the load.
