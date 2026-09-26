#!/usr/bin/env python3
"""Training / grind calculator: where to spend the fewest battle turns to level a
Pokémon up, staying with that mon (no switching to a stronger sweeper).

This module owns the experience model and the per-area grind cost; it reuses the
existing engine for turns-to-KO and the encounter tables for what a wild area
offers. Everything here is grounded in the decompilation:

  - the growth curves are the macros in src/data/pokemon/experience_tables.h
    (gExperienceTables) transcribed as their closed forms;
  - experience gained on a KO is expYield * faintedLevel / 7 (integer), from
    src/battle_script_commands.c:3166 (single participant, no trade bonus).

The rest of the pipeline (the "run never grinds") ignores XP entirely, so this is
a standalone calculator: a catch-up tool that answers "if you're behind the
level targets, where do you close the gap fastest?".
"""


# ------------------------------------------------------------------ exp curve
def _cube(n):
    return n * n * n


def exp_at_level(growth, level):
    """Total experience needed to *be* at `level`, per gExperienceTables. `growth`
    is a species' growthRate string ("MEDIUM_FAST", "GROWTH_SLOW", ...). The decomp
    table hardcodes levels 0 and 1 (0 and 1 exp) and uses the macro for level >= 2;
    the closed forms below are exactly those macros with C integer division."""
    n = int(level)
    if n <= 0:
        return 0
    if n == 1:
        return 1
    g = str(growth).upper().replace("GROWTH_", "")
    if g == "MEDIUM_FAST":
        return _cube(n)
    if g == "FAST":
        return 4 * _cube(n) // 5
    if g == "SLOW":
        return 5 * _cube(n) // 4
    if g == "MEDIUM_SLOW":
        return 6 * _cube(n) // 5 - 15 * n * n + 100 * n - 140
    if g == "ERRATIC":
        if n <= 50:
            return (100 - n) * _cube(n) // 50
        if n <= 68:
            return (150 - n) * _cube(n) // 100
        if n <= 98:
            return ((1911 - 10 * n) // 3) * _cube(n) // 500
        return (160 - n) * _cube(n) // 100
    if g == "FLUCTUATING":
        if n <= 15:
            return ((n + 1) // 3 + 24) * _cube(n) // 50
        if n <= 36:
            return (n + 14) * _cube(n) // 50
        return (n // 2 + 32) * _cube(n) // 50
    return _cube(n)   # unknown group -> Medium Fast, the modal curve


def exp_to_next(growth, level):
    """Experience from the start of `level` to the start of `level + 1`."""
    return exp_at_level(growth, level + 1) - exp_at_level(growth, level)


# ------------------------------------------------------------------ exp gain
def exp_on_ko(exp_yield, foe_level):
    """Experience a single participant gains for fainting a wild Pokémon:
    expYield * level / 7, integer (src/battle_script_commands.c:3166). No trade
    bonus -- a mon you caught yourself; an in-game-trade mon would earn 1.5x."""
    return int(exp_yield) * int(foe_level) // 7


# ------------------------------------------------------------------ grind cost
import json as _json, os as _os, math as _math
import engine as E
import progression as P
import optimize as O
import hms as HM
import build_graph as BG
import tms as _TMS

_OUT = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data")

# TM item -> obtainable stage (data/itemStages.json), and single-use vs renewable
# (tms.extract: copies is None for a restockable Dept.-store TM, repeatable for a
# Game Corner one). HMs are never consumed, so they are always allowed.
_ITEM_STAGE = (_json.load(open(f"{_OUT}/itemStages.json"))
               if _os.path.exists(f"{_OUT}/itemStages.json") else {})
_SUPPLY = _TMS.extract()
_HM_MOVES = [(h, mv) for h, mv in HM.HM_MOVE.items()]
TOGGLES = ("none", "renew", "any")   # TMs: none / renewable-only / any single-use
SUSTAIN_CAP = 99   # fights before healing; above this is effectively "no limit"

# Effective grind time = battle time + the walk to a Pokémon Center and back
# whenever HP/PP runs out. Time constants mirror the app's playtime model
# (SEC_PER_STEP / SEC_PER_TURN / SEC_PER_FIGHT); HEAL_SEC covers the nurse.
SEC_PER_STEP, SEC_PER_TURN, SEC_PER_FIGHT, HEAL_SEC = 0.28, 9.0, 22.0, 15.0

# Nearest-Center round-trip steps per map folder, via the world graph. The world
# BFS is expensive (cold-grid parse + Dijkstra), so we compute ONE round-trip per
# area at a "settled" stage where the local Centers are open, and reuse it across
# levels -- the nearest Center barely moves once a town is reachable, and this is
# a heal-cost estimate for ranking, not an exact walk. Cached by folder and (in
# main) prefilled before forking so workers share it. See tour.insert_heal for
# the same door-tile BFS pattern.
_HEAL_CACHE = {}
_CENTER_DOORS = {}
_SETTLE_STAGE = 25   # by here every Kanto Pokémon Center is open


def _center_doors_at(stage):
    import world as WD, tour as TOUR
    if stage not in _CENTER_DOORS:
        _CENTER_DOORS[stage] = [(m, x, y) for m, xys in TOUR.center_doors().items()
                                if WD._open_map(m, stage) for (x, y) in xys]
    return _CENTER_DOORS[stage]


def heal_roundtrip(folder, area_stage):
    """(round-trip steps, Center map) from a map's grind tile to the nearest
    Pokémon Center, or (None, None) if none is reachable. Computed once per folder
    at a settled stage (so the town's own Center is open) and cached."""
    if folder in _HEAL_CACHE:
        return _HEAL_CACHE[folder]
    import world as WD
    stage = max(int(area_stage or 0), _SETTLE_STAGE)
    res = (None, None)
    anchor = WD.encounter_anchor(folder, "land") or WD.first_open_tile(folder)
    if anchor:
        start = WD.reach_tile((folder, anchor[0], anchor[1]), stage)
        doors = _center_doors_at(stage)
        dist = WD.bfs(start, stage, targets=set(doors)) if doors else {}
        reach = [(dist[t], t[0]) for t in doors if t in dist]
        if reach:
            near, cmap = min(reach)
            res = (2 * near, cmap)
    _HEAL_CACHE[folder] = res
    return res


def _folder_of(node):
    import world as WD
    const = (node.get("id") or "").split(":")
    const = const[1] if len(const) > 1 else None
    return WD._const_to_folder().get(const) if const else None


def _tm_renewable(item):
    s = _SUPPLY.get(item)
    return bool(s and (s.get("copies") is None or s.get("repeatable")))


def build_pool(species, level, stage, toggle):
    """The moves this mon could grind with at `stage`: its level-up moves, every
    HM it can field by now (reusable, so always offered), and TMs per `toggle`
    (none / renewable-only / any). Damaging moves are picked from this by the
    engine; teaching an HM or TM here models "give it a stronger attack to grind"."""
    moves = set(E.learnable_by(species, level))
    for h, mv in _HM_MOVES:
        if P.HM_STAGE.get(h, 99) <= stage and HM.can_learn(species, mv):
            moves.add(mv)
    if toggle != "none":
        for tag in E.TMHM.get(species, []):
            if tag.startswith("HM"):
                continue
            item = "ITEM_" + tag.split("_")[0]      # "TM06_TOXIC" -> "ITEM_TM06"
            mv = BG.TMHM_MOVE.get(item)
            if not mv:
                continue
            st = _ITEM_STAGE.get(item, {}).get("stage")
            if st is None or st > stage:
                continue
            if toggle == "renew" and not _tm_renewable(item):
                continue
            moves.add(mv)
    return list(moves)


def _wild_areas(encounters, stage):
    """Every wild area reachable by `stage`, from data/encounters.json."""
    return [e for e in encounters if e.get("kind") == "wild" and e.get("stage", 99) <= stage]


def area_grind(species, level, stage, pool, node, badges):
    """Battle turns to gain one full level grinding this mon in one wild area, or
    None if it can't clear the area's encounters. Turns and XP are both averaged
    over the area's encounter slots (weighted by slot chance), so the ratio is
    scale-free -- turns/level = xp_to_next * (turns/encounter) / (xp/encounter)."""
    slots = node.get("wildMons") or []
    opps, xp_per = [], 0.0
    for s in slots:
        w = (s.get("chance") or 0) / 100.0
        if w <= 0 or s["species"] not in E.SPECIES:
            continue
        lvl = round((s["minLevel"] + s["maxLevel"]) / 2)
        opps.append((E.make_mon(s["species"], lvl), w))
        xp_per += w * exp_on_ko(E.SPECIES[s["species"]].get("expYield", 0), lvl)
    if not opps or xp_per <= 0:
        return None
    res = O._weighted_sweep(E.make_mon(species, level), opps, pool, badges)
    if res is None or not res.get("turns"):
        return None
    xp_needed = exp_to_next(E.SPECIES[species].get("growthRate", "MEDIUM_FAST"), level)
    turns_per_level = xp_needed / xp_per * res["turns"]
    per = res.get("perOpponent") or []
    # the move it leans on (vs the likeliest slot), for the "which HM/TM helps" note
    top = max(per, key=lambda r: r.get("chance", 0), default=None)
    # Sustainability: how many fights before you must walk to a Center, the tighter
    # of HP attrition and PP. HP: you heal once your HP would fall below the worst
    # single fight (so the next one can't KO you), losing `damageTaken` per fight.
    # PP: each attacking move drains by how often it's used; the first to empty caps
    # the run. (The sweep already computes damage taken and the per-slot move.)
    maxhp, avg_taken = res["hp"], res["damageTaken"]
    worst = max((p.get("takenHere", 0) for p in per), default=0)
    sustain_hp = (maxhp - worst) / avg_taken if avg_taken > 0 else float("inf")
    usage = {}
    for p in per:
        mv = p.get("moveConst")
        usage[mv] = usage.get(mv, 0.0) + (p.get("chance", 0) / 100.0) * p.get("turns", 0)
    sustain_pp = min((E.MOVES[mv]["pp"] / u for mv, u in usage.items()
                      if u > 0 and mv in E.MOVES), default=float("inf"))
    sustain = min(SUSTAIN_CAP, max(0, int(min(sustain_hp, sustain_pp))))
    # per-wild-mon breakdown: the move used against each slot (it differs by type)
    mons = [[p["opp"], p["oppLevel"], round(p.get("chance", 0)), p.get("move")]
            for p in per]
    # Effective time to level = the fights, plus a Center round-trip each time HP
    # or PP would run dry. This is the ranking key, so a fragile spot near no
    # Center loses to a steady one you can camp -- sustainability, not raw speed.
    battles = turns_per_level / res["turns"]
    battle_sec = battles * (res["turns"] * SEC_PER_TURN + SEC_PER_FIGHT)
    folder = _folder_of(node)
    rt, cmap = heal_roundtrip(folder, stage) if folder else (None, None)
    heal_trips = max(0, _math.ceil(battles / max(1, sustain)) - 1)
    step_sec = (rt if rt is not None else 400) * SEC_PER_STEP + HEAL_SEC
    eff_sec = battle_sec + heal_trips * step_sec
    return {"turns_per_level": turns_per_level, "turns_per_battle": res["turns"],
            "xp_per_battle": xp_per, "move": (top or {}).get("move"),
            "sustain": sustain, "mons": mons, "eff_sec": eff_sec,
            "round_trip": rt, "center": cmap, "node": node}


def best_grind(species, level, stage, toggle, encounters, badges):
    """The reachable wild area with the least EFFECTIVE grind time (fights plus
    heal round-trips), or None. Ranking by effective time is what folds
    sustainability into the recommendation."""
    pool = build_pool(species, level, stage, toggle)
    if not pool:
        return None
    best = None
    for node in _wild_areas(encounters, stage):
        got = area_grind(species, level, stage, pool, node, badges)
        if got and (best is None or got["eff_sec"] < best["eff_sec"]):
            best = got
    if not best:
        return None
    n = best["node"]
    return {
        "area": n.get("location") or n.get("name"), "method": n.get("method"),
        "map": n.get("locationRaw") or n.get("location"),
        "encRate": n.get("encounterRate"),
        "turnsPerLevel": round(best["turns_per_level"], 1),
        "turnsPerBattle": round(best["turns_per_battle"], 2),
        "battles": round(best["turns_per_level"] / best["turns_per_battle"]) if best["turns_per_battle"] else 0,
        "move": best["move"],   # perOpponent already carries the display name
        "sustain": best["sustain"], "mons": best["mons"], "effSec": best["eff_sec"],
        "roundTrip": best["round_trip"], "center": best["center"],
    }


# ------------------------------------------------------------------ pipeline
def _faster(a, b):
    """The lower effective-time of two grind results (either may be None)."""
    if not a:
        return b
    if not b:
        return a
    return a if a["effSec"] <= b["effSec"] else b


# The leveling itinerary is computed at EVERY level (not a coarse grid), then
# consecutive levels that share the same spot and move are merged into one
# segment -- so the app can show "L12-18: Route 3, Double Kick" for each mon. A
# mon's level pins its stage (the run never grinds, so stage levels climb), so
# the stage is derived from the level.
ITIN_LEVELS = list(range(5, 56))


def stage_for_level(level):
    """The point in the run a mon of this level fits: the last stage whose party
    baseline is at or below it. Gates which wild areas are reachable."""
    best = 0
    for s in P.STAGES:
        if s["level"] <= level:
            best = s["id"]
    return best


# Set before forking so every worker inherits it (fork start method); the grind
# math is pure and keyed by (attacker, defender), so workers never contend.
_ENCOUNTERS = None


def _clamped(species, level):
    """All three TM policies at one level, clamped so more move freedom never
    yields a slower spot (the engine can over-pick a recharge move like Hyper
    Beam; a broader pool can always fall back to a narrower moveset)."""
    stage = stage_for_level(level)
    badges = O.badges_for(stage)
    r = {tg: best_grind(species, level, stage, tg, _ENCOUNTERS, badges) for tg in TOGGLES}
    r["renew"] = _faster(r["none"], r["renew"])
    r["any"] = _faster(r["renew"], r["any"])
    return r


def species_itinerary(species):
    """Per-TM-policy leveling itinerary for one species: an ordered list of
    segments {from, to, area, method, move, tplLo, tplHi, battles}, one per run
    of levels that share a best spot and move. Levels with no reachable spot are
    left out, so the itinerary is exactly the trainable stretch."""
    per = {lv: _clamped(species, lv) for lv in ITIN_LEVELS}
    out = {}
    for tg in TOGGLES:
        segs = []
        for lv in ITIN_LEVELS:
            v = per[lv][tg]
            key = (v["area"], v["move"]) if v else None
            if key is not None and segs and segs[-1]["_k"] == key:
                s = segs[-1]
                s["to"] = lv
                # turns/level isn't monotonic across a segment (the mon can
                # strengthen faster than the xp cost rises), so track true min/max
                s["tplLo"] = min(s["tplLo"], v["turnsPerLevel"])
                s["tplHi"] = max(s["tplHi"], v["turnsPerLevel"])
                s["battles"] = max(s["battles"], v["battles"])
                s["sustainLo"] = min(s["sustainLo"], v["sustain"])
                s["sustainHi"] = max(s["sustainHi"], v["sustain"])
                s["mons"] = v["mons"]           # breakdown at the top of the range
                s["roundTrip"] = v["roundTrip"]
                s["center"] = v["center"]
            elif key is not None:
                segs.append({"_k": key, "from": lv, "to": lv, "area": v["area"],
                             "method": v["method"], "move": v["move"],
                             "tplLo": v["turnsPerLevel"], "tplHi": v["turnsPerLevel"],
                             "battles": v["battles"], "sustainLo": v["sustain"],
                             "sustainHi": v["sustain"], "mons": v["mons"],
                             "roundTrip": v["roundTrip"], "center": v["center"]})
        out[tg] = [{k: s[k] for k in
                    ("from", "to", "area", "method", "move", "tplLo", "tplHi",
                     "battles", "sustainLo", "sustainHi", "mons", "roundTrip", "center")}
                   for s in segs]
    return out


def _worker(species_list):
    return [(sp, species_itinerary(sp)) for sp in species_list]


def _prefill_heals():
    """Compute each wild area's nearest-Center round-trip once, before forking, so
    the workers inherit a warm cache instead of each rebuilding the world graph."""
    for node in _wild_areas(_ENCOUNTERS, P.MAX_STAGE):
        folder = _folder_of(node)
        if folder and folder not in _HEAL_CACHE:
            heal_roundtrip(folder, node.get("stage", 0))


def main():
    global _ENCOUNTERS
    import multiprocessing as _mp
    import concurrent.futures as _cf
    _ENCOUNTERS = _json.load(open(f"{_OUT}/encounters.json"))
    _prefill_heals()
    species = sorted(sp for sp in P.full_availability() if sp in E.SPECIES)
    workers = int(_os.environ.get("LGMAX_WORKERS") or (_os.cpu_count() or 4))
    workers = max(1, min(workers, len(species)))
    # fork so the loaded engine/encounter tables are shared; one species per task
    n_chunks = workers * 4
    chunks = [species[i::n_chunks] for i in range(n_chunks)]
    chunks = [c for c in chunks if c]
    ctx = _mp.get_context("fork")
    with _cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        parts = list(ex.map(_worker, chunks))
    itin = {}
    for part in parts:
        for sp, tg_segs in part:
            itin[E.SPECIES[sp]["name"]] = tg_segs
    out = {"itineraries": itin, "range": [ITIN_LEVELS[0], ITIN_LEVELS[-1]]}
    with open(f"{_OUT}/training.json", "w") as f:
        _json.dump(out, f, separators=(",", ":"))
    n_segs = sum(len(t) for s in itin.values() for t in s.values())
    print(f"training: {len(itin)} species, {n_segs} itinerary segments")


if __name__ == "__main__":
    main()
