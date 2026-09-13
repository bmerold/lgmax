# Single-use TMs — supply, scarcity, and the assignment model

## TMs are consumed on teach

`src/party_menu.c`, immediately after the move is taught:

```c
GiveMoveToMon(&gPlayerParty[gPartyMenu.slotId], ItemIdToBattleMoveId(gSpecialVar_ItemId));
AdjustFriendship(...);
if (gSpecialVar_ItemId < ITEM_HM01)
    RemoveBagItem(gSpecialVar_ItemId, 1);
```

Consumed for anything below `ITEM_HM01` — every TM. **HMs are exempt**, so Cut / Fly / Surf /
Strength / Rock Smash / Waterfall can be spread across the whole party at no cost.

## The supply (`tms.py` → `data/tmSupply.json`)

| Category | Count | Notes |
|---|---|---|
| Single-use, **exactly one copy** | **43** | a permanent, run-wide decision each |
| Restockable | 6 | Celadon Dept. Store 2F: Roar, Hyper Beam, Dig, Brick Break, Secret Power, Attract |
| Game Corner (re-buyable with coins) | 5 | Ice Beam, Iron Tail, Thunderbolt, Shadow Ball, Flamethrower |
| Not obtainable in LeafGreen at all | 1 | TM10 Hidden Power |

- **Gym leader TMs** come from the gym's own `giveitem_msg`, so the script scan finds them; the
  `GYM_TM` table only supplies a readable label. Counting both reported every gym TM as 2 copies.
- **Dept. Store roof**: the thirsty girl trades one TM per drink (Fresh Water → TM16, Soda Pop →
  TM20, Lemonade → TM33). Her script passes the item through `setvar VAR_0x8009`, so plain
  give/find patterns miss it entirely.
- A single gift can appear in several script branches, so sources are deduplicated **per map**.

## Extraction gotchas

1. `giveitem_msg` takes the **text argument first**: `giveitem_msg <TEXT>, ITEM_TMxx`. A naive
   `giveitem_msg\s+(ITEM_TM\d\d)` finds nothing at all.
2. Mart inventories are `.2byte` lists referenced by `pokemart <label>`. The Celadon TM clerk uses
   a **separate list** (`..._TMs`, not `..._Items`), so matching on `_Items` misses all six.
3. Items handed over through a variable are invisible to item-name scanning.

## The assignment model

**A single-use TM should go to the Pokémon that gains most across the whole run, not the first one
it happens to help** — otherwise a great TM lands on something benched by Saffron.

- **Pricing.** Each time a party member uses a scarce TM move in the pass-1 solve, it is priced
  against what that Pokémon would have managed with its best *remaining* move against the same
  opponent (`alt_turns`). A move with no fallback at all is worth a flat 12 turns.
- **Aggregation.** Summed over every section, so longevity beats a single early spike.
- **Assignment.** Most-contested TM first. Copies go to the highest total above a 0.25-turn floor.
  Candidates violating a run-wide commitment are excluded.
- **Cap:** no Pokémon gets more than 3 single-use TMs — it has 4 slots and needs level-up moves.
- **Pass 2** re-solves with the plan in force, so a party can never hold a TM spent elsewhere.

## Guard rails

- no TM is taught more often than the run can obtain it
- no party knows a single-use TM move it was not assigned
- HMs are never treated as scarce
