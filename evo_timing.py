"""Which evolutions to hold back so the pre-evolution learns a move first.

A player can keep a Pokemon from evolving -- cancel a level-up evolution with B
(src/evolution_scene.c lets the player stop it), or simply not use the stone or
trade yet -- and some pre-evolutions learn a strong move by level long before the
evolved form does, or one it never learns: Gastly learns Shadow Ball at L36,
Haunter at L45 (src/data/pokemon/level_up_learnsets.h); Pikachu learns Thunderbolt
at L26, Raichu never by level. Held moves stay known after the evolution.

The hold is not free: the line fights in its weaker form until then. So each
candidate is decided by weighing both plans over the fights that matter, the
boss battles (gyms, rivals, Rocket bosses, the League) from where the line could
first evolve up to the League:

  * evolve on time -- the evolved form's stats, without the move;
  * hold it        -- the pre-evolution until it learns the move, then evolved.

Each fight is scored the way core.py screens League picks: the share of HP the
Pokemon loses beating each foe one on one (>= 1: it loses), capped. A hold is
kept when it lowers the total. Single-copy TMs are left out of both pools: one
may well go to someone else (the TM plan is solved later, and adapts to holds),
while a held move is the line's own.

Writes data/evo_timing_<v>.json, which progression.use_evo_timing() adopts from
sections.py on (the route and the grind simulator run without it). One plan per
version: it is about the species, not the starter or the party.
"""
import json, os, sys, time
import engine as E
import progression as P
import optimize as O
import sections as S
import tactics as TAC
import tms as TM

HOLD_MAX = 15        # levels a line may be held past its first chance to evolve
POWER_MIN = 60       # weaker moves are never worth a hold
LATE_GAP = 3         # the evolved form must learn the move at least this much later
HOLD_MARGIN = 0.02   # a hold must cut the fights' HP cost by 2% to be worth the bother
CAP = 1.5            # per-foe cost cap (a hopeless matchup counts as a loss, not more)
BOSS_KINDS = {"gym", "boss", "rival", "elite4", "champion"}
LEAGUE_LEVEL = P.STAGE_BY_ID[TAC.LEAGUE_STAGE]["level"]
ROUTE_STAGES = [st for st in P.STAGES if st["id"] <= TAC.LEAGUE_STAGE]


def damaging(mv):
    m = E.MOVES.get(mv)
    return bool(m) and m["effect"] not in E.UNUSABLE_EFFECTS and (
        m["power"] > 0 or m["effect"] in ("DRAGON_RAGE", "SONICBOOM", "LEVEL_DAMAGE",
                                         "PSYWAVE", "SUPER_FANG"))


def first_chance(frm, to, ev, avail):
    """The level the line could first evolve at (optimize._levelup_pool's rule)."""
    if ev["method"] == "LEVEL" and isinstance(ev["param"], int):
        return ev["param"]
    if ev["method"] in ("ITEM", "TRADE", "TRADE_ITEM") and to in avail:
        return P.STAGE_BY_ID[avail[to]["stage"]]["level"] + 1
    return None


def candidates(avail):
    """(from, to, hold level, move) for every pre-evolution move worth a look:
    damaging, strong enough, inside the hold window and before the League, and
    one the evolved form learns clearly later by level or never."""
    out = []
    for frm, evs in sorted(E.EVOS.items()):
        if frm not in avail: continue
        for ev in evs:
            to = ev["to"]
            if to not in avail or P.NAT_DEX.get(to, 0) > 151: continue
            early = first_chance(frm, to, ev, avail)
            if early is None: continue
            later = {}
            for lv, mv in E.LEVELUP.get(to, []):
                if lv > 1: later[mv] = min(later.get(mv, 999), lv)
            for lv, mv in E.LEVELUP.get(frm, []):
                if not (early < lv <= min(early + HOLD_MAX, LEAGUE_LEVEL)): continue
                if not damaging(mv) or E.MOVES[mv]["power"] < POWER_MIN: continue
                if later.get(mv, 999) - lv < LATE_GAP: continue
                out.append((frm, to, lv, mv))
    return out


def _bosses(stage):
    encs = [e for e in S._SECTIONS_GRAPH.get(stage, []) if e.get("kind") in BOSS_KINDS]
    return [E.realize_trainer_mon(e["trainerConst"], i)
            for e in encs for i in range(len(e["party"]))]


_PRE = {ev["to"]: f for f, evs in E.EVOS.items() for ev in evs}

