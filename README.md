# LeafGreen Maximizer

### ▶ Play it live: **https://lgmax.arcane-collectibles.com/**

A step-by-step companion for an efficient, 100%-completion playthrough of Pokémon
LeafGreen. Open it on your phone beside the game: it tells you exactly where to walk,
which fights are next, the smallest party that clears each stretch, who to teach the
game's one-copy TMs, and — for every species — where to grind it fastest. Nothing is
hand-authored trivia; it's all computed from the game itself.

It's for the kind of player who wants to move through the whole game deliberately — no
backtracking, no wasted grinding, nothing missed — and for anyone curious what falls out
when you treat a 20-year-old cartridge as a dataset and a solver problem.

One self-contained page (~14 MB), installable, works offline, and syncs your progress
across devices with no account.

---

## What makes it interesting

**It's compiled, not written.** Every fact comes from parsing the
[pret/pokefirered](https://github.com/pret/pokefirered) decompilation — base stats,
learnsets, TM/HM compatibility, evolutions, the type chart, trainer rosters, wild-encounter
tables, item and evolution scripts, healing spots, and the map layouts down to per-tile
collision, warps and encounter ground. The guide is a *build artifact of the ROM's own
source*, so it can't drift from the game. Where a wiki disagreed with the ROM — Bulbapedia
lists Harden on Brock's Onix; the ROM doesn't — the ROM won.

**A from-scratch Gen 3 battle engine.** `engine.py` reimplements the Game Boy Advance's own
`CalculateBaseDamage`: the type-based physical/special split, badge stat boosts, the exact
`gStatStageRatios` table, Gen 3's crit rule, per-ability multipliers, the obedience roll,
and the capture and encounter-rate math. Every one of the 923 encounters is solved with the
cartridge's arithmetic and an exact turn-by-turn DP, not an approximation — then the smallest
party that clears each section is found by simulating it end to end with HP and PP carried
across the whole stretch.

**The whole game as one gated graph, then routed.** `world.py` builds a tile-level model of
Kanto and the Sevii Islands — collision, ledges, spin floors, water currents, warps, map
connections, HM obstacles, every gate that a badge or story flag opens — and `tour.py` runs a
travelling-salesman pass over it (nearest-neighbour seed, 2-opt/Or-opt, story-precedence
constraints) to produce one continuous ~33,000-step walk that collects every item ball and
hidden item, fights every trainer, and catches every newly available species, drawn back onto
the rendered maps.

**An executable specification.** `verify.py` encodes **86 invariants the guide must satisfy**
— route coverage and continuity, no illegal party, line-of-sight/aggro legality, HM/TM and
Fly/Teleport legality, in-game-trade sequencing, wild-lead legality, Moon-Stone accounting,
prize-money affordability — and the build fails loudly if any one breaks. Every bug a real
playthrough surfaces becomes a new guard, so the output only gets more correct over time. It's
the discipline of a compiler's test suite pointed at a game guide.

**A time model grounded in the ROM.** The training planner estimates *real minutes*, not
hand-waved "levels," by reading the game's own mechanics: the per-step wild-encounter dice roll
and its ramping pity buff (`src/wild_encounter.c`), the fishing minigame's state machine — a
flat 50% bite chance and dot-game rounds that scale by rod, timed in frames
(`src/field_player_avatar.c`) — walking distance at running speed, and Pokémon-Center
round-trips found by BFS on the same tile graph. Spots are gated by whether a mon actually
survives there, so a frail catch is never sent somewhere it faints.

**Reproducible, parallel, self-contained.** The three starter solves fork across processes
with strictly isolated memoization caches and a pinned hash seed, so the build is byte-for-byte
reproducible. The result packs into array-encoded payloads (~25 MB → ~5 MB) and ships as a
single offline-capable page with cross-device sync through a tiny Cloudflare Worker — an
anonymous key, a conflict-resolving merge, no account and no tracking.

---

## What it does

| | |
|---|---|
| **Encounters** | 923 encounters (gym leaders, the Elite Four and Champion fought twice, 566 trainer battles including VS Seeker rematches, 319 wild areas), each solved for which level-appropriate Pokémon clears it fastest, with per-opponent counters |
| **Section parties** | the smallest party that clears each section, simulated end to end **with no items** — HP and PP spent across the whole stretch, HMs carried, and a running list of what to keep levelled for later. Sections are cut at every full heal (a Pokémon Center walked past, or an in-dungeon healing spot), so a PP bar never quietly spans a heal |
| **TM plan** | 43 of the 49 TMs exist in exactly one copy; this decides who gets each one, judged over the whole run rather than the first fight it helps |
| **Choices** | starter, fossil, Fighting Dojo, Eevee stone, Game Corner prize — each ranked over whole evolution lines |
| **Training** | per species, where to grind it fastest at every level to 100, and which move to use against each wild Pokémon there — effective time modelled down to walking, fishing casts and Center trips. Two itineraries: **in-journey** (only what you've reached) and **post-game** (everything unlocked) |
| **Play mode** | the default screen: one viewport, no scrolling — the current objective on top, the party (game sprites, tap for moves and PP), the current map fitted to fill the view with the walk and numbered stops, and this step's battle plan below; floor changes, doors, ferries and flights are their own steps |
| **The route** | one continuous ~33,000-step walk through the whole game — every item ball, hidden item, trainer, one-off and first-catch, ordered by a travelling-salesman pass over the real tile graph and drawn onto the maps |
| **Catching & Dex** | the GBA's own capture math per target (catch rate, ball, HP, status) and legendary playbooks, plus a National Dex that registers each species only when you check its own obtain step — catch, evolve or gift — so completion reflects what you've actually done, not an auto-chain |
| **Cross-device sync** | check-off progress and play position follow you between devices via an anonymous **sync key** — no account, no personal data. Backed by a free Cloudflare Worker (see [`sync/`](sync/README.md)); an offline copy-paste **sync code** works with no backend at all |
| **Money** | prize-money income over the whole run (Gen 3's `4 × last-mon level × class value`) vs what it can buy — Celadon stones and the Game Corner's coin-only Pokémon — each with the earliest stage the run can afford it |

Toggles for **starter** and **trading on/off** re-solve the recommendations rather than
filtering them.

## The ruleset

- **Objective:** fewest expected turns to KO, tie-broken by damage taken.
- **Levels:** natural playthrough, no grinding. One player level per section.
- **No in-battle items** on the player's side, ever. Trainer Full Restores *are* counted.
- Player specimen: 15 IVs, 0 EVs, neutral nature.
- LeafGreen only. Trade evolutions are available behind the trading toggle.

## Running it

```sh
git clone https://github.com/pret/pokefirered ~/pokefirered
export LGMAX_POKEFIRERED=~/pokefirered      # optional; this is the default
./build.sh
```

Python 3.10+, no third-party packages. `build.sh` runs the whole pipeline and finishes with
`verify.py`, which must print **87 OKs**. The output is `app.html` — one self-contained file,
no server needed. The per-starter solves run in parallel (one process each) and the build pins
`PYTHONHASHSEED=0`, so it's byte-reproducible. Two slow stages reuse prior work when their
inputs are unchanged: the map-geometry TSP skips with `LGMAX_REUSE_ROUTE=1` (set it only when
`tour.py`/`world.py`/routing inputs are unchanged), and the grind itineraries auto-skip via an
input hash (reused unless the grind code or its data changed; `LGMAX_FORCE_TRAINING=1` forces a
rebuild). An edit to the app, guards or section solver reuses both, turning a rebuild into the
~2-minute path.

`./deploy.sh` publishes the current `app.html` to
**https://lgmax.arcane-collectibles.com/** (GitHub Pages, `gh-pages` branch, kept at a single
amended commit so the page never piles up in history).

## Pipeline

| File | Role |
|---|---|
| `extract.py` | decomp → normalized JSON in `data/` |
| `engine.py` | Gen 3 stat + damage engine, trainer party realization, the turn DP, obedience |
| `abilities.py` | every Gen 3 ability as a function; damage immunities, crit block, accuracy and status immunities the engine consults |
| `progression.py` | story-stage model, map→section gating, species availability |
| `build_graph.py` | encounter graph, TM/HM availability by section |
| `route_order.py` | the order you actually meet trainers, from map geometry |
| `walking.py` | wild-battle load: the grass/cave tiles a path really enters → encounters fought |
| `capture.py` | the ROM capture formula (shakes, ball and status multipliers) |
| `tms.py` | TM supply from the item scripts; which moves are one-copy-only |
| `hms.py` | field obstacles per map → which HMs each section demands |
| `constraints.py` | evolution lines, one-of groups, run-wide commitments, outgrown forms |
| `optimize.py` | per-encounter optimizer (scalar screen → exact turn DP), double-battle model |
| `economy.py` | prize-money income by stage vs the run's purchases and their affordability |
| `world.py` | the game as one walkable graph: every tile, ledge, spin floor, warp, ferry and elevator, stage-gated |
| `tour.py` | the completionist route: a gated travelling-salesman pass that collects every item, fights every trainer, catches every new species |
| `training.py` | the grind calculator: fastest wild spot per species per level, real-time-modelled, survival-gated, in-journey and post-game |
| `render_maps.py` | renders each section's maps to PNG from the decomp's tilesets/layouts, and pins every trainer and interacted NPC to the tile its object event stands on |
| `sections.py` | two-pass per-section party optimizer + choice evaluation |
| `compact.py` | array-encoded payload (~25 MB → ~5 MB) |
| `verify.py` | **87 guard rails; all must pass after every rebuild** |
| `setup_study.py` | standalone: does setting up beat hit-and-switch? (it does not) |

`sections.py` runs before `optimize.py` because it writes `data/commitments.json`, which the
encounter rankings read so the two agree about the run's one-time choices.

## Some things the ROM settled

- **Gen 3 splits physical/special by TYPE**, not by move. Jynx's Ice Punch runs off Special Attack.
- **Obedience only applies to traded Pokémon**, capped at 10/30/50/70 by badges 2/4/6/8. It bites
  in exactly one place: Erika's gym, level 31 against a cap of 30.
- **TMs are consumed on teach**; HMs are not. 43 TMs have exactly one copy.
- **An HM cannot be overwritten** — the Move Deleter in Fuchsia is the only way out of the slot, so
  HMs taught before then stay stuck.
- **A Kanto Pokémon whose evolution is a Johto-or-later form** (Crobat, Blissey…) cannot evolve
  until Prof. Oak upgrades the Pokédex to National after the Elite Four — the model gates them there.
- **All four trade-evolution items** (Metal Coat, Dragon Scale, King's Rock, Up-Grade) are
  post-game Sevii item balls, so Porygon2, Steelix, Kingdra, Politoed and Slowking are post-game.
- **Route 3 does not touch Mt. Moon** — it runs up into the west end of Route 4, where both the
  Pokémon Center and the cave mouth are.
- **A caught Pokémon only knows its last four moves in learn order**, per the game's own capture
  routine — so a wild Dugtrio's level-1 Tri Attack, and an evolved form's level-1-only moves
  (Gyarados's Thrash), are both crowded out and exist only through the Two Island Move Maniac.

See `docs/` for the working notes behind each of these.

## Known, deliberate limitations

- Stat stages ARE modelled in the damage calc on both sides — the exact `gStatStageRatios` table,
  with Gen 3's crit rule (a crit ignores the attacker's own Attack drops and the defender's Defense
  boosts). **Intimidate** is applied at fight entry. What's not yet modelled: the planner doesn't
  proactively *use* setup moves (Swords Dance, Calm Mind…), and the opponent AI doesn't throw stat
  moves (Leer, Screech) — both sides open at neutral stages unless an entry ability says otherwise.
  The setup-on-your-side payoff was measured at about one turn across the whole game
  (`docs/setup-moves-study.md`).
- Secondary effects of damaging moves ARE folded in, as expected value on both sides: flinch (only
  when faster), freeze with its 20% thaw, paralysis's 25% full-para, burn's attack halving and chip,
  poison chip, and confusion. Deliberate status *moves* (Thunder Wave, Sleep Powder) are still not
  used or faced. No held items.
- Abilities are modeled where they touch a 1v1 damage/turn calc: type immunities and absorptions,
  crit prevention, accuracy (Compound Eyes, Hustle), the CalculateBaseDamage stat mults (Huge/Pure
  Power, Guts, Thick Fat, Marvel Scale, the Overgrow line) and status immunities. `abilities.py`
  implements every Gen 3 ability as a function; the ones outside this model — weather, switch-in
  tricks (Intimidate, Trace), contact effects (Static, Flame Body), Pressure — are present and
  documented but not simulated. Lightning Rod is correctly a no-op in single battles.
- Thrash, Outrage and Petal Dance are never recommended: the 2–3-turn lock-in and the confusion
  after are not modeled, and a plan can't steer a move that refuses orders.
- Trainer AI is "their single best damaging move", close to but not identical to
  `AI_SCRIPT_CHECK_BAD_MOVE`.
- **Double battles** (the 26 paired-NPC fights the ROM flags with `.doubleBattle = TRUE`) are
  recognized and given a two-vs-two recommendation: the encounter card names the **pair** to lead
  with, each with the foe it answers, scored by a round-based model where all four Pokémon resolve
  in speed order (`optimize.double_plan`). Out of scope for now: spread moves (Surf/Earthquake
  hitting both), the exact turn distribution, and the section simulator's HP/PP ledger, which still
  walks a double's two foes one at a time under Gen 3's free Shift switches.
- Handovers are searched two segments deep; the party builder is greedy, so individual section
  numbers are not monotonic in the size of the candidate pool.
- **Teleport and Fly are modelled as a return to the *nearest open* Pokémon Center**, not the exact
  one the game would send you to. Because the route heals as it passes each Center, the nearest open
  one is almost always that spot anyway. Teleport is treated like an optional HM — a spare slot or a
  dedicated carrier takes it, never a sacrificed attack.
