"""The wild half of the matchup space: every one-on-one fight against a wild
Pokemon, computed once, independent of version, starter, route or story stage.

matchups.py tabulates the trainer Pokemon; this module does the same for the
wild slots, so the grind simulator's question -- how many turns does a level
cost here, and can I camp it -- becomes table lookups too (mq.WildSpace).

A wild foe is fully determined by (species, level): CreateWildMon builds it with
GiveMonInitialMoveset's level-up moves, and the grind model fixes the rest the
way training.py does (engine.make_mon: flat 15 IVs, neutral nature, first
ability). The level is the one training.py fights: the rounded midpoint of the
slot's aggregated level range (build_graph._wild_node, training._area_opponents).

Unlike a trainer foe, a wild foe has no story stage of its own -- you can return
to Route 1 with eight badges -- so the badge boost (ShouldGetStatBadgeBoost,
player side only) is NOT baked in. Each cell stores both variants, and a move
needs only one badge: a physical hit reads Boulder (+10% Atk), a special hit
Volcano (+10% SpA); a foe's physical hit reads Soul (+10% Def), a special one
Volcano (+10% SpD). So a cell is four integers:
  [normal, crit, normal with the badge, crit with the badge]
and the query layer picks the pair the run's badges and the move's category name.

Output: data/wild_matchups.bin.gz (offense + defense, uint16) and
data/wild_matchups_meta.json (the index, with the species and move universe --
the same one matchups.py uses, rebuilt here so the wild table stands alone).
The build is skipped when its inputs are unchanged (inputsHash in the meta);
LGMAX_FORCE_WILD=1 rebuilds anyway.
"""
import gzip, hashlib, json, os, subprocess, sys, time
import multiprocessing as mp
from array import array
import engine as E
import build_graph as G
import matchups as M

OUT = M.OUT

# The player-level band per wild foe, as in matchups.py: an underlevelled
# newcomer grinding up from below matters, and so does a strong mon farming a
# weak area. Cells outside it are computed on demand by the query layer.
LEVEL_BELOW = 10
LEVEL_ABOVE = 30
MIN_LEVEL = 2
NO_BADGE = {"atk": False, "spatk": False, "def": False, "spdef": False}
ALL_BADGES = {"atk": True, "spatk": True, "def": True, "spdef": True}
VALUES = 4          # normal, crit, normal + badge, crit + badge

_METHOD_LABEL = {"land_mons": "Grass / Cave", "water_mons": "Surfing",
                 "rock_smash_mons": "Rock Smash"}

def wild_level(slot):
    """The level training._area_opponents fights a slot at."""
    return round((slot["minLevel"] + slot["maxLevel"]) / 2)

def wild_areas():
    """Every wild table in BOTH versions, aggregated per species exactly as
    build_graph._wild_node does (the stage is context and left at 0)."""
    areas = []
    for enc in E.WILD["encounters"]:
        for method, tbl in enc["tables"].items():
            if not tbl["slots"]: continue
            groups = ([(rod.replace("_", " ").title(), [s for s in tbl["slots"] if s["slot"] in idx])
                       for rod, idx in E.WILD["fishingGroups"].items()]
                      if method == "fishing_mons" else [(_METHOD_LABEL[method], tbl["slots"])])
            for label, slots in groups:
                if slots:
                    node = G._wild_node(enc, "", label, slots, 0, tbl["encounterRate"])
                    node["version"] = enc["version"]
                    areas.append(node)
    return areas

def foe_universe():
    """Distinct (species, level) wild foes across both versions."""
    seen = set()
    for node in wild_areas():
        for s in node["wildMons"]:
            if s["species"] in E.SPECIES:
                seen.add((s["species"], wild_level(s)))
    foes = []
    for sp, lv in sorted(seen):
        m = E.make_mon(sp, lv)
        foes.append({"species": sp, "level": lv, "moves": list(m["moves"]),
                     "ability": m["ability"], "types": m["types"], "stats": m["stats"],
                     "lo": max(MIN_LEVEL, lv - LEVEL_BELOW), "hi": min(100, lv + LEVEL_ABOVE)})
    return foes

# ---- per-foe work (forked workers read these globals)
_SPECIES, _MOVES, _FOES = None, None, None

def cell(att, dfn, move, attacker_is_player):
    """The four integers of one cell (see the module doc)."""
    b0 = NO_BADGE
    b1 = ({"atk": True, "spatk": True} if attacker_is_player
          else {"def": True, "spdef": True})
    return (E.damage_rolls(att, dfn, move, False, b0)[0],
            E.damage_rolls(att, dfn, move, True, b0)[0],
            E.damage_rolls(att, dfn, move, False, b1)[0],
            E.damage_rolls(att, dfn, move, True, b1)[0])

