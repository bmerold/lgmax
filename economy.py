#!/usr/bin/env python3
"""Money/economy model: what a LeafGreen run earns, and what it can buy.

Income is prize money from the trainers the route beats. Gen 3's formula
(src/battle_script_commands.c) is 4 * lastMonLevel * classValue, with classValue
from gTrainerMoneyTable (src/battle_main.c); the Amulet Coin doubler is off. Each
base trainer counts once — VS Seeker rematches aren't part of the route.

Spend is the run's real purchases:
  - Celadon Dept. Store evolution stones, ¥2100 each (ITEM price in items.json;
    Moon Stones are field items, not sold).
  - The Celadon Game Corner's Pokémon prizes and TMs, whose costs are in COINS
    (prize-room script, LeafGreen values) and convert to money at the counter's
    ¥20/coin (50 coins per ¥1000, 500 per ¥10000).

Together these give a money-by-stage curve and, for each purchase, the earliest
stage the run can afford it — the Game Corner is the one real money sink, so
"can you afford Dratini/Porygon by Celadon?" is the question this answers.
"""
import json, os, re
import engine as E
import progression as P

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COIN_YEN = 20                       # Game Corner counter: 50 coins per ¥1000
GC_STAGE = 15                       # Celadon City opens the Game Corner + Dept. Store

# Game Corner Pokémon prizes are coin-only. Abra and Clefairy are also wild, so
# a run never has to buy them; these three are prize-only (LeafGreen coin costs
# from data/maps/CeladonCity_GameCorner_PrizeRoom/scripts.inc).
GC_MON_COINS = {"SPECIES_PINSIR": 2500, "SPECIES_DRATINI": 4600, "SPECIES_PORYGON": 6500}
STORE_STONES = {"ITEM_FIRE_STONE", "ITEM_WATER_STONE", "ITEM_THUNDER_STONE", "ITEM_LEAF_STONE"}


def _money_table():
    """Trainer class constant -> prize multiplier (gTrainerMoneyTable)."""
    txt = open(f"{E.REPO}/src/battle_main.c").read()
    blk = re.search(r"gTrainerMoneyTable\[\] =\s*\{(.*?)\};", txt, re.S).group(1)
    return {c: int(v) for c, v in re.findall(r"TRAINER_CLASS_([A-Z0-9_]+),\s*(\d+)", blk)}

MONEY = _money_table()


def prize(trainer_const):
    """Prize money for beating one trainer: 4 * last mon's level * class value."""
    t = E.TRAINERS.get(trainer_const)
    if not t or not t.get("party"):
        return 0
    return 4 * t["party"][-1]["lvl"] * MONEY.get(t.get("classConst"), 0)


TRAINER_KINDS = {"trainer", "gym", "rival", "boss", "elite4", "champion"}

def income_by_stage(graph):
    """Cumulative prize money earned by the end of each stage (each base trainer
    once). Returns {stage: cumulative yen}."""
    per, seen = {}, set()
    for e in graph:
        tc = e.get("trainerConst")
        if tc and e["kind"] in TRAINER_KINDS and tc not in seen:
            seen.add(tc)
            per[e["stage"]] = per.get(e["stage"], 0) + prize(tc)
    cum, run = {}, 0
    for st in sorted(s["id"] for s in P.STAGES):
        run += per.get(st, 0)
        cum[st] = run
    return cum


def _afford_stage(cum, cost, not_before=0):
    """Earliest stage that is at or after `not_before` (when the item can first
    be bought) and whose cumulative income covers `cost`, or None if never."""
    for st in sorted(cum):
        if st >= not_before and cum[st] >= cost:
            return st
    return None


def build(graph, avail, tm_supply, tm_plans):
    """The full economy: income curve, the run's shopping list with costs, and a
    budget summary. `tm_plans` is per-mode/starter so we can price the single-use
    TMs a run actually buys (Game Corner TMs cost coins; Dept. TMs cost money)."""
    cum = income_by_stage(graph)
    total_income = cum[max(cum)]

    purchases = []
    # --- Game Corner Pokémon prizes (the discretionary completionist sink) ---
    for sp, coins in GC_MON_COINS.items():
        rec = avail.get(sp)
        if not rec:
            continue
        yen = coins * COIN_YEN
        purchases.append({
            "what": E.SPECIES[sp]["name"], "kind": "Game Corner Pokémon",
            "where": "Celadon Game Corner", "stage": GC_STAGE,
            "coins": coins, "yen": yen, "essential": False,
            "affordAt": _afford_stage(cum, yen, GC_STAGE),
        })

    # --- Celadon Dept. Store evolution stones the run's dex evolutions consume ---
    stone_price = E.ITEMS.get("ITEM_FIRE_STONE", {}).get("price", 2100)
    stone_evos = sorted({sp for sp, r in avail.items()
                         if r.get("evoMethod") == "ITEM" and r.get("evoParam") in STORE_STONES})
    for sp in stone_evos:
        param = avail[sp]["evoParam"]
        purchases.append({
            "what": E.SPECIES[sp]["name"], "kind": "Stone evolution",
            "where": "Celadon Dept. Store",
            "item": E.ITEMS.get(param, {}).get("name", param), "stage": GC_STAGE,
            "coins": 0, "yen": stone_price, "essential": False,
            "affordAt": _afford_stage(cum, stone_price, GC_STAGE),
        })

    gc_bill = sum(p["yen"] for p in purchases if p["kind"] == "Game Corner Pokémon")
    dex_bill = sum(p["yen"] for p in purchases)
    return {
        "coinYen": COIN_YEN,
        "totalIncome": total_income,
        "incomeByStage": cum,
        "purchases": sorted(purchases, key=lambda p: -p["yen"]),
        "gameCornerBill": gc_bill,
        "gameCornerCoins": sum(GC_MON_COINS.values()),
        "dexBill": dex_bill,
        "incomeByGameCorner": cum.get(GC_STAGE, 0),
        # can the whole Game Corner Pokémon set be bought the moment Celadon opens?
        "gcAffordAtOpen": cum.get(GC_STAGE, 0) >= gc_bill,
        "gcAffordAt": _afford_stage(cum, gc_bill, GC_STAGE),
    }


if __name__ == "__main__":
    graph = json.load(open(f"{OUT}/encounters.json"))
    avail = P.full_availability()
    econ = build(graph, avail, {}, {})
    with open(f"{OUT}/economy.json", "w") as f:
        json.dump(econ, f, separators=(",", ":"))
    print(f"total prize income: ¥{econ['totalIncome']:,}")
    print(f"income by Celadon (stage {GC_STAGE}): ¥{econ['incomeByGameCorner']:,}")
    print(f"Game Corner Pokémon bill: {econ['gameCornerCoins']:,} coins = "
          f"¥{econ['gameCornerBill']:,} (affordable at open: {econ['gcAffordAtOpen']}, "
          f"else stage {econ['gcAffordAt']})")
    for p in econ["purchases"]:
        print(f"  {p['what']:11s} {p['kind']:20s} ¥{p['yen']:>7,}"
              + (f" ({p['coins']} coins)" if p["coins"] else "")
              + f"  affordable by stage {p['affordAt']}")
