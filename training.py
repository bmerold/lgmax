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
# Besides the max-damage move set, the grind search also scores the top-N
# damaging moves in the pool (plus the single highest-PP damaging move) as
# one-move grind sets: a weaker but higher-PP attack KOs slower yet lets you camp
# far longer, and can win on effective time by saving Pokémon Center round-trips.
GRIND_ALT_MOVES = 3

# Effective grind time = time to FIND each wild battle (walk or fish) + the battle
# itself + the walk to a Pokemon Center and back whenever HP/PP runs out.
#
# Battle timing is measured with the battle scene OFF and text speed FAST (the way
# you grind): a one-turn wild fight -- encounter load, one turn, faint/XP, exit --
# runs ~15s, and a turn is ~5s, so the fixed per-fight overhead is ~10s. HEAL_SEC
# covers the nurse. SEC_PER_STEP is running-speed walking.
SEC_PER_STEP, SEC_PER_TURN, SEC_PER_FIGHT, HEAL_SEC = 0.28, 5.0, 10.0, 15.0

# --- time to find the next wild battle -------------------------------------
# In grass/cave/water each eligible step rolls `WildEncounterRandom() % 1600 <
# 16*rate` (src/wild_encounter.c DoWildEncounterRateTest/DiceRoll), so the
# per-step chance is rate/100; a ramping buff adds `buff*16/200` to the roll and
# grows by `rate` each dry step (AddToWildEncounterRateBuff), pulling low-rate
# water in a little sooner. _steps_per_encounter sums that to its expectation.
_STEPS_CACHE = {}
def _steps_per_encounter(rate):
    """Expected steps walked between wild battles for a terrain of this encounter-
    rate byte, decomp-faithful (rate/100 per step, ramped by the encounter-rate
    buff). Route 1 (rate 21) ~5 steps, Mt. Moon B1F (rate 5) ~20, surf (rate 1) ~80."""
    rate = int(rate or 0)
    if rate <= 0:
        return 0.0
    if rate in _STEPS_CACHE:
        return _STEPS_CACHE[rate]
    base = rate / 100.0
    exp_steps, surv = 0.0, 1.0            # surv = P(no battle in the first k-1 steps)
    for k in range(1, 4001):
        p = min(1.0, base * (1.0 + (k - 1) / 200.0))   # step k's chance, with buff
        exp_steps += k * surv * p
        surv *= (1.0 - p)
        if surv < 1e-9:
            break
    _STEPS_CACHE[rate] = exp_steps
    return exp_steps

# Fishing (src/field_player_avatar.c Task_Fishing). Each cast is a 50/50 for a bite
# (Fishing6: `Random() & 1` -> NO_BITE), so it takes ~2 casts to land one fish, on
# any rod. A cast plays dot-game rounds: `rounds = 1 + Random()%{1,3,6}` for
# {Old,Good,Super} rod, and each round waits some dots -- first round `rand%10+4`,
# later rounds `rand%10+1`, capped at 10 -- at 20 frames (SEC_PER_DOT) per dot. So
# a Super-Rod cast runs much longer than an Old-Rod one, and casts vary in length.
SEC_PER_DOT = 20 / 60.0
_ROD_ROUNDS = {"old rod": 1.0, "good rod": 2.0, "super rod": 3.5}
_FIRST_ROUND_DOTS = 7.9        # E[min(rand%10 + 4, 10)]
_LATER_ROUND_DOTS = 5.5        # E[rand%10 + 1]
# Per-cast fixed overhead: rod out/in, the 1s pre-round wait (Fishing3), and the
# bite/nibble result message at fast text. An estimate (the decomp fixes the dot
# and round counts, not text-render time); the one knob to tune against a stopwatch.
FISH_CAST_SEC = 4.0
def _fish_seconds(method):
    """Expected real seconds to land ONE wild fish with this rod: ~2 casts (50%
    bite each) -- one failing after the first round, one playing the rod's rounds
    -- plus their dot-game time."""
    rounds = _ROD_ROUNDS.get((method or "").lower().strip(), 2.0)
    dots_success = _FIRST_ROUND_DOTS + (rounds - 1) * _LATER_ROUND_DOTS
    return 2 * FISH_CAST_SEC + (_FIRST_ROUND_DOTS + dots_success) * SEC_PER_DOT

