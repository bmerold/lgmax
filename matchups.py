"""The matchup space: every one-on-one fight in the game, computed once, independent
of version, starter, trades, team or route.

A fight between one of your Pokemon and one trainer Pokemon is decided by three
context-free pieces, and this module tabulates each of them:

  * OFFENSE  (species, level, move) -> foe: the damage of one hit, as the two
    integers CalculateBaseDamage + Cmd_damagecalc produce BEFORE the random roll
    (normal and critical). engine.damage_rolls applies ApplyRandomDmgMultiplier
    to that integer -- max(1, d * (100 - r) // 100) for r in 0..15 -- so the full
    16-roll distribution is rebuilt exactly from it (see `rolls`). One entry per
    move, so a Kadabra out of Psychic falls back to Psybeam's own entry.
  * DEFENSE  foe move -> (species, level): the same pair of integers for each of
    the foe's four moves, so the trainer AI's pick (and anything after it) is a
    lookup.
  * SPEED / HP: the player's stats per (species, level) and the foe's realized
    stats, so who moves first and how much HP there is are lookups too.

What is NOT context-free is applied when fights are combined (mq.py): HP and PP
carried between fights, switching, trainer item use, League boosts, obedience.
The one environmental input baked in is the badge stat boost (FRLG's
ShouldGetStatBadgeBoost: Boulder +10% Atk, Soul +10% Def, Volcano +10% SpA/SpD),
which is a property of WHERE the foe stands in the story -- the same in both
versions -- so each foe carries the badge tier of its story stage.

Trainer parties are identical in FireRed and LeafGreen (src/data/trainer_parties.h
has no version #ifdefs); the species universe is the union of what either version
can obtain, so filtering by version, starter or trade is done downstream.

Output: data/matchups.bin.gz (flat uint16 arrays, layout in the meta) +
data/matchups_meta.json (the index). Standard library only, like the rest of the
pipeline.
"""
import gzip, json, os, subprocess, sys, time
import multiprocessing as mp
from array import array
import engine as E
import progression as P
import build_graph as G

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# "Within reason": nobody needs to know an L100 beats Brock. The band runs from a
# little under the foe (an underlevelled specialist still matters) to well over it
# (a solo run of one Pokemon overlevels everything).
LEVEL_BELOW = 10
LEVEL_ABOVE = 30
MIN_LEVEL = 2

# Moves the engine prices with a fixed or level-based formula, not power
# (optimize.move_pool keeps exactly these zero-power damaging effects).
SPECIAL_DAMAGE = {"DRAGON_RAGE", "SONICBOOM", "LEVEL_DAMAGE", "PSYWAVE", "SUPER_FANG"}

def badges_at(stage):
    """optimize.badges_for, restated so this module stays free of solve state."""
    n = 0
    for b, st in P.BADGE_STAGE.items():
        if stage >= st: n = max(n, b)
    return {"count": n, "atk": n >= 1, "def": n >= 5, "spatk": n >= 7, "spdef": n >= 7}

def is_damaging(mv):
    m = E.MOVES.get(mv)
    if not m or m["effect"] in E.UNUSABLE_EFFECTS: return False
    return m["power"] > 0 or m["effect"] in SPECIAL_DAMAGE

def move_universe(species):
    """Every damaging move this species can ever know -> (learn level, source).
    Level-up moves carry their level; TM/HM and tutor moves carry 0 (the filter
    layer gates them by story stage, which is context)."""
    out = {}
    for lv, mv in E.LEVELUP.get(species, []):
        if is_damaging(mv) and (mv not in out or lv < out[mv][0]):
            out[mv] = (lv, "level")
    for item in E.TMHM.get(species, []):
        mv = G.TMHM_MOVE.get("ITEM_" + item.split("_")[0])
        if mv and is_damaging(mv) and mv not in out:
            out[mv] = (0, item.split("_")[0])
    for t in E.TUTOR.get(species, []):
        mv = "MOVE_" + t
        if is_damaging(mv) and mv not in out:
            out[mv] = (0, "tutor")
    return out

