# Do LeafGreen's version exclusives matter?

Measured by leave-one-out: ban a species, re-solve the whole run, compare totals.
Charmander, no trades, baseline **1674.2** turns.

## The lists, from the encounter tables

Each map's wild table carries a `version` field. 113 wild species each; 17 differ either way.

**LeafGreen only (wild):** Bellsprout, Kingler, Magmar, Mantine, Marill, Misdreavus, Muk, Pinsir\*,
Remoraid, Sandshrew, Sandslash, Slowbro, Slowpoke, Sneasel, Staryu, Vulpix, Weepinbell — rolled
forward through evolutions, **23 obtainable species**.
(\*Pinsir is the Game Corner prize from the `#if defined(LEAFGREEN)` branch; FireRed gets Scyther.)

**FireRed only (wild):** Arbok, Delibird, Ekans, Electabuzz, Gloom, Golduck, Growlithe, Murkrow,
Oddish, Psyduck, Qwilfish, Scyther, Seadra, Shellder, Skarmory, Weezing, Wooper

## What they earn

Party slots across all three starters, 35 sections each: **Starmie 11, Sandslash 3, Pinsir 2,
Weepinbell 2, Slowbro 1** (no trades). Everything else on the list — Magmar, Muk, Kingler, Sneasel,
Misdreavus, Ninetales, Victreebel, Azumarill, Mantine, Octillery — earns **zero slots in the entire
game**.

## What they are worth

| Banned | Total | Delta | |
|---|---|---|---|
| **every LeafGreen exclusive** | 1682.5 | **+8.3** | +0.50% |
| Sandshrew line only | 1681.1 | +6.9 | +0.41% |
| Slowpoke line only | 1678.2 | +4.0 | +0.24% |
| Staryu line only | 1674.8 | +0.6 | +0.04% |
| Bellsprout line only | 1674.7 | +0.5 | +0.03% |
| Pinsir only | 1674.5 | +0.3 | +0.02% |
| Vulpix line only | 1674.2 | +0.0 | 0.00% |

**Losing all 23 costs half a percent.** Kanto's roster is deep enough that nearly every exclusive
has a close substitute.

## The one real dependency is a field move, not a battle

**Starmie earns the most slots by far and is worth almost nothing** — 11 party slots, 0.6 turns.

**Sandshrew is the opposite**: 3 slots and the most load-bearing exclusive at +6.9. Almost all of
it lands in one place, through the sticky-HM rule:

```
s18 with Sandshrew    44.5   Nidoking  [Ice Beam, Thrash, Thunderbolt, Peck]
s18 without           50.3   Nidoking  [Ice Beam, Thrash, Cut,         Peck]
```

Sandslash is the run's Cut carrier through the mid-game. Take it away and Cut goes on **Nidoking**,
which — because the Move Deleter is in Fuchsia and this is section 18 — is *still stuck with it* at
Pokémon Tower, in the slot Thunderbolt wanted. One field move displaces one attack: 5.8 turns in a
single section.

That is the whole answer: the exclusives matter for **who can carry an HM without giving up a real
move**, not for damage.

## Caveat

This bans species from a LeafGreen run; it does not simulate a FireRed run, which would also
*gain* Growlithe, Arcanine, Scyther and Electabuzz. The symmetric answer needs a second
availability model keyed to FireRed's tables.
