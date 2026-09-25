# LeafGreen Maximizer

Every trainer battle and wild encounter in Pokémon LeafGreen, ordered by when you can reach it and
solved with the Game Boy Advance's own damage formula — plus a recommended party for each stretch
of the game, with PP, HP, HMs and single-use TMs counted across it.

**Everything is extracted from the [pret/pokefirered](https://github.com/pret/pokefirered)
decompilation, never from a wiki.** Base stats, movesets, learnsets, TM/HM compatibility,
evolutions, the type chart, trainer rosters, wild encounter tables, item scripts, healing spots,
map tile attributes, the obedience roll and the damage formula itself all come out of the C and
the map JSON. Where Bulbapedia disagreed with the ROM — it lists Harden on Brock's Onix; the ROM
does not — the ROM won.

## Running it

```sh
git clone https://github.com/pret/pokefirered ~/pokefirered
export LGMAX_POKEFIRERED=~/pokefirered      # optional; this is the default
./build.sh
```

Python 3.10+, no third-party packages. `build.sh` runs the whole pipeline and finishes with
`verify.py`, which must print 64 OKs. The output is `app.html` — one self-contained file, ~10 MB,
no server needed. The per-starter solves run in parallel (one process each) and the build pins
`PYTHONHASHSEED=0`, so it's byte-reproducible; `route.json` (map-geometry TSP) is the one slow
stage, so `LGMAX_REUSE_ROUTE=1 ./build.sh` reuses it and turns an engine-only rebuild into ~2
minutes. Set it only when `tour.py`/`world.py`/routing inputs are unchanged.

`./deploy.sh` publishes the current `app.html` to
**https://lgmax.arcane-collectibles.com/** (GitHub Pages, `gh-pages` branch, kept at a
single amended commit so the page never piles up in history).

## What it does

| | |
|---|---|
| **Encounters** | 923 encounters (20 boss fights, 566 trainer battles including VS Seeker rematches, 319 wild areas) across 35 sections, each solved for which level-appropriate Pokémon clears it fastest, with per-opponent counters |
| **Section parties** | the smallest party that clears each section, simulated end to end **with no items** — HP and PP spent across the whole stretch, HMs carried, and a running list of what to keep levelled for later. Sections are presented cut at every full heal (a Pokémon Center walked past, or an in-dungeon healing spot), so a PP bar never quietly spans a heal |
| **TM plan** | 43 of the 49 TMs exist in exactly one copy; this decides who gets each one, judged over the whole run rather than the first fight it helps |
| **Choices** | starter, fossil, Fighting Dojo, Eevee stone, Game Corner prize — each ranked on whole evolution lines |
| **Play mode** | the default screen: one viewport, no scrolling — the current objective on top, the party (game sprites, tap for moves and PP), the current map fitted to fill the view with the walk and numbered stops, and this step's battle plan below; floor changes, doors, ferries and flights are their own steps |
| **The route** | one continuous ~25,000-step walk through the whole game — every item ball, hidden item, trainer, one-off and first-catch, ordered by a travelling-salesman pass over the real tile graph and drawn onto the maps |
| **Cross-device sync** | check-off progress and play position follow you between devices via an anonymous **sync key** — no account, no personal data. Backed by a free Cloudflare Worker (see [`sync/`](sync/README.md)); an offline copy-paste **sync code** works with no backend at all |
| **Dex** | a National Dex of every species a run can obtain. Each entry registers when you check its own obtain step — the catch stop, the evolve step (shown as a from→to card with a checkbox, like the trade cards), the gift/static event — so completion reflects what you've actually done, not an auto-chain. Kanto and off-route (Game Corner, prize) counts, filters, and a manual tick to override any entry |
| **Money** | prize-money income over the whole run (Gen 3's `4 × last-mon level × class value`) vs what it can buy — Celadon stones and the Game Corner's coin-only Pokémon (Pinsir/Dratini/Porygon), each with the earliest stage the run can afford it. The Game Corner set alone is ~¥272k of the run's ~¥537k, so affordability is a real constraint, not a given |

Toggles for **starter** and **trading on/off** re-solve the recommendations rather than filtering
them.

## The ruleset

- **Objective:** fewest expected turns to KO, tie-broken by damage taken.
- **Levels:** natural playthrough, no grinding. One player level per section.
- **No in-battle items** on the player's side, ever. Trainer Full Restores *are* counted.
- Player specimen: 15 IVs, 0 EVs, neutral nature.
- LeafGreen only. Trade evolutions are available behind the trading toggle.

## Pipeline

| File | Role |
|---|---|
| `extract.py` | decomp → normalized JSON in `data/` |
| `engine.py` | Gen 3 stat + damage engine, trainer party realization, the turn DP, obedience |
| `abilities.py` | every Gen 3 ability as a function; damage immunities, crit block, accuracy and status immunities the engine consults |
| `progression.py` | 35-section story model, map→section gating, species availability |
| `build_graph.py` | encounter graph, TM/HM availability by section |
| `route_order.py` | the order you actually meet trainers, from map geometry |
| `walking.py` | wild-battle load: tiles walked per map → encounters fought |
| `tms.py` | TM supply from the item scripts; which moves are one-copy-only |
| `hms.py` | field obstacles per map → which HMs each section demands |
| `constraints.py` | evolution lines, one-of groups, run-wide commitments, outgrown forms |
| `optimize.py` | per-encounter optimizer (scalar screen → exact turn DP) |
| `economy.py` | prize-money income by stage vs the run's purchases (stones, Game Corner) and their affordability |
| `world.py` | the game as one walkable graph: every tile, ledge, spin floor, warp, ferry and elevator, stage-gated |
| `tour.py` | the completionist route: a gated travelling-salesman pass that collects every item, fights every trainer, catches every new species |
| `render_maps.py` | renders each section's maps to PNG from the decomp's tilesets/layouts, and pins every trainer to the tile its object event stands on |
| `sections.py` | two-pass per-section party optimizer + choice evaluation |
| `compact.py` | array-encoded payload (~25 MB → ~5 MB) |
| `verify.py` | **71 guard rails; all must pass after every rebuild** |
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
- **The Move Maniac on Two Island relearns level-up moves only**, for a Big Mushroom.
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
  boosts). **Intimidate** is applied at fight entry, lowering the foe's Attack. What's not yet
  modelled: the planner does not proactively *use* setup moves (Swords Dance, Calm Mind…), and the
  opponent AI doesn't throw stat moves (Leer, Screech, Sand-Attack) — both sides still open at
  neutral stages unless an entry ability says otherwise. The setup-on-your-side payoff was measured
  at about one turn across the whole game (`docs/setup-moves-study.md`).
