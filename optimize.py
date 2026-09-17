#!/usr/bin/env python3
"""For every encounter in the graph, rank the Pokemon a player could actually
have at that point by how fast they clear the fight.

Objective (as chosen): fewest expected turns to KO, tie-broken by damage taken.
Ruleset: natural playthrough levels (no grinding), no in-battle items.

Two-phase for speed: a cheap scalar pass ranks every candidate, then the exact
Gen 3 damage distribution and turn DP run only on the finalists.
"""
import json, os, math
from collections import defaultdict
import engine as E
import progression as P
import build_graph as G
import constraints as C

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FINALISTS = 22          # how many candidates get the exact treatment
KEEP = 16               # kept before the page filters by starter and by trading

AVG_ROLL = 0.925        # mean of the 85-100% damage roll

# ------------------------------------------------------------------ move pools
SCARCE_TM = {}     # move const -> {item, copies, ...}; set by the TM planner
TM_OWNER = {}      # move const -> set of species allowed to know it

def set_tm_plan(scarce, owners):
    """Restrict single-use TM moves to their assigned holders."""
    SCARCE_TM.clear(); SCARCE_TM.update(scarce or {})
    TM_OWNER.clear()
    TM_OWNER.update({m: set(v) for m, v in (owners or {}).items()})
    _pool_cache.clear()

def tm_allowed(move, species, stage=None):
    rec = SCARCE_TM.get(move)
    if rec is None: return True                # HM, shop-only TM, or a level-up move
    ss = rec.get("shopStage")
    if ss is not None and stage is not None and stage >= ss:
        return True                            # buyable again -- no owner needed
    owners = TM_OWNER.get(move)
    if owners is None: return True             # no plan in force yet
    return species in owners

MOVE_MANIAC_STAGE = 28   # Two Island; relearns any level-up move for a mushroom

def _avail():
    if not _AVAIL_CACHE:
        _AVAIL_CACHE.update(P.full_availability())
    return _AVAIL_CACHE

_TO_HOP = None
def _levelup_pool(species, level, stage):
    """Level-up moves this specimen can actually KNOW. A form the run reaches
    by evolving only contributes moves from the level it joined the line --
    an evolved form's Lv-1-only moves (Gyarados's Thrash) exist solely
    through the Two Island Move Maniac. A form the run catches outright
    keeps its whole list, exactly like the game's starting-moveset walk."""
    if stage >= MOVE_MANIAC_STAGE:
        return E.learnable_by(species, level)
    av = _avail()
    rec = av.get(species)
    if not rec or not str(rec.get("source", "")).startswith("Evolve"):
        return E.learnable_by(species, level)
    global _TO_HOP
    if _TO_HOP is None:
        _TO_HOP = {}
        for frm, evs in E.EVOS.items():
            for ev in evs:
                _TO_HOP.setdefault(ev["to"], (frm, ev.get("method"), ev.get("param")))
    chain, cur = [], species
    while True:
        r2, hop = av.get(cur), _TO_HOP.get(cur)
        if not r2 or not str(r2.get("source", "")).startswith("Evolve") or not hop:
            chain.append((cur, 0)); break
        frm, meth, param = hop
        if meth == "LEVEL" and isinstance(param, int):
            join = param
        else:
            # stone/trade: no level-up learning fires on the evolution itself;
            # the form starts contributing at the level the run performs it
            join = P.STAGE_BY_ID.get(r2.get("stage"), {}).get("level", level) + 1
        chain.append((cur, join))
        cur = frm
    chain.reverse()          # caught base first
    out = []
    for i, (form, join) in enumerate(chain):
        upper = min(chain[i + 1][1], level) if i + 1 < len(chain) else level
        for lv, mv in E.LEVELUP.get(form, []):
            if i == 0:
                if lv <= upper: out.append(mv)
            elif join <= lv <= upper:
                out.append(mv)
    return out