def find_seconds(node):
    """Real time to reach the next wild battle in this area: fishing casts for a
    rod spot, else the walk between grass/cave/water encounters."""
    method = (node.get("method") or "")
    if "rod" in method.lower():
        return _fish_seconds(method)
    return _steps_per_encounter(node.get("encounterRate")) * SEC_PER_STEP

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


def _ungrindable(node):
    """Wild areas you can't actually grind in, so they're never grind spots:
      - the Safari Zone is catch-only (Safari Balls + bait/rocks, no battle) -- only
        the Safari Zone carries "Safari" in its name;
      - Rock Smash yields a battle only from a smashable rock, and a map holds just a
        few that don't respawn until you leave and re-enter, so it can't be farmed
        for XP (it's for finding a specific mon, not grinding)."""
    tag = " ".join(str(node.get(k) or "") for k in ("map", "locationRaw", "location"))
    if "SAFARI" in tag.upper():
        return True
    if "rock smash" in (node.get("method") or "").lower():
        return True
    return False


def _wild_areas(encounters, stage):
    """Every wild area reachable by `stage` where you can actually grind (excludes
    the catch-only Safari Zone and un-farmable Rock Smash), from encounters.json."""
    return [e for e in encounters if e.get("kind") == "wild"
            and e.get("stage", 99) <= stage and not _ungrindable(e)]


_OPPS_CACHE = {}
def _area_opponents(node):
    """The area's wild slots and the XP one KO yields, computed once per area
    (they depend on nothing but the encounter table). Keyed by the node OBJECT,
    not its id string: the two versions give one map different slots. The node
    is held in the cache so its id() can't be reused."""
    hit = _OPPS_CACHE.get(id(node))
    if hit is None or hit[0] is not node:
        hit = _OPPS_CACHE[id(node)] = (node, _area_opponents_uncached(node))
    return hit[1]


def _area_opponents_uncached(node):
    """The wild slots as (mon, slot-chance) plus the chance-weighted XP one KO
    yields -- both averaged over the encounter table so the grind ratio is
    scale-free. Returns (opps, xp_per) or ([], 0) if the area has no fightable
    slot. XP is expYield * level / 7 per src/battle_script_commands.c."""
    opps, xp_per = [], 0.0
    for s in node.get("wildMons") or []:
        w = (s.get("chance") or 0) / 100.0
        if w <= 0 or s["species"] not in E.SPECIES:
            continue
        lvl = round((s["minLevel"] + s["maxLevel"]) / 2)
        opps.append((E.make_mon(s["species"], lvl), w))
        xp_per += w * exp_on_ko(E.SPECIES[s["species"]].get("expYield", 0), lvl)
    return opps, xp_per


# The fights come from the wild matchup tables (wild_matchups.py) when main()
# finds them: the same numbers optimize._weighted_sweep computes, as lookups
# (`grind_space.py validate` checks the two agree). Without the tables, or with
# LGMAX_GRIND_ENGINE=1, every fight is simulated by the engine, as before.
_WILD = None


def _sweep(player, node, opps, pool, badges, obey):
    """One wild area's weighted sweep, from the tables or the engine."""
    if _WILD is None:
        return O._weighted_sweep(player, opps, pool, badges, obey)
    return _WILD.sweep(_WILD.sp_index[player["species"]], player["level"],
                       _WILD.opps_of(node), pool, badges, obey)