def _one_foe(fi):
    f = _FOES[fi]
    foe = E.make_mon(f["species"], f["level"])
    nl = f["hi"] - f["lo"] + 1
    offs, defs = [], []
    for si, sp in enumerate(_SPECIES):
        mv = _MOVES[si]
        off = array("H", bytes(2 * nl * len(mv) * VALUES))
        dfn = array("H", bytes(2 * nl * len(f["moves"]) * VALUES))
        for li, lv in enumerate(range(f["lo"], f["hi"] + 1)):
            pm = M.player_mon(sp, lv)
            a, b = E.with_entry_boosts(pm, foe)          # Intimidate, both ways
            base = li * len(mv) * VALUES
            for mi, m in enumerate(mv):
                off[base + VALUES * mi: base + VALUES * (mi + 1)] = array("H", cell(a, b, m, True))
            fb, pb = E.with_entry_boosts(foe, pm)
            base = li * len(f["moves"]) * VALUES
            for mi, m in enumerate(f["moves"]):
                if m in E.MOVES:
                    dfn[base + VALUES * mi: base + VALUES * (mi + 1)] = array("H", cell(fb, pb, m, False))
        offs.append(off); defs.append(dfn)
    return fi, offs, defs

HERE = os.path.dirname(os.path.abspath(__file__))
META = os.path.join(OUT, "wild_matchups_meta.json")
BIN = os.path.join(OUT, "wild_matchups.bin.gz")

def inputs_hash():
    """Everything a cell depends on: the damage engine and abilities, the wild
    tables and species/move data, and the builders themselves."""
    h = hashlib.sha256()
    for mod in ("wild_matchups.py", "matchups.py", "engine.py", "abilities.py",
                "build_graph.py", "progression.py"):
        h.update(mod.encode()); h.update(open(os.path.join(HERE, mod), "rb").read())
    for d in ("wild.json", "species.json", "moves.json", "levelUpLearnsets.json",
              "tmhmLearnsets.json", "tutorLearnsets.json",
              "evolutions.json", "typeChart.json"):
        p = os.path.join(OUT, d)
        if os.path.exists(p):
            h.update(d.encode()); h.update(open(p, "rb").read())
    try:
        rev = subprocess.run(["git", "-C", E.REPO, "rev-parse",
                              "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
        h.update(rev.encode())
    except Exception:
        pass
    h.update(repr((LEVEL_BELOW, LEVEL_ABOVE, MIN_LEVEL, VALUES)).encode())
    return h.hexdigest()

def up_to_date(want=None):
    if not (os.path.exists(META) and os.path.exists(BIN)): return False
    try:
        return json.load(open(META)).get("inputsHash") == (want or inputs_hash())
    except Exception:
        return False

def build():
    global _SPECIES, _MOVES, _FOES
    t0 = time.time()
    want = inputs_hash()
    if not os.environ.get("LGMAX_FORCE_WILD") and up_to_date(want):
        print("wild_matchups: inputs unchanged, reusing data/wild_matchups.bin.gz "
              "(LGMAX_FORCE_WILD=1 rebuilds)")
        return
    # the species and move universe matchups.py uses (both versions' obtainable
    # species, every damaging move each can ever know), so indices agree
    uni = M.species_universe()
    _SPECIES = sorted(uni)
    mu = [M.move_universe(s) for s in _SPECIES]
    _MOVES = [sorted(m) for m in mu]
    _FOES = foe_universe()
    print(f"  {len(_SPECIES)} species, {len(_FOES)} distinct wild foes", flush=True)

    workers = int(os.environ.get("LGMAX_WORKERS") or max(1, (os.cpu_count() or 2) - 2))
    results = [None] * len(_FOES)
    with mp.get_context("fork").Pool(workers) as pool:
        for fi, offs, defs in pool.imap_unordered(_one_foe, range(len(_FOES)), chunksize=2):
            results[fi] = (offs, defs)

    # every (foe, species) pair is stored -- no stage filter: any Pokemon can
    # walk back into any area -- so a pair's block start is an index lookup
    off_all, def_all, starts = array("H"), array("H"), []
    for fi, (offs, defs) in enumerate(results):
        row = []
        for o, d in zip(offs, defs):
            row.append([len(off_all), len(def_all)])
            off_all.extend(o); def_all.extend(d)
        starts.append(row)
    with gzip.open(BIN, "wb", compresslevel=6) as fh:
        for arr in (off_all, def_all):
            fh.write(arr.tobytes())
    meta = {"species": [{"const": sp, "name": E.SPECIES[sp]["name"],
                         "ability": E.SPECIES[sp]["abilities"][0],
                         "moves": [[m, mu[i][m][0], mu[i][m][1]] for m in _MOVES[i]]}
                        for i, sp in enumerate(_SPECIES)],
            "foes": _FOES, "areas": wild_areas(), "inputsHash": want,
            "band": {"below": LEVEL_BELOW, "above": LEVEL_ABOVE, "min": MIN_LEVEL},
            # starts[foe][species] = [offense start, defense start]; offense block is
            # [level lo..hi][species move][VALUES], defense block is
            # [level lo..hi][foe move][VALUES], indexed by "species" and each
            # species' "moves" above. All uint16, native byte order.
            "starts": starts, "values": VALUES,
            "sizes": {"off": len(off_all), "def": len(def_all)},
            "byteorder": sys.byteorder}
    json.dump(meta, open(META, "w"))
    print(f"  {len(_FOES)} wild foes x {len(_SPECIES)} species, "
          f"{len(off_all) // VALUES:,} offense cells, {len(def_all) // VALUES:,} defense cells "
          f"in {time.time() - t0:.0f}s", flush=True)

if __name__ == "__main__":
    build()