_pool_cache = {}
def move_pool(species, level, stage):
    """Every damaging move this species could actually know at this point:
    level-up moves learned by `level`, plus TMs/HMs/tutor moves obtainable by `stage`."""
    key = (species, level, stage)
    if key in _pool_cache: return _pool_cache[key]
    moves = set(_levelup_pool(species, level, stage))
    tms = G.tm_moves_by_stage(stage)
    for item in E.TMHM.get(species, []):
        const = "ITEM_" + item.split("_")[0] if item.startswith(("TM", "HM")) else None
        mv = G.TMHM_MOVE.get(const)
        if mv and mv in tms:
            moves.add(mv)
    if stage >= 28:      # Move tutors live on Two Island (post-Blaine)
        for t in E.TUTOR.get(species, []):
            mv = "MOVE_" + t
            if mv in E.MOVES: moves.add(mv)
    dmg = []
    for mv in moves:
        m = E.MOVES.get(mv)
        if not m: continue
        if m["effect"] in E.UNUSABLE_EFFECTS: continue
        if not tm_allowed(mv, species, stage): continue
        if m["power"] > 0 or m["effect"] in ("DRAGON_RAGE", "SONICBOOM",
                                             "LEVEL_DAMAGE", "PSYWAVE", "SUPER_FANG"):
            dmg.append(mv)
    _pool_cache[key] = dmg
    return dmg

def unrestricted_pool(species, level, stage):
    saved = dict(TM_OWNER); TM_OWNER.clear(); _pool_cache.clear()
    try:
        return list(move_pool(species, level, stage))
    finally:
        TM_OWNER.update(saved); _pool_cache.clear()

_mon_cache = {}
def player_mon(species, level):
    key = (species, level)
    if key not in _mon_cache:
        _mon_cache[key] = E.make_mon(species, level, ivs_flat=15, nature=0, moves=[])
    return _mon_cache[key]

# ------------------------------------------------------------------ fast scalar pass
def scalar_damage(attacker, defender, move_const, badges):
    """Mean damage of one use of a move — no distribution, no DP."""
    m = E.MOVES[move_const]
    eff = m["effect"]
    if eff in E.UNUSABLE_EFFECTS: return 0.0
    if eff == "DRAGON_RAGE": return 40.0
    if eff == "SONICBOOM": return 20.0
    if eff in E.LEVEL_DAMAGE_EFFECTS: return float(attacker["level"])
    if eff == "SUPER_FANG": return defender["stats"]["hp"] / 2.0
    mult = E.type_mult(E.move_type_for(attacker, move_const), defender["types"])
    if mult == 0: return 0.0
    d = E.base_damage(attacker, defender, move_const, False,
                      badges.get("atk", False), badges.get("def", False),
                      badges.get("spatk", False), badges.get("spdef", False))
    if d <= 0: return 0.0
    if E.move_type_for(attacker, move_const) in attacker["types"]: d = d * 15 // 10
    d = d * mult
    cc = E.crit_chance(move_const)
    d = d * (1 + cc)                       # crits double, so mean scales by 1+p
    hits = E.MULTI_HIT_EFFECTS.get(eff, 1.0)
    tpu = 2.0 if eff in E.CHARGE_EFFECTS or eff in E.RECHARGE_EFFECTS else 1.0
    return d * AVG_ROLL * hits / tpu

def best_scalar(attacker, defender, pool, badges):
    best, bmv = 0.0, None
    for mv in pool:
        m = E.MOVES[mv]
        acc = (m["accuracy"] or 100) / 100.0
        v = scalar_damage(attacker, defender, mv, badges) * acc
        if v > best: best, bmv = v, mv
    return best, bmv

# ------------------------------------------------------------------ sweep model
_AVAIL_CACHE = {}
def obey_mult(player, badges):
    """Traded Pokemon above the badge obedience cap spend real turns ignoring
    you (`IsMonDisobedient`). Nothing you caught yourself is affected."""
    if not _AVAIL_CACHE:
        _AVAIL_CACHE.update(P.full_availability())
    rec = _AVAIL_CACHE.get(player["species"])
    if not rec or not rec.get("source", "").startswith("In-game trade"):
        return 1.0
    odds = E.obedience_odds(player["level"], badges.get("count", 0))
    return 1.0 / odds if odds > 0 else 99.0

