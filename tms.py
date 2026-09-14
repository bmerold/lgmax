#!/usr/bin/env python3
"""TM supply in LeafGreen.

TMs are consumed when taught. Confirmed in the game's own code
(`src/party_menu.c`): after `GiveMoveToMon`,

    if (gSpecialVar_ItemId < ITEM_HM01)
        RemoveBagItem(gSpecialVar_ItemId, 1);

so a TM is spent and an HM is not. That makes most TMs a one-shot decision for
the whole playthrough, which is what this module quantifies: how many copies of
each TM a run can actually hold, and where they come from.
"""
import json, os, re, glob, collections
import engine as E

REPO = E.REPO
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Game Corner TMs can be re-bought, but at thousands of coins each. A normal
# playthrough buys one; treat them as scarce and surface the price instead.
GAME_CORNER_COPIES = 1

# The thirsty girl on the Celadon Dept. Store roof trades one TM per drink.
# Her script hands the item through a variable (`setvar VAR_0x8009, ITEM_TM16`)
# and a shared give routine, so the plain give/find patterns never see it.
# Each is flag-gated (FLAG_GOT_TMxx_FROM_THIRSTY_GIRL), so one copy each.
ROOF_TM = {
    "ITEM_TM16": ("Fresh Water", "Light Screen"),
    "ITEM_TM20": ("Soda Pop", "Safeguard"),
    "ITEM_TM33": ("Lemonade", "Reflect"),
}

# Leaders hand these over on defeat, once each. The gym scripts DO give the item
# with `giveitem_msg`, so the scanner already finds them -- this table only
# supplies a readable label and the right stage, and must not add a second copy.
GYM_TM = {
    "ITEM_TM39": ("Brock", 4, "PewterCity_Gym"),
    "ITEM_TM03": ("Misty", 8, "CeruleanCity_Gym"),
    "ITEM_TM34": ("Lt. Surge", 11, "VermilionCity_Gym"),
    "ITEM_TM19": ("Erika", 16, "CeladonCity_Gym"),
    "ITEM_TM06": ("Koga", 22, "FuchsiaCity_Gym"),
    "ITEM_TM04": ("Sabrina", 24, "SaffronCity_Gym"),
    "ITEM_TM38": ("Blaine", 27, "CinnabarIsland_Gym"),
    "ITEM_TM26": ("Giovanni", 29, "ViridianCity_Gym"),
}

def _files():
    return (glob.glob(f"{REPO}/data/maps/*/scripts.inc")
            + glob.glob(f"{REPO}/data/scripts/*.inc"))

def extract():
    import build_graph as G
    import progression as P

    finite = collections.defaultdict(list)     # item -> [(kind, where)]
    unlimited = {}                             # item -> where
    mart_lists = set()

    for path in _files():
        txt = open(path, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r"pokemart\s+(\w+)", txt):
            mart_lists.add(m.group(1))

    for path in _files():
        txt = open(path, encoding="utf-8", errors="replace").read()
        mapname = path.split("/maps/")[1].split("/")[0] if "/maps/" in path else None
        label, seen_here = None, set()
        for line in txt.splitlines():
            lm = re.match(r"^(\w+)::", line)
            if lm: label = lm.group(1)
            area = mapname or (label.split("_EventScript_")[0]
                               if label and "_EventScript_" in label else label)
            for pat, kind in ((r"\bfinditem\s+(ITEM_TM\d{2})\b", "item ball"),
                              (r"\bhiddenitem\s+(ITEM_TM\d{2})\b", "hidden item"),
                              (r"\badditem\s+(ITEM_TM\d{2})\b", "gift"),
                              (r"\bgiveitem\s+(ITEM_TM\d{2})\b", "gift"),
                              (r"\bgiveitem_msg\s+[^,]+,\s*(ITEM_TM\d{2})\b", "gift")):
                mm = re.search(pat, line)
                if mm:
                    # one script can mention the same TM twice (retry branches)
                    key = (mm.group(1), area)
                    if key not in seen_here:
                        seen_here.add(key)
                        finite[mm.group(1)].append((kind, area))
                    break
        # purchasable: any TM inside a list that `pokemart` points at
        for blk in re.finditer(r"^(\w+)::\s*\n((?:\s*\.2byte [A-Z_0-9]+\s*\n)+)", txt, re.M):
            if blk.group(1) not in mart_lists: continue
            for it in re.findall(r"\.2byte (ITEM_TM\d{2})", blk.group(2)):
                unlimited[it] = mapname or blk.group(1)

    # Game Corner prizes, with their coin price
    corner = {}
    gc = open(f"{REPO}/data/maps/CeladonCity_GameCorner_PrizeRoom/scripts.inc",
              encoding="utf-8").read()
    for m in re.finditer(r"setvar VAR_TEMP_1, (ITEM_TM\d{2})\s*\n\s*setvar VAR_TEMP_2, (\d+)", gc):
        corner[m.group(1)] = int(m.group(2))

    out = {}
    for i in range(1, 51):
        item = f"ITEM_TM{i:02d}"
        move = G.TMHM_MOVE.get(item)
        if not move: continue
        sources, copies, repeatable, cost = [], 0, False, None

        if item in ROOF_TM:
            drink, _ = ROOF_TM[item]
            sources.append({"kind": "gift", "stage": 15,
                            "where": f"Celadon Dept. Store roof, for a {drink}"})
            copies += 1
        gym = GYM_TM.get(item)
        gym_seen = False
        for kind, area in finite.get(item, []):
            if gym and area == gym[2]:
                # the same copy the leader hands you, just labelled properly
                sources.append({"kind": "gym leader", "where": f"{gym[0]} on defeat",
                                "stage": gym[1]})
                gym_seen = True
            else:
                st = P.location_stage(area)
                if st is None: st = G._stage_from_area_name(area) if area else None
                corr = G.ITEM_STAGE_CORRECTIONS.get(item)
                if corr: st = corr[0]      # story-gated later than its map
                sources.append({"kind": kind, "where": _pretty(area), "stage": st})
            copies += 1
        if gym and not gym_seen:
            sources.append({"kind": "gym leader", "where": f"{gym[0]} on defeat",
                            "stage": gym[1]})
            copies += 1
        if item in corner:
            sources.append({"kind": "Game Corner", "where": f"Celadon Game Corner, {corner[item]} coins",
                            "stage": 15, "cost": corner[item]})
            copies += GAME_CORNER_COPIES
            repeatable = True
            cost = corner[item]
        if item in unlimited:
            sources.append({"kind": "shop", "where": _pretty(unlimited[item]), "stage": 15})
            copies = None            # buy as many as you like
            repeatable = True

        stages = [s["stage"] for s in sources if s.get("stage") is not None]
        out[item] = {
            "item": item, "move": move,
            "name": __import__("engine").MOVES[move]["name"] if move in __import__("engine").MOVES else move,
            "copies": copies, "repeatable": repeatable, "coinCost": cost,
            "sources": sources,
            "earliest": min(stages) if stages else None,
            "obtainable": bool(sources),
        }
    return out

