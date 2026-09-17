#!/usr/bin/env python3
"""Build the encounter graph: every trainer battle and every wild-encounter area,
ordered by when in the game it can happen, plus the TM/move pool available at
each stage.
"""
import json, os, re, glob
from collections import defaultdict
import engine as E
import progression as P

REPO = E.REPO
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ------------------------------------------------------------------ TM/HM -> move
def tmhm_moves():
    txt = open(f"{REPO}/src/data/party_menu.h", encoding="utf-8").read()
    blk = txt[txt.index("sTMHMMoves[]"):]
    blk = blk[:blk.index("};")]
    moves = re.findall(r"(MOVE_[A-Z0-9_]+)", blk)
    out = {}
    for i, mv in enumerate(moves):
        item = f"ITEM_TM{i+1:02d}" if i < 50 else f"ITEM_HM{i-49:02d}"
        out[item] = mv
    return out

TMHM_MOVE = tmhm_moves()
MOVE_TO_TM = {v: k for k, v in TMHM_MOVE.items()}

# ------------------------------------------------------------------ item -> earliest stage
MART_STAGE_HINT = {   # marts keyed by the town they sit in
    "ViridianCity": 1, "PewterCity": 4, "CeruleanCity": 6, "VermilionCity": 9,
    "LavenderTown": 14, "CeladonCity": 15, "FuchsiaCity": 21, "SaffronCity": 23,
    "CinnabarIsland": 26, "IndigoPlateau": 30, "OneIsland": 28, "TwoIsland": 28,
    "ThreeIsland": 28, "FourIsland": 33, "FiveIsland": 33, "SixIsland": 33,
    "SevenIsland": 33,
}

def item_stage_table():
    """Earliest stage at which each TM/HM can be in the player's bag."""
    best = {}
    def record(item, stage, where):
        if stage is None: return
        cur = best.get(item)
        if cur is None or stage < cur[0]:
            best[item] = (stage, where)

    files = glob.glob(f"{REPO}/data/maps/*/scripts.inc") + glob.glob(f"{REPO}/data/scripts/*.inc")
    for path in files:
        txt = open(path, encoding="utf-8", errors="replace").read()
        mapname = path.split("/maps/")[1].split("/")[0] if "/maps/" in path else None
        cur_label = None
        for line in txt.splitlines():
            lm = re.match(r"^(\w+)::", line)
            if lm: cur_label = lm.group(1)
            for m in re.finditer(r"\b(ITEM_(?:TM|HM)\d{2})\b", line):
                item = m.group(1)
                area = mapname
                if area is None and cur_label:
                    area = re.split(r"_(EventScript|Text|Items)_?", cur_label)[0]
                if not area: continue
                st = P.location_stage(area)
                if st is None:
                    st = _stage_from_area_name(area)
                record(item, st, area)
    return best

def _stage_from_area_name(area):
    for town, st in MART_STAGE_HINT.items():
        if area.startswith(town): return st
    base = re.split(r"_", area)[0]
    st = P.location_stage(base)
    if st is not None: return st
    m = P.MAP_STAGE.get(_camel_to_const(area))
    return m

def _camel_to_const(s):
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", s).upper()
    return s.replace("__", "_")

ITEM_STAGE = item_stage_table()

# Corrections/additions the scripts don't express directly.
# Gym leaders hand out their TM on defeat; the Game Corner sells four TMs for coins.
ITEM_STAGE_OVERRIDES = {
    "ITEM_TM39": (4, "Brock (Rock Tomb)"),
    "ITEM_TM03": (8, "Misty (Water Pulse)"),
    "ITEM_TM34": (11, "Lt. Surge (Shock Wave)"),
    "ITEM_TM19": (16, "Erika (Giga Drain)"),
    "ITEM_TM06": (22, "Koga (Toxic)"),
    "ITEM_TM04": (24, "Sabrina (Calm Mind)"),
    "ITEM_TM38": (27, "Blaine (Fire Blast)"),
    "ITEM_TM26": (29, "Giovanni (Earthquake)"),
    "ITEM_TM13": (15, "Celadon Game Corner (Ice Beam, 4000 coins)"),
    "ITEM_TM23": (15, "Celadon Game Corner (Iron Tail, 3500 coins)"),
    "ITEM_TM24": (15, "Celadon Game Corner (Thunderbolt, 4000 coins)"),
    "ITEM_TM30": (15, "Celadon Game Corner (Shadow Ball, 4500 coins)"),
    "ITEM_HM01": (11, "S.S. Anne Captain (Cut) — needs Cascade Badge"),
    "ITEM_HM02": (20, "Route 16 house (Fly) — needs Thunder Badge"),
    "ITEM_HM03": (23, "Safari Zone Secret House (Surf) — needs Soul Badge"),
    "ITEM_HM04": (21, "Warden, Fuchsia (Strength) — needs Rainbow Badge"),
    "ITEM_HM05": (13, "Route 2 East Building (Flash) — needs Boulder Badge"),
    "ITEM_HM06": (28, "Ember Spa, One Island (Rock Smash) — needs Marsh Badge"),
    "ITEM_HM07": (33, "Icefall Cave, Four Island (Waterfall) — needs Volcano Badge"),
}
for k, v in ITEM_STAGE_OVERRIDES.items():
    cur = ITEM_STAGE.get(k)
    if cur is None or v[0] < cur[0]:
        ITEM_STAGE[k] = v

