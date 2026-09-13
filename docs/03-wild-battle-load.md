# The walking cost — estimating wild battles per section

Module: `walking.py` → `data/wildLoad.json`.

## Three of the four inputs come straight from the ROM

**1. Encounter chance per step** — `src/wild_encounter.c`:

```c
static bool8 DoWildEncounterRateDiceRoll(u16 encounterRate) {
    if (WildEncounterRandom() % MAX_ENCOUNTER_RATE < encounterRate) return TRUE;   // 1600
```

`DoWildEncounterRateTest` first does `encounterRate *= 16`, so the base chance is exactly
**`rate / 100` per step**. A map with `encounter_rate: 20` is 1-in-5; Mt. Moon's 7 is 1-in-14.

Failed checks feed a pity buff: `AddToWildEncounterRateBuff` adds the rate each time and the test
adds `buff * 16 / 200` back, so after *k* misses the chance is `(rate/100) × (1 + k/200)`.
`steps_per_encounter()` sums that series exactly rather than approximating `100/rate`.

There is also a 60% gate (`DoGlobalWildEncounterDiceRoll`) that applies **only when the metatile
behaviour changes** — stepping into grass, not walking through it. Not modelled, which makes the
figures slightly conservative.

**2. Which tiles can trigger** — each map's `map.bin` stores a u16 per tile (metatile id in bits
0–9, collision in 10–11). Each tileset's `metatile_attributes.bin` stores a u32 per metatile with
`METATILE_ATTRIBUTE_ENCOUNTER_TYPE` in **bits 24–26** (1 = land). Ids below
`NUM_METATILES_IN_PRIMARY` (640) index the primary tileset, the rest the secondary. So the share of
walkable ground that triggers encounters is **counted**: Mt. Moon 1F is 100%, a route far less.

**3. How far you walk** — a nearest-neighbour tour over the real x/y of every item ball, hidden
item (`bg_events` of type `hidden_item`), trainer object event and warp in that map's `map.json`,
in Manhattan tiles. Maps you only pass through fall back to `(width + height) / 2`.

## The one genuine estimate

That the walk is spread evenly over the map, so the share of steps landing on encounter tiles
equals the tile share. Everything else is measured.

## Result

**357 wild battles to clear every area in the game.** Worked example, Mt. Moon 1F:

> walk 208 tiles · 100% of the floor triggers encounters · rate 7 → one battle per 13.5 steps
> → **15.3 battles**

They are ~20% of all turns in a run. Party sizes went up where the walking is heavy — Mt. Moon
wants four Pokémon rather than two — and the optimizer visibly favours high-PP moves. PP became
the binding constraint on route sections, which is the whole point.

## Not modelled

- Repels, running away, Poké Doll escapes — all of which cut the number. This is the no-shortcuts figure.
- Catching Pokémon; surfing and fishing encounters (land tables only).
- Re-walking a map on a later visit — each map is costed once, in the section that first opens it.