def _move_rate(player, ref, node, ref_i, mv, badges):
    """best_move's ranking key for one move against the area's likeliest slot
    (expected damage per turn spent), or None when the move does nothing."""
    ab = {"atk": badges["atk"], "spatk": badges["spatk"]}
    if _WILD is not None:
        fi = _WILD.opps_of(node)[ref_i][0]
        got = _WILD.offense(_WILD.sp_index[player["species"]], player["level"], mv, fi, ab)
        return got[0] if got else None
    p = E.move_profile(player, ref, mv, ab)
    if not p or p.get("immune"):
        return None
    return p["avgPerTurn"] / p.get("turnsPerUse", 1.0)


def _alt_grind_moves(player, opps, pool, badges, node=None):
    """Higher-PP attacking moves worth trying as a single-move grind set: the
    top-N damaging moves in the pool plus the one with the most PP, ranked
    against the likeliest wild slot. PP is engine.MOVES[m]["pp"]; damage-per-turn
    comes from the same engine the sweep uses, so this adds no new mechanic --
    just a shortlist of alternatives to the max-damage pick. Order is explicit
    (damage desc, then const) so the byte-reproducible build stays stable."""
    if not opps:
        return []
    # the slot you meet most often (the first, on a tie, as max() picks it)
    ref_i = max(range(len(opps)), key=lambda i: opps[i][1])
    # the tables carry entry abilities already; the engine needs them applied
    pl, ref = (player, None) if _WILD is not None else E.with_entry_boosts(player, opps[ref_i][0])
    scored = []
    for mv in pool:
        if mv not in E.MOVES:
            continue
        rate = _move_rate(pl, ref, node, ref_i, mv, badges)
        if rate is None or rate <= 0:
            continue
        scored.append((rate, E.MOVES[mv]["pp"], mv))
    if not scored:
        return []
    cands = [mv for _, _, mv in sorted(scored, key=lambda t: (-t[0], t[2]))[:GRIND_ALT_MOVES]]
    high_pp = sorted(scored, key=lambda t: (-t[1], t[2]))[0][2]
    if high_pp not in cands:
        cands.append(high_pp)
    return cands


def _score_sweep(res, species, level, xp_per, node, stage):
    """Turn one weighted sweep into the grind-cost metrics, including the
    EFFECTIVE time (the fights plus a Pokémon Center round-trip whenever HP or PP
    runs dry). Split out of area_grind so several candidate move sets can be
    scored the same way and the fastest kept. Carries the raw HP/PP fight limits
    and heal-trip count so the caller can tell whether PP is the binding
    constraint before spending sweeps on alternatives."""
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
    # each battle also costs the time to FIND it -- the walk between grass/cave/water
    # encounters, or the fishing casts for a rod spot -- on top of the fight itself
    battle_sec = battles * (find_seconds(node) + res["turns"] * SEC_PER_TURN + SEC_PER_FIGHT)
    folder = _folder_of(node)
    rt, cmap = heal_roundtrip(folder, stage) if folder else (None, None)
    heal_trips = max(0, _math.ceil(battles / max(1, sustain)) - 1)
    step_sec = (rt if rt is not None else 400) * SEC_PER_STEP + HEAL_SEC
    eff_sec = battle_sec + heal_trips * step_sec
    return {"turns_per_level": turns_per_level, "turns_per_battle": res["turns"],
            "xp_per_battle": xp_per, "move": (top or {}).get("move"),
            "sustain": sustain, "mons": mons, "eff_sec": eff_sec,
            "round_trip": rt, "center": cmap, "node": node,
            # gating hints for the alt-move search (not emitted downstream)
            "_pp_bound": sustain_pp < sustain_hp, "_heal_trips": heal_trips}


