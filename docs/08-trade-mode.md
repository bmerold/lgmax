# Trade mode

## Two whole solves

**Section parties, choices, commitments and the TM plan are solved twice** — once for a run that
never trades, once for a run that will. `optimize.set_allow_trade()` is the switch; the pipeline
runs the whole two-pass solve under each setting. Payload shape is
`sections[mode][starter][stage]`, with `choices`, `tmPlans` and `commitments` nested the same way.
Sections are only ~0.36 MB, so carrying both costs almost nothing.

**The encounter rankings are built once, with trading allowed, and each entry tagged.** Building
them twice would add ~4 MB for the same information; instead every ranked entry and every
per-opponent counter carries a `requiresTrade` flag and the page filters. `KEEP` went 12 → 16 and
counter rows 7 → 9 so the lists stay full after filtering.

## The held item is the real gate

`TRADE_ITEM` evolutions were treated exactly like plain `TRADE`: the model took the pre-evolution's
stage and **ignored the item entirely**. Stone evolutions were already gated; this branch wasn't.
So **Porygon2 appeared from section 15** — Porygon is a Game Corner prize — and landed in
trade-mode parties for Rocket Hideout and Pokémon Tower, 443 rows across the rankings.

Porygon2 *is* in LeafGreen
(`[SPECIES_PORYGON] = {{EVO_TRADE_ITEM, ITEM_UP_GRADE, SPECIES_PORYGON2}}`), but the Up-Grade is
not in Kanto. Scanning `item_ball_scripts.inc`:

| Item | Only source in LeafGreen | Section |
|---|---|---|
| Metal Coat | Five Island, Memorial Pillar | 33 |
| Dragon Scale | Six Island, Water Path | 33 |
| King's Rock | Seven Island, Sevault Canyon | 33 |
| Up-Grade | Five Island, Rocket Warehouse | 33 |

**Every one is a post-game Sevii item ball**, and nothing catchable in LeafGreen holds any of them.
So Porygon2 (was 15), Steelix (13), Kingdra (19), Politoed (19) and Slowking (19) are all section
33. `progression.evo_item_stage()` derives this from the ROM, and a `TRADE_ITEM` evolution whose
item has no source is dropped entirely. The four plain trade evolutions are unaffected:
Alakazam 7, Golem 11, Machamp 13, Gengar 18.

## What trading is actually worth

| Starter | Solo | Trades OK | Delta | |
|---|---|---|---|---|
| Bulbasaur | 1717.5 | 1683.7 | −33.8 | −1.97% |
| Charmander | 1708.3 | 1670.7 | −37.6 | −2.20% |
| Squirtle | 1724.8 | 1676.9 | −47.9 | −2.78% |

**About 2%, or 35–48 turns out of roughly 1,700.** Faints barely move and unanswered opponents are
unchanged, so it does not rescue anything that was failing.

**It is Alakazam, and almost nothing else.** Party slots across all three starters and 35 sections
each: **Alakazam 45, Gengar 13, Machamp 7, Golem 3** — and **zero** for Porygon2, Steelix, Kingdra,
Politoed and Slowking, which are post-game-only and lose to what is already there.

**The gain is concentrated.** For Charmander the post-game section alone is −18.3 of the −37.6;
then Koga −7.5 and Rock Tunnel −6.1. 19 of 35 sections change party; 16 are identical.

Side effects: a trading run spends fewer single-use TMs (10–14 vs 15), and the Eevee commitment
flips from Vaporeon to Jolteon.

**Some sections get slightly worse with trading** — Victory Road +4.0, Silph Co. +3.4. Not a
finding about the game: the party builder is greedy, so a larger candidate pool can land on a worse
local optimum, and the flipped Eevee commitment cascades.

## Guard rails

- every trade-only recommendation is tagged as needing a trade
- a no-trade run's rankings still contain no trade evolutions
- **no evolution is reachable before the item it needs** — stones and trade items both