# Forced corrections that DELAY an item past its map's stage. The burgled
# house in Cerulean is blocked by a policeman until Bill's S.S. Ticket is in
# hand (FLAG_GOT_SS_TICKET), so the Grunt's TM28 does not exist on the first
# Cerulean visit.
ITEM_STAGE_CORRECTIONS = {
    "ITEM_TM28": (8, "CeruleanCity — the burgled house, after the S.S. Ticket"),
}
ITEM_STAGE.update(ITEM_STAGE_CORRECTIONS)

_MAP_TYPE_CACHE = {}
def battle_terrain(map_name, kind=None, method=None):
    """Secret Power's arena, the way BattleSetup_GetTerrainId sees it:
    'cave' underground, 'building' indoors, 'grass' for wild land battles,
    'water' for surf/rod battles, else 'plain'."""
    mt = _MAP_TYPE_CACHE.get(map_name)
    if mt is None:
        try:
            mt = json.load(open(f"{REPO}/data/maps/{map_name}/map.json")).get("map_type", "")
        except Exception:
            mt = ""
        _MAP_TYPE_CACHE[map_name] = mt
    if mt == "MAP_TYPE_UNDERGROUND": return "cave"
    if mt in ("MAP_TYPE_INDOOR", "MAP_TYPE_SECRET_BASE"): return "building"
    m = (method or "").lower()
    if kind == "wild":
        if "land" in m: return "grass"
        if "water" in m or "rod" in m or "surf" in m: return "water"
    return "plain"

def tm_moves_by_stage(stage):
    """Every TM/HM move the player could have taught by `stage`."""
    out = {}
    for item, mv in TMHM_MOVE.items():
        info = ITEM_STAGE.get(item)
        if info and info[0] <= stage:
            out[mv] = info
    return out

# ------------------------------------------------------------------ VS Seeker rematches
def rematch_maps():
    """src/vs_seeker.c sRematches: each row is {base, tier2, tier3, ...} plus the
    map the trainer stands on. Only the entries after the first are rematches --
    the numeric suffix alone is not a reliable signal, because plenty of ordinary
    trainers (Team Rocket Grunt 22, Youngster 1) are just numbered."""
    txt = open(f"{REPO}/src/vs_seeker.c", encoding="utf-8").read()
    blk = txt[txt.index("sRematches[]"):]
    blk = blk[:blk.index("\n};")]
    maps, tiers = {}, set()
    for m in re.finditer(r"\{\s*\{([^}]*)\}\s*,\s*MAP\((MAP_[A-Z0-9_]+)\)", blk, re.S):
        trainers = re.findall(r"(TRAINER_[A-Z0-9_]+)", m.group(1))
        for i, t in enumerate(trainers):
            maps[t] = m.group(2)
            if i > 0 and t != trainers[0]:
                tiers.add(t)
    return maps, tiers

REMATCH_MAP, REMATCH_TIERS = rematch_maps()

# ------------------------------------------------------------------ classification
BOSS_CLASSES = {"Leader", "Elite Four", "Champion", "Rival Late", "Boss"}

POSTGAME_REMATCH = lambda c: c.endswith("_2") and "ELITE_FOUR" in c or "CHAMPION_REMATCH" in c

def classify(const, t):
    c = t["class"]
    if "CHAMPION" in const: return "champion"
    if "ELITE_FOUR" in const or c == "Elite Four": return "elite4"
    if c == "Leader" or "LEADER" in const: return "gym"
    if "RIVAL" in const: return "rival"
    if "GIOVANNI" in const: return "boss"
    if const in REMATCH_TIERS: return "rematch"
    return "trainer"