def _turn_cost(off, hits):
    """Charge moves cost a wind-up turn per use; Hyper Beam costs a recharge
    turn after every hit except the one that faints the target."""
    e = E.MOVES[off["move"]]["effect"]
    if e in E.RECHARGE_EFFECTS: return max(1.0, 2.0 * hits - 1.0)
    if e in E.CHARGE_EFFECTS:   return 2.0 * hits
    return hits

def sweep(player, opp_mons, pool, badges, stage, terrain=None):
    """Walk the opposing party in order, tracking expected turns and cumulative
    damage on a single player Pokemon that never switches or heals."""
    hp = player["stats"]["hp"]
    remaining = float(hp)
    total_turns = 0.0
    per_opp = []
    _ob = obey_mult(player, badges)
    for opp in opp_mons:
        off = E.best_move(player, opp, badges={"atk": badges.get("atk", False),
                                               "spatk": badges.get("spatk", False)},
                          moves=pool)
        if off is None:
            return None                       # cannot damage this Pokemon at all
        t = _turn_cost(off, E.expected_turns_to_ko(off["dist"], off["accuracy"],
                                                   opp["stats"]["hp"]))
        if not math.isfinite(t) or t >= 40:
            return None
        t *= _ob
        thr = E.best_move(opp, player, badges={"def": badges.get("def", False),
                                               "spdef": badges.get("spdef", False)},
                          moves=opp["moves"])
        faster = player["stats"]["speed"] > opp["stats"]["speed"]
        # fold secondary-effect tempo both ways (flinch, freeze, paralysis,
        # burn, poison, confusion), exactly as the section simulator does
        act, burnf, chip = E.status_tempo(off["move"], faster, t, terrain)
        chip_in = 0.0
        if thr:
            a2, b2, c2 = E.status_tempo(thr["move"], not faster, t, terrain)
            t = t / max(a2, 0.25)
            if b2 > 0 and E.MOVES[off["move"]]["category"] == "PHYSICAL":
                t /= max(1e-6, 1.0 - 0.5 * b2)
            chip_in = c2
        t = t / (1.0 + chip * t)
        # the opponent attacks on each turn it is alive; if the player is faster
        # it gets one fewer swing on the turn the opponent faints
        swings = max(0.0, t - (1.0 if faster else 0.0))
        taken = 0.0
        if thr:
            scale = act * ((1.0 - 0.5 * burnf)
                           if E.MOVES[thr["move"]]["category"] == "PHYSICAL" else 1.0)
            taken = (thr["avg"] * thr["accuracy"] / 100.0 * swings * scale
                     + chip_in * t * hp)
            worst = thr["max"] * swings
        else:
            worst = 0.0
        remaining -= taken
        total_turns += t
        per_opp.append({
            "opp": opp["name"], "oppLevel": opp["level"], "oppHP": opp["stats"]["hp"],
            "move": off["name"], "moveConst": off["move"], "moveType": off["type"],
            "power": off["power"], "acc": off["accuracy"],
            "eff": off["effectiveness"],
            "dmgMin": off["min"], "dmgMax": off["max"],
            "critMin": off["critMin"], "critMax": off["critMax"],
            "tm": SCARCE_TM.get(off["move"], {}).get("item"),
            "dmgAvg": round(off["avg"], 1),
            "pctPerHit": round(off["avg"] / opp["stats"]["hp"] * 100, 1),
            "turns": round(t, 2),
            "ohko": round(E.ko_probability(off["dist"], off["accuracy"], opp["stats"]["hp"], 1), 3),
            "faster": faster,
            "playerSpe": player["stats"]["speed"], "oppSpe": opp["stats"]["speed"],
            "threat": (thr["name"] if thr else None),
            "threatType": (thr["type"] if thr else None),
            "threatAvg": (round(thr["avg"], 1) if thr else 0),
            "threatMin": (thr["min"] if thr else 0),
            "threatMax": (thr["max"] if thr else 0),
            "threatCritMax": (thr["critMax"] if thr else 0),
            "threatPct": (round(thr["avg"] / hp * 100, 1) if thr else 0.0),
            "takenHere": round(taken, 1),
        })
    taken_total = hp - remaining
    # how far down the opposing party it gets before its own HP runs out
    kos, run = 0, float(hp)
    for d in per_opp:
        run -= d["takenHere"]
        if run <= 0: break
        kos += 1
    return {
        "turns": total_turns,
        "hp": hp,
        "damageTaken": max(0.0, taken_total),
        "damageTakenPct": max(0.0, taken_total) / hp * 100,
        "survives": remaining > 0,
        "kos": kos,
        "hpLeftPct": max(0.0, remaining) / hp * 100,
        "perOpponent": per_opp,
    }