def area_grind(species, level, stage, pool, node, badges, pp_aware=True, obey=None):
    """Least EFFECTIVE grind time to gain one full level in one wild area, or None
    if this mon can't clear its encounters. The max-damage move set is the
    baseline; when it's PP-limited AND pays a Center round-trip, also score a few
    higher-PP single-move sets and keep whichever minimizes effective time. A
    higher-PP move only ever helps that case: it KOs slower (never less HP damage,
    so never a longer HP camp) but drains PP more slowly, so it's only worth the
    extra sweeps when PP -- not HP -- is what forces the walk. `pp_aware=False`
    returns the pure max-damage baseline (used by the verify guard)."""
    opps, xp_per = _area_opponents(node)
    if not opps or xp_per <= 0:
        return None
    player = E.make_mon(species, level)
    if obey is None:
        obey = O.obey_mult(player, badges)
    base = _sweep(player, node, opps, pool, badges, obey)
    if base is None or not base.get("turns"):
        return None
    # Quick reject: if even the max-damage set (fewest turns, least damage taken)
    # can't clear the average encounter without going down, no move can.
    if not base.get("survives"):
        return None
    best = _score_sweep(base, species, level, xp_per, node, stage)
    if pp_aware and best["_heal_trips"] > 0 and best["_pp_bound"]:
        for mv in _alt_grind_moves(player, opps, pool, badges, node):
            alt = _sweep(player, node, opps, [mv], badges, obey)
            if alt is None or not alt.get("turns"):
                continue
            cand = _score_sweep(alt, species, level, xp_per, node, stage)
            if cand["eff_sec"] < best["eff_sec"]:
                best = cand
    # A real grind spot lets you clear at least one full fight -- worst encounter
    # and PP -- before walking to a Center. sustain 0 means you'd heal after every
    # single battle, which isn't a place you grind; reject it so a frail late-caught
    # mon (Venonat, floored to stage 19) is sent to a survivable spot, not a high-XP
    # Super-Rod hole where its 25 XP/fight comes with a heal every fight. If no area
    # clears the bar at this level, the level is simply left out of the itinerary.
    if best["sustain"] < 1:
        return None
    return best


# Float headroom on the floor test, so an area whose effective time rounds a
# hair under its floor is still visited (the skip must never change the winner).
FLOOR_SLACK = 1e-9


def best_grind(species, level, stage, toggle, encounters, badges, obey=None):
    """The reachable wild area with the least EFFECTIVE grind time (fights plus
    heal round-trips), or None. Ranking by effective time is what folds
    sustainability into the recommendation."""
    return best_grind_versions(species, level, stage, toggle, {"_": encounters},
                               badges, obey)["_"]


def best_grind_versions(species, level, stage, toggle, area_lists, badges, obey=None):
    """best_grind for several versions at once: {version: area list} -> {version:
    result or None}. Each version keeps its own winner (least effective time, the
    earliest area in ITS list on a tie), but an area both versions share -- the
    same node object, i.e. the same slots -- is fought once. The version is only
    a filter over which areas exist."""
    out = {v: None for v in area_lists}
    pool = build_pool(species, level, stage, toggle)
    if not pool:
        return out
    # Visit the areas cheapest-floor first and stop once no remaining area can
    # win: an area's effective time is at least its fights at ONE turn per slot
    # and no heal trips (every KO takes >= 1 turn; battles per level is XP alone).
    # A battle's turns are the chance-weighted sum over the slots, and the
    # rounded chances need not add to exactly 1, so the floor uses their real
    # sum. The winner is the same as scanning every area in order, so this only
    # skips hopeless areas.
    xp_needed = exp_to_next(E.SPECIES[species].get("growthRate", "MEDIUM_FAST"), level)
    where = {}                                 # id(node) -> [node, {version: index}]
    for v, areas in area_lists.items():
        for idx, node in enumerate(_wild_areas(areas, stage)):
            where.setdefault(id(node), [node, {}])[1][v] = idx
    cands = []
    for node, idxs in where.values():
        opps, xp_per = _area_opponents(node)
        if xp_per <= 0:
            continue
        min_turns = sum(w for _m, w in opps)
        floor = xp_needed / xp_per * (find_seconds(node) + min_turns * SEC_PER_TURN
                                      + SEC_PER_FIGHT)
        cands.append((floor, min(idxs.values()), node, idxs))
    cands.sort(key=lambda c: (c[0], c[1]))
    best = {}                                  # version -> (eff_sec, index, result)
    for floor, _i, node, idxs in cands:
        if len(best) == len(area_lists) and \
                floor > max(b[0] for b in best.values()) * (1 + FLOOR_SLACK):
            break
        if all(v in best and floor > best[v][0] * (1 + FLOOR_SLACK) for v in idxs):
            continue                           # can't win for any version that has it
        got = area_grind(species, level, stage, pool, node, badges, obey=obey)
        if not got:
            continue
        for v, idx in idxs.items():
            if v not in best or (got["eff_sec"], idx) < best[v][:2]:
                best[v] = (got["eff_sec"], idx, got)
    for v, (_e, _i, got) in best.items():
        out[v] = _grind_result(got)
    return out