def _pretty(area):
    if not area: return "—"
    import build_graph as G
    return G.pretty_location(area)

# ------------------------------------------------------------------ scarcity
def scarce_moves(supply):
    """Moves whose TM a run can only spend once (or a handful of times).
    HMs are excluded entirely: the item is never consumed, so any number of
    Pokemon can learn Surf, Strength, Cut and the rest."""
    out = {}
    for item, rec in supply.items():
        if not rec["obtainable"]: continue
        if rec["copies"] is None:
            # Dept.-store TMs are unlimited -- but only once the shop opens.
            # Before that stage the run holds exactly its finite copies (the
            # Cerulean TM28, the S.S. Anne TM31...), so they are scarce until
            # then and free afterwards.
            pre = [s for s in rec["sources"] if s["kind"] != "shop"]
            if not pre: continue      # shop-only: the stage gate already covers it
            out[rec["move"]] = {"item": item, "copies": len(pre),
                                "earliest": rec["earliest"], "name": rec["name"],
                                "repeatable": True, "coinCost": rec["coinCost"],
                                "shopStage": min(s["stage"] for s in rec["sources"]
                                                 if s["kind"] == "shop")}
            continue
        out[rec["move"]] = {"item": item, "copies": rec["copies"],
                            "earliest": rec["earliest"], "name": rec["name"],
                            "repeatable": rec["repeatable"], "coinCost": rec["coinCost"]}
    return out

if __name__ == "__main__":
    import engine as E
    supply = extract()
    with open(f"{OUT}/tmSupply.json", "w") as f:
        json.dump(supply, f, indent=1)
    sc = scarce_moves(supply)
    unob = [r for r in supply.values() if not r["obtainable"]]
    shop = [r for r in supply.values() if r["copies"] is None]
    print(f"TMs total: {len(supply)}")
    print(f"  unobtainable in LeafGreen: {len(unob)} -> {[r['name'] for r in unob]}")
    print(f"  shop-stocked (unlimited):  {len(shop)} -> {[r['name'] for r in shop]}")
    print(f"  single-use, one copy only: {sum(1 for r in sc.values() if r['copies'] == 1)}")
    print(f"  single-use, 2+ copies:     {sum(1 for r in sc.values() if r['copies'] > 1)}")
    print()
    print("the scarce TMs that matter most (by move power):")
    rows = sorted(sc.values(), key=lambda r: -E.MOVES[[k for k, v in sc.items() if v is r][0]]["power"])
    for r in rows[:14]:
        mv = [k for k, v in sc.items() if v is r][0]
        m = E.MOVES[mv]
        tag = f"{r['coinCost']} coins" if r["coinCost"] else f"stage {r['earliest']}"
        print(f"  {r['name']:15} {m['type']:9} {m['power']:3} BP  x{r['copies']}  ({tag})")