PRETTY_MAP = {}
def pretty_location(loc):
    """Turn CamelCase / SCREAMING_SNAKE map identifiers into readable place names."""
    if loc in PRETTY_MAP: return PRETTY_MAP[loc]
    s = loc
    if s.isupper():
        s = s.replace("_", " ").title()
    else:
        s = s.replace("_", " ")
        s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
        s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    for a, b in (("SSAnne", "S.S. Anne"), ("Ssanne", "S.S. Anne"),
                 ("SS Anne", "S.S. Anne"), ("Ss Anne", "S.S. Anne"),
                 ("Agathas", "Agatha's"), ("Loreleis", "Lorelei's"),
                 ("Brunos", "Bruno's"), ("Lances", "Lance's"),
                 ("Champions", "Champion's"), ("Silph Co", "Silph Co."),
                 ("Silph Co..", "Silph Co."), ("Digletts", "Diglett's"),
                 ("Mt Moon", "Mt. Moon"), ("Mt Ember", "Mt. Ember"),
                 ("Pokemon", "Pokémon"), ("Diglett S", "Diglett's"),
                 ("Lorelei S", "Lorelei's"), ("Bruno S", "Bruno's"),
                 ("Agatha S", "Agatha's"), ("Lance S", "Lance's"),
                 ("Champion S", "Champion's"), ("Oaks", "Oak's"),
                 ("Professor Oak's Lab", "Prof. Oak's Lab")):
        s = s.replace(a, b)
    s = re.sub(r"\bB\s*(\d+)\s*F\b", r"B\1F", s)
    s = re.sub(r"\b(\d+)\s*F\b", r"\1F", s)
    s = re.sub(r"\bRoute\s*(\d+)\b", r"Route \1", s)
    s = re.sub(r"\bRoom\s*(\d+)\b", r"Room \1", s)
    PRETTY_MAP[loc] = s
    return s

# The trainer constant's suffix names the RIVAL's starter; the player picked the
# one it is strong against.
RIVAL_STARTER_TO_PLAYER = {
    "SQUIRTLE": "Charmander", "BULBASAUR": "Squirtle", "CHARMANDER": "Bulbasaur",
}

def variant_label(const):
    for k, v in RIVAL_STARTER_TO_PLAYER.items():
        if const.endswith("_" + k):
            return v
    return None

# ------------------------------------------------------------------ build
def build_trainer_encounters():
    encs = []
    for const, t in E.TRAINERS.items():
        if not t["party"]: continue
        if not t["name"]: continue           # unused Red/Blue leftovers have no name
        party = t["party"]
        if len(party) == 1 and party[0]["species"] in ("SPECIES_EKANS", "SPECIES_STARMIE") \
           and party[0]["lvl"] in (5, 38) and not t["locations"]:
            continue                          # RS placeholder entries
        if not t["locations"] and const not in REMATCH_MAP:
            continue   # entry exists in the ROM's trainer table but nothing spawns it
        stage = P.trainer_stage(const, t)
        loc = t["locations"][0] if t["locations"] else None
        kind = classify(const, t)
        if stage is None and const in REMATCH_MAP:
            loc = REMATCH_MAP[const].replace("MAP_", "")
            stage = P.MAP_STAGE.get(loc)
            kind = "rematch"
        if stage is None:
            # fall back to the level curve: earliest stage whose baseline level
            # matches the party's strength
            mx = max(m["lvl"] for m in party)
            for s in P.STAGES:
                if s["level"] >= mx:
                    stage = s["id"]; break
            if stage is None: stage = P.MAX_STAGE
        if kind == "rematch":
            # rematches are only meaningful once the VS Seeker exists and the
            # tier is unlocked; anchor on the party's own level.
            mx = max(m["lvl"] for m in party)
            for s in P.STAGES:
                if s["level"] >= mx:
                    stage = max(stage, s["id"]); break

        mons = [E.realize_trainer_mon(const, i) for i in range(len(party))]
        cls = t["class"]
        if kind in ("rival", "champion"):
            cls = "Champion" if kind == "champion" else "Rival"
        starter = variant_label(const)
        rematch = POSTGAME_REMATCH(const)
        if rematch:
            stage = 33   # Elite Four / Champion rematches are post-National-Dex
        display = f"{cls} {t['name']}".strip()
        if rematch:
            display += " (rematch)"
        if starter:
            display += f" — you chose {starter}"
        encs.append({
            "id": f"trainer:{const}",
            "kind": kind,
            "trainerConst": const,
            "pic": t.get("pic"),
            "name": display,
            "starterVariant": starter,
            "trainerClass": cls,
            "trainerName": t["name"],
            "location": pretty_location(loc) if loc else "—",
            "locationRaw": loc,
            "stage": stage,
            "doubleBattle": t["doubleBattle"],
            "items": [E.ITEMS.get(i, {}).get("name", i) for i in t["items"] if i != "ITEM_NONE"],
            "party": [{
                "species": m["species"], "name": m["name"], "level": m["level"],
                "types": m["types"], "ability": m["ability"], "nature": m["natureName"],
                "moves": [{"const": mv, "name": E.MOVES[mv]["name"], "type": E.MOVES[mv]["type"],
                           "power": E.MOVES[mv]["power"], "category": E.MOVES[mv]["category"],
                           "accuracy": E.MOVES[mv]["accuracy"]}
                          for mv in m["moves"] if mv in E.MOVES],
                "stats": m["stats"], "item": m["item"],
            } for m in mons],
            "maxLevel": max(m["level"] for m in mons),
            "partySize": len(mons),
        })
    return encs

