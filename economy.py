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

# Porygon is the only Game-Corner-exclusive Pokémon a run has to buy. Abra and
# Clefairy are wild (Route 24 / Mt. Moon), and — the correction — Pinsir and
# Dratini are wild too (Safari Zone grass / super rod), so a completionist catches
# them free rather than paying their prize coins. (LeafGreen coin cost from
# data/maps/CeladonCity_GameCorner_PrizeRoom/scripts.inc.)
GC_MON_COINS = {"SPECIES_PORYGON": 6500}
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


def _tm_costs(tm_supply):
    """Purchase-only TMs (no free item-ball/gift source) -> cost record. Game
    Corner TMs cost coins; Celadon Dept. Store TMs cost money (the item price)."""
    out = {}
    for rec in tm_supply.values():
        srcs = rec.get("sources", [])
        if not srcs or any(s.get("kind") not in ("shop", "Game Corner") for s in srcs):
            continue
        gc = next((s for s in srcs if s.get("kind") == "Game Corner"), None)
        coins = gc["cost"] if gc else 0
        out[rec["name"]] = {
            "item": rec["item"], "coins": coins,
            "yen": coins * COIN_YEN if coins else E.ITEMS.get(rec["item"], {}).get("price", 0),
            "where": "Celadon Game Corner" if gc else "Celadon Dept. Store",
            "kind": "Game Corner TM" if gc else "Dept. Store TM",
        }
    return out


def tm_purchases(sections, tm_supply):
    """Per (trade mode, starter), the purchasable TMs that run actually teaches —
    one TM bought per (Pokémon, move), priced. These were previously free in the
    model because Game Corner / Dept. TMs are repeatable, so their coin/money cost
    went uncounted."""
    costs = _tm_costs(tm_supply)
    out = {}
    for mode, per in sections.items():
        out[mode] = {}
        for starter, secs in per.items():
            teaches = {}   # move name -> set of species taught it via a TM
            for sec in (secs or {}).values():
                if not sec:
                    continue
                for t in sec.get("team", []):
                    for m in t.get("moves", []):
                        nm = m.get("name")
                        # only a real TM teach — a level-up copy is free
                        if nm in costs and str(m.get("src", "")).startswith("TM"):
                            teaches.setdefault(nm, set()).add(t["species"])
            buys = []
            for nm, species in teaches.items():
                c = costs[nm]
                n = len(species)
                buys.append({
                    "what": nm, "kind": c["kind"], "where": c["where"], "stage": GC_STAGE,
                    "item": c["item"], "count": n,
                    "coins": c["coins"] * n, "yen": c["yen"] * n,
                    "unitCoins": c["coins"], "unitYen": c["yen"],
                    "teaches": sorted(E.SPECIES[s]["name"] for s in species),
                })
            out[mode][starter] = sorted(buys, key=lambda b: -b["yen"])
    return out


def build(graph, avail, sections, tm_supply):
    """Income curve + the run's shopping list. Run-agnostic purchases (the
    Game-Corner-exclusive Porygon and the Celadon stones) are `fixed`; the
    purchasable TMs a run buys are per (mode, starter) in `tmBuys`. Affordability
    and per-run totals are computed in the app off `incomeByStage`."""
    cum = income_by_stage(graph)

    fixed = []
    for sp, coins in GC_MON_COINS.items():        # Porygon — Game-Corner-only
        if sp in avail:
            fixed.append({"what": E.SPECIES[sp]["name"], "kind": "Game Corner Pokémon",
                          "where": "Celadon Game Corner", "stage": GC_STAGE,
                          "coins": coins, "yen": coins * COIN_YEN})
    stone_price = E.ITEMS.get("ITEM_FIRE_STONE", {}).get("price", 2100)
    for item in sorted(STORE_STONES):             # buy one per stone evolution you do
        fixed.append({"what": E.ITEMS.get(item, {}).get("name", item), "kind": "Evolution stone",
                      "where": "Celadon Dept. Store", "stage": GC_STAGE,
                      "coins": 0, "yen": stone_price})

    return {
        "coinYen": COIN_YEN,
        "totalIncome": cum[max(cum)],
        "incomeByStage": cum,
        "incomeByGameCorner": cum.get(GC_STAGE, 0),
        "gcStage": GC_STAGE,
        "fixed": sorted(fixed, key=lambda p: -p["yen"]),
        "tmBuys": tm_purchases(sections, tm_supply),
    }


if __name__ == "__main__":
    graph = json.load(open(f"{OUT}/encounters.json"))
    avail = P.full_availability()
    secs = json.load(open(f"{OUT}/sections.json"))
    tm_supply = json.load(open(f"{OUT}/tmSupply.json")) if os.path.exists(f"{OUT}/tmSupply.json") \
        else secs.get("tmSupply", {})
    econ = build(graph, avail, secs["sections"], tm_supply)
    with open(f"{OUT}/economy.json", "w") as f:
        json.dump(econ, f, separators=(",", ":"))
    print(f"total prize income: ¥{econ['totalIncome']:,}")
    print(f"income by Celadon (stage {GC_STAGE}): ¥{econ['incomeByGameCorner']:,}")
    # summarize a representative run (best no-trade starter)
    tot = {k: sum((v[s] or {}).get("turns", 0) or 0 for s in v)
           for k, v in secs["sections"]["no"].items()}
    ref = min(tot, key=tot.get)
    tms = econ["tmBuys"]["no"][ref]
    tm_yen = sum(b["yen"] for b in tms)
    tm_coins = sum(b["coins"] for b in tms)
    print(f"fixed purchases: {[(p['what'], p['yen']) for p in econ['fixed']][:3]} …")
    print(f"TMs bought ({ref}, solo): ¥{tm_yen:,} ({tm_coins:,} coins) — "
          + ", ".join(f"{b['what']}×{b['count']}" for b in tms))
