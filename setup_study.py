#!/usr/bin/env python3
"""Does setting up beat just hitting things?

For every trainer battle, compare two ways of playing it with the SAME Pokemon:

  A. Hit and switch -- what the app already models. Best damaging move every
     turn; switching costs a turn and a hit.
  B. Set up and sweep -- spend k turns on a stat-boosting move, then clear the
     whole remaining party with the boost applied, never switching (boosts are
     lost the moment you switch out, so a sweep has to be a sweep).

Gen 3 stat stages, from `gStatStageRatios`: +1 is x1.5, +2 x2, +3 x2.5, +4 x3,
+5 x3.5, +6 x4, applied as an integer multiply-then-divide on the raw stat --
which is exactly what scaling the stat in `make_mon` does.

The break-even is easy to state. If a trainer's whole party would take T turns
to clear unboosted, k turns of setup are worth it when

    k + T/m(k)  <  T      i.e.   T > k / (1 - 1/m(k))

so one Swords Dance (x2) pays from T > 2 turns, one Sharpen (x1.5) from T > 3,
two Swords Dances (x4) from T > 2.67. The catch is everything the formula
leaves out: you eat k extra hits while setting up, a single KO ends the sweep,
and anything that forces you out throws the boosts away.
"""
import json, os, copy, collections
import engine as E
import progression as P
import optimize as O
import constraints as C
import sections as S

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# effect -> (which stat it scales, stages per use)
BOOST = {
    "ATTACK_UP": ("attack", 1), "ATTACK_UP_2": ("attack", 2),
    "SPECIAL_ATTACK_UP": ("spAttack", 1), "SPECIAL_ATTACK_UP_2": ("spAttack", 2),
    "DRAGON_DANCE": ("attack", 1),      # +1 Atk and +1 Speed; only Atk matters here
    "BULK_UP": ("attack", 1),           # +1 Atk, +1 Def
    "CALM_MIND": ("spAttack", 1),       # +1 SpA, +1 SpD
    "SPEED_UP_2": ("speed", 2),
}
# gStatStageRatios, +0 .. +6
RATIO = [(10, 10), (15, 10), (20, 10), (25, 10), (30, 10), (35, 10), (40, 10)]

def boosted(mon, stat, stages):
    """A copy of this Pokemon with one stat at +n, the way the ROM applies it."""
    if stages <= 0: return mon
    n, d = RATIO[min(6, stages)]
    out = copy.deepcopy(mon)
    out["stats"] = dict(mon["stats"])
    out["stats"][stat] = mon["stats"][stat] * n // d
    return out

def sweep_cost(pm, opps, pool, badges):
    """Turns to clear the whole party and damage taken, never switching."""
    r = O.sweep(pm, opps, pool, badges, 0)
    if r is None: return None
    return r["turns"], r["damageTaken"]

def study(stage, enc, sp, level, badges):
    """Best setup plan for one Pokemon against one trainer, vs not setting up."""
    pm = O.player_mon(sp, level)
    pool = O.move_pool(sp, level, stage)
    if not pool: return None
    opps = [E.realize_trainer_mon(enc["trainerConst"], i)
            for i in range(len(enc["party"]))]
    base = sweep_cost(pm, opps, pool, badges)
    if base is None: return None
    base_turns, base_taken = base

    # what it could set up with
    setups = [(mv, BOOST[E.MOVES[mv]["effect"]])
              for mv in O.unrestricted_pool(sp, level, stage) + list(E.learnable_by(sp, level))
              if E.MOVES.get(mv, {}).get("effect") in BOOST]
    seen, uniq = set(), []
    for mv, b in setups:
        if mv in seen: continue
        seen.add(mv); uniq.append((mv, b))
    if not uniq: return None

    best = None
    for mv, (stat, per) in uniq:
        if stat == "speed": continue          # speed changes turn order, not turns
        hits = [E.best_move(o, pm, badges={"def": badges["def"], "spdef": badges["spdef"]},
                            moves=o["moves"]) for o in opps]
        hits = [h["avg"] * h["accuracy"] / 100.0 for h in hits if h]
        thr = (sum(hits) / len(hits)) if hits else 0.0
        for k in (1, 2, 3):
            stages = min(6, per * k)
            bm = boosted(pm, stat, stages)
            r = sweep_cost(bm, opps, pool, badges)
            if r is None: continue
            t, taken = r
            total = k + t
            # k extra turns standing there being hit, by whatever leads
            extra = thr * k
            if total < base_turns and taken + extra < pm["stats"]["hp"]:
                cand = (total, k, mv, stat, stages, taken + extra)
                if best is None or cand[0] < best[0]: best = cand
    if best is None: return None
    total, k, mv, stat, stages, taken = best
    return {"species": sp, "name": E.SPECIES[sp]["name"], "trainer": enc["name"],
            "stage": stage, "baseTurns": round(base_turns, 2),
            "setupTurns": round(total, 2), "saved": round(base_turns - total, 2),
            "move": E.MOVES[mv]["name"], "stat": stat, "stages": stages, "k": k,
            "baseTaken": round(base_taken, 1), "setupTaken": round(taken, 1),
            "hp": pm["stats"]["hp"], "mons": len(opps)}

def main():
    graph = json.load(open(f"{OUT}/encounters.json"))
    secs = json.load(open(f"{OUT}/sections.json"))["sections"]["no"]["Charmander"]
    C.load_commitments(f"{OUT}/commitments.json")
    rows, considered = [], 0
    for e in graph:
        if e["kind"] in ("wild", "rematch"): continue
        if e.get("starterVariant") not in (None, "Charmander"): continue
        st = e["stage"]
        sec = secs.get(str(st))
        if not sec or not sec.get("team"): continue
        level, badges = sec["level"], O.badges_for(st)
        for t in sec["team"]:                     # only the party you actually field
            considered += 1
            r = study(st, e, t["species"], level, badges)
            if r: rows.append(r)
    rows.sort(key=lambda r: -r["saved"])
    print(f"trainer battles x party members examined: {considered}")
    print(f"cases where setting up is strictly faster AND survivable: {len(rows)}")
    print()
    print("biggest wins:")
    for r in rows[:18]:
        print(f"  s{r['stage']:<2d} {r['name']:11s} vs {r['trainer'][:26]:26s} "
              f"{r['mons']} mons | {r['baseTurns']:5.1f}t -> {r['setupTurns']:5.1f}t "
              f"(save {r['saved']:4.1f}) via {r['k']}x {r['move']} (+{r['stages']}) "
              f"| HP {r['setupTaken']:.0f}/{r['hp']}")
    print()
    by_move = collections.Counter(r["move"] for r in rows)
    print("which setup moves earn it:", dict(by_move.most_common()))
    print(f"total turns saved across the run: {sum(r['saved'] for r in rows):.0f}")
    if rows:
        big = [r for r in rows if r["saved"] >= 1.0]
        print(f"cases saving a full turn or more: {len(big)}")
    json.dump(rows, open(f"{OUT}/setupStudy.json", "w"), indent=1)

if __name__ == "__main__":
    main()