def _path(to, avail):
    """Root -> ... -> `to`, then on through the first evolution after it. A Gen 2
    baby (Pichu, Cleffa, Igglybuff) can't be had before the National Dex, so the
    line starts at the first form this version can actually hold."""
    path = [to]
    while path[0] in _PRE: path.insert(0, _PRE[path[0]])
    while E.EVOS.get(path[-1]): path.append(E.EVOS[path[-1]][0]["to"])
    while len(path) > 1 and path[0] not in avail: path.pop(0)
    return path


def form_at(path, level, stage, avail):
    """The form of this line fielded at (level, stage) under the plan in force."""
    held = path[0] if path[0] in avail and avail[path[0]]["stage"] <= stage else None
    for a, b in zip(path, path[1:]):
        if held != a: break
        ev = next(e for e in E.EVOS[a] if e["to"] == b)
        rec = avail.get(b)
        if not rec: break
        if ev["method"] == "LEVEL" and isinstance(ev["param"], int):
            ok = P.evolve_level(a, b, ev["param"]) <= level
        else:
            ok = rec["stage"] <= stage and (P.evo_delay(a, b) or 0) <= level
        if not ok: break
        held = b
    return held


def plan_cost(path, early, plan, scarce):
    """Total capped HP cost of the line's boss fights from `early` to the League
    under `plan` (a full evo-timing dict)."""
    P.use_evo_timing(plan)
    O.refresh_availability()
    avail = O.AVAIL                 # refresh_availability just rebuilt it
    total = 0.0
    for st in ROUTE_STAGES:
        lv = st["level"]
        if lv < early: continue
        form = form_at(path, lv, st["id"], avail)
        opps = _bosses(st["id"])
        if not form or not opps: continue
        badges = O.badges_for(st["id"])
        pm = O.player_mon(form, lv)
        own = O.own_moves(form, lv, st["id"])
        pool = [mv for mv in O.learnable(form, lv, st["id"])
                if damaging(mv) and (mv in own or mv not in scarce(st["id"]))]
        mvs = S.choose_moveset(pm, pool, opps, badges) if pool else []
        for opp in opps:
            total += min(S.duel_cost(pm, opp, mvs, badges)[0], CAP)
    return total


def solve():
    graph = json.load(open(f"{S.OUT}/encounters.json"))
    S._set_solve_inputs(S.build_sections(graph), None)
    supply = TM.extract()
    S.SCARCE.update(TM.scarce_moves(supply))
    S.set_repeatable(supply)
    scarce = lambda st: {mv for mv in S.SCARCE if S.scarce_spend(mv, st)}
    P.use_evo_timing({})
    base_avail = P.full_availability()
    cands = candidates(base_avail)
    by_hop = {}
    for frm, to, lv, mv in cands:
        by_hop.setdefault((frm, to), []).append((lv, mv))
    plan, t0 = {}, time.time()
    for (frm, to), opts in sorted(by_hop.items()):
        ev = next(e for e in E.EVOS[frm] if e["to"] == to)
        early = first_chance(frm, to, ev, base_avail)
        path = _path(to, base_avail)
        on_time = plan_cost(path, early, {}, scarce)
        best = None
        for lv, mv in opts:
            key = f"{frm}>{to}"
            hold = {key: {"level": lv, "move": mv}}
            c = plan_cost(path, early, hold, scarce)
            if c < on_time * (1 - HOLD_MARGIN) and (best is None or c < best[0]):
                best = (c, lv, mv)
        name = lambda sp: E.SPECIES[sp]["name"]
        if best:
            c, lv, mv = best
            plan[f"{frm}>{to}"] = {"level": lv, "move": mv, "early": early,
                                   "from": name(frm), "to": name(to),
                                   "cost": round(c, 2), "onTime": round(on_time, 2)}
            print(f"  hold {name(frm)} to L{lv} for {E.MOVES[mv]['name']} "
                  f"(evolve at L{early} otherwise): fight cost {on_time:.1f} -> {c:.1f}",
                  flush=True)
    P.use_evo_timing({})
    print(f"evolution timing: {len(plan)} of {len(by_hop)} candidate evolutions held "
          f"({time.time() - t0:.0f}s)", flush=True)
    return plan


if __name__ == "__main__":
    plan = solve()
    with open(P.evo_timing_path(), "w") as f:
        json.dump(plan, f, indent=1, sort_keys=True)
    print(f"wrote {P.evo_timing_path()}")
