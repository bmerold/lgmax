#!/usr/bin/env python3
"""Per-section team recommendations for Pokemon LeafGreen.

A "section" is one stage of the progression model. For each section we pick the
smallest party that clears every trainer battle in it, simulating the whole run
with no items: HP and PP are spent across the section and restored only at the
section boundary (you passed a Pokemon Center) or at an in-game healing spot.

A section that contains a mid-run heal is additionally reported as separate
LEGS, cut at every full heal, so the page never shows a PP bar that quietly
spans a Pokemon Center (see build_legs). The PARTY is still chosen for the
whole section — the legs change how the run is reported, not how it is solved.

The only mid-dungeon healing spots in FRLG, per the game's scripts
(`special HealPlayerParty` outside a Pokemon Center):
    Pokemon Tower 5F   — the Purified Zone
    Ember Spa          — One Island, Kindle Road
    Seven Island house — post-game
Notably Rocket Hideout has none, so its whole basement is one continuous run.
"""
import json, os, math, re, collections
from collections import defaultdict
import engine as E
import progression as P
import build_graph as G
import optimize as O
import constraints as C
import walking as W
import hms as HM
import route_order as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

STARTERS = {
    "Bulbasaur": "SPECIES_BULBASAUR",
    "Charmander": "SPECIES_CHARMANDER",
    "Squirtle": "SPECIES_SQUIRTLE",
}
# The rival's trainer constant is suffixed with HIS starter, which is the one
# strong against yours.
RIVAL_SUFFIX_FOR = {"Bulbasaur": "CHARMANDER", "Charmander": "SQUIRTLE",
                    "Squirtle": "BULBASAUR"}

MAX_TEAM = 6
SCREEN = 26            # candidates promoted to exact evaluation per section
MIN_GAIN = 0.12        # a 6th member must cut section turns by >=12% to be worth it

# Where the party gets patched up mid-section. Two kinds:
#
#   * a Pokemon Center you walk past. Read from the game's heal-location table,
#     so it includes the two easily-forgotten route Centers -- Route 4 outside
#     Mt. Moon, and Route 10 sitting right at the mouth of Rock Tunnel.
#   * the two in-dungeon healing stations (`special HealPlayerParty` outside a
#     Center): the Purified Zone on Pokemon Tower 5F and the Ember Spa.
#
# A Center also covers the maps hanging off it (ThreeIsland_BondBridge is a walk
# from the Three Island Center), and the heal is anchored to the LAST battle
# there, i.e. you top up on your way out rather than after every trainer.
def _center_maps():
    hl = json.load(open(f"{G.REPO}/src/data/heal_locations.json"))["heal_locations"]
    out = set()
    for h in hl:
        raw = h["map"].replace("MAP_", "")
        out.add("".join(w.capitalize() for w in raw.split("_")))
    return out

HEAL_CENTERS = _center_maps()
HEAL_STATIONS = {"PokemonTower_5F", "OneIsland_KindleRoad"}
HEAL_AFTER = HEAL_CENTERS | HEAL_STATIONS

def heal_points(battles):
    """Index -> why the party is restored there. A heal on the very last battle
    of a section changes nothing (the section boundary restores you anyway), so
    it is dropped rather than shown as a pointless green row."""
    out = {}
    last = len(battles) - 1
    for loc in HEAL_AFTER:
        def at(e):
            raw = e.get("locationRaw") or ""
            return raw == loc or (loc in HEAL_CENTERS and raw.startswith(loc + "_"))
        # anchor on trainer battles: the wild ones are spread across the whole
        # section, so the last of those would push the heal far too late
        hits = [i for i, e in enumerate(battles) if at(e) and e["kind"] != "wild"]
        if not hits:
            hits = [i for i, e in enumerate(battles) if at(e)]
        if not hits: continue
        i = max(hits)
        if i >= last: continue
        if loc == "PokemonTower_5F":
            why = "the Purified Zone on Pokémon Tower 5F"
        elif loc == "OneIsland_KindleRoad":
            why = "the Ember Spa on Kindle Road"
        else:
            why = G.pretty_location(loc) + " Pokémon Center"
        out[i] = why
    return out

# Dungeon floor ordering, so a section's battles run in the order you meet them.
FLOOR_ORDER = {
    "RocketHideout_B1F": 1, "RocketHideout_B2F": 2, "RocketHideout_B3F": 3,
    "RocketHideout_B4F": 4,
    "PokemonTower_2F": 1, "PokemonTower_3F": 2, "PokemonTower_4F": 3,
    "PokemonTower_5F": 4, "PokemonTower_6F": 5, "PokemonTower_7F": 6,
    "SilphCo_2F": 1, "SilphCo_3F": 2, "SilphCo_4F": 3, "SilphCo_5F": 4,
    "SilphCo_6F": 5, "SilphCo_7F": 6, "SilphCo_8F": 7, "SilphCo_9F": 8,
    "SilphCo_10F": 9, "SilphCo_11F": 10,
    "VictoryRoad_1F": 1, "VictoryRoad_2F": 2, "VictoryRoad_3F": 3,
    "MtMoon_1F": 1, "MtMoon_B1F": 2, "MtMoon_B2F": 3,
    "RockTunnel_1F": 1, "RockTunnel_B1F": 2,
    "SeafoamIslands_1F": 1, "SeafoamIslands_B1F": 2, "SeafoamIslands_B2F": 3,
    "SeafoamIslands_B3F": 4, "SeafoamIslands_B4F": 5,
    "CeruleanCave_1F": 1, "CeruleanCave_2F": 2, "CeruleanCave_B1F": 3,
    "MtEmber_Exterior": 1, "MtEmber_RubyPath_1F": 2, "MtEmber_RubyPath_B1F": 3,
    "MtEmber_RubyPath_B2F": 4, "MtEmber_RubyPath_B3F": 5,
    "PokemonMansion_1F": 1, "PokemonMansion_2F": 2, "PokemonMansion_3F": 3,
    "PokemonMansion_B1F": 4,
    "PokemonLeague_LoreleisRoom": 1, "PokemonLeague_BrunosRoom": 2,
    "PokemonLeague_AgathasRoom": 3, "PokemonLeague_LancesRoom": 4,
    "PokemonLeague_ChampionsRoom": 5,
    "SSAnne_1F_Room2": 1, "SSAnne_1F_Room5": 2, "SSAnne_1F_Room7": 3,
    "SSAnne_2F_Room2": 4, "SSAnne_2F_Room4": 5, "SSAnne_2F_Corridor": 6,
    "SSAnne_B1F_Room1": 7, "SSAnne_B1F_Room2": 8, "SSAnne_B1F_Room3": 9,
    "SSAnne_B1F_Room4": 10, "SSAnne_Deck": 11,
}

# ------------------------------------------------------------------ sections
def build_sections(graph):
    """Trainer battles grouped by the stage the ROUTE fights them in — which
    can be later than the battle's own stage when the walk physically can't
    reach it yet (Lass Crissy behind Cerulean's cut tree). Falls back to the
    battle's own stage when no route has been solved. Rematches and wild
    areas are excluded from the PP load; rematches are opt-in and wild
    encounters are avoidable and unbounded."""
    by_stage = defaultdict(list)
    for e in graph:
        if e["kind"] in ("wild", "rematch"): continue
        rs = route_stage_of(e)
        by_stage[rs if rs is not None else e["stage"]].append(e)
    return dict(by_stage)

WILD_LOAD = {}     # stage -> [per-map estimate]; filled from walking.py

def route_wild_load():
    """Wild battles per section, counted from the ACTUAL route walk rather
    than a generic map clear: the tiles the path really covers on each map,
    at the stage it covers them, times that map's encounter share and rate.
    Reassigns each map's wild load to the stage the route walks it in."""
    import collections as _c
    rp = f"{OUT}/route.json"
    if not os.path.exists(rp): return W.section_wild_load()
    info = {}
    for maps in W.section_wild_load().values():
        for m in maps: info[m["map"]] = m
    rt = json.load(open(rp))
    out = {}
    for st in rt["stages"]:
        stage = st["stage"]
        walked = _c.defaultdict(float)
        for s2 in st["steps"]:
            path = s2.get("path") or []
            for a, b in zip(path, path[1:]):
                if a[0] == b[0]:
                    walked[a[0]] += abs(a[1]-b[1]) + abs(a[2]-b[2])
        entries = []
        for mp, steps in walked.items():
            m = info.get(mp)
            spe = m.get("stepsPerEncounter") if m else None
            if not m or not spe: continue
            enc_steps = steps * m["encounterShare"]
            battles = enc_steps / spe
            if battles < 0.5: continue
            e = dict(m); e["stage"] = stage; e["steps"] = int(steps)
            e["encounterSteps"] = round(enc_steps, 1); e["battles"] = round(battles, 1)
            entries.append(e)
        if entries: out[stage] = entries
    return out

def wild_battles_for(stage):
    """Wild encounters you have to fight to clear a section's maps: every item,
    every trainer, every corridor. One pseudo-battle each, so switching and the
    free send-out after a faint behave as they really would."""
    out = []
    for m in WILD_LOAD.get(str(stage), []):
        n = int(round(m["battles"]))
        if n <= 0 or not m.get("species"): continue
        # the map's encounter table as you actually meet it, weighted by slot
        # frequency -- this is what the route's lead has to answer blind
        dist = [(E.make_mon(sp["species"], sp["level"]), sp["share"])
                for sp in m["species"] if sp["species"] in E.SPECIES]
        tot = sum(s for _, s in dist)
        if not dist or tot <= 0: continue
        dist = [(mon, s / tot) for mon, s in dist]
        # split the count across the map's slots in proportion to how often
        # each actually appears
        picks = []
        for mon, share in dist:
            picks.extend([mon] * int(round(n * share)))
        while len(picks) < n: picks.append(dist[0][0])
        for mon in picks[:n]:
            out.append({
                "id": f"wild:{m['map']}", "kind": "wild", "name": f"Wild on {m['map']}",
                "location": m["map"], "locationRaw": m["map"], "group": m["map"],
                "items": [], "_mons": [mon], "_dist": dist,
            })
    return out