HEAL_ITEMS = {"Full Restore", "Hyper Potion", "Super Potion", "Potion", "Full Heal"}

def heal_penalty(sw, items):
    """Gym leaders and the Elite Four carry Full Restores and will use them.
    Each one effectively adds another Pokemon's worth of work to the fight."""
    n = sum(1 for i in items if i in HEAL_ITEMS)
    if not n or not sw["perOpponent"]: return sw
    per_turns = sw["turns"] / len(sw["perOpponent"])
    per_taken = sw["damageTaken"] / len(sw["perOpponent"])
    sw = dict(sw)
    sw["turns"] += n * per_turns
    sw["damageTaken"] += n * per_taken
    sw["damageTakenPct"] = sw["damageTaken"] / sw["hp"] * 100
    sw["survives"] = sw["damageTaken"] < sw["hp"]
    sw["hpLeftPct"] = max(0.0, sw["hp"] - sw["damageTaken"]) / sw["hp"] * 100
    sw["healItems"] = n
    return sw

# ------------------------------------------------------------------ candidate pool
def badges_for(stage):
    n = 0
    for b, st in P.BADGE_STAGE.items():
        if stage >= st: n = max(n, b)
    return {"count": n,
            "atk": n >= 1,          # Boulder Badge: +10% Attack
            "def": n >= 5,          # Soul Badge:    +10% Defense
            "spatk": n >= 7,        # Volcano Badge: +10% Sp. Atk
            "spdef": n >= 7}        # Volcano Badge: +10% Sp. Def

C.load_commitments(os.path.join(OUT, "commitments.json"))
try:
    import tms as _TM
    _supply = json.load(open(os.path.join(OUT, "tmSupply.json")))
    SCARCE_TM.update(_TM.scarce_moves(_supply))
except Exception:
    pass
AVAIL = P.full_availability()
UNCATCHABLE = {"SPECIES_MAROWAK"}   # the Pokemon Tower ghost is not obtainable there

ALLOW_TRADE = False
def set_allow_trade(on):
    """Whether the run is willing to trade. Off, the four trade evolutions
    (Alakazam, Machamp, Golem, Gengar) simply do not exist for it."""
    global ALLOW_TRADE
    ALLOW_TRADE = bool(on)
    _pool_cache.clear(); _mon_cache.clear()

def candidates(stage, allow_trade=None):
    if allow_trade is None: allow_trade = ALLOW_TRADE
    out = []
    for sp, rec in AVAIL.items():
        if rec["stage"] > stage: continue
        if sp not in E.SPECIES: continue
        if not allow_trade and rec.get("requiresTrade"): continue
        if not C.allowed(sp): continue        # contradicts a one-time choice
        if sp in UNCATCHABLE and rec["kind"] == "static": continue
        out.append(sp)
    return out