def _availability(version):
    """progression.full_availability() as `version` sees it, from a clean
    interpreter (engine binds the version at import)."""
    code = ("import json,progression as P;"
            "print(json.dumps({k:{'stage':v['stage'],'trade':bool(v.get('requiresTrade')),"
            "'source':v.get('source')} for k,v in P.full_availability().items()}))")
    env = dict(os.environ, LGMAX_VERSION=version, PYTHONHASHSEED="0")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                         text=True, check=True, cwd=os.path.dirname(os.path.abspath(__file__)))
    return json.loads(out.stdout.strip().splitlines()[-1])

def species_universe():
    """Union over both versions: species -> earliest stage, per-version detail."""
    out = {}
    for v in ("lg", "fr"):
        for sp, rec in _availability(v).items():
            if sp not in E.SPECIES: continue
            r = out.setdefault(sp, {"stage": rec["stage"], "versions": {}})
            r["stage"] = min(r["stage"], rec["stage"])
            r["versions"][v] = rec
    return out

def foe_universe():
    """Every trainer Pokemon the story can put in front of you, deduplicated.
    Two trainers fielding the identical specimen (species, level, IVs, nature,
    moves, held item) at the same badge tier share one foe entry."""
    foes, index = [], {}
    for const, t in sorted(E.TRAINERS.items()):
        if not t.get("party") or t.get("class") in P.SKIP_CLASSES: continue
        stage = P.trainer_stage(const, t)
        if stage is None: continue
        b = badges_at(stage)
        for i in range(len(t["party"])):
            m = E.realize_trainer_mon(const, i)
            key = (m["species"], m["level"], m["ivs"]["hp"], m["nature"],
                   tuple(m["moves"]), m["item"], b["atk"], b["spatk"], b["def"], b["spdef"])
            fid = index.get(key)
            if fid is None:
                fid = index[key] = len(foes)
                foes.append({"species": m["species"], "level": m["level"],
                             "iv": m["ivs"]["hp"], "nature": m["nature"],
                             "moves": list(m["moves"]), "item": m["item"],
                             "ability": m["ability"], "types": m["types"],
                             "stats": m["stats"], "stage": stage, "badges": b,
                             "lo": max(MIN_LEVEL, m["level"] - LEVEL_BELOW),
                             "hi": min(100, m["level"] + LEVEL_ABOVE),
                             "trainers": []})
            foes[fid]["trainers"].append([const, i])
            foes[fid]["stage"] = min(foes[fid]["stage"], stage)
    return foes

def player_mon(species, level):
    """The player's specimen, exactly as optimize.player_mon builds it."""
    return E.make_mon(species, level, ivs_flat=15, nature=0, moves=[])

# ---- per-foe work (forked workers read these globals)
_SPECIES, _MOVES, _FOES = None, None, None

def _foe_mon(f, idx):
    """Rebuild the foe exactly as realize_trainer_mon does (first trainer that
    fields it -- every trainer sharing the entry fields the same specimen)."""
    const, i = f["trainers"][0]
    return E.realize_trainer_mon(const, i)

def _one_foe(fi):
    f = _FOES[fi]
    foe = _foe_mon(f, fi)
    ob = {"atk": f["badges"]["atk"], "spatk": f["badges"]["spatk"]}
    db = {"def": f["badges"]["def"], "spdef": f["badges"]["spdef"]}
    offs, defs, sps = [], [], []
    for si, sp in enumerate(_SPECIES):
        if _AVAIL_STAGE[si] > f["stage"]: continue
        mv = _MOVES[si]
        nl = f["hi"] - f["lo"] + 1
        # row-major [level][move][normal, crit]
        off = array("H", bytes(2 * nl * len(mv) * 2))
        dfn = array("H", bytes(2 * nl * len(f["moves"]) * 2))
        for li, lv in enumerate(range(f["lo"], f["hi"] + 1)):
            pm = player_mon(sp, lv)
            a, b = E.with_entry_boosts(pm, foe)          # Intimidate, both ways
            base = li * len(mv) * 2
            for mi, m in enumerate(mv):
                off[base + 2 * mi] = E.damage_rolls(a, b, m, False, ob)[0]
                off[base + 2 * mi + 1] = E.damage_rolls(a, b, m, True, ob)[0]
            fb, pb = E.with_entry_boosts(foe, pm)
            base = li * len(f["moves"]) * 2
            for mi, m in enumerate(f["moves"]):
                if m not in E.MOVES: continue
                dfn[base + 2 * mi] = E.damage_rolls(fb, pb, m, False, db)[0]
                dfn[base + 2 * mi + 1] = E.damage_rolls(fb, pb, m, True, db)[0]
        sps.append(si); offs.append(off); defs.append(dfn)
    return fi, sps, offs, defs