def interleave_wild(stage, battles, wild):
    """Put the walking where it actually happens.

    The wild battles used to be spread evenly across the section regardless of
    which map they belonged to, which -- now that the trainers are in walk order
    -- read as fighting Mt. Moon Zubats while standing on Route 3. Each map's
    wild battles now sit among that map's trainers, and a map with no trainers
    at all (Mt. Moon B1F) empties out at the point in the walk where you cross
    it.
    """
    if not wild: return battles
    by_map_w = collections.defaultdict(list)
    for w in wild:
        by_map_w[w.get("group") or w.get("location") or ""].append(w)
    by_map_t = collections.defaultdict(list)
    seq = []
    for b in battles:
        m = b.get("locationRaw") or ""
        if m not in by_map_t: seq.append(m)
        by_map_t[m].append(b)
    walk = R.order_maps(stage, seq + [m for m in by_map_w if m not in by_map_t],
                        FLOOR_ORDER)
    pos = {m: i for i, m in enumerate(walk)}

    out, done, cur = [], set(), None
    def flush_before(limit):
        for m in walk:
            if m in done or pos[m] >= limit: continue
            if by_map_t.get(m): continue          # handled with its own trainers
            out.extend(by_map_w.get(m, [])); done.add(m)
    for b in battles:
        m = b.get("locationRaw") or ""
        if m != cur:
            flush_before(pos.get(m, len(walk)))
            cur = m
            ws, ts = by_map_w.get(m, []), by_map_t.get(m, [])
            if m not in done:
                done.add(m)
                # spread this map's walking across its own trainers
                n, k = len(ws), max(1, len(ts))
                by_map_t[m] = ts
                spread = [ws[(n * i) // k:(n * (i + 1)) // k] for i in range(k)]
                by_map_t[m + "\x00spread"] = spread
        spread = by_map_t.get(m + "\x00spread")
        if spread:
            out.extend(spread.pop(0))
        out.append(b)
    flush_before(len(walk))
    return out

def variant_ok(enc, starter):
    """Keep only the rival/champion variant matching the player's starter."""
    sv = enc.get("starterVariant")
    return sv is None or sv == starter

# ------------------------------------------------------------------ route order
# The completionist route (tour.py, solved before this runs) is the single
# source of truth for the order battles happen in. The PP/HP simulation walks
# the same line the player walks, so the guide's checklist, its maps and its
# ledger all agree. A Fly mid-stage lands at a Pokémon Center, which is a
# full heal, and is treated as one.
def _center_doors():
    """Outdoor map -> Pokémon Center door tiles, from the warps themselves."""
    doors = {}
    for name, mj in R.maps().items():
        for w in mj.get("warp_events", []):
            dm = w.get("dest_map") or ""
            if "_POKEMON_CENTER" in dm and dm.endswith("_1F"):
                doors.setdefault(name, []).append((w.get("x", 0), w.get("y", 0)))
    return doors

def _heal_zones():
    """The two in-dungeon healing spots, as trigger tiles."""
    out = {}
    for name, why in (("PokemonTower_5F", "the Purified Zone on Pokémon Tower 5F"),
                      ("OneIsland_KindleRoad_EmberSpa", "the Ember Spa on Kindle Road")):
        mj = R.maps().get(name) or {}
        tiles = [(c.get("x", 0), c.get("y", 0)) for c in mj.get("coord_events") or []]
        if tiles: out[name] = (why, tiles)
    return out

def _step_heal(step, doors, zones):
    """Does this route step's walk pass a heal? Path tiles within 4 tiles of
    a Center door, inside a Center, on a heal-zone trigger, or a flight."""
    if step.get("fly"):
        return (f"flying — you land at the {G.pretty_location(step['fly'])} "
                "Pokémon Center")
    prev = None
    for m, x, y in step["path"]:
        if "PokemonCenter" in m:
            town = m.split("_PokemonCenter")[0]
            return G.pretty_location(town) + " Pokémon Center"
        if prev and prev[0] == m:
            n = max(abs(x - prev[1]), abs(y - prev[2]), 1)
            pts = [(round(prev[1] + (x - prev[1]) * t / n),
                    round(prev[2] + (y - prev[2]) * t / n)) for t in range(n + 1)]
        else:
            pts = [(x, y)]
        for dx_, dy_ in pts:
            for wxy in doors.get(m, ()):
                if max(abs(dx_ - wxy[0]), abs(dy_ - wxy[1])) <= 4:
                    return G.pretty_location(m) + " Pokémon Center"
            z = zones.get(m)
            if z and any(max(abs(dx_ - zx), abs(dy_ - zy)) <= 1
                         for zx, zy in z[1]):
                return z[0]
        prev = (m, x, y)
    return None

_ROUTE_POS = None

_CATCH_JOINS = None
def catch_joins():
    """stage -> {species display name: trainer ordinal}: how many of the
    stage's trainer fights the walk clears BEFORE that species' catch stop.
    A Pokemon first caught mid-stage cannot answer the fights before its
    grass -- Abra's patch is on the far side of Nugget Bridge, so nothing
    of Abra's line exists for the six bridge trainers."""
    global _CATCH_JOINS
    if _CATCH_JOINS is not None: return _CATCH_JOINS
    _CATCH_JOINS = {}
    path = f"{OUT}/route.json"
    if not os.path.exists(path): return _CATCH_JOINS
    rt = json.load(open(path))
    for st in rt["stages"]:
        joins, seen = {}, 0
        for s in st["steps"]:
            if s["kind"] == "trainer":
                seen += 1
            elif s["kind"] == "catch":
                for spec in s.get("species", []):
                    joins.setdefault(spec.split(" (")[0], seen)
        if joins: _CATCH_JOINS[st["stage"]] = joins
    return _CATCH_JOINS

_TM_JOINS = None
def tm_joins():
    """stage -> {move const: trainer ordinal}: how many of the stage's fights
    the walk clears BEFORE picking up that move's TM. Secret Power's TM43
    sits at the far end of Route 25 — nothing can be taught it for the
    Nugget Bridge fights."""
    global _TM_JOINS
    if _TM_JOINS is not None: return _TM_JOINS
    _TM_JOINS = {}
    path = f"{OUT}/route.json"
    if not os.path.exists(path): return _TM_JOINS
    rt = json.load(open(path))
    for st in rt["stages"]:
        joins, seen = {}, 0
        for s in st["steps"]:
            if s["kind"] == "trainer":
                seen += 1
            elif s["kind"] in ("item", "hidden") and str(s["what"]).startswith("TM"):
                mv = G.TMHM_MOVE.get("ITEM_" + str(s["what"]))
                if mv and seen > 0: joins.setdefault(mv, seen)
        if joins: _TM_JOINS[st["stage"]] = joins
    return _TM_JOINS

JOIN_AT = {}     # species const -> trainer ordinal it becomes usable at, this section
MOVE_JOIN = {}   # move const -> trainer ordinal its TM is picked up at, this section
MOVE_LOCKED = set()   # of MOVE_JOIN, the moves not yet held at the current battle
def set_join_at(stage, avail):
    JOIN_AT.clear()
    MOVE_JOIN.clear(); MOVE_LOCKED.clear()
    MOVE_JOIN.update(tm_joins().get(stage) or {})
    joins = catch_joins().get(stage)
    if not joins: return
    for sp in O.candidates(stage):
        if (avail.get(sp) or {}).get("stage") != stage: continue
        form = sp
        while form:
            j = joins.get(E.SPECIES[form]["name"])
            if j is not None:
                if j > 0: JOIN_AT[sp] = j
                break
            form = _pre_evo(form)

def route_positions():
    """battle key -> (global index, heal-after reason or None, route stage).
    The route stage is where the walk actually fights it, which can be LATER
    than the battle's own stage: Lass Crissy stands behind Cerulean's cut
    tree, so the route comes back for her once Cut is live."""
    global _ROUTE_POS
    if _ROUTE_POS is not None: return _ROUTE_POS
    _ROUTE_POS = {}
    path = f"{OUT}/route.json"
    if not os.path.exists(path): return _ROUTE_POS
    rt = json.load(open(path))
    doors, zones = _center_doors(), _heal_zones()
    i, last_key = 0, None
    for st in rt["stages"]:
        last_key = None                    # heals don't cross stage ends
        for s in st["steps"]:
            why = _step_heal(s, doors, zones)
            if why and last_key is not None:
                idx, old_why, rs = _ROUTE_POS[last_key]
                if not old_why:
                    _ROUTE_POS[last_key] = (idx, why, rs)
            if s["kind"] != "trainer": continue
            const = (s.get("enc") or "").split(":")[-1]
            key = R.battle_key(const)
            if key and key not in _ROUTE_POS:
                _ROUTE_POS[key] = (i, None, st["stage"])
                last_key = key
            i += 1
    return _ROUTE_POS

def route_order_battles(battles):
    """Reorder a section's battles to match the route; battles the route does
    not know keep their walk order at the end. Returns (battles, heal_after)
    where heal_after marks Centers walked past and Fly landings between
    consecutive fights."""
    pos = route_positions()
    def rp(e):
        hit = pos.get(R.battle_key(e["trainerConst"]))
        return hit[0] if hit else None
    if not any(rp(e) is not None for e in battles):
        return battles, {}
    order = sorted(range(len(battles)),
                   key=lambda i: (rp(battles[i]) if rp(battles[i]) is not None
                                  else 1 << 30, i))
    battles = [battles[i] for i in order]
    heal_after = {}
    for a in battles:
        hit = pos.get(R.battle_key(a["trainerConst"]))
        if hit and hit[1]:
            heal_after[a["id"]] = hit[1]
    return battles, heal_after

_COLD = None

def cold_stage_starts():
    """Stages whose start has NO heal: between the previous stage's last
    battle and this stage's first, the walk passes no Pokémon Center and no
    flight home. Beating the Champion respawns you at home fully healed, so
    the post-game boundary is warm by definition. For a cold start the party
    carries its HP and PP forward instead of resetting."""
    global _COLD
    if _COLD is not None: return _COLD
    _COLD = set()
    path = f"{OUT}/route.json"
    if not os.path.exists(path): return _COLD
    rt = json.load(open(path))
    for i in range(len(rt["stages"]) - 1):
        a, b = rt["stages"][i], rt["stages"][i + 1]
        if a["stage"] == 32:               # Champion -> respawn at home
            continue
        steps = []
        tb = [j for j, x in enumerate(a["steps"]) if x["kind"] == "trainer"]
        steps += a["steps"][tb[-1] + 1:] if tb else a["steps"]
        nb = [j for j, x in enumerate(b["steps"]) if x["kind"] == "trainer"]
        steps += b["steps"][:nb[0] + 1] if nb else b["steps"]
        doors, zones = _center_doors(), _heal_zones()
        if not any(_step_heal(st, doors, zones) for st in steps):
            _COLD.add(b["stage"])
    return _COLD

def route_stage_of(enc):
    """The stage the route actually fights this battle in, if known."""
    hit = route_positions().get(R.battle_key(enc["trainerConst"]))
    return hit[2] if hit else None

# ------------------------------------------------------------------ caches
_profile_cache = {}
def profile(pm, opp, move, badges, obey=1.0):
    key = (pm["species"], pm["level"], move, opp["species"], opp["level"],
           opp["stats"]["attack"], opp["stats"]["defense"], opp["stats"]["spAttack"],
           opp["stats"]["spDefense"], opp["stats"]["hp"],
           badges["atk"], badges["spatk"], obey)
    hit = _profile_cache.get(key)
    if hit is None:
        p = E.move_profile(pm, opp, move, badges={"atk": badges["atk"],
                                                  "spatk": badges["spatk"]})
        if p is None or p["immune"]:
            hit = None
        else:
            hp = opp["stats"]["hp"]
            raw = E.turn_table(p["dist"], p["accuracy"], hp)
            if raw is None:
                hit = None
            else:
                # charge/recharge moves cost more than one turn per use, so the
                # whole curve is converted to turns, not just its endpoint.
                # `obey` stretches it for a traded Pokemon over the badge
                # obedience cap: the turns it spends ignoring you are real turns.
                tbl = [0.0] + [O._turn_cost(p, h) * obey for h in raw[1:]]
                t = tbl[hp]
                hit = {"move": move, "name": p["name"], "type": p["type"],
                       "eff": p["effectiveness"], "min": p["min"], "max": p["max"],
                       "turns": t if math.isfinite(t) else 999.0,
                       "pp": E.MOVES[move]["pp"], "avg": p["avg"],
                       "acc": p["accuracy"], "table": tbl}
        _profile_cache[key] = hit
    return hit

def seg(p, hi, lo):
    """Expected turns for this move to take an opponent from `hi` HP down to
    `lo`. Read off the same DP as the full fight, so prefix + finish is exactly
    the cost of the whole fight when nobody hands over."""
    tbl = p["table"]
    if hi >= len(tbl): hi = len(tbl) - 1
    if lo >= len(tbl): lo = len(tbl) - 1
    return max(0.0, tbl[hi] - tbl[max(0, lo)])

_threat_cache = {}
def threat(opp, pm, badges):
    """Damage the opponent deals to this player Pokemon per turn."""
    key = (opp["species"], opp["level"], tuple(opp["moves"]), pm["species"],
           pm["level"], badges["def"], badges["spdef"])
    hit = _threat_cache.get(key)
    if hit is None:
        t = E.best_move(opp, pm, badges={"def": badges["def"], "spdef": badges["spdef"]},
                        moves=opp["moves"])
        hit = ({"name": t["name"], "avg": t["avg"] * t["accuracy"] / 100.0,
                "max": t["max"], "const": t["move"],
                "cat": E.MOVES[t["move"]]["category"]}
               if t else {"name": None, "avg": 0.0, "max": 0,
                          "const": None, "cat": None})
        _threat_cache[key] = hit
    return hit

# ------------------------------------------------------------------ movesets
def choose_moveset(pm, pool, opponents, badges):
    """Greedily pick the 4 moves that most reduce total turns across everything
    this section throws at you. Submodular, so greedy is near-optimal."""
    scored = []
    for mv in pool:
        tot = 0.0
        for opp in opponents:
            d = O.scalar_damage(pm, opp, mv, badges) * (E.MOVES[mv]["accuracy"] or 100) / 100
            tot += min(opp["stats"]["hp"], d)
        if tot > 0: scored.append((tot, mv))
    scored.sort(reverse=True)
    shortlist = [m for _, m in scored[:14]]
    if not shortlist: return []

    chosen, best_dpt = [], {id(o): 0.0 for o in opponents}
    while len(chosen) < 4 and shortlist:
        gain_best, pick, newbest = -1, None, None
        for mv in shortlist:
            if mv in chosen: continue
            cand, gain = {}, 0.0
            for o in opponents:
                d = O.scalar_damage(pm, o, mv, badges) * (E.MOVES[mv]["accuracy"] or 100) / 100
                d = min(d, o["stats"]["hp"])
                cur = best_dpt[id(o)]
                cand[id(o)] = max(cur, d)
                gain += max(0.0, d - cur)
            # a little credit for PP, so a 4th slot prefers a usable filler
            gain += E.MOVES[mv]["pp"] * 0.01
            if gain > gain_best:
                gain_best, pick, newbest = gain, mv, cand
        if pick is None or gain_best <= 0.02: break
        chosen.append(pick); best_dpt = newbest
        shortlist.remove(pick)
    return chosen

# ------------------------------------------------------------------ section run
class Member:
    __slots__ = ("species", "name", "mon", "moves", "pp", "hp", "maxhp", "types",
                 "level", "used", "minHpPct", "fainted", "refills", "pool", "obey",
                 "legBase", "legMinHpPct", "legFaints")
    def __init__(self, mon, moves, pool=None, obey=1.0):
        # >1 for a traded Pokemon above the badge obedience cap: every turn it
        # spends loafing is a turn, but it is not a use of the move, so the
        # penalty lands on the clock and not on the PP bar.
        self.obey = obey
        self.species = mon["species"]; self.name = mon["name"]; self.mon = mon
        self.moves = moves
        self.pp = {m: float(E.MOVES[m]["pp"]) for m in moves}
        self.maxhp = mon["stats"]["hp"]; self.hp = float(self.maxhp)
        self.types = mon["types"]; self.level = mon["level"]
        self.used = {m: 0.0 for m in moves}   # cumulative across the whole section
        self.minHpPct = 100.0
        self.fainted = 0
        self.refills = 0
        # every move it could have known, so a TM's alternative is knowable
        self.pool = list(pool) if pool else list(moves)
        self.leg_mark()
    def alive(self): return self.hp > 0
    def teach(self, move, replacing=None):
        """Put an HM in a move slot, dropping `replacing` if the four are full."""
        if replacing is not None and replacing in self.moves:
            self.moves = [move if m == replacing else m for m in self.moves]
            self.pp.pop(replacing, None); self.used.pop(replacing, None)
        elif move not in self.moves:
            self.moves = list(self.moves) + [move]
        self.pp[move] = float(E.MOVES[move]["pp"])
        self.used.setdefault(move, 0.0)
        if move not in self.pool: self.pool.append(move)
    def spend(self, move, amount):
        take = min(amount, self.pp[move])
        self.pp[move] -= take
        self.used[move] += take
    def hurt(self, dmg):
        self.hp -= dmg
        pct = max(0.0, self.hp) / self.maxhp * 100
        self.minHpPct = min(self.minHpPct, pct)
        self.legMinHpPct = min(self.legMinHpPct, pct)
    def reset(self):
        """Start of section: wipe the ledger too."""
        self.hp = float(self.maxhp); self.minHpPct = 100.0; self.fainted = 0
        self.refills = 0
        for m in self.pp:
            self.pp[m] = float(E.MOVES[m]["pp"]); self.used[m] = 0.0
        self.leg_mark()
    def restore(self):
        """An in-game healing spot: HP and PP come back, the ledger does not."""
        self.hp = float(self.maxhp)
        self.refills += 1
        for m in self.pp: self.pp[m] = float(E.MOVES[m]["pp"])
    def leg_mark(self):
        """Start a new leg: the stretch between two full heals. Everything
        since the last mark is what THIS leg cost."""
        self.legBase = dict(self.used)
        self.legMinHpPct = max(0.0, self.hp) / self.maxhp * 100
        self.legFaints = self.fainted

def usable(member, opp, badges):
    """Best move this member can still afford against this opponent."""
    best = None
    for mv in member.moves:
        if mv in MOVE_LOCKED: continue
        if member.pp[mv] < 1: continue
        p = profile(member.mon, opp, mv, badges, member.obey)
        if not p or p["turns"] >= 900: continue
        if best is None or p["turns"] < best["turns"]:
            best = p
    return best

def alt_turns(member, opp, badges, exclude):
    """Best this Pokemon could manage against the same opponent WITHOUT one move
    — the honest price of the TM it is holding."""
    best = None
    for mv in member.pool:
        if mv == exclude: continue
        p = profile(member.mon, opp, mv, badges, member.obey)
        if not p or p["turns"] >= 900: continue
        if best is None or p["turns"] < best: best = p["turns"]
    return best

# ------------------------------------------------------------------ HM coverage
def move_value(pm, move, opponents, badges):
    """What a move is worth against this section: total damage it can land."""
    tot = 0.0
    for o in opponents:
        d = O.scalar_damage(pm, o, move, badges) * (E.MOVES[move]["accuracy"] or 100) / 100
        tot += min(o["stats"]["hp"], d)
    return tot

# An HM cannot be overwritten the normal way -- `IsHMMove2` blocks it outright,
# and sHMMoves lists all seven. The only way back out of that slot is the Move
# Deleter, who lives in Fuchsia City House 3. So an HM taught before Fuchsia is
# stuck on that Pokemon, and this model has to carry it forward rather than
# quietly handing the Pokemon four fresh moves next section.
MOVE_DELETER_STAGE = 21     # Fuchsia City House 3
RELEARNER_STAGE = 28        # the Move Maniac, Two Island House

HM_HELD = defaultdict(set)  # species -> HM moves it is stuck with, this run
def reset_hm_held(): HM_HELD.clear()

REPEATABLE_TM = {}          # move const -> (item, stage the shop opens)
def set_repeatable(supply):
    REPEATABLE_TM.clear()
    for item, v in (supply or {}).items():
        if v.get("obtainable") and v.get("repeatable") and v.get("move"):
            shops = [s["stage"] for s in v.get("sources", [])
                     if s.get("kind") in ("shop", "Game Corner")
                     and s.get("stage") is not None]
            REPEATABLE_TM[v["move"]] = (item, min(shops) if shops else 0)

def move_recovery(species, move, level, stage):
    """If an HM takes this move's slot, can you ever get the move back?

    Returns (rank, label); a lower rank is cheaper to give up. The Move Maniac
    on Two Island (a Big Mushroom, or two Tiny ones) relearns **level-up moves
    only** -- `GetMoveRelearnerMoves` walks `gLevelUpLearnsets` and nothing
    else -- and he is not reachable until the Sevii Islands. A single-use TM
    move is simply gone.
    """
    if move in HM.HM_MOVE.values():
        return (0, "HMs are reusable — it goes straight back on")
    levelup = move in E.learnable_by(species, level)
    if levelup and stage >= RELEARNER_STAGE:
        return (0, "relearnable now from the Move Maniac on Two Island")
    rep = REPEATABLE_TM.get(move)
    if rep and stage >= rep[1]:
        return (1, f"{rep[0].replace('ITEM_', '')} can be bought again")
    if levelup:
        return (2, "a level-up move — the Move Maniac on Two Island can put it "
                   "back once you reach the Sevii Islands")
    if move in SCARCE:
        return (3, f"{SCARCE[move]['item'].replace('ITEM_', '')} is single-use — gone for good")
    return (2, "not re-obtainable in this run")

def drop_key(member, move, level, stage, opponents, badges):
    """Which move to give up for an HM: what you can get back first, what you
    barely use second. Losing a level-up move you can relearn beats losing the
    run's only Flamethrower, even if the Flamethrower is doing less right here."""
    return (move_recovery(member.species, move, level, stage)[0],
            move_value(member.mon, move, opponents, badges))

def assign_hms(team, stage, level, badges, avail, opponents, starter,
               held_roots, held_groups, chosen):
    """Make sure the party can actually cross the section, not just win in it.

    Field moves are cheap to carry and expensive to be without, so they are
    placed in the cheapest way that works, in order:

      1. **Already knows it.** Surf and Strength are real attacks (95 and 80
         power), so the battle optimizer often picks them up unprompted and the
         HM costs nothing at all.
      2. **A spare move slot.** A Pokemon whose fourth slot was not worth filling
         carries the HM for free.
      3. **A carrier.** If nothing on the team can learn it and the party is not
         yet six, bring something that can. It is scored on how many of the gaps
         it closes first and on how much it contributes in battle second -- an
         HM slave that also fights is strictly better than one that does not.
      4. **A sacrificed move.** With a full party and no carrier possible, the
         most capable member gives up its least valuable move. This is the only
         branch that costs anything, and it is priced: the section is re-run
         afterwards, so the turn count reflects the move that is gone.

    Returns (plan, extra members to add to the party).
    """
    req = HM.moves_here(stage)
    plan, extra = [], []

    # Before Fuchsia there is no Move Deleter, so an HM taught in an earlier
    # section is STILL sitting in that Pokemon's move slots. Put it back before
    # anything else, so the section is costed with the moves it really has.
    if stage < MOVE_DELETER_STAGE:
        for m in team:
            for mv in sorted(HM_HELD.get(m.species, ())):
                if mv in m.moves: continue
                if len(m.moves) < 4:
                    m.teach(mv)
                else:
                    worst = min(m.moves, key=lambda x: drop_key(m, x, level, stage,
                                                                opponents, badges))
                    m.teach(mv, replacing=worst)

    if not req:
        for m in team:
            for mv in m.moves:
                if mv in HM.HM_MOVE.values(): HM_HELD[m.species].add(mv)
        return plan, extra

    def holder(mv):
        return next((m for m in team + extra if mv in m.moves), None)

    # ---- 1 & 2: what the party can already do
    uncovered = []
    for mv in req:
        who = holder(mv)
        if who:
            plan.append({"move": mv, "by": who.name, "species": who.species,
                         "how": "known", "gave": None, "gaveBack": None,
                         "permanent": False})
            continue
        free = [m for m in team + extra
                if HM.can_learn(m.species, mv) and len(m.moves) < 4]
        if free:
            m = min(free, key=lambda x: (len(x.moves), x.name))
            m.teach(mv)
            plan.append({"move": mv, "by": m.name, "species": m.species,
                         "how": "spare", "gave": None, "gaveBack": None,
                         "permanent": False})
            continue
        uncovered.append(mv)

    # ---- 3: carriers, each closing as many remaining gaps as it can
    pool = None
    while uncovered and len(team) + len(extra) < MAX_TEAM:
        if pool is None:
            pool = [sp for sp in O.candidates(stage)
                    if sp not in chosen and C.allowed(sp)
                    and C.starter_of(sp) in (None, starter)
                    and not C.outgrown(sp, stage, avail)]
        best, bestkey = None, None
        roots = set(held_roots) | {C.line_root(m.species) for m in extra}
        groups = set(held_groups) | {g for g in
                                     (C.exclusive_group(m.species) for m in extra) if g}
        for sp in pool:
            if C.conflicts(sp, roots, groups): continue
            covers = [mv for mv in uncovered if HM.can_learn(sp, mv)]
            if not covers: continue
            pm = O.player_mon(sp, level)
            fight = max((move_value(pm, mv, opponents, badges)
                         for mv in O.move_pool(sp, level, stage)), default=0.0)
            key = (-len(covers), -fight, E.SPECIES[sp]["name"])
            if bestkey is None or key < bestkey:
                best, bestkey = (sp, covers), key
        if best is None: break
        sp, covers = best
        pm = O.player_mon(sp, level)
        mpool = O.move_pool(sp, level, stage)
        # HMs first, then whatever battle moves still fit
        mvs = list(covers) + [m for m in choose_moveset(pm, mpool, opponents, badges)
                              if m not in covers]
        m = Member(pm, mvs[:4], mpool, obey_factor(sp, level, badges, avail))
        extra.append(m)
        for mv in covers:
            plan.append({"move": mv, "by": m.name, "species": sp,
                         "how": "carrier", "gave": None, "gaveBack": None,
                         "permanent": False})
        uncovered = [mv for mv in uncovered if mv not in covers]

    # ---- 4: last resort, someone gives up a move
    for mv in list(uncovered):
        if mv not in uncovered: continue   # a prior swap already covered it
        cands = [m for m in team + extra if HM.can_learn(m.species, mv)]
        if not cands and len(team) + len(extra) >= MAX_TEAM:
            # A full party where nobody can even learn the move -- Silph Co.
            # fills all six before the field moves are handed out, and not one
            # of them can carry Fly. Trade the body that contributes least for
            # one that can. A slot spent on a field move you cannot otherwise
            # take is worth more than a sixth attacker.
            roots = {C.line_root(m.species) for m in team + extra}
            groups = {g for g in (C.exclusive_group(m.species)
                                  for m in team + extra) if g}
            swap_in, key = None, None
            for sp in O.candidates(stage):
                if sp in chosen or not C.allowed(sp): continue
                if C.starter_of(sp) not in (None, starter): continue
                if C.outgrown(sp, stage, avail): continue
                if not HM.can_learn(sp, mv): continue
                pm = O.player_mon(sp, level)
                mpool = O.move_pool(sp, level, stage)
                covers = sum(1 for x in uncovered if HM.can_learn(sp, x))
                fight = max((move_value(pm, x, opponents, badges) for x in mpool),
                            default=0.0)
                k = (-covers, -fight, E.SPECIES[sp]["name"])
                if key is None or k < key:
                    if not C.conflicts(sp, roots, groups) or True:
                        swap_in, key = (sp, pm, mpool, covers), k
            if swap_in:
                sp, pm, mpool, _ = swap_in
                worst = min(team, key=lambda m: max(
                    (move_value(m.mon, x, opponents, badges) for x in m.moves),
                    default=0.0))
                team.remove(worst)
                if worst.species in chosen: chosen.remove(worst.species)
                mine = [x for x in uncovered if HM.can_learn(sp, x)]
                mvs = list(mine) + [x for x in choose_moveset(pm, mpool, opponents, badges)
                                    if x not in mine]
                m = Member(pm, mvs[:4], mpool, obey_factor(sp, level, badges, avail))
                extra.append(m); chosen.append(sp)
                for x in mine:
                    plan.append({"move": x, "by": m.name, "species": sp,
                                 "how": "swap", "gave": worst.name,
                                 "gaveBack": None, "permanent": False})
                    if x in uncovered: uncovered.remove(x)
                continue
        if not cands:
            plan.append({"move": mv, "by": None, "species": None,
                         "how": "uncovered", "gave": None, "gaveBack": None,
                         "permanent": False})
            if mv in uncovered: uncovered.remove(mv)
            continue
        best, bestkey = None, None
        for m in cands:
            worst = min(m.moves, key=lambda x: drop_key(m, x, level, stage,
                                                        opponents, badges))
            cost = drop_key(m, worst, level, stage, opponents, badges)
            if bestkey is None or cost < bestkey:
                best, bestkey = (m, worst), cost
        m, worst = best
        rank, why = move_recovery(m.species, worst, level, stage)
        m.teach(mv, replacing=worst)
        plan.append({"move": mv, "by": m.name, "species": m.species,
                     "how": "sacrifice", "gave": E.MOVES[worst]["name"],
                     "gaveBack": why, "permanent": rank >= 3})
        if mv in uncovered: uncovered.remove(mv)

    # whatever HMs the party now holds, it is stuck with until Fuchsia
    for m in team + extra:
        for mv in m.moves:
            if mv in HM.HM_MOVE.values(): HM_HELD[m.species].add(mv)
    return plan, extra

# ------------------------------------------------------------------ obedience
def obey_factor(species, level, badges, avail):
    """How much longer everything takes because the Pokemon will not listen.

    `IsMonDisobedient` only fires for a Pokemon with someone else's OT, which in
    a normal playthrough means the in-game trades and nothing else. Above the
    badge cap it ignores you on a fixed roll, and the turns it spends loafing,
    napping or throwing out a move you did not pick are real turns -- so the
    expected-turn curve is stretched by 1/P(obey). It is not charged PP for
    them, which is why the PP bar stays honest.

    Treating every disobedient turn as wasted is slightly harsh: one branch of
    the ROM's roll does fire a random move, which is sometimes the one you
    wanted. Nothing else in this model tracks per-turn move choice, so the
    conservative reading stands.
    """
    if not avail: return 1.0
    rec = avail.get(species)
    if not rec or not rec.get("source", "").startswith("In-game trade"):
        return 1.0
    odds = E.obedience_odds(level, badges["count"])
    return 1.0 / odds if odds > 0 else 99.0

# ------------------------------------------------------------------ route leads
LEAD_PENALTY = 30.0     # what an encounter you cannot answer at all is worth

def lead_cost(m, dist, badges):
    """What this Pokemon expects to spend on ONE wild battle drawn from a map's
    encounter table: turns, and damage taken. A slot it cannot answer is scored
    as a disaster rather than a switch, because a lead that gets walled by a
    tenth of the grass is not the lead you want."""
    t = d = 0.0
    for opp, share in dist:
        p = usable(m, opp, badges)
        if p is None:
            t += share * LEAD_PENALTY
            continue
        faster = m.mon["stats"]["speed"] > opp["stats"]["speed"]
        t += share * p["turns"]
        d += share * threat(opp, m.mon, badges)["avg"] * \
             max(0.0, p["turns"] - (1.0 if faster else 0.0))
    return t, d

_DEX_EVOS = None
def dex_evolutions(avail):
    """stage -> [rows] of caught Pokémon that first fill a new Pokédex entry by
    EVOLUTION at that stage: level-ups the moment your level reaches them,
    store-stone evolutions once Celadon opens, Moon-Stone ones flagged as
    spending a scarce stone. Whenever you next pass a Pokémon Center (where you
    could grind a level or use a stone), these are the entries within reach."""
    global _DEX_EVOS
    if _DEX_EVOS is not None: return _DEX_EVOS
    STORE_STONES = {"ITEM_FIRE_STONE", "ITEM_WATER_STONE",
                    "ITEM_THUNDER_STONE", "ITEM_LEAF_STONE"}
    out = collections.defaultdict(list)
    for tgt, rec in avail.items():
        if rec.get("kind") != "evolution": continue
        frm = rec.get("from"); meth = rec.get("evoMethod"); param = rec.get("evoParam")
        fname = E.SPECIES[frm]["name"] if frm else "?"
        tname = E.SPECIES[tgt]["name"]
        if meth and meth.startswith("LEVEL"):
            lv = param if isinstance(param, int) else None
            how = f"level it to {lv}" if lv else "level it up"
            note = None
            # optional: a strong move the pre-evo learns a few levels before the
            # evolved form, within a plausible playthrough window. Delaying trades
            # evolved stats meanwhile, so it is framed as a choice, not advice.
            if lv:
                fl = {m2: l2 for l2, m2 in E.LEVELUP.get(frm, [])}
                tl = {m2: l2 for l2, m2 in E.LEVELUP.get(tgt, [])}
                best = None
                for m2, lf in fl.items():
                    if E.MOVES[m2]["power"] < 90: continue
                    lt = tl.get(m2, 999)
                    gap = lt - lf
                    if lf > lv and 3 <= gap <= 12 and lf <= lv + 22:
                        if best is None or gap > best[0]:
                            best = (gap, E.MOVES[m2]["name"], lf, lt)
                if best:
                    g2, mvn, lf, lt = best
                    note = (f"optional: {fname} learns {mvn} at L{lf}, "
                            f"{g2} levels before {tname}"
                            + (f" (which learns it at L{lt})" if lt < 999 else " (never, by level)")
                            + " — delay the evolution only if you want it early")
        elif meth == "ITEM":
            stn = E.ITEMS.get(param, {}).get("name", param)
            buy = " (buy at Celadon)" if param in STORE_STONES else ""
            how = f"use a {stn}{buy}"
            note = ("only 4 in the game — save them for the evolutions you'll keep"
                    if param == "ITEM_MOON_STONE" else None)
            # if the evolved form learns nothing more by level, warn to delay
            keep = [E.MOVES[mv2]["name"] for lv2, mv2 in E.LEVELUP.get(tgt, [])
                    if lv2 > 1 and E.MOVES[mv2]["power"] >= 40]
            if not keep:
                pre = sorted({mv2 for lv2, mv2 in E.LEVELUP.get(frm, [])
                              if E.MOVES[mv2]["power"] >= 40}, key=lambda x: x)
                pre_names = [E.MOVES[x]["name"] for x in
                             sorted({m2 for l2, m2 in E.LEVELUP.get(frm, [])
                                     if E.MOVES[m2]["power"] >= 40})][:3]
                warn = (f"{tname} learns nothing more by level — teach {fname} its "
                        f"level-up moves ({', '.join(pre_names)}) BEFORE using the stone")
                note = (note + " · " + warn) if note else warn
        elif meth == "FRIENDSHIP":
            how = "raise its friendship"; note = "needs the National Dex (post-Elite Four)"
        else:
            continue                              # trades / trade-items: handled as their own stops
        out[rec["stage"]].append({"from": fname, "to": tname, "how": how, "note": note})
    for st in out: out[st].sort(key=lambda r: r["to"])
    _DEX_EVOS = dict(out)
    return _DEX_EVOS

def wild_plan(team, stage, badges):
    """For each map the section walks, the point Pokémon and how it answers
    each wild species in the grass: the move, expected turns, and damage.
    This is what the POINT lead actually does while you walk."""
    plans = []
    for m in WILD_LOAD.get(str(stage), []):
        specs = [sp for sp in m.get("species", []) if sp["species"] in E.SPECIES]
        if not specs or not m.get("battles"): continue
        dist = [(E.make_mon(sp["species"], sp["level"]), sp["share"]) for sp in specs]
        tot = sum(sh for _, sh in dist) or 1
        dist = [(mon, sh / tot) for mon, sh in dist]
        lead = min((mm for mm in team if mm.alive()),
                   key=lambda mm: lead_cost(mm, dist, badges), default=None)
        if lead is None: continue
        rows = []
        for (mon, share), sp in zip(dist, specs):
            pr = usable(lead, mon, badges)
            faster = lead.mon["stats"]["speed"] > mon["stats"]["speed"]
            rows.append({
                "species": sp["species"], "name": E.SPECIES[sp["species"]]["name"],
                "level": sp["level"], "share": round(share, 3),
                "by": pr["name"] if pr else None,
                "turns": round(pr["turns"], 1) if pr else None,
                "dmg": round(pr["avg"], 0) if pr else 0,
                "hp": mon["stats"]["hp"],
                "faster": bool(faster) if pr else False,
                "walled": pr is None,
            })
        rows.sort(key=lambda r: -r["share"])
        plans.append({"map": m["map"], "lead": E.SPECIES[lead.species]["name"],
                      "battles": round(m["battles"], 1), "rows": rows})
    return plans

def pick_lead(team, enc, badges, leads):
    """Who is walking the route. Chosen once per map against the whole weighted
    encounter table -- not per battle, because you do not get to see what the
    grass sends before you send yours. Kept until it faints or runs dry."""
    dist = enc.get("_dist") or [(o, 1.0) for o in enc["_mons"]]
    g = enc.get("group") or enc["location"]
    cur = leads.get(g)
    if cur is not None and cur.alive():
        t, d = lead_cost(cur, dist, badges)
        # keep walking with it while it can still answer the table and take a
        # couple more average encounters; you rotate the lead when it gets low
        # or runs out of PP, not after every fight
        if t < LEAD_PENALTY and d * 2.0 < cur.hp:
            return cur
    best, bestkey = None, None
    for m in team:
        if not m.alive(): continue
        t, d = lead_cost(m, dist, badges)
        key = (round(t, 4), round(d, 3), E.SPECIES[m.species]["name"])
        if bestkey is None or key < bestkey:
            best, bestkey = m, key
    if best is not None: leads[g] = best
    return best

HANDOVER_FLOOR = 3.0   # a handover costs a switch turn plus a finishing turn,
                       # so it cannot beat a solo that is already this quick
HANDOVER_WIDTH = 3     # openers and finishers considered, best-first
HANDOVER_STEPS = 10    # evenly spaced handover points, on top of the natural ones

def _thresh(tbl, limit):
    """Highest remaining HP from which this move still finishes within `limit`
    turns -- i.e. the HP a partner should aim to leave the target on."""
    lo, hi = 0, len(tbl) - 1
    if tbl[hi] <= limit: return hi
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tbl[mid] <= limit: lo = mid
        else: hi = mid - 1
    return lo

def plan_vs(team, opp, badges, active, terrain=None):
    """Cheapest way for the party to take ONE opposing Pokemon down.

    Two shapes are considered. A solo: one member does the whole thing, paying a
    turn and a hit if it has to switch in. A handover: one member softens the
    target and a second finishes it, paying a turn and a hit for the mid-fight
    switch. The handover's prefix is priced off the same DP as the solo, so
    softening for k turns costs exactly the turns it removes from the finisher.

    Returns a list of segments, each (member, profile, threat, turns, hits,
    isSwitch, selfKO), or None if nothing on the team can touch this Pokemon.
    """
    H = opp["stats"]["hp"]
    opts = []
    for m in team:
        if not m.alive(): continue
        p = usable(m, opp, badges)
        if not p or "table" not in p: continue
        opts.append((m, p, threat(opp, m.mon, badges),
                     m.mon["stats"]["speed"] > opp["stats"]["speed"]))
    if not opts: return None

    def sw(m): return 0 if (active is None or m is active) else 1
    def boom(p): return E.MOVES[p["move"]]["effect"] == "EXPLOSION"

    # ---- solo
    best, bestkey = None, None
    for m, p, thr, faster in opts:
        s, t = sw(m), p["turns"]
        # secondary-effect tempo, both directions: our flinch/freeze/paralysis
        # cuts the opponent's acting turns and burn/poison chips it down; its
        # own status moves stretch our clock and add residual damage to us
        act, burnf, chip = E.status_tempo(p["move"], faster, t, terrain,
            def_ability=opp.get("ability", "NONE"), atk_ability=m.mon.get("ability", "NONE"))
        t_me, chip_in = t, 0.0
        if thr.get("const"):
            a2, b2, c2 = E.status_tempo(thr["const"], not faster, t, terrain,
                def_ability=m.mon.get("ability", "NONE"), atk_ability=opp.get("ability", "NONE"))
            t_me = t / max(a2, 0.25)
            if b2 > 0 and E.MOVES[p["move"]]["category"] == "PHYSICAL":
                t_me /= max(1e-6, 1.0 - 0.5 * b2)
            chip_in = c2
        t_me = t_me / (1.0 + chip * t_me)      # our chip shortens the fight
        hits = max(0.0, t_me - (1.0 if faster else 0.0)) + s
        scale = act * ((1.0 - 0.5 * burnf) if thr.get("cat") == "PHYSICAL" else 1.0)
        dmg = thr["avg"] * hits * scale + chip_in * t_me * m.maxhp
        hits_dmg = dmg / max(thr["avg"], 1e-6)     # so m.hurt() charges the real toll
        kills_self = boom(p)
        survives = dmg < m.hp and not kills_self
        key = (0 if survives else 1, round(t_me + s, 4), dmg)
        if bestkey is None or key < bestkey:
            bestkey = key
            best = [(m, p, thr, t_me + s, hits_dmg, s > 0, kills_self, t / m.obey)]

    # Expected turns are very nearly linear in the target's remaining HP, so
    # prefix + switch + finish is essentially never FASTER than whichever solo
    # is best -- the switch turn is pure tax. What a handover buys is survival:
    # two bodies split the incoming damage, and a limited-PP nuke gets stretched
    # across a fight it could not have carried alone. So the search runs
    # whenever the best solo drops a Pokemon, and otherwise only for fights slow
    # enough that the tax could still be worth paying.
    if bestkey[0] == 0 and bestkey[1] <= HANDOVER_FLOOR:
        return best

    ranked = sorted(opts, key=lambda o: o[1]["turns"] + sw(o[0]))
    openers = ranked[:HANDOVER_WIDTH]
    finishers = sorted(opts, key=lambda o: o[1]["turns"])[:HANDOVER_WIDTH]

    for mA, pA, thrA, fastA in openers:
        sA, ppA = sw(mA), mA.pp[pA["move"]]
        if boom(pA): continue                      # nothing to hand over after
        for mB, pB, thrB, fastB in finishers:
            if mB is mA: continue
            tblB, ppB = pB["table"], mB.pp[pB["move"]]
            # the handover points worth testing: an even spread, plus the exact
            # HP at which the finisher gets a one- or two-turn kill
            marks = {_thresh(tblB, 1.0), _thresh(tblB, 2.0), _thresh(tblB, 3.0)}
            step = max(1, H // HANDOVER_STEPS)
            marks.update(range(step, H, step))
            for h in sorted(x for x in marks if 0 < x < H):
                pre = seg(pA, H, h)
                if pre < 0.5 or pre > ppA: continue
                fin = tblB[h]
                if fin > ppB: continue
                total = sA + pre + 1.0 + fin
                # only prune on turns when the incumbent already keeps everyone
                # standing -- otherwise a slower plan that saves a Pokemon has
                # to be allowed to win
                if bestkey[0] == 0 and total >= bestkey[1] - 1e-9: continue
                hitsA = pre + sA                   # A never lands the KO
                hitsB = 1.0 + max(0.0, fin - (1.0 if fastB else 0.0))
                dmgA, dmgB = thrA["avg"] * hitsA, thrB["avg"] * hitsB
                kills_self = boom(pB)
                survives = dmgA < mA.hp and dmgB < mB.hp and not kills_self
                key = (0 if survives else 1, round(total, 4), dmgA + dmgB)
                if key < bestkey:
                    bestkey = key
                    best = [(mA, pA, thrA, pre + sA, hitsA, sA > 0, False,
                             pre / mA.obey),
                            (mB, pB, thrB, fin + 1.0, hitsB, True, kills_self,
                             fin / mB.obey)]
    return best

def run_section(team, battles, badges, heal_after_idx, tm_value=None,
                carry=None, stage=None):
    """Walk the section with one party, no items. Returns a full ledger.
    `carry` seeds members with the HP and PP they ended the previous section
    on, for the stage boundaries the game leaves unhealed."""
    for m in team:
        m.reset()
        c = carry.get(m.species) if carry else None
        if c:
            for mv in m.pp:
                if mv in c["pp"]: m.pp[mv] = c["pp"][mv]
            m.hp = m.maxhp * c["hpPct"]
            m.leg_mark()
    log, total_turns, faints, failed = [], 0.0, 0, 0
    wild_turns = 0.0
    wild_led = collections.Counter()   # species -> wild battles it walked point for
    leads = {}          # map -> the Pokemon you are walking around with
    # A "leg" is the stretch between two full heals. Cumulative counters are
    # snapshotted at every heal so the section can be reported leg by leg.
    marks = []
    def mark(bi, why):
        marks.append({
            "battle": bi, "why": why,
            "turns": total_turns, "wildTurns": wild_turns,
            "faints": faints, "failed": failed,
            "members": [{"used": {mv: round(m.used[mv] - m.legBase.get(mv, 0.0), 1)
                                  for mv in m.moves},
                         "minHpPct": m.legMinHpPct,
                         "fainted": m.fainted - m.legFaints} for m in team]})
    trainers_seen = 0
    for bi, enc in enumerate(battles):
        # A Pokemon first caught mid-stage does not exist for the fights before
        # its catch stop (Abra's grass is past Nugget Bridge, so its line sits
        # out the bridge). JOIN_AT holds the trainer ordinal each newly-caught
        # species becomes usable at, in this section's route order.
        have = ([m for m in team if JOIN_AT.get(m.species, 0) <= trainers_seen]
                if JOIN_AT else team)
        if MOVE_JOIN:
            MOVE_LOCKED.clear()
            MOVE_LOCKED.update(mv for mv, j in MOVE_JOIN.items() if j > trainers_seen)
        if enc["kind"] == "wild":
            # You cannot pick your lead against something you have not seen yet.
            # One Pokemon walks the route and meets whatever the grass sends;
            # anything else has to be switched in at the usual price.
            active = pick_lead(have, enc, badges, leads)
            if active is not None: wild_led[active.species] += 1
        else:
            active = None          # party order is free to set before a trainer
            trainers_seen += 1
        entries = []
        for oi, opp in enumerate(enc["_mons"]):
            # Gen 3's default battle style is SHIFT: when a trainer's Pokemon
            # goes down, the game offers a free switch before the next one
            # comes out. So against a trainer, every opponent gets a free
            # choice of who fights it -- only mid-fight handovers pay the
            # switch tax. Wild leads still carry over unpriced choices.
            if enc["kind"] != "wild" and oi > 0:
                active = None
            plan = plan_vs(have, opp, badges, active,
                           G.battle_terrain(enc.get("locationRaw") or enc.get("map") or "",
                                            enc.get("kind"), enc.get("method")))
            if plan is None:
                failed += 1
                entries.append({"opp": opp["name"], "lvl": opp["level"],
                                "by": None, "move": None, "turns": 0.0})
                continue
            for si, (m, p, thr, turns, hits, switched, selfko, ppc) in enumerate(plan):
                m.spend(p["move"], ppc)
                m.hurt(thr["avg"] * hits)
                # Self-Destruct and Explosion knock out the user as well
                if selfko: m.hp = 0.0; m.minHpPct = 0.0; m.legMinHpPct = 0.0
                if m.hp <= 0:
                    faints += 1; m.fainted += 1
                    active = None      # the next send-out after a faint is free
                else:
                    active = m
                total_turns += turns
                if enc["kind"] == "wild": wild_turns += turns
                if (tm_value is not None and p["move"] in SCARCE
                        and scarce_spend(p["move"], stage)):
                    a = alt_turns(m, opp, badges, p["move"])
                    # no fallback at all means the TM is the only answer here
                    gain = (12.0 if a is None else max(0.0, a - p["turns"]))
                    if gain: tm_value[(p["move"], m.species)] += gain
                entries.append({
                    "selfKO": selfko,
                    "_moveConst": p["move"], "_by": m.species, "_oppIdx": (bi, oi),
                    "opp": opp["name"], "lvl": opp["level"], "by": m.name,
                    "bySpecies": m.species, "move": p["name"], "moveType": p["type"],
                    "eff": p["eff"], "turns": round(turns, 2),
                    "switched": bool(switched),
                    "handover": len(plan) > 1 and si > 0,
                    "hpLeft": round(max(0.0, m.hp) / m.maxhp * 100),
                    "dmgMin": p["min"], "dmgMax": p["max"],
                })
        log.append({"enc": enc["name"], "id": enc["id"], "kind": enc["kind"],
                    "location": enc["location"], "steps": entries,
                    "group": enc.get("group")})
        if bi in heal_after_idx:
            why = (heal_after_idx[bi]
                   if isinstance(heal_after_idx, dict) else True)
            mark(bi, why)
            for m in team:
                m.restore(); m.leg_mark()
            log[-1]["healedAfter"] = why
    if battles: mark(len(battles) - 1, None)
    hp_lost = sum(1.0 - (max(0.0, m.hp) / m.maxhp) for m in team) / max(1, len(team))
    return {"turns": total_turns, "wildTurns": wild_turns, "hpLost": hp_lost,
            "wildLed": dict(wild_led),

            "faints": faints, "failed": failed, "log": log, "legMarks": marks,
            "ppLeft": min([min(m.pp.values()) / max(1, max(E.MOVES[x]["pp"] for x in m.moves))
                           for m in team], default=1.0),
            "team": team}


# How many sections of this run each species earns a place in, measured on the
# unconstrained first pass. Used only to break ties: when several Pokemon clear
# a section equally well, name the one you are already carrying rather than a
# stranger you would catch for one battle and drop.
USAGE = {}

def set_usage(counts):
    USAGE.clear(); USAGE.update(counts or {})

def pick_key(res, sp):
    """Ordering for 'which Pokemon should join the party next'."""
    return (res["failed"], res["faints"], round(res["turns"], 2),
            round(res.get("hpLost", 0.0), 3), -USAGE.get(sp, 0),
            E.SPECIES[sp]["name"])

def score(res):
    return (res["failed"], res["faints"], round(res["turns"], 2))

# ------------------------------------------------------------------ solve one section
SCARCE = {}       # move const -> supply record; filled in from tms.py

def scarce_spend(mv, stage):
    """The TM item this move spends at this stage, or None once the Celadon
    shop makes more copies purchasable (a repeatable TM stops being scarce
    the stage its shop opens)."""
    rec = SCARCE.get(mv)
    if not rec: return None
    ss = rec.get("shopStage")
    if ss is not None and stage is not None and stage >= ss: return None
    return rec["item"]

def collapse_wild(log):
    """Dozens of one-Pokemon wild rows read as noise; one row per map reads as
    the walk it actually is."""
    out = []
    for entry in log:
        if entry["kind"] != "wild":
            out.append(entry); continue
        prev = out[-1] if out else None
        if prev and prev["kind"] == "wild" and prev.get("group") == entry.get("group"):
            prev["steps"].extend(entry["steps"])
            prev["count"] = prev.get("count", 1) + 1
            if entry.get("healedAfter") and not prev.get("healedAfter"):
                prev["healedAfter"] = entry["healedAfter"]
        else:
            e = dict(entry); e["count"] = 1
            out.append(e)
    for e in out:
        if e["kind"] == "wild":
            e["enc"] = f"Walking {e.get('group','the area')}"
            e["turns"] = round(sum(s.get("turns", 0) for s in e["steps"]), 1)
            # a per-species tally is far more useful than every single fight
            tally = {}
            for s in e["steps"]:
                k = (s["opp"], s.get("by"))
                tally[k] = tally.get(k, 0) + 1
            e["steps"] = [{"opp": k[0], "by": k[1], "n": v,
                           "lvl": next(x["lvl"] for x in e["steps"] if x["opp"] == k[0]),
                           "move": next((x.get("move") for x in e["steps"]
                                         if x["opp"] == k[0] and x.get("by") == k[1]), None),
                           "turns": 0}
                          for k, v in sorted(tally.items(), key=lambda kv: -kv[1])]
    return out

# ------------------------------------------------------------------ legs
# A section used to be shown as one block even when a Pokemon Center sat in the
# middle of it, which read wrong: 50 PP of Confusion "in Mt. Moon" really meant
# 25 on Route 3, a heal at the Route 4 Center, then 25 more in the cave. So the
# section is cut into LEGS at every full heal, and each leg reports its own
# ledger — PP, lowest HP and faints spent on that stretch alone.
_TAIL_SEG = re.compile(r"^(B?\d+F|Room\d+|Entrance)$")

def _leg_span_names(span):
    """Ordered unique base locations of a leg (floor/room suffixes stripped),
    plus the floor tags seen for each, for naming the leg after its ground."""
    tr = [e for e in span if e["kind"] != "wild"] or span
    seq, tails = [], {}
    for e in tr:
        raw = e.get("locationRaw") or e.get("location") or ""
        parts = raw.split("_")
        tail = []
        while len(parts) > 1 and _TAIL_SEG.match(parts[-1]):
            tail.insert(0, parts.pop())
        base = "_".join(parts)
        if base not in seq: seq.append(base)
        tails.setdefault(base, []).append(" ".join(tail))
    return seq, tails

def _title_legs(legs):
    """Name each leg after the ground it covers: one map's name, or the walk
    from the first map to the last. Two legs of the same dungeon (split at an
    in-dungeon healing spot) are told apart by floor range instead."""
    titles = []
    for leg in legs:
        seq, _ = _leg_span_names(leg["_span"])
        names = [G.pretty_location(b) for b in seq]
        titles.append(names[0] if len(names) == 1
                      else f"{names[0]} → {names[-1]}")
    dup = {t for t in titles if titles.count(t) > 1}
    for i, leg in enumerate(legs):
        if titles[i] in dup:
            seq, tails = _leg_span_names(leg["_span"])
            tl = [t for t in tails.get(seq[0], []) if t]
            if len(seq) == 1 and tl:
                rng = tl[0] if tl[0] == tl[-1] else f"{tl[0]}–{tl[-1]}"
                titles[i] = f"{G.pretty_location(seq[0])} {rng}"
        leg["title"] = titles[i]
        leg.pop("_span", None)

_PRE_EVO = {}
def _pre_evo(species):
    if not _PRE_EVO:
        for frm, evs in E.EVOS.items():
            for ev in evs:
                _PRE_EVO.setdefault(ev["to"], frm)
    return _PRE_EVO.get(species)

def build_party_plans(per_stage, avail):
    """A full six-slot party for every section: the section's own roster first
    (fighters, then pure HM carriers), then the seats filled with the already-
    caught Pokémon the run will need soonest, so nothing that matters later
    sits in the PC. Attached to each section as `partyPlan`."""
    hm_names = {E.MOVES[mv]["name"] for mv in HM.HM_MOVE.values()}
    ids = sorted(per_stage)
    for i, st in enumerate(ids):
        sec = per_stage[st]
        if not sec: continue
        plan, seen, lines = [], set(), set()
        for m in sec["team"]:
            used = sum(mv["used"] for mv in m["moves"])
            hms = [mv["name"] for mv in m["moves"] if mv["name"] in hm_names]
            plan.append({"species": m["species"], "name": m["name"],
                         "level": m["level"], "hms": hms,
                         "role": "hm" if hms and used == 0 else "fight"})
            seen.add(m["species"]); lines.add(C.line_root(m["species"]))
        for st2 in ids[i + 1:]:
            if len(plan) >= 6: break
            sec2 = per_stage[st2]
            if not sec2: continue
            for m2 in sec2["team"]:
                if len(plan) >= 6: break
                sp = m2["species"]
                # the future form may not exist yet -- you carry the form you
                # actually own (Spearow now, because it becomes Fearow)
                form = sp
                while form and avail.get(form, {}).get("stage", 99) > st:
                    form = _pre_evo(form)
                # never fill with a Pokémon already on the plan by evolution
                # line -- no Kadabra beside its own Alakazam
                if not form or form in seen or sp in seen: continue
                if C.line_root(form) in lines: continue
                seen.add(sp); seen.add(form); lines.add(C.line_root(form))
                plan.append({"species": form, "name": E.SPECIES[form]["name"],
                             "level": m2["level"], "hms": [],
                             "role": "next", "nextStage": st2,
                             "becomes": m2["name"] if form != sp else None})
        sec["partyPlan"] = plan

def move_origin(species, level, mv):
    """Where this specimen got the move: its own level-up learnset first
    (that is how move_pool found it too), else somewhere down the evolution
    line (a carried move like Gyarados\'s Tackle may not appear in the
    evolved form\'s learnset at all), else the TM/HM that teaches it, else
    the Two Island tutor."""
    lvls = [lv for lv, m2 in E.LEVELUP.get(species, []) if m2 == mv and lv <= level]
    if lvls and max(lvls) > 1:
        return f"Lv {max(lvls)}"
    pre = _pre_evo(species)
    while pre:
        plv = [l for l, m2 in E.LEVELUP.get(pre, [])
               if m2 == mv and 1 < l <= level]
        if plv:
            return f"Lv {max(plv)} ({E.SPECIES[pre]['name']})"
        pre = _pre_evo(pre)
    if lvls:
        return "start"
    item = G.MOVE_TO_TM.get(mv)
    if item:
        return item.replace("ITEM_", "")
    if mv.replace("MOVE_", "") in E.TUTOR.get(species, []):
        return "Tutor"
    # a pre-evolution starting move carried through (Splash, Tackle...)
    return "start"

def _leg_member(m, md, avail, stage):
    """One party member's ledger for one leg: PP and HP spent on this stretch
    alone, out of a single (unrefilled) PP bar — a leg never spans a heal."""
    rec = avail[m.species]
    return {
        "species": m.species, "name": m.name, "level": m.level,
        "types": [t for i, t in enumerate(m.types) if i == 0 or t != m.types[0]],
        "hp": m.maxhp, "hpLeft": round(md["minHpPct"]), "fainted": md["fainted"],
        "obtainedAt": rec["stage"], "obtainedVia": rec["source"],
        "newHere": rec["stage"] == stage,
        "moves": [{"name": E.MOVES[mv]["name"],
                   "tm": (None if move_origin(m.species, m.level, mv)
                          .split()[0] in ("start", "Lv")
                          else scarce_spend(mv, stage)),
                   "src": move_origin(m.species, m.level, mv),
                   "type": E.move_type_for(m.mon, mv),
                   "power": E.nominal_power(m.mon, mv),
                   "pp": E.MOVES[mv]["pp"], "basePP": E.MOVES[mv]["pp"],
                   "refills": 0,
                   "used": md["used"].get(mv, 0.0),
                   "left": round(max(0.0, float(E.MOVES[mv]["pp"])
                                      - md["used"].get(mv, 0.0)), 1)}
                  for mv in m.moves],
    }

def build_legs(battles, final, avail, stage):
    """Cut the final run into legs at its full heals. Returns the leg records
    (each holding its own roster ledger and row count) and the section log,
    with the wild rows collapsed per leg so a walk never merges across a heal."""
    legs, sec_log, start = [], [], 0
    prev = {"turns": 0.0, "wildTurns": 0.0, "faints": 0, "failed": 0}
    for mk in final.get("legMarks") or []:
        end = mk["battle"]
        span = battles[start:end + 1]
        rows = collapse_wild(final["log"][start:end + 1])
        trainers = [e for e in span if e["kind"] != "wild"]
        legs.append({
            "title": None, "_span": span,
            "endsAt": mk["why"] if isinstance(mk["why"], str) else None,
            "rows": len(rows),
            "battles": len(trainers),
            "opposingMons": sum(len(e["_mons"]) for e in trainers),
            "wildBattles": len(span) - len(trainers),
            "turns": round(mk["turns"] - prev["turns"], 1),
            "wildTurns": round(mk["wildTurns"] - prev["wildTurns"], 1),
            "faints": mk["faints"] - prev["faints"],
            "unanswered": mk["failed"] - prev["failed"],
            "team": [_leg_member(m, md, avail, stage)
                     for m, md in zip(final["team"], mk["members"])],
        })
        sec_log.extend(rows)
        prev, start = mk, end + 1
    _title_legs(legs)
    return legs, sec_log

def solve_section(stage, encs, starter, avail, tm_value=None, carry=None):
    st = P.STAGE_BY_ID[stage]
    level, badges = st["level"], O.badges_for(stage)
    encs = encs or []
    set_join_at(stage, avail)   # mid-stage catches sit out the fights before their grass
    # In the order you actually meet them: by where each trainer stands on the
    # map, and by the order you walk the maps -- not by party level, which is
    # only a proxy for "further in" and is not even monotonic along a route.
    battles, path_heals = R.section_order(stage, [e for e in encs if variant_ok(e, starter)],
                                          FLOOR_ORDER)
    # the route, once solved, decides the real order (and the heals along it)
    if route_positions():
        battles, path_heals = route_order_battles(battles)
    for e in battles:
        if "_mons" not in e:
            e["_mons"] = [E.realize_trainer_mon(e["trainerConst"], i)
                          for i in range(len(e["party"]))]
    trainer_battles = list(battles)
    # A section with no trainers is still a section you have to WALK: the Safari
    # Zone and Cerulean Cave have no trainer in them and still want Cut, Strength,
    # Surf and Rock Smash, so they go through the same solve on their wild load
    # rather than returning an empty recommendation.
    battles = interleave_wild(stage, battles, wild_battles_for(stage))
    if not battles:
        return {"stage": stage, "starter": starter, "level": level,
                "badges": badges["count"], "battles": 0, "opposingMons": 0,
                "turns": 0.0, "faints": 0, "unanswered": 0, "healPoints": [],
                "team": [], "bench": [], "log": [], "legs": [], "buildOrder": [],
                "noBattles": True,
                "hms": [], "hmKit": [{"name": E.MOVES[HM.HM_MOVE[h]]["name"],
                                      "hm": HM.HM_ITEM[h].replace("ITEM_", ""),
                                      "from": st0}
                                     for h, st0 in sorted(HM.kit(stage).items(),
                                                          key=lambda kv: P.HM_STAGE[kv[0]])]}
    opponents = [m for e in battles for m in e["_mons"]]
    # A healing spot restores you once per pass through its floor, not once per
    # trainer standing on it -- so heal after the LAST battle there. (The Purified
    # Zone can in fact be re-walked at will, which makes this the conservative
    # reading rather than an optimistic one.)
    # heals are anchored where the ROUTE's walked tiles actually pass a
    # Center door, a heal zone, or a flight home -- heal_points' map-level
    # guess produced phantom heals (a "Route 4 Center" cut on a walk that
    # never went near its door) and zero-battle legs
    heal_idx = {}
    if route_positions():
        for i, e in enumerate(battles):
            if e["id"] in path_heals and i < len(battles) - 1:
                heal_idx.setdefault(i, path_heals[e["id"]])
    else:
        heal_idx = heal_points(battles)
        for i, e in enumerate(battles):
            if e["id"] in path_heals and i < len(battles) - 1:
                heal_idx.setdefault(i, path_heals[e["id"]])

    # candidate pool: everything obtainable by now, minus the other two starters'
    # entire evolution lines -- excluding only the base forms let Ivysaur and
    # Charmeleon slip into parties they could never legally join
    cands = [sp for sp in O.candidates(stage)
             if C.starter_of(sp) in (None, starter) and C.allowed(sp)
             and not C.outgrown(sp, stage, avail)]

    # ---- screen with the scalar model
    screened = []
    for sp in cands:
        pm = O.player_mon(sp, level)
        pool = O.move_pool(sp, level, stage)
        if not pool: continue
        cover = 0.0
        for opp in opponents:
            d, _ = O.best_scalar(pm, opp, pool, badges)
            cover += min(1.0, d / max(1, opp["stats"]["hp"]))
        screened.append((cover, sp))
    screened.sort(reverse=True)
    finalists = [sp for _, sp in screened[:SCREEN]]
    # The starter always gets a look, but as the form you would actually be
    # holding by now. Force-adding the BASE form put a Charmander on the
    # Champion's doorstep in the post-game, where it was picked purely as a
    # warm body once everything else had fainted.
    line = [sp for _, sp in screened if C.starter_of(sp) == starter]
    if line and not any(sp in finalists for sp in line):
        finalists.append(line[0])

    members = {}
    for sp in finalists:
        pm = O.player_mon(sp, level)
        pool = O.move_pool(sp, level, stage)
        mvs = choose_moveset(pm, pool, opponents, badges)
        if not mvs: continue
        members[sp] = (pm, mvs, pool, obey_factor(sp, level, badges, avail))

    # ---- greedy team build
    team, chosen, history = [], [], []
    held_roots, held_groups = set(), set()
    prev = None
    while len(team) < MAX_TEAM:
        best, bestsp, bestres = None, None, None
        for sp, (pm, mvs, pool, ob) in members.items():
            if sp in chosen: continue
            if C.conflicts(sp, held_roots, held_groups): continue
            trial = team + [Member(pm, mvs, pool, ob)]
            res = run_section(trial, battles, badges, heal_idx, carry=carry)
            k = pick_key(res, sp)
            if best is None or k < best:
                best, bestsp, bestres = k, sp, res
        if bestsp is None: break
        if prev is not None:
            # stop once the party already clears the section and another body
            # no longer meaningfully speeds it up
            cleared = prev["failed"] == 0 and prev["faints"] == 0
            gain = (prev["turns"] - bestres["turns"]) / max(1e-9, prev["turns"])
            still_broken = bestres["failed"] < prev["failed"] or bestres["faints"] < prev["faints"]
            if cleared and gain < MIN_GAIN and not still_broken:
                break
        pm, mvs, _pool, _ob = members[bestsp]
        team.append(Member(pm, mvs, _pool, _ob)); chosen.append(bestsp)
        held_roots.add(C.line_root(bestsp))
        g = C.exclusive_group(bestsp)
        if g: held_groups.add(g)
        prev = bestres
        history.append({"added": E.SPECIES[bestsp]["name"], "turns": round(bestres["turns"], 2),
                        "faints": bestres["faints"], "failed": bestres["failed"]})

    # ---- prune: the greedy seed can end up with no job once the specialists
    # join (a solo-strong opener whose every fight gets taken over). Drop any
    # member whose removal costs nothing -- the party the page shows should
    # be exactly the bodies the plan actually uses.
    pruned = True
    while pruned and len(team) > 1:
        pruned = False
        base = run_section(team, battles, badges, heal_idx, carry=carry, stage=stage)
        bkey = (base["failed"], base["faints"], round(base["turns"], 2))
        for m in list(team):
            rest = [x for x in team if x is not m]
            r = run_section(rest, battles, badges, heal_idx, carry=carry, stage=stage)
            if (r["failed"], r["faints"], round(r["turns"], 2)) <= bkey:
                team.remove(m)
                if m.species in chosen: chosen.remove(m.species)
                pruned = True
                break

    # ---- HM coverage: the party has to be able to CROSS the section, not just
    # win the battles in it. Done before the final run so that a move given up
    # for a field move is paid for in the turn count.
    hm_plan, hm_extra = assign_hms(team, stage, level, badges, avail, opponents,
                                   starter, held_roots, held_groups, chosen)
    for m in hm_extra:
        team.append(m); chosen.append(m.species)
    final = run_section(team, battles, badges, heal_idx, tm_value, carry=carry,
                        stage=stage)

    # ---- bench: strong candidates that didn't make the cut
    bench = []
    for sp, (pm, mvs, pool, ob) in members.items():
        if sp in chosen: continue
        res = run_section([Member(pm, mvs, pool, ob)], battles, badges,
                          heal_idx, carry=carry)
        if res["failed"]: continue      # can't clear it, so not an alternative
        bench.append(((res["faints"], round(res["turns"], 2),
                       round(res.get("hpLost", 0.0), 3), -USAGE.get(sp, 0),
                       E.SPECIES[sp]["name"]), sp, res))
    bench.sort(key=lambda t: t[0])
    kept, seen_r, seen_g = [], set(held_roots), set(held_groups)
    for turns, sp, r in bench:
        if C.conflicts(sp, seen_r, seen_g): continue
        seen_r.add(C.line_root(sp))
        g = C.exclusive_group(sp)
        if g: seen_g.add(g)
        kept.append((turns, sp, r))
    bench = kept
    bench = [{"species": sp, "name": E.SPECIES[sp]["name"],
              "types": [t for i, t in enumerate(E.SPECIES[sp]["types"]) if i == 0 or t != E.SPECIES[sp]["types"][0]],
              "soloTurns": round(r["turns"], 1), "soloFailed": r["failed"],
              "via": avail[sp]["source"]}
             for _, sp, r in bench[:6]]

    roster = []
    for m in final["team"]:
        rec = avail[m.species]
        used = {mv: round(m.used[mv], 1) for mv in m.moves}
        roster.append({
            "species": m.species, "name": m.name, "level": m.level,
            "types": [t for i, t in enumerate(m.types) if i == 0 or t != m.types[0]],
            "hp": m.maxhp, "hpLeft": round(m.minHpPct), "fainted": m.fainted,
            "obtainedAt": rec["stage"], "obtainedVia": rec["source"],
            "newHere": rec["stage"] == stage,
            "moves": [{"name": E.MOVES[mv]["name"],
                       "tm": (None if move_origin(m.species, m.level, mv)
                              .split()[0] in ("start", "Lv")
                              else scarce_spend(mv, stage)),
                       "src": move_origin(m.species, m.level, mv),
                       "type": E.move_type_for(m.mon, mv),
                       "power": E.nominal_power(m.mon, mv),
                       "pp": E.MOVES[mv]["pp"] * (1 + m.refills),
                       "basePP": E.MOVES[mv]["pp"], "refills": m.refills,
                       "used": used[mv],
                       "left": round(max(0.0, float(E.MOVES[mv]["pp"]) * (1 + m.refills)
                                          - used[mv]), 1)}
                      for mv in m.moves],
        })
    wild_only = [e for e in battles if e["kind"] == "wild"]
    legs, sec_log = build_legs(battles, final, avail, stage)
    if legs and stage in cold_stage_starts():
        legs[0]["cold"] = True
    return {
        "carryOut": {m.species: {"pp": dict(m.pp),
                                 "hpPct": max(0.0, m.hp) / m.maxhp}
                     for m in final["team"]},
        # the join constraints actually applied, for the verify guard
        "joins": {E.SPECIES[sp]["name"]: j for sp, j in JOIN_AT.items()},
        "moveJoins": {E.MOVES[mv]["name"]: j for mv, j in MOVE_JOIN.items()},
        "stage": stage, "starter": starter, "level": level,
        "badges": badges["count"],
        "battles": len(trainer_battles),
        "noBattles": not trainer_battles,
        "wildBattles": len(wild_only),
        "wildLead": (E.SPECIES[max(final["wildLed"], key=final["wildLed"].get)]["name"]
                     if final.get("wildLed") else None),
        "wildPlan": wild_plan(final["team"], stage, badges),
        "dexEvos": dex_evolutions(avail).get(stage, []),
        "wildTurns": round(final.get("wildTurns", 0.0), 1),
        "opposingMons": sum(len(e["_mons"]) for e in trainer_battles),
        "turns": round(final["turns"], 1),
        "faints": final["faints"], "unanswered": final["failed"],
        "healPoints": sorted(heal_idx),
        "team": roster, "bench": bench, "log": sec_log, "legs": legs,
        "buildOrder": history,
        "hms": [{**p, "name": E.MOVES[p["move"]]["name"],
                 "hm": HM.HM_ITEM[HM._hm_of(p["move"])].replace("ITEM_", ""),
                 "badge": HM.HM_BADGE[HM._hm_of(p["move"])],
                 "where": HM.here(stage).get(HM._hm_of(p["move"]), []),
                 "stuck": p["move"] in HM_HELD.get(p.get("species") or "", ()) and
                          stage < MOVE_DELETER_STAGE,
                 # Fly answers no obstacle, so it has no spot count to report
                 "why": ("town to town" if HM._hm_of(p["move"]) in HM.ALWAYS else
                         "%d spot%s" % (len(HM.here(stage).get(HM._hm_of(p["move"]), [])),
                                        "" if len(HM.here(stage).get(HM._hm_of(p["move"]), [])) == 1 else "s"))}
                for p in hm_plan],
        "hmKit": [{"name": E.MOVES[HM.HM_MOVE[h]]["name"],
                   "hm": HM.HM_ITEM[h].replace("ITEM_", ""),
                   "from": st0} for h, st0 in
                  sorted(HM.kit(stage).items(), key=lambda kv: P.HM_STAGE[kv[0]])],
    }

# ------------------------------------------------------------------ keep at level
def keep_lists(per_stage, avail):
    """For each section, the Pokemon a LATER section is going to want.

    The whole model assumes no grinding: a Pokemon is at the section's level the
    moment it is used. That assumption only holds if the thing was in your
    rotation the whole way, so this is its practical form -- the running list of
    what you must not leave in the box.

    Keyed on the EVOLUTION LINE, not the species. A Charmander you are holding
    now and the Charmeleon a later section wants are the same Pokemon at two
    points in time; listing them as separate entries, or missing the entry
    because the names differ, is the same mistake the ranked lists used to make.
    Each row therefore names the form you are holding now and, when it differs,
    the form it needs to be by then.

    Attaches `keep` to every section: lines needed later that are NOT on this
    section's team, soonest first, with the gap that has to be bridged.
    """
    stages = sorted(s for s in per_stage if per_stage[s])
    use = defaultdict(list)              # line root -> [(stage, species, roles)]
    for st in stages:
        sec = per_stage[st]
        hm_by = defaultdict(list)
        for p in sec.get("hms", []):
            if p.get("species"): hm_by[p["species"]].append(p["name"])
        for t in sec["team"]:
            roles = []
            if t["species"] in hm_by:
                roles.append("carries " + "/".join(sorted(hm_by[t["species"]])))
            use[C.line_root(t["species"])].append((st, t["species"], roles))

    for st in stages:
        sec = per_stage[st]
        on_team = {C.line_root(t["species"]) for t in sec["team"]}
        keep = []
        for root, uses in use.items():
            if root in on_team: continue     # already levelling it by using it
            future = sorted(u for u in uses if u[0] > st)
            if not future: continue
            nxt, want_sp, roles = future[0]
            # The form you would be holding right now: the one it needs to be,
            # if you could already have it, and otherwise the most evolved form
            # of the line that is reachable and allowed. Nothing to keep
            # levelled if the line is not obtainable yet at all.
            rw = avail.get(want_sp)
            if (rw and rw["stage"] <= st and C.allowed(want_sp)
                    and not C.outgrown(want_sp, st, avail)):
                now = want_sp
            else:
                now = C.form_at(root, st, avail)
            if not now: continue
            keep.append({
                "species": now, "name": E.SPECIES[now]["name"],
                "becomes": (E.SPECIES[want_sp]["name"]
                            if want_sp != now else None),
                "next": nxt, "nextName": P.STAGE_BY_ID[nxt]["name"],
                "nextLevel": P.STAGE_BY_ID[nxt]["level"],
                "gap": nxt - st, "times": len(future),
                "why": "; ".join(roles) or "battles",
            })
        keep.sort(key=lambda k: (k["next"], -k["times"], k["name"]))
        sec["keep"] = keep
    return per_stage

# ------------------------------------------------------------------ TM plan
MAX_SCARCE_TMS_PER_MON = 3     # four move slots; leave room for level-up moves

def assign_tms(tm_value, scarce):
    """Spend each single-use TM on the Pokemon that gains most from it over the
    WHOLE run, not the first one it happens to help. A TM handed to an early
    Pokemon that is benched by Saffron is a TM wasted."""
    by_move = defaultdict(list)
    for (mv, sp), v in tm_value.items():
        by_move[mv].append((v, sp))

    owners, load, plan = {}, collections.Counter(), []
    # spend the most contested TMs first: the ones with the biggest single claim
    order = sorted(by_move, key=lambda mv: -max(v for v, _ in by_move[mv]))
    for mv in order:
        copies = scarce[mv]["copies"]
        cands = [(v, sp) for v, sp in sorted(by_move[mv], reverse=True)
                 if C.allowed(sp)]
        picked = []
        for v, sp in cands:
            if v < 0.25: break                      # not worth a permanent item
            if load[sp] >= MAX_SCARCE_TMS_PER_MON: continue
            picked.append((sp, round(v, 1)))
            load[sp] += 1
            if len(picked) >= copies: break
        owners[mv] = [sp for sp, _ in picked]
        if picked:
            runners = [(E.SPECIES[sp]["name"], round(v, 1))
                       for v, sp in cands if sp not in owners[mv]][:3]
            plan.append({
                "move": mv, "name": scarce[mv]["name"], "item": scarce[mv]["item"],
                "copies": copies, "earliest": scarce[mv]["earliest"],
                "coinCost": scarce[mv]["coinCost"],
                "shopStage": scarce[mv].get("shopStage"),
                "teach": [{"species": sp, "name": E.SPECIES[sp]["name"], "value": v}
                          for sp, v in picked],
                "runnersUp": [{"name": n, "value": v} for n, v in runners],
            })
    # every scarce move needs an entry, so an unspent TM stays unspent
    for mv in scarce:
        owners.setdefault(mv, [])
    plan.sort(key=lambda p: -max((t["value"] for t in p["teach"]), default=0))
    return owners, plan

# ------------------------------------------------------------------ choices
CHOICE_GROUPS = [
    {"id": "starter", "label": "Starter Pokémon", "stage": 0,
     "where": "Prof. Oak's Lab, Pallet Town",
     "options": ["SPECIES_BULBASAUR", "SPECIES_CHARMANDER", "SPECIES_SQUIRTLE"],
     "note": "Locks your rival's starter to the one that beats yours."},
    {"id": "fossil", "label": "Fossil", "stage": 26,
     "where": "Mt. Moon — revived at the Cinnabar Lab",
     "options": ["SPECIES_OMANYTE", "SPECIES_KABUTO"],
     "note": "You may only take one. Helix → Omanyte, Dome → Kabuto."},
    {"id": "dojo", "label": "Fighting Dojo prize", "stage": 23,
     "where": "Saffron City Dojo",
     "options": ["SPECIES_HITMONLEE", "SPECIES_HITMONCHAN"],
     "note": "Beat the Dojo and pick one of the two."},
    {"id": "eevee", "label": "Eevee evolution", "stage": 15,
     "where": "Celadon Condominiums roof, evolved with a Celadon stone",
     "options": ["SPECIES_VAPOREON", "SPECIES_JOLTEON", "SPECIES_FLAREON"],
     "note": "One Eevee, one stone, one permanent choice."},
    {"id": "gamecorner", "label": "Game Corner prize", "stage": 15,
     "where": "Celadon Game Corner",
     "options": ["SPECIES_ABRA", "SPECIES_CLEFAIRY", "SPECIES_DRATINI",
                 "SPECIES_PORYGON", "SPECIES_PINSIR"],
     "note": "Coins are finite in practice, so treat this as one pick."},
]

def evo_line(sp, avail):
    """A species plus every form it can still reach without trading."""
    out, frontier = [sp], [sp]
    while frontier:
        cur = frontier.pop()
        for ev in E.EVOS.get(cur, []):
            tgt = ev["to"]
            if tgt in out: continue
            rec = avail.get(tgt)
            if not rec or rec.get("requiresTrade"): continue
            out.append(tgt); frontier.append(tgt)
    return out

def best_line_form(line, stage, avail):
    """The strongest form of a line you could actually be holding by `stage`."""
    ok = [sp for sp in line if sp in avail and avail[sp]["stage"] <= stage]
    if not ok: return None
    return max(ok, key=lambda sp: sum(E.SPECIES[sp][k] for k in
               ("baseHP","baseAttack","baseDefense","baseSpAttack","baseSpDefense","baseSpeed")))

def solo_run(sp, stage, battles, badges, heal_idx, opponents, avail=None):
    """How one Pokemon alone handles a whole section."""
    level = P.STAGE_BY_ID[stage]["level"]
    pm = O.player_mon(sp, level)
    pool = O.move_pool(sp, level, stage)
    if not pool: return None
    mvs = choose_moveset(pm, pool, opponents, badges)
    if not mvs: return None
    ob = obey_factor(sp, level, badges, avail) if avail else 1.0
    return run_section([Member(pm, mvs, pool, ob)], battles, badges, heal_idx)

def _section_ctx(sections, ref, starter):
    ctx = {}
    for stage, encs in sections.items():
        battles, path_heals = R.section_order(
            stage, [e for e in encs if variant_ok(e, starter)], FLOOR_ORDER)
        if not battles: continue
        for e in battles:
            if "_mons" not in e:
                e["_mons"] = [E.realize_trainer_mon(e["trainerConst"], i)
                              for i in range(len(e["party"]))]
        heal = heal_points(battles)
        for i, e in enumerate(battles):
            if e["id"] in path_heals and i < len(battles) - 1:
                heal.setdefault(i, path_heals[e["id"]])
        ctx[stage] = {
            "battles": battles,
            "badges": O.badges_for(stage),
            "heal": heal,
            "opponents": [m for e in battles for m in e["_mons"]],
            "teamTurns": (ref.get(stage) or {}).get("turns") or 0,
        }
    return ctx

def _solo_span(line, ctx, ref, avail, from_stage):
    """How much of the game this line can carry alone, from when you get it."""
    cleared, ratios, scored = 0, [], 0
    for stage in sorted(ctx):
        if stage < from_stage: continue
        sec = ref.get(stage)
        if not sec or not sec["battles"]: continue
        scored += 1
        form = best_line_form(line, stage, avail)
        if not form: continue
        c = ctx[stage]
        r = solo_run(form, stage, c["battles"], c["badges"], c["heal"], c["opponents"], avail)
        if r and r["failed"] == 0:
            cleared += 1
            if c["teamTurns"]: ratios.append(r["turns"] / c["teamTurns"])
    return cleared, scored, (round(sum(ratios) / len(ratios), 2) if ratios else None)

def evaluate_choices(result, avail, sections, ref_starter):
    """Rank each branching decision by what the option actually does for you:
    how often the optimizer puts it on a recommended party, and how much of the
    remaining game its line can carry single-handedly."""
    out = []
    ref = result[ref_starter]

    ctx = _section_ctx(sections, ref, ref_starter)

    for grp in CHOICE_GROUPS:
        rows = []
        for sp in grp["options"]:
            if sp not in avail: continue
            line = evo_line(sp, avail)
            line_names = [E.SPECIES[x]["name"] for x in line]
            from_stage = avail[sp]["stage"]

            if grp["id"] == "starter":
                starter_name = E.SPECIES[sp]["name"]
                runs = result[starter_name]
                total = sum((runs[st] or {}).get("turns", 0) or 0 for st in runs)
                picks, gyms = 0, []
                for st, sec in runs.items():
                    if not sec: continue
                    if any(t["species"] in line for t in sec["team"]): picks += 1
                    stg = P.STAGE_BY_ID[st]
                    if stg.get("gym") and any(t["species"] in line for t in sec["team"]):
                        gyms.append(stg["gym"])
                sctx = _section_ctx(sections, runs, starter_name)
                cleared, scored, ratio = _solo_span(line, sctx, runs, avail, 0)
                rows.append({
                    "species": sp, "name": starter_name,
                    "types": [t for i, t in enumerate(E.SPECIES[sp]["types"])
                              if i == 0 or t != E.SPECIES[sp]["types"][0]],
                    "line": line_names, "fromStage": from_stage,
                    "teamPicks": picks, "sectionsScored": scored,
                    "soloClears": cleared, "soloRatio": ratio,
                    "totalTurns": round(total, 1),
                    "gymsCarried": gyms,
                    "obtainedVia": avail[sp]["source"],
                    "sort": (-picks, -cleared, total),
                })
                continue

            picks, cleared, ratios, scored = 0, 0, [], 0
            for stage in sorted(ctx):
                if stage < from_stage: continue
                sec = ref.get(stage)
                if not sec or not sec["battles"]: continue
                scored += 1
                if any(t["species"] in line for t in sec["team"]): picks += 1
                form = best_line_form(line, stage, avail)
                if not form: continue
                c = ctx[stage]
                r = solo_run(form, stage, c["battles"], c["badges"], c["heal"], c["opponents"], avail)
                if r and r["failed"] == 0:
                    cleared += 1
                    if c["teamTurns"]:
                        ratios.append(r["turns"] / c["teamTurns"])
            rows.append({
                "species": sp, "name": E.SPECIES[sp]["name"],
                "types": [t for i, t in enumerate(E.SPECIES[sp]["types"])
                          if i == 0 or t != E.SPECIES[sp]["types"][0]],
                "line": line_names, "fromStage": from_stage,
                "teamPicks": picks, "sectionsScored": scored,
                "soloClears": cleared,
                "soloRatio": round(sum(ratios) / len(ratios), 2) if ratios else None,
                "obtainedVia": avail[sp]["source"],
                "sort": (-picks, -cleared, sum(ratios) / len(ratios) if ratios else 99),
            })
        rows.sort(key=lambda r: r["sort"])
        for r in rows: r.pop("sort", None)
        if rows:
            out.append({**{k: v for k, v in grp.items() if k != "options"},
                        "rows": rows, "best": rows[0]["name"]})
    return out

# ------------------------------------------------------------------ run
if __name__ == "__main__":
    import time
    graph = json.load(open(f"{OUT}/encounters.json"))
    sections = build_sections(graph)
    avail = P.full_availability()
    print("estimating the walk...", flush=True)
    wl = route_wild_load()
    with open(f"{OUT}/wildLoad.json", "w") as f:
        json.dump({str(k): v for k, v in wl.items()}, f)
    WILD_LOAD.update({str(k): v for k, v in wl.items()})
    print(f"wild battles to clear every area: "
          f"{sum(m['battles'] for v in wl.values() for m in v):.0f}", flush=True)

    import tms as TM
    supply = TM.extract()
    with open(f"{OUT}/tmSupply.json", "w") as f:
        json.dump(supply, f)
    SCARCE.update(TM.scarce_moves(supply))
    set_repeatable(supply)
    O.set_tm_plan(SCARCE, None)    # pass 1: every TM available to everyone
    print(f"single-use TMs in play: {len(SCARCE)}", flush=True)

    if os.path.exists(f"{OUT}/commitments.json"):
        os.remove(f"{OUT}/commitments.json")
    t0 = time.time()

    all_sections, all_choices, all_plans, all_commits = {}, {}, {}, {}
    # Solved twice over: once for a run that never trades, once for a run that
    # will. Trading is not a small tweak -- it adds Alakazam, Machamp, Golem and
    # Gengar, four of the strongest things in the game -- so the commitments,
    # the TM plan and every party get re-derived from scratch for each.
    for mode, trading in (("no", False), ("yes", True)):
        O.set_allow_trade(trading)
        print(f"\n=== solving with trades {'ENABLED' if trading else 'off'}", flush=True)
        C.set_commitments({})      # pass 1 compares the options on even footing
        O.set_tm_plan(SCARCE, None)
        result, tm_value = {}, {}
        for starter in STARTERS:
            result[starter] = {}
            tm_value[starter] = defaultdict(float)
            reset_hm_held()
            carry = None
            for stage in sorted(st["id"] for st in P.STAGES):
                result[starter][stage] = solve_section(
                    stage, sections.get(stage, []), starter, avail,
                    tm_value[starter],
                    carry=carry if stage in cold_stage_starts() else None)
                carry = (result[starter][stage] or {}).get("carryOut") or carry
            print(f"  {starter}: pass 1 done ({time.time()-t0:.0f}s)", flush=True)

    # the reference starter for the non-starter choices is whichever clears the
    # game in the fewest turns
        totals = {k: sum((v[st] or {}).get("turns", 0) or 0 for st in v)
                  for k, v in result.items()}
        ref_starter = min(totals, key=totals.get)
        print("  total section turns by starter:",
              {k: round(v, 1) for k, v in sorted(totals.items(), key=lambda kv: kv[1])})
        choices = evaluate_choices(result, avail, sections, ref_starter)

    # ---- pass 2: lock the one-time choices and re-solve every section, so a run
    # that spends its single Eevee on Vaporeon is still holding Vaporeon in the
    # next town rather than a Jolteon it could never have.
        picks = {}
        for grp in choices:
            if grp["id"] not in C.COMMIT_GROUPS: continue
            picks[grp["id"]] = grp["rows"][0]["species"]
        C.set_commitments(picks)
        print("  commitments:",
              {g: E.SPECIES[sp]["name"] for g, sp in picks.items()}, flush=True)

        tm_plans, pass1, result = {}, result, {}
        for starter in STARTERS:
            counts = collections.Counter()
            reset_hm_held()
            for sec in (pass1[starter] or {}).values():
                if not sec: continue
                for t in sec["team"]:
                    counts[t["species"]] += 1
            set_usage(counts)
            owners, plan = assign_tms(tm_value[starter], SCARCE)
            tm_plans[starter] = plan
            O.set_tm_plan(SCARCE, owners)
            result[starter] = {}
            carry = None
            for stage in sorted(st["id"] for st in P.STAGES):
                result[starter][stage] = solve_section(
                    stage, sections.get(stage, []), starter, avail,
                    carry=carry if stage in cold_stage_starts() else None)
                carry = (result[starter][stage] or {}).get("carryOut") or carry
            build_party_plans(result[starter], avail)
            print(f"  {starter}: re-solved — {sum(len(p['teach']) for p in plan)} "
                  f"single-use TMs spent ({time.time()-t0:.0f}s)", flush=True)
        O.set_tm_plan(SCARCE, None)
        for per_stage in result.values():
            keep_lists(per_stage, avail)
            for sec in per_stage.values():
                if not sec: continue
                for log in sec["log"]:
                    for step in log["steps"]:
                        for k in ("_moveConst", "_by", "_oppIdx"): step.pop(k, None)
        all_sections[mode] = result
        all_choices[mode] = choices
        all_plans[mode] = tm_plans
        all_commits[mode] = {g: E.SPECIES[sp]["name"] for g, sp in picks.items()}
        if mode == "no":
            with open(f"{OUT}/commitments.json", "w") as f:
                json.dump({"picks": picks, "labels": all_commits[mode]}, f)
    O.set_allow_trade(False)
    C.load_commitments(f"{OUT}/commitments.json")
    with open(f"{OUT}/sections.json", "w") as f:
        json.dump({"sections": all_sections, "choices": all_choices,
                   "tmPlans": all_plans, "tradeCommitments": all_commits,
                   "wildLoad": {str(k): v for k, v in wl.items()},
                   "tmSupply": {k: v for k, v in supply.items() if v["obtainable"]},
                   "commitments": all_commits["no"]},
                  f, separators=(",", ":"))
    print(f"sections.json: {os.path.getsize(f'{OUT}/sections.json')/1e6:.2f} MB "
          f"in {time.time()-t0:.0f}s")

    demo = all_sections["no"]["Bulbasaur"]
    for stage in [4, 8, 18, 32]:
        s = demo.get(stage)
        if not s: continue
        print(f"\n--- Section {stage}: {P.STAGE_BY_ID[stage]['name']} "
              f"(Lv {s['level']}, {s['battles']} battles, {s['opposingMons']} Pokémon) ---")
        for t in s["team"]:
            mv = ", ".join(f"{m['name']} {m['used']:.0f}/{m['pp']}" for m in t["moves"])
            print(f"  {t['name']:12} L{t['level']:<3} HP left {t['hpLeft']:3}%  | {mv}")
        print(f"  total {s['turns']} turns, {s['faints']} faints, {s['unanswered']} unanswered")