def build_wild_encounters():
    encs = []
    for enc in E.WILD["encounters"]:
        if enc["version"] == "FireRed": continue
        ms = P.map_stage(enc["map"])
        pretty = pretty_location(enc["map"].replace("MAP_", ""))
        for method, tbl in enc["tables"].items():
            if not tbl["slots"]: continue
            if method == "fishing_mons":
                for rod, idxs in E.WILD["fishingGroups"].items():
                    slots = [s for s in tbl["slots"] if s["slot"] in idxs]
                    if not slots: continue
                    st = max(ms, P.ROD_STAGE.get(rod, 0))
                    encs.append(_wild_node(enc, pretty, rod.replace("_", " ").title(),
                                           slots, st, tbl["encounterRate"]))
                continue
            st = max(ms, P.METHOD_GATE.get(method, 0))
            label = {"land_mons": "Grass / Cave", "water_mons": "Surfing",
                     "rock_smash_mons": "Rock Smash"}[method]
            encs.append(_wild_node(enc, pretty, label, tbl["slots"], st, tbl["encounterRate"]))
    return encs

def _wild_node(enc, pretty, method_label, slots, stage, rate):
    agg = defaultdict(lambda: {"rate": 0, "min": 99, "max": 0})
    for s in slots:
        a = agg[s["species"]]
        a["rate"] += s["rate"]
        a["min"] = min(a["min"], s["minLevel"])
        a["max"] = max(a["max"], s["maxLevel"])
    total = sum(a["rate"] for a in agg.values()) or 1
    mons = []
    for sp, a in sorted(agg.items(), key=lambda kv: -kv[1]["rate"]):
        mons.append({
            "species": sp, "name": E.SPECIES[sp]["name"], "types": E.SPECIES[sp]["types"],
            "minLevel": a["min"], "maxLevel": a["max"],
            "chance": round(a["rate"] / total * 100, 1),
        })
    return {
        "id": f"wild:{enc['map']}:{method_label.replace(' ', '')}",
        "kind": "wild",
        "name": f"{pretty} — {method_label}",
        "location": pretty, "locationRaw": enc["map"].replace("MAP_", ""),
        "method": method_label, "stage": stage,
        "encounterRate": rate,
        "wildMons": mons,
        "maxLevel": max(m["maxLevel"] for m in mons),
    }

if __name__ == "__main__":
    tenc = build_trainer_encounters()
    wenc = build_wild_encounters()
    print(f"trainer encounters: {len(tenc)}")
    print(f"wild encounter areas: {len(wenc)}")
    by_kind = defaultdict(int)
    for e in tenc: by_kind[e["kind"]] += 1
    print(dict(by_kind))
    # Altering Cave ships nine alternate encounter tables for an e-Reader event
    # that never released outside Japan; without it only the first (Zubat) applies.
    seen, deduped = set(), []
    for e in tenc + wenc:
        if e["id"] in seen: continue
        seen.add(e["id"]); deduped.append(e)
    graph = sorted(deduped, key=lambda e: (e["stage"], e["kind"] != "gym", e["name"]))
    with open(os.path.join(OUT, "encounters.json"), "w") as f:
        json.dump(graph, f, separators=(",", ":"))
    with open(os.path.join(OUT, "itemStages.json"), "w") as f:
        json.dump({k: {"stage": v[0], "where": v[1]} for k, v in ITEM_STAGE.items()}, f)
    with open(os.path.join(OUT, "tmhmMoves.json"), "w") as f:
        json.dump(TMHM_MOVE, f)

    print("\nTM availability spot-check:")
    for it in ["ITEM_TM24", "ITEM_TM13", "ITEM_TM26", "ITEM_TM38", "ITEM_TM03", "ITEM_HM03"]:
        s = ITEM_STAGE.get(it)
        print(f"  {it} {TMHM_MOVE.get(it,''):20} stage {s[0] if s else '?':>3}  {s[1] if s else 'NOT FOUND'}")

    print("\nfirst 12 encounters in order:")
    for e in graph[:12]:
        print(f"  [{e['stage']:2}] {e['kind']:8} {e['name'][:42]:44} {e['location'][:26]:28} L{e['maxLevel']}")