# ------------------------------------------------------------------ per-encounter
def analyze(enc, allow_trade=False):
    stage = enc["stage"]
    st = P.STAGE_BY_ID.get(stage, P.STAGES[-1])
    level = st["level"]
    badges = badges_for(stage)

    if enc["kind"] == "wild":
        opps = []
        for w in enc["wildMons"]:
            lv = round((w["minLevel"] + w["maxLevel"]) / 2)
            opps.append((E.make_mon(w["species"], lv), w["chance"] / 100.0))
        weighted = True
    else:
        opps = [(E.realize_trainer_mon(enc["trainerConst"], i), 1.0)
                for i in range(len(enc["party"]))]
        weighted = False
    if not opps: return None
    opp_mons = [o for o, _ in opps]

    cands = candidates(stage, allow_trade)
    # ---- phase A: scalar screen, both for the whole party and per opponent
    scored = []
    per_opp_scalar = [[] for _ in opps]
    for sp in cands:
        pm = player_mon(sp, level)
        pool = move_pool(sp, level, stage)
        if not pool: continue
        tot_ratio, ok, taken = 0.0, True, 0.0
        for i, (opp, weight) in enumerate(opps):
            dmg, _ = best_scalar(pm, opp, pool, badges)
            thr, _ = best_scalar(opp, pm, opp["moves"], {"def": badges["def"],
                                                         "spdef": badges["spdef"]})
            if dmg <= 0:
                ok = False
                continue
            ratio = opp["stats"]["hp"] / dmg
            per_opp_scalar[i].append((ratio, thr, sp))
            tot_ratio += weight * ratio
            taken += weight * thr
        if not ok: continue
        scored.append((tot_ratio, taken, sp))
    scored.sort(key=lambda x: (x[0], x[1]))
    for lst in per_opp_scalar: lst.sort(key=lambda x: (x[0], x[1]))

    # ---- phase B: exact turn DP, on the party finalists plus each opponent's
    # own best answers (a great answer to one Pokemon may be a poor sweeper)
    finalists = [sp for _, _, sp in scored[:FINALISTS]]
    seen = set(finalists)
    for lst in per_opp_scalar:
        for _, _, sp in lst[:6]:
            if sp not in seen:
                seen.add(sp); finalists.append(sp)

    evaluated = {}
    for sp in finalists:
        pm = player_mon(sp, level)
        pool = move_pool(sp, level, stage)
        sw = _weighted_sweep(pm, opps, pool, badges) if weighted \
             else sweep(pm, opp_mons, pool, badges, stage,
                        G.battle_terrain(enc.get("locationRaw") or "", enc["kind"]))
        if sw is None: continue
        if not weighted:
            sw = heal_penalty(sw, enc.get("items", []))
        rec = AVAIL[sp]
        evaluated[sp] = {
            "species": sp, "name": E.SPECIES[sp]["name"],
            "types": [t for i, t in enumerate(E.SPECIES[sp]["types"])
                      if i == 0 or t != E.SPECIES[sp]["types"][0]],
            "level": level,
            "turns": round(sw["turns"], 2),
            "takenPct": round(sw["damageTakenPct"], 1),
            "hpLeftPct": round(sw["hpLeftPct"], 1),
            "survives": sw["survives"],
            "kos": sw["kos"],
            "healItems": sw.get("healItems", 0),
            "hp": sw["hp"],
            "stats": pm["stats"],
            "obtainedAt": rec["stage"],
            "obtainedVia": rec["source"],
            "requiresTrade": rec.get("requiresTrade", False),
            "detail": sw["perOpponent"],
        }

    ranked = sorted(evaluated.values(), key=lambda r: (-r["kos"], r["turns"], r["takenPct"]))
    ranked = C.dedupe_ranked(ranked)[:KEEP]

    # per-opponent counter table
    counters = []
    for i, (opp, weight) in enumerate(opps):
        rows = []
        for sp in {s for _, _, s in per_opp_scalar[i][:10]} | set(evaluated):
            ev = evaluated.get(sp)
            if not ev or i >= len(ev["detail"]): continue
            d = ev["detail"][i]
            rows.append({
                "species": sp, "name": ev["name"], "types": ev["types"],
                "requiresTrade": ev["requiresTrade"],
                "move": d["move"], "moveType": d["moveType"], "eff": d["eff"],
                "dmgMin": d["dmgMin"], "dmgMax": d["dmgMax"],
                "critMax": d["critMax"], "tm": d.get("tm"),
                "pctPerHit": d["pctPerHit"], "turns": d["turns"], "ohko": d["ohko"],
                "faster": d["faster"], "threat": d["threat"],
                "threatPct": d["threatPct"], "threatMax": d["threatMax"],
            })
        rows.sort(key=lambda r: (r["turns"], r["threatPct"]))
        rows = C.dedupe_ranked(rows)
        counters.append({
            "opponent": opp["name"], "level": opp["level"],
            "types": [t for j, t in enumerate(opp["types"])
                      if j == 0 or t != opp["types"][0]],
            "hp": opp["stats"]["hp"],
            "chance": round(weight * 100, 1) if weighted else None,
            "best": rows[:9],
        })
    return {"team": ranked, "counters": counters,
            "playerLevel": level, "badges": badges["count"]}

