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
import json as _json, os as _os
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
    # the move it leans on (vs the likeliest slot), for the "which HM/TM helps" note
    top = max(res.get("perOpponent") or [], key=lambda r: r.get("chance", 0), default=None)
    return {"turns_per_level": turns_per_level, "turns_per_battle": res["turns"],
            "xp_per_battle": xp_per, "move": (top or {}).get("move"),
            "node": node}


def best_grind(species, level, stage, toggle, encounters, badges):
    """The reachable wild area with the fewest battle turns per level, or None."""
    pool = build_pool(species, level, stage, toggle)
    if not pool:
        return None
    best = None
    for node in _wild_areas(encounters, stage):
        got = area_grind(species, level, stage, pool, node, badges)
        if got and (best is None or got["turns_per_level"] < best["turns_per_level"]):
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
    }


# ------------------------------------------------------------------ pipeline
def _section_keys(secs):
    """Every (species, level, stage) a solved party mon actually stands at -- the
    scope-A key set. Grind cost is starter/mode-independent, so we dedupe across
    all of them and the app looks each party mon up by this key."""
    keys = set()
    for by_starter in secs.get("sections", {}).values():
        for per_stage in by_starter.values():
            for stg, sec in per_stage.items():
                if not sec:
                    continue
                for m in sec.get("team", []):
                    if m.get("species") in E.SPECIES:
                        keys.add((m["species"], int(m["level"]), int(stg)))
    return keys


def _faster(a, b):
    """The fewer-turns-per-level of two grind results (either may be None)."""
    if not a:
        return b
    if not b:
        return a
    return a if a["turnsPerLevel"] <= b["turnsPerLevel"] else b


def main():
    encounters = _json.load(open(f"{_OUT}/encounters.json"))
    secs = _json.load(open(f"{_OUT}/sections.json"))
    grind, badges_cache = {}, {}
    # keyed by DISPLAY NAME (the app's party rows carry the name, not the const)
    for species, level, stage in sorted(_section_keys(secs)):
        badges = badges_cache.get(stage) or badges_cache.setdefault(stage, O.badges_for(stage))
        name = E.SPECIES[species]["name"]
        r = {tg: best_grind(species, level, stage, tg, encounters, badges) for tg in TOGGLES}
        # The pools nest (none subset of renew subset of any), so a broader TM
        # policy can always fall back to a narrower moveset -- never recommend a
        # slower spot for allowing more moves. (The engine ranks moves by damage
        # per turn, which can over-pick a recharge move like Hyper Beam that the
        # turn accounting then penalizes; the fallback corrects that.)
        r["renew"] = _faster(r["none"], r["renew"])
        r["any"] = _faster(r["renew"], r["any"])
        for tg in TOGGLES:
            grind[f"{name}|{level}|{stage}|{tg}"] = r[tg]
    with open(f"{_OUT}/training.json", "w") as f:
        _json.dump({"grind": grind}, f, separators=(",", ":"))
    resolved = sum(1 for v in grind.values() if v)
    print(f"training: {len(grind)} grind keys, {resolved} resolved")


if __name__ == "__main__":
    main()