- Secondary effects of damaging moves ARE folded in, as expected value on both sides: flinch
  (only when faster), freeze with its 20% thaw, paralysis's 25% full-para, burn's attack halving
  and chip, poison chip, and confusion. Deliberate status *moves* (Thunder Wave, Sleep Powder)
  are still not used or faced — the plan and the modeled AI both throw damage. No held items.
- Abilities are modeled where they touch a 1v1 damage/turn calc: type immunities and absorptions
  (Levitate, Volt/Water Absorb, Flash Fire, Wonder Guard, Soundproof), crit prevention (Battle/Shell
  Armor), accuracy (Compound Eyes, Hustle), the CalculateBaseDamage stat mults (Huge/Pure Power,
  Guts, Thick Fat, Marvel Scale, the Overgrow line) and status immunities (Limber, Insomnia, Inner
  Focus, …). `abilities.py` implements every Gen 3 ability as a function; the ones that live outside
  this model — weather (Drizzle, Swift Swim), switch-in tricks (Intimidate, Trace), contact effects
  (Static, Flame Body), PP Pressure — are present and documented but not simulated. Lightning Rod is
  correctly a no-op in single battles (Gen 3 redirects only in doubles).
- Thrash, Outrage and Petal Dance are never recommended: the 2–3-turn lock-in and the confusion
  after are not modeled, and a plan can't steer a move that refuses orders.
- Trainer AI is "their single best damaging move", close to but not identical to
  `AI_SCRIPT_CHECK_BAD_MOVE`.
- Handovers are searched two segments deep; the party builder is greedy, so individual section
  numbers are not monotonic in the size of the candidate pool.
- The post-game stage lumps Sevii 4–7 and the Elite Four rematch together, so the route fights
  round two of the League straight after round one. That is a staging artifact, not a verdict.
- **Teleport and Fly are modelled as a return to the *nearest open* Pokémon Center**, not the
  exact one the game would send you to (Teleport returns to your last heal spot; Fly, to any
  visited town). Because the route heals as it passes each Center, the nearest open one is almost
  always that spot anyway. Teleport is treated like an HM — the party fields a Teleport user (the
  Abra line, catchable at Route 24) for the legs where returning to a Center shortens the walk —
  but as an *optional* field move: a spare slot or a dedicated carrier takes it, never a sacrificed
  attack, so a full party that can't spare a seat simply walks those legs instead.
