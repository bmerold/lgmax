# Healing — where the party gets restored, and where it doesn't

## The rule

**You heal at every Pokémon Center you walk past, and at the two in-dungeon healing stations.
Nothing else.** HP and PP carry across everything in between.

## Where the Centers are

`src/data/heal_locations.json` lists all **20**. Two are the ones people forget, and both matter:
**Route 4** (at the Mt. Moon entrance) and **Route 10** (at the mouth of Rock Tunnel). The rest:
Pallet, Viridian, Pewter, Cerulean, Vermilion, Lavender, Celadon, Saffron, Fuchsia, Cinnabar,
Indigo Plateau, and One through Seven Island.

## In-dungeon healing stations

Only two in the whole game, found by scanning for `special HealPlayerParty` outside a Center:
**the Purified Zone (Pokémon Tower 5F)** and **the Ember Spa (Kindle Road)**, plus a Seven Island
house post-game.

**Rocket Hideout, Silph Co. and Victory Road have none**, and the **Elite Four is one unbroken
gauntlet** from the Indigo Plateau Center. Those are the sections where PP genuinely binds.

## How a heal point is placed

- A Center covers the maps hanging off it, matched by prefix.
- The heal is anchored to the **last trainer battle** at that location — you top up on your way
  out, not after every trainer. Wild battles anchor only if the map has no trainer battles.
- A heal landing on the **final battle of a section is dropped**: the boundary restores you anyway.
- **Centers you walk *past* between maps also count.** Route 3 does not touch Mt. Moon — it runs up
  into the west end of Route 4, where both the Center and the cave mouth are. Keying heals off
  battle locations alone missed it, because no stage-5 battle is on Route 4.

## Reporting

A refilled move shows its **whole-section total against the larger pool** — Pokémon Tower's Haunter
reads `28/30 Thunderbolt PP (base 15, refilled once)` and carries a ✦.

## Not modelled

- Walking back to a Center you have already passed. Possible in-game, but not what a normal
  playthrough does mid-dungeon, and modelling it would make PP non-binding everywhere but the E4.
- Items of any kind. That is the chosen ruleset, not an oversight.