_AVAIL_STAGE = None

def build():
    global _SPECIES, _MOVES, _FOES, _AVAIL_STAGE
    t0 = time.time()
    uni = species_universe()
    _SPECIES = sorted(uni)
    _AVAIL_STAGE = [uni[s]["stage"] for s in _SPECIES]
    mu = [move_universe(s) for s in _SPECIES]
    _MOVES = [sorted(m) for m in mu]
    _FOES = foe_universe()
    print(f"  {len(_SPECIES)} species, {len(_FOES)} distinct foes, "
          f"{sum(len(m) for m in _MOVES)} species-moves", flush=True)

    workers = int(os.environ.get("LGMAX_WORKERS", max(1, (os.cpu_count() or 2) - 2)))
    results = [None] * len(_FOES)
    with mp.get_context("fork").Pool(workers) as pool:
        for fi, sps, offs, defs in pool.imap_unordered(_one_foe, range(len(_FOES)), chunksize=4):
            results[fi] = (sps, offs, defs)

    # flatten: per foe, the species it was computed against and where each
    # (foe, species) block starts in the offense / defense arrays
    off_all, def_all, pairs = array("H"), array("H"), []
    for fi, (sps, offs, defs) in enumerate(results):
        for si, o, d in zip(sps, offs, defs):
            pairs.append([fi, si, len(off_all), len(def_all)])
            off_all.extend(o); def_all.extend(d)
    stats = array("H")
    for sp in _SPECIES:
        for lv in range(0, 101):
            s = player_mon(sp, max(1, lv))["stats"]
            stats.extend([s["hp"], s["attack"], s["defense"], s["speed"],
                          s["spAttack"], s["spDefense"]])
    with gzip.open(os.path.join(OUT, "matchups.bin.gz"), "wb", compresslevel=6) as fh:
        for arr in (off_all, def_all, stats):
            fh.write(arr.tobytes())
    meta = {"species": [{"const": s, "name": E.SPECIES[s]["name"],
                         "types": E.SPECIES[s]["types"],
                         "ability": E.SPECIES[s]["abilities"][0],
                         "stage": uni[s]["stage"], "versions": uni[s]["versions"],
                         "moves": [[m, mu[i][m][0], mu[i][m][1]] for m in _MOVES[i]]}
                        for i, s in enumerate(_SPECIES)],
            "foes": _FOES,
            "band": {"below": LEVEL_BELOW, "above": LEVEL_ABOVE, "min": MIN_LEVEL},
            # [foe, species, offense start, defense start] -- offense block is
            # [level lo..hi][species move][normal, crit]; defense block is
            # [level lo..hi][foe move][normal, crit]; stats is [species][level 0..100]
            # [hp, atk, def, spe, spa, spd]. All uint16, native byte order.
            "pairs": pairs,
            "sizes": {"off": len(off_all), "def": len(def_all), "stats": len(stats)},
            "byteorder": sys.byteorder}
    json.dump(meta, open(os.path.join(OUT, "matchups_meta.json"), "w"))
    print(f"  {len(pairs)} foe x species pairs, {len(off_all) // 2:,} offense entries, "
          f"{len(def_all) // 2:,} defense entries in {time.time() - t0:.0f}s", flush=True)

if __name__ == "__main__":
    build()