def _weighted_sweep(player, opps, pool, badges):
    """Wild areas: one encounter at a time, weighted by how often each shows up."""
    hp = player["stats"]["hp"]
    turns = 0.0; taken = 0.0; per = []
    _ob = obey_mult(player, badges)
    for opp, weight in opps:
        off = E.best_move(player, opp, badges={"atk": badges["atk"], "spatk": badges["spatk"]},
                          moves=pool)
        if off is None: return None
        t = _turn_cost(off, E.expected_turns_to_ko(off["dist"], off["accuracy"],
                                                   opp["stats"]["hp"]))
        if not math.isfinite(t) or t >= 40: return None
        t *= _ob
        thr = E.best_move(opp, player, badges={"def": badges["def"], "spdef": badges["spdef"]},
                          moves=opp["moves"])
        faster = player["stats"]["speed"] > opp["stats"]["speed"]
        swings = max(0.0, t - (1.0 if faster else 0.0))
        tk = (thr["avg"] * thr["accuracy"] / 100.0 * swings) if thr else 0.0
        turns += weight * t
        taken += weight * tk
        per.append({
            "opp": opp["name"], "oppLevel": opp["level"], "oppHP": opp["stats"]["hp"],
            "chance": round(weight * 100, 1),
            "move": off["name"], "moveConst": off["move"], "moveType": off["type"],
            "power": off["power"], "acc": off["accuracy"], "eff": off["effectiveness"],
            "dmgMin": off["min"], "dmgMax": off["max"],
            "critMin": off["critMin"], "critMax": off["critMax"],
            "tm": SCARCE_TM.get(off["move"], {}).get("item"),
            "dmgAvg": round(off["avg"], 1),
            "pctPerHit": round(off["avg"] / opp["stats"]["hp"] * 100, 1),
            "turns": round(t, 2),
            "ohko": round(E.ko_probability(off["dist"], off["accuracy"], opp["stats"]["hp"], 1), 3),
            "faster": faster,
            "playerSpe": player["stats"]["speed"], "oppSpe": opp["stats"]["speed"],
            "threat": (thr["name"] if thr else None),
            "threatType": (thr["type"] if thr else None),
            "threatAvg": (round(thr["avg"], 1) if thr else 0),
            "threatMin": (thr["min"] if thr else 0),
            "threatMax": (thr["max"] if thr else 0),
            "threatCritMax": (thr["critMax"] if thr else 0),
            "threatPct": (round(thr["avg"] / hp * 100, 1) if thr else 0.0),
            "takenHere": round(tk, 1),
        })
    # A wild area is a series of independent single fights, so `taken` is the
    # expected cost of ONE encounter rather than a cumulative sweep total.
    survives = taken < hp
    return {"turns": turns, "hp": hp, "damageTaken": taken,
            "damageTakenPct": taken / hp * 100, "survives": survives,
            "kos": len(per) if survives else 0,
            "hpLeftPct": max(0.0, hp - taken) / hp * 100, "perOpponent": per}

# ------------------------------------------------------------------ run
if __name__ == "__main__":
    import sys, time
    graph = json.load(open(os.path.join(OUT, "encounters.json")))
    only = sys.argv[1] if len(sys.argv) > 1 else None
    t0 = time.time()
    out = {}
    done = 0
    for enc in graph:
        if only and only not in enc["id"]: continue
        # built with trades allowed and each entry tagged, so the page can hide
        # the trade evolutions rather than the pipeline building everything twice
        res = analyze(enc, allow_trade=True)
        if res: out[enc["id"]] = res
        done += 1
        if done % 50 == 0:
            print(f"  {done}/{len(graph)}  {time.time()-t0:.0f}s", flush=True)
    with open(os.path.join(OUT, "recommendations.json"), "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"done: {len(out)} encounters analyzed in {time.time()-t0:.0f}s")
    sz = os.path.getsize(os.path.join(OUT, "recommendations.json"))
    print(f"recommendations.json: {sz/1e6:.1f} MB")
