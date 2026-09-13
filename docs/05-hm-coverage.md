# HM coverage, move recovery, and keeping Pokémon at level

Built by `hms.py` and `sections.py` (`assign_hms`, `move_recovery`, `keep_lists`).

## Where the requirements come from

Field obstacles live in the ROM in two shapes, and both are read:

- **Object events.** A cuttable tree, smashable rock and pushable boulder are each an object whose
  `script` is `EventScript_CutTree`, `EventScript_RockSmash` or `EventScript_StrengthBoulder` —
  **49 trees, 97 rocks, 54 boulders** across the game.
- **Metatile behaviours.** Surfable water and waterfalls are terrain, so they come from each
  layout's blockdata against the tileset's `metatile_attributes.bin`. "Surfable" is exactly the
  ROM's `sBehaviorSurfable` table; a waterfall is `MB_WATERFALL`.

Flash is a map's own `requires_flash` flag, and **exactly two maps set it** — both floors of Rock
Tunnel. A map needs at least 12 surfable tiles to count, so a town fountain doesn't demand HM03.

**Fly is the exception**: gated on no obstacle at all, and nothing is unreachable without it — but
it is the field move you actually use most, so it is a **standing requirement from the moment HM02
is in the bag** (section 20 on).

## When an obstacle becomes your problem

At `max(the map's stage, the HM's stage)`. Route 2's cut trees sit there from the first walk
through, but they are a locked door, not a task, until you own HM01 *and* the Cascade Badge.

| HM | Move | Badge | Obtainable |
|---|---|---|---|
| HM01 | Cut | Cascade | S.S. Anne captain |
| HM02 | Fly | Thunder | Route 16 house — required from then on, everywhere |
| HM03 | Surf | Soul | Safari Zone Secret House |
| HM04 | Strength | Rainbow | the Warden, for the Gold Teeth |
| HM05 | Flash | Boulder | Route 2 East Building |
| HM06 | Rock Smash | Marsh | Kindle Road — post-Blaine |
| HM07 | Waterfall | Volcano | Icefall Cave — post-game |

`here(stage)` is what this section's own maps demand and is what the party must field;
`kit(stage)` is the cumulative "you should own these by now" footnote. Conflating the two would
demand Cut inside the Pokémon League.

## An HM cannot be taken back off

`IsHMMove2` blocks the normal overwrite. The only exit is the **Move Deleter, Fuchsia City House 3**.
Two consequences, both modelled:

1. **HM slots are sticky before Fuchsia.** An HM taught in section 11 still occupies a slot in
   section 20. `HM_HELD` carries each species' HMs forward and re-teaches them at the start of
   every section before 21, so those sections are costed with the moves the Pokémon really has.
2. **Which move gets sacrificed matters** — it is chosen on what is cheapest to lose *permanently*,
   not on what is weakest right now.

## Move recovery ranks

| Rank | Case | Why |
|---|---|---|
| 0 | the move is itself an HM | HMs are reusable — teach it again |
| 0 | a level-up move, section 28+ | the **Move Maniac, Two Island House** relearns it |
| 1 | a repeatable TM (11 of them) | Dept. Store or Game Corner |
| 2 | a level-up move before section 28 | recoverable, but only once you reach the Sevii Islands |
| 3 | a single-use TM | **gone for good** |

The Move Maniac relearns **level-up moves only** (`GetMoveRelearnerMoves` walks `gLevelUpLearnsets`
and nothing else) and charges a **Big Mushroom, or two Tiny ones** — FRLG uses mushrooms, not the
Heart Scale of the Hoenn games.

**Result: every sacrifice across all three starter runs is recoverable. Zero permanent losses.**

## How a party gets its HMs

Cheapest first: **already knows it** (Surf and Strength are 95- and 80-power attacks the battle
optimizer often picks up unprompted) → **a spare move slot** → **a carrier** brought along, scored
on gaps closed first and battle contribution second → **a sacrificed move** → and finally, with a
full party and nobody able to learn it, **swap out the body that contributes least**.

Carriers are rarely dead weight: Kangaskhan carries Cut, Strength and Rock Smash through the Sevii
Islands while fighting; Persian carries Cut and Flash through Rock Tunnel.

Before this pass, **17 of 22 sections had a required HM that nobody on the party could even learn**.

## Keep-at-level list

The model never grinds: a Pokémon is at its section's level the moment it is used. That only holds
if it stayed in your rotation, so every section carries a running list of what a *later* section
wants that is **not** on this section's team.

Keyed on the **evolution line root**, not the species — a Charmander you hold now and the
Charmeleon a later section wants are the same Pokémon. Each row names the form you would be
holding now and, when it differs, the form it needs to be by then. Branching lines resolve through
the run's commitment, so a run committed to Hitmonlee never reads "Hitmonchan — as Hitmonlee".
