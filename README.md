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

Python 3.10+, no third-party packages. Takes 6–8 minutes; `build.sh` runs the whole pipeline and
finishes with `verify.py`, which must print 27 OKs. The output is `app.html` — one self-contained
file, ~6 MB, no server needed.

## What it does

| | |
|---|---|
| **Encounters** | 923 encounters (20 boss fights, 566 trainer battles including VS Seeker rematches, 319 wild areas) across 35 sections, each solved for which level-appropriate Pokémon clears it fastest, with per-opponent counters |
| **Section parties** | the smallest party that clears each section, simulated end to end **with no items** — HP and PP spent across the whole stretch, HMs carried, and a running list of what to keep levelled for later. Sections are presented cut at every full heal (a Pokémon Center walked past, or an in-dungeon healing spot), so a PP bar never quietly spans a heal |
| **TM plan** | 43 of the 49 TMs exist in exactly one copy; this decides who gets each one, judged over the whole run rather than the first fight it helps |
| **Choices** | starter, fossil, Fighting Dojo, Eevee stone, Game Corner prize — each ranked on whole evolution lines |

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
| `progression.py` | 35-section story model, map→section gating, species availability |
| `build_graph.py` | encounter graph, TM/HM availability by section |
| `route_order.py` | the order you actually meet trainers, from map geometry |
| `walking.py` | wild-battle load: tiles walked per map → encounters fought |
| `tms.py` | TM supply from the item scripts; which moves are one-copy-only |
| `hms.py` | field obstacles per map → which HMs each section demands |
| `constraints.py` | evolution lines, one-of groups, run-wide commitments, outgrown forms |
| `optimize.py` | per-encounter optimizer (scalar screen → exact turn DP) |
| `sections.py` | two-pass per-section party optimizer + choice evaluation |
| `compact.py` | array-encoded payload (~25 MB → ~5 MB) |
| `verify.py` | **27 guard rails; all must pass after every rebuild** |
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
- **All four trade-evolution items** (Metal Coat, Dragon Scale, King's Rock, Up-Grade) are
  post-game Sevii item balls, so Porygon2, Steelix, Kingdra, Politoed and Slowking are post-game.
- **Route 3 does not touch Mt. Moon** — it runs up into the west end of Route 4, where both the
  Pokémon Center and the cave mouth are.

See `docs/` for the working notes behind each of these.

## Known, deliberate limitations

- No stat-stage modelling on either side. Measured cost on the player's side: about one turn across
  the whole game (`docs/setup-moves-study.md`). The opponent's side still matters — Sabrina, Agatha
  and the Champion are harder than the numbers suggest.
- No status effects, no held-item type boosts.
- Trainer AI is "their single best damaging move", close to but not identical to
  `AI_SCRIPT_CHECK_BAD_MOVE`.
- Handovers are searched two segments deep; the party builder is greedy, so individual section
  numbers are not monotonic in the size of the candidate pool.
- The post-game section lumps Sevii 4–7 and the Elite Four rematch into one continuous run with no
  healing, so it wipes. That is a sectioning artifact, not a verdict.