def _grind_result(best):
    """The emitted fields of one area's grind."""
    n = best["node"]
    return {
        "area": n.get("location") or n.get("name"), "method": n.get("method"),
        "map": n.get("locationRaw") or n.get("location"),
        "encRate": n.get("encounterRate"),
        "turnsPerLevel": round(best["turns_per_level"], 1),
        "turnsPerBattle": round(best["turns_per_battle"], 2),
        # at least one fight per level: a high-XP spot can over-level an
        # underleveled mon in a single battle (turns_per_level/turns < 1), but
        # you still fight once -- rounding that to 0 would misread as "no fight".
        "battles": max(1, round(best["turns_per_level"] / best["turns_per_battle"])) if best["turns_per_battle"] else 0,
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
# Cover the whole climb to the level cap, not just the through-story range: a
# completionist keeps leveling in the post-game. A segment at level L is "grind
# here to reach L+1", so the last grind level is 99 (99 -> 100); 100 is the cap.
ITIN_LEVELS = list(range(5, 100))


def stage_for_level(level):
    """The point in the run a mon of this level fits: the last stage whose party
    baseline is at or below it. Gates which wild areas are reachable."""
    best = 0
    for s in P.STAGES:
        if s["level"] <= level:
            best = s["id"]
    return best


# One pass prices BOTH versions. The fights are version-free (the wild matchup
# tables); a version is a filter: which areas exist (its encounter tables), when
# each species can first be held, and which are in-game trades (obedience). Every
# other grind input -- story stages, badges, HM/rod gates, TM timing -- is the
# same in FireRed and LeafGreen, so it is shared. Set in main() before forking.
VERSIONS = ("lg", "fr")
# version -> {"areas": [wild nodes, encounters.json order], "avail": {species:
# {"stage", "source"}}}
_VERS = None

# The two itineraries we build per species (the app shows one at a time):
#   journey  -- while playing through: assume your progress tracks this mon's level
#               (party ~ its level), so only the areas unlocked by then are options.
#               A level->stage map is a rough proxy, but it keeps the mid-run guide
#               from sending you somewhere you can't reach yet.
#   postgame -- everything unlocked: every area is a candidate and only the survival
#               + sustain gates decide where a mon of this level can grind. Assumes
#               full progression (all badges, every TM per toggle) -> the most
#               optimal reachable spot, wherever it is.
GRIND_MODES = ("journey", "postgame")


def _first_stage(species, avail):
    """The earliest stage the run could hold this species (full_availability); the
    journey itinerary is floored here so an underleveled late-catch (Super-Rod
    Staryu, stage 19) isn't sent to grind somewhere it couldn't have been yet."""
    rec = avail.get(species)
    return rec["stage"] if rec else 0


def _grind_stage(species, level, mode, avail):
    """The progression stage that gates areas/badges/TMs for this mode at a level."""
    if mode == "postgame":
        return P.MAX_STAGE
    return max(stage_for_level(level), _first_stage(species, avail))


def _obey(species, level, badges, avail):
    """optimize.obey_mult against one version's availability."""
    return O.obey_mult({"species": species, "level": level}, badges, avail)


def _clamp(r):
    """More move freedom never yields a slower spot (the engine can over-pick a
    recharge move like Hyper Beam; a broader pool can always fall back to a
    narrower moveset)."""
    r["renew"] = _faster(r["none"], r["renew"])
    r["any"] = _faster(r["renew"], r["any"])
    return r


def _clamped_versions(species, level, mode, versions):
    """All three TM policies at one level for one mode, per version. Versions that
    agree on the grind stage and obedience share one search over their areas."""
    groups = {}
    for v in versions:
        avail = _VERS[v]["avail"]
        stage = _grind_stage(species, level, mode, avail)
        ob = _obey(species, level, O.badges_for(stage), avail)
        groups.setdefault((stage, ob), []).append(v)
    out = {}
    for (stage, ob), vs in sorted(groups.items()):
        badges = O.badges_for(stage)
        lists = {v: _VERS[v]["areas"] for v in vs}
        per_tg = {tg: best_grind_versions(species, level, stage, tg, lists, badges, ob)
                  for tg in TOGGLES}
        for v in vs:
            out[v] = _clamp({tg: per_tg[tg][v] for tg in TOGGLES})
    return out


def _segments(per):
    """Merge one mode's per-level results into segments per TM policy: an ordered
    list {from, to, area, method, move, ...}, one per run of levels that share a
    best spot and move. Levels with no reachable spot are left out, so the
    itinerary is exactly the trainable stretch."""
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


def species_itinerary(species):
    """Both leveling itineraries for one species in every version that has it ->
    {version: {mode: {toggle: [segment...]}}}. The journey and post-game modes
    differ only in how far the run is assumed to have progressed (GRIND_MODES)."""
    versions = [v for v in VERSIONS if species in _VERS[v]["avail"]]
    per = {v: {mode: {} for mode in GRIND_MODES} for v in versions}
    for lv in ITIN_LEVELS:
        if _WILD is not None:
            _WILD.clear()     # its memo is per player level
        for mode in GRIND_MODES:
            for v, r in _clamped_versions(species, lv, mode, versions).items():
                per[v][mode][lv] = r
    return {v: {mode: _segments(per[v][mode]) for mode in GRIND_MODES} for v in versions}


def _worker(species_list):
    return [(sp, species_itinerary(sp)) for sp in species_list]


def _all_areas():
    """Every distinct wild node across the versions, each once."""
    seen, out = set(), []
    for v in VERSIONS:
        for node in _VERS[v]["areas"]:
            if id(node) not in seen:
                seen.add(id(node)); out.append(node)
    return out


def _prefill_heals():
    """Compute each wild area's nearest-Center round-trip once, before forking, so
    the workers inherit a warm cache instead of each rebuilding the world graph."""
    for node in _wild_areas(_all_areas(), P.MAX_STAGE):
        folder = _folder_of(node)
        if folder and folder not in _HEAL_CACHE:
            heal_roundtrip(folder, node.get("stage", 0))


# A version's grind inputs, from a clean interpreter (the engine binds the
# version at import): its wild nodes exactly as build_graph writes them to
# encounters.json (deduplicated by id, sorted by stage then name -- the trainer
# nodes interleaved there don't change the wild nodes' relative order) and its
# availability.
_VERSION_CODE = """
import json, build_graph as G, progression as P
seen, wild = set(), []
for e in G.build_wild_encounters():
    if e["id"] not in seen:
        seen.add(e["id"]); wild.append(e)
wild.sort(key=lambda e: (e["stage"], e["name"]))
av = {k: {"stage": r["stage"], "source": r.get("source")} for k, r in P.full_availability().items()}
items = {k: {"stage": v[0], "where": v[1]} for k, v in G.item_stage_table().items()}
print(json.dumps({"areas": wild, "avail": av, "items": items}))
"""


def _version_inputs():
    import subprocess, sys
    raw = {}
    for v in VERSIONS:
        env = dict(_os.environ, LGMAX_VERSION=v, PYTHONHASHSEED="0")
        res = subprocess.run([sys.executable, "-c", _VERSION_CODE], env=env, check=True,
                             capture_output=True, text=True,
                             cwd=_os.path.dirname(_os.path.abspath(__file__)))
        raw[v] = res.stdout.strip().splitlines()[-1]
    return raw


def _share_nodes(raw):
    """Parse each version's inputs, making an area both versions list with the
    identical slots ONE node object -- best_grind_versions fights it once."""
    canon, out = {}, {}
    for v, text in raw.items():
        d = _json.loads(text)
        areas = []
        for node in d["areas"]:
            key = _json.dumps(node, sort_keys=True)
            areas.append(canon.setdefault(key, node))
        out[v] = {"areas": areas, "avail": d["avail"], "items": d["items"]}
    return out


def _shared_item_stages():
    """TM timing, which build_pool reads: identical in both versions (the same
    map scripts give the same TMs at the same story points), so one table serves
    the shared pass. Taken from the version inputs rather than
    data/itemStages.json, which build_graph writes only later in a fresh build."""
    first = _VERS[VERSIONS[0]]["items"]
    if any(_VERS[v]["items"] != first for v in VERSIONS):
        raise SystemExit("training: TM timing differs between versions -- the shared "
                         "grind pass assumes it doesn't")
    return first


# Build avoidance: the grind itineraries are the heaviest stage, but they only
# depend on the wild tables, the engine/XP data, this grind code, and the pinned
# decomp (world.py walks its maps for the heal round-trips) -- NOT on the route,
# the party solve, the payload packer, the app or the guards. So hash exactly those
# inputs; if every data/training_<v>.json carries the same hash, reuse them.
# LGMAX_FORCE_TRAINING=1 forces a fresh run.
def _inputs_hash(raw):
    import hashlib, subprocess
    h = hashlib.sha256()
    here = _os.path.dirname(_os.path.abspath(__file__))
    # every local module the grind imports (tour.py's Center doors drive the heal
    # round-trips; optimize pulls in trainer_items/tactics). sections.py,
    # training_cost.py and measure.py are left out: they read this stage's output
    # (mq imports sections only for its own validate), they don't feed it.
    for mod in ("training.py", "optimize.py", "engine.py", "abilities.py",
                "grind_space.py", "wild_matchups.py", "mq.py", "matchups.py",
                "progression.py", "build_graph.py", "constraints.py", "hms.py",
                "tms.py", "world.py", "walking.py", "tour.py", "boulders.py",
                "route_order.py", "trainer_items.py", "tactics.py"):
        p = _os.path.join(here, mod)
        if _os.path.exists(p):
            h.update(mod.encode()); h.update(open(p, "rb").read())
    # extract.py's outputs only: build_graph's (encounters, item stages) are in the
    # version inputs below, and they don't exist yet when a fresh build gets here
    for d in ("species.json", "moves.json", "levelUpLearnsets.json",
              "tmhmLearnsets.json", "tutorLearnsets.json", "wild.json",
              "evolutions.json", "typeChart.json", "expTables.json", "items.json"):
        p = f"{_OUT}/{d}"
        if _os.path.exists(p):
            h.update(d.encode()); h.update(open(p, "rb").read())
    # world.py reads the decomp maps directly for the Center round-trips; pin them
    # cheaply by the decomp's git revision rather than hashing every blockdata file.
    try:
        rev = subprocess.run(["git", "-C", E.REPO,
                              "rev-parse", "HEAD"], capture_output=True, text=True,
                             timeout=5).stdout.strip()
        h.update(rev.encode())
    except Exception:
        pass
    # each version's wild areas and availability (its catchable pool and trades)
    for v in VERSIONS:
        h.update(v.encode()); h.update(raw[v].encode())
    h.update(repr((TOGGLES, ITIN_LEVELS[0], ITIN_LEVELS[-1], GRIND_MODES)).encode())
    return h.hexdigest()


def _load_wild():
    """The wild matchup tables, checked against the engine before they are
    trusted (a table built before an engine change would silently grind on the
    old numbers). None -> simulate every fight with the engine."""
    if _os.environ.get("LGMAX_GRIND_ENGINE") or not _os.path.exists(f"{_OUT}/wild_matchups.bin.gz"):
        print("training: simulating every wild fight with the engine")
        return None
    import grind_space as GS, wild_matchups as W, sys
    if not W.up_to_date():
        raise SystemExit("training: the wild matchup tables are out of date -- "
                         "rebuild them (python3 wild_matchups.py)")
    ws = GS.WildSpace()
    # validate with THIS module (run as __main__, `import training` would load a
    # second copy without the shared TM timing main() set)
    if not GS.validate(WILD_CHECK_SAMPLES, ws=ws, areas=_all_areas(),
                       grind=sys.modules[__name__]):
        raise SystemExit("training: the wild matchup tables disagree with the engine -- "
                         "rebuild them (python3 wild_matchups.py)")
    print("training: wild fights from the matchup tables (data/wild_matchups.bin.gz)")
    ws.clear()
    return ws


# Random area sweeps re-checked against the engine before the tables are used.
WILD_CHECK_SAMPLES = 300


def _out_path(v):
    return f"{_OUT}/training_{v}.json"


def main():
    global _VERS, _WILD, _ITEM_STAGE
    import multiprocessing as _mp
    import concurrent.futures as _cf
    raw = _version_inputs()
    want = _inputs_hash(raw)
    if not _os.environ.get("LGMAX_FORCE_TRAINING"):
        try:
            if all(_json.load(open(_out_path(v))).get("inputsHash") == want for v in VERSIONS):
                print("training: inputs unchanged, reusing data/training_<v>.json "
                      "(set LGMAX_FORCE_TRAINING=1 to rebuild)")
                return
        except Exception:
            pass
    _VERS = _share_nodes(raw)
    _ITEM_STAGE = _shared_item_stages()
    _WILD = _load_wild()
    _prefill_heals()
    species = sorted({sp for v in VERSIONS for sp in _VERS[v]["avail"] if sp in E.SPECIES})
    workers = int(_os.environ.get("LGMAX_WORKERS") or (_os.cpu_count() or 4))
    workers = max(1, min(workers, len(species)))
    # fork so the loaded tables and areas are shared; several species per task
    n_chunks = workers * 4
    chunks = [species[i::n_chunks] for i in range(n_chunks)]
    chunks = [c for c in chunks if c]
    ctx = _mp.get_context("fork")
    with _cf.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
        parts = list(ex.map(_worker, chunks))
    itin = {v: {} for v in VERSIONS}
    for part in parts:
        for sp, by_v in sorted(part):
            for v, modes in by_v.items():
                itin[v][E.SPECIES[sp]["name"]] = modes
    for v in VERSIONS:
        # range is the levels the guide covers: from the first grind level to the
        # cap you can reach (the last grind level, 99, takes you to 100)
        out = {"itineraries": dict(sorted(itin[v].items())),
               "range": [ITIN_LEVELS[0], ITIN_LEVELS[-1] + 1], "inputsHash": want}
        with open(_out_path(v), "w") as f:
            _json.dump(out, f, separators=(",", ":"))
        n_segs = sum(len(segs) for sp in itin[v].values()
                     for modes in sp.values() for segs in modes.values())
        print(f"training {v}: {len(itin[v])} species x {len(GRIND_MODES)} modes, "
              f"{n_segs} itinerary segments")


if __name__ == "__main__":
    main()
