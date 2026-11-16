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
import json, os, sys, math, re, collections
from collections import defaultdict
import engine as E
import progression as P
import build_graph as G
import optimize as O
import constraints as C
import walking as W
import hms as HM
import trainer_items as TI
import measure as MEAS
import tactics as TAC
import route_order as R
import training_cost as TC

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_MAX_STAGE = max(s["id"] for s in P.STAGES)

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

# Teleport returns you to the last Pokémon Center, so the route uses it to skip
# the walk back (tour.py). It is a field move like an HM -- carried by whoever
# can hold it -- but a level-up move, not an HM item, so it lives outside the
# HM tables. In Kanto that is the Abra line (Route 24); the availability gate on
# the carrier pool keeps other Teleport learners (Natu, Ralts…) to where a run
# can actually obtain them.
TELE_MOVE = "MOVE_TELEPORT"
TELE_LEARNERS = {sp for sp in E.LEVELUP if any(mv == TELE_MOVE for _, mv in E.LEVELUP[sp])}

def field_can_learn(species, move):
    """Can this species hold this field move? HMs from the TM/HM table; Teleport
    from its level-up learnset (it isn't an HM)."""
    if move == TELE_MOVE:
        return species in TELE_LEARNERS
    return HM.can_learn(species, move)

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

def _catch_only_map(name):
    """The Safari Zone is catch-only: you throw Safari Balls and bait/rocks and
    never battle. Its encounters still field an HM-carrying party for the walk-
    through, but they cost no PP, HP or turns -- run_section skips them."""
    return "safari" in str(name or "").lower()

def _segment_tiles(a, b):
    """The tiles ENTERED walking the straight route segment a->b (same map,
    excludes the start so consecutive segments don't double-count the shared
    corner). a, b are [map, x, y] waypoints from route.json."""
    _, x0, y0 = a; _, x1, y1 = b
    if x0 == x1 and y0 != y1:
        step = 1 if y1 > y0 else -1
        for y in range(y0 + step, y1 + step, step): yield (x0, y)
    elif y0 == y1 and x0 != x1:
        step = 1 if x1 > x0 else -1
        for x in range(x0 + step, x1 + step, step): yield (x, y0)
    elif (x0, y0) != (x1, y1):     # diagonal corner artifact: just the endpoint
        yield (x1, y1)

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
        # Count the tiles the path actually ENTERS that are encounter ground
        # (grass/cave), not the whole map's grass share applied to every step --
        # otherwise a route that skirts the grass (Cycling Road, a road across
        # Route 13) is charged wild battles it never fights.
        steps = _c.defaultdict(int)          # map -> tiles entered
        enc = _c.defaultdict(int)            # map -> tiles entered that are grass/cave
        for s2 in st["steps"]:
            path = s2.get("path") or []
            for a, b in zip(path, path[1:]):
                if a[0] != b[0]: continue
                et = W.encounter_tiles(a[0])
                for t in _segment_tiles(a, b):
                    steps[a[0]] += 1
                    if t in et: enc[a[0]] += 1
        entries = []
        for mp, enc_steps in enc.items():
            m = info.get(mp)
            spe = m.get("stepsPerEncounter") if m else None
            if not m or not spe: continue
            battles = enc_steps / spe
            if battles < 0.5: continue
            e = dict(m); e["stage"] = stage; e["steps"] = steps[mp]
            e["encounterSteps"] = enc_steps; e["battles"] = round(battles, 1)
            e["catchOnly"] = _catch_only_map(mp)
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
                # Safari Zone encounters keep the section non-empty (so it still
                # fields HM carriers for the walk-through) but are never fought.
                "catchOnly": bool(m.get("catchOnly")),
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
    # The Elite Four is a locked gauntlet: five trainer fights with no grass and no
    # walking between them. Any wild load on this stage is the climb UP to the
    # Plateau (Victory Road's last floors), walked once before the first fight --
    # never between Lorelei and Bruno. Keep the fights contiguous with the walk-in
    # wilds ahead of them, instead of spreading the wilds across the rooms.
    if any(b.get("kind") == "elite4" for b in battles):
        return wild + battles
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

# The Ember Spa's heal (special HealPlayerParty) fires from a coord trigger sitting
# in the spring itself -- a water tile the walk can't stand on, and the dry path to
# the Rock Smash tutor skirts the pool entirely. But you only ever enter this dead-end
# map to fetch HM06, and you soak in the spring to do it, so the heal is tied to the
# VISIT, not to a tile the route can reach. Mark it whole-map; the Purified Zone stays
# tile-based (its trigger is on the walked floor and 5F has fights that must not heal).
_WHOLE_MAP_HEAL = {"OneIsland_KindleRoad_EmberSpa"}

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
    if step.get("teleport"):
        return (f"teleporting — you return to the "
                f"{G.pretty_location(step['teleport'])} Pokémon Center")
    prev = None
    for m, x, y in step["path"]:
        if "PokemonCenter" in m:
            town = m.split("_PokemonCenter")[0]
            return G.pretty_location(town) + " Pokémon Center"
        if m in _WHOLE_MAP_HEAL and m in zones:
            return zones[m][0]
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

_TELE_STAGES = None
def teleport_stages():
    """The set of stages whose route uses Teleport to shorten the walk (tour.py
    marks each stage). Those legs want a Teleport user on the party, assigned
    like an HM -- see assign_hms."""
    global _TELE_STAGES
    if _TELE_STAGES is not None: return _TELE_STAGES
    _TELE_STAGES = set()
    path = f"{OUT}/route.json"
    if os.path.exists(path):
        rt = json.load(open(path))
        _TELE_STAGES = {st["stage"] for st in rt["stages"] if st.get("teleport")}
    return _TELE_STAGES

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
           badges["atk"], badges["spatk"], obey,
           tuple(sorted((pm.get("boosts") or {}).items())))
    hit = _profile_cache.get(key)
    if hit is None:
        MEAS.note("profile", key)
        # entry abilities (Intimidate) set opening stat stages; deterministic
        # from the pair's species, which the cache key already carries.
        pm2, opp2 = E.with_entry_boosts(pm, opp)
        p = E.move_profile(pm2, opp2, move, badges={"atk": badges["atk"],
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
DUEL_TURN_CAP = 40     # a KO slower than this is no answer at all
DUEL_LOSS = 9.0        # the cost recorded for a fight with no answer

def duel_cost(pm, opp, moves, badges):
    """One-on-one screen: the share of its HP `pm` loses beating `opp` with the
    best of `moves` (>= 1: it loses), and that move. The faster side gets a free
    turn. Used by the core pick, the evolution-timing weigh-up and the League
    analysis, so all three judge a fight the same way."""
    best, t = None, 999.0
    for mv in moves:
        p = profile(pm, opp, mv, badges)
        if p and p["turns"] < t:
            best, t = mv, p["turns"]
    if t >= DUEL_TURN_CAP:
        return DUEL_LOSS, None
    thr = threat(opp, pm, badges)["avg"]
    first = 1.0 if pm["stats"]["speed"] > opp["stats"]["speed"] else 0.0
    return max(0.0, t - first) * thr / pm["stats"]["hp"], best

def threat(opp, pm, badges):
    """Damage the opponent deals to this player Pokemon per turn."""
    key = (opp["species"], opp["level"], tuple(opp["moves"]), pm["species"],
           pm["level"], badges["def"], badges["spdef"],
           tuple(sorted((pm.get("boosts") or {}).items())))
    hit = _threat_cache.get(key)
    if hit is None:
        MEAS.note("threat", key)
        # our Intimidate lowers the opponent's Attack; theirs lowers ours
        opp2, pm2 = E.with_entry_boosts(opp, pm)
        t = E.best_move(opp2, pm2, badges={"def": badges["def"], "spdef": badges["spdef"]},
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
    # each move's own damage output, to break near-ties in the greedy below toward
    # the stronger attack rather than the higher-PP one
    base = {m: t for t, m in scored}
    n_opp = max(1, len(opponents))

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
            # Once coverage is saturated the marginal gains go to ~0, so the slot
            # is decided by the tie-breaks: prefer the stronger standalone attack
            # (its own average damage), then a little PP so a genuine filler is
            # spammable. Without the damage term a weak high-PP move (Lick's 30 PP)
            # would beat a real attack (Ice Punch) for the last Jynx slot.
            gain += base.get(mv, 0.0) / n_opp * 0.01 + E.MOVES[mv]["pp"] * 1e-4
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
                 "legBase", "legMinHpPct", "legFaints", "boosts", "stint")
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
        # stat stages from X items / setup moves (League only); they last while it
        # stays in, and `stint` counts the boost actions spent this stint
        self.boosts = {}; self.stint = 0
        self.leg_mark()
    def alive(self): return self.hp > 0
    def bmon(self):
        """The mon as it stands on the field, stat stages included."""
        return with_boosts(self.mon, self.boosts)
    def unboost(self):
        self.boosts = {}; self.stint = 0
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
        self.unboost()
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
    def patch_up(self):
        """Heal and revive from the bag between League members: Revive / Full
        Restore bring HP back (and a fainted mon back up), but nothing in the bag
        short of an Elixir refills PP, so PP keeps draining across the run."""
        self.hp = float(self.maxhp)
    def leg_mark(self):
        """Start a new leg: the stretch between two full heals. Everything
        since the last mark is what THIS leg cost."""
        self.legBase = dict(self.used)
        self.legMinHpPct = max(0.0, self.hp) / self.maxhp * 100
        self.legFaints = self.fainted

def usable(member, opp, badges, boosts=None):
    """Best move this member can still afford against this opponent, at its
    current stat stages (or `boosts`, for a what-if)."""
    best = None
    mon = member.bmon() if boosts is None else with_boosts(member.mon, boosts)
    for mv in member.moves:
        if mv in MOVE_LOCKED: continue
        if member.pp[mv] < 1: continue
        p = profile(mon, opp, mv, badges, member.obey)
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
               held_roots, held_groups, chosen, teleport=False, moon_used=None,
               skip=frozenset(), protected=frozenset(), helpers=()):
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

    `skip` drops field moves the run has chosen to do without here (the core
    approach walks instead of flying until a core member can carry Fly);
    `protected` species are never traded away for a carrier; `helpers` are line
    roots a core run already brought in to carry an HM, tried first so one
    helper keeps the job instead of a new one joining every section.

    Returns (plan, extra members to add to the party).
    """
    req = [mv for mv in HM.moves_here(stage) if mv not in skip]
    # Teleport rides along as a field move on the legs the route uses it (it saves
    # the walk back to a Center). Unlike an HM it's optional -- worth a spare slot
    # or a carrier, never a sacrificed attack -- so it's pulled out before the
    # last-resort branch below.
    if teleport and TELE_MOVE not in req:
        req.append(TELE_MOVE)
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
                if field_can_learn(m.species, mv) and len(m.moves) < 4]
        if free:
            m = min(free, key=lambda x: (len(x.moves), x.name))
            m.teach(mv)
            plan.append({"move": mv, "by": m.name, "species": m.species,
                         "how": "spare", "gave": None, "gaveBack": None,
                         "permanent": False})
            continue
        uncovered.append(mv)

    # ---- 3: carriers, each closing as many remaining gaps as it can. Under the
    # core approach a field move a core member can learn stays with the core (it
    # gives up a move in step 4) -- the point is the six carry the HMs; a carrier
    # joins only for a move none of them can hold.
    held_back = [mv for mv in uncovered
                 if any(m.species in protected and field_can_learn(m.species, mv)
                        for m in team)]
    uncovered = [mv for mv in uncovered if mv not in held_back]
    pool = None
    while uncovered and len(team) + len(extra) < MAX_TEAM:
        if pool is None:
            pool = [sp for sp in O.candidates(stage)
                    if sp not in chosen and C.allowed(sp)
                    and sp not in NO_TEAM_SPECIES
                    and C.starter_of(sp) in (None, starter)
                    and not C.outgrown(sp, stage, avail)
                    # a core section is at the core's own level (see solve_section)
                    and not (protected and P.level_evolved(sp, level, avail))]
        best, bestkey = None, None
        roots = set(held_roots) | {C.line_root(m.species) for m in extra}
        groups = set(held_groups) | {g for g in
                                     (C.exclusive_group(m.species) for m in extra) if g}
        # A carrier is still a party member, so a Moon-Stone evolution brought in
        # to hold an HM counts against the run's stone budget just like a fighter
        # would -- otherwise Cut could smuggle a third Nidoking in before the third
        # stone exists (a real bug this guard caught).
        live_moon = (moon_used or set()) | {m.species for m in team + extra
                                            if m.species in MOON_EVOS}
        for sp in pool:
            if C.conflicts(sp, roots, groups): continue
            if not moon_ok(sp, stage, live_moon): continue
            covers = [mv for mv in uncovered if field_can_learn(sp, mv)]
            if not covers: continue
            pm = O.player_mon(sp, level)
            fight = max((move_value(pm, mv, opponents, badges)
                         for mv in O.move_pool(sp, level, stage)), default=0.0)
            key = (C.line_root(sp) not in helpers, -len(covers), -fight,
                   E.SPECIES[sp]["name"])
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

    uncovered += held_back
    # Teleport is a convenience, not a barrier: if no spare slot or carrier took
    # it, leave it -- the route's teleport hops just cost the full walk instead.
    # Nobody gives up an attack for it.
    uncovered = [mv for mv in uncovered if mv != TELE_MOVE]

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
                if sp in NO_TEAM_SPECIES: continue
                if C.starter_of(sp) not in (None, starter): continue
                if C.outgrown(sp, stage, avail): continue
                if not HM.can_learn(sp, mv): continue
                # same stone budget as the carrier step: a swapped-in body that is
                # a Moon-Stone evolution still needs a stone the run has by now.
                if not moon_ok(sp, stage, (moon_used or set()) |
                               {x.species for x in team + extra if x.species in MOON_EVOS}):
                    continue
                pm = O.player_mon(sp, level)
                mpool = O.move_pool(sp, level, stage)
                covers = sum(1 for x in uncovered if HM.can_learn(sp, x))
                fight = max((move_value(pm, x, opponents, badges) for x in mpool),
                            default=0.0)
                # the helper already carrying a field move this run swaps back in
                # before a stranger (Raticate held Cut, then a full party at Route
                # 12 swapped in a Scyther for it: two carriers for one job)
                k = (C.line_root(sp) not in helpers, -covers, -fight, E.SPECIES[sp]["name"])
                if key is None or k < key:
                    if not C.conflicts(sp, roots, groups) or True:
                        swap_in, key = (sp, pm, mpool, covers), k
            if swap_in and any(m.species not in protected for m in team):
                sp, pm, mpool, _ = swap_in
                worst = min((m for m in team if m.species not in protected), key=lambda m: max(
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
            # never trade away a field move this section needs for another one
            keep = [x for x in m.moves if not (protected and x in req)] or m.moves
            worst = min(keep, key=lambda x: drop_key(m, x, level, stage,
                                                     opponents, badges))
            cost = drop_key(m, worst, level, stage, opponents, badges)
            if protected:
                # the core spreads its field moves: whoever holds the fewest
                # gives up a slot, so no member ends up all HMs (Charizard was
                # handed Cut, Fly, Strength and Rock Smash at once)
                cost = (sum(1 for x in m.moves if x in HM.HM_MOVE.values()), cost)
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

_ROUTE_CATCH = None
def route_catch_stages():
    """species name -> the earliest stage the ROUTE actually catches it. The route
    is reachability-aware (tour.py holds a catch until you can stand where it's
    made), so for the few species gated by map geometry this is later than
    progression.full_availability's method-based stage -- e.g. the Celadon Super-Rod
    Grimer needs Surf to reach its fishing tile, so the route catches it at the Surf
    stage, not when the Super Rod is first in hand. dex_evolutions keys the pre-evo's
    obtainment off this so an 'evolve it' prompt never precedes catching one."""
    global _ROUTE_CATCH
    if _ROUTE_CATCH is not None: return _ROUTE_CATCH
    out = {}
    path = f"{OUT}/route.json"
    if os.path.exists(path):
        for st in json.load(open(path))["stages"]:
            for s in st["steps"]:
                if s.get("kind") != "catch": continue
                for sp in (s.get("species") or []):
                    nm = str(sp).split(" (")[0]
                    if nm not in out or st["stage"] < out[nm]:
                        out[nm] = st["stage"]
    _ROUTE_CATCH = out
    return out

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
        hold = rec.get("holdTo")
        hold_mv = E.MOVES.get(rec.get("holdMove"), {}).get("name") if hold else None
        if meth and meth.startswith("LEVEL"):
            lv = param if isinstance(param, int) else None
            how = f"level it to {lv}" if lv else "level it up"
            note = None
            if hold:
                # the timing plan holds this one back for a move (evo_timing.py)
                how = f"let it evolve at L{hold}, once it knows {hold_mv}"
                note = (f"cancel the evolution (press B) at L{lv}–{hold - 1}: "
                        f"{fname} learns {hold_mv} at L{hold}, which {tname} learns later or never")
            # optional: a strong move the pre-evo learns a few levels before the
            # evolved form, within a plausible playthrough window. Delaying trades
            # evolved stats meanwhile, so it is framed as a choice, not advice.
            if lv and not hold:
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
            if hold:
                how += f" once it knows {hold_mv} (L{hold})"
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
        # Show the evolution one stage AFTER its pre-evo first becomes obtainable
        # when they'd otherwise coincide: the panel means "evolve it next time you
        # pass a Center", and you can't do that until you actually HAVE the base.
        # Otherwise the section that first offers a rod fish (Horsea, Staryu... on
        # the Super Rod you only pick up mid-section) would already list its
        # evolution, before you've caught -- or could catch -- the thing.
        disp = rec["stage"]
        # the pre-evo's obtainment stage is the later of its method-based
        # availability and the stage the ROUTE actually catches it: a geometry-gated
        # catch (the Celadon Grimer needs Surf to reach its fishing tile) lands
        # stages after availability suggests, and "evolve it" must not precede it.
        bs = avail.get(frm, {}).get("stage") if frm else None
        rc = route_catch_stages().get(fname) if frm else None
        if rc is not None:
            bs = rc if bs is None else max(bs, rc)
        if bs is not None and bs >= disp and bs + 1 <= _MAX_STAGE:
            disp = bs + 1
        out[disp].append({"from": fname, "to": tname, "how": how, "note": note})
    for st in out: out[st].sort(key=lambda r: r["to"])
    _DEX_EVOS = dict(out)
    return _DEX_EVOS

def price_dex_evos(result, avail):
    """Put a grind time on each level-up Pokédex evolution of one solved run (the
    rows dex_evolutions shows per section), for the guide's playtime estimate.
    An evolution the run's own parties reach is free -- the line levelled there
    fighting. Otherwise the player raises the pre-evolution from where it's caught
    to the evolution level; a line with several such entries (Caterpie ->
    Metapod -> Butterfree) is one mon raised up its chain, so each row pays only
    the levels past the previous one. Stones, trades and friendship cost no XP,
    unless the timing plan holds one until a move's level."""
    by_name = {v["name"]: k for k, v in E.SPECIES.items()}
    fielded = {t["species"] for sec in result.values() if sec for t in sec.get("team", [])}
    reached = set()
    for sp in fielded:              # a fielded form has passed every form below it
        while sp:
            reached.add(sp); sp = TC._pre_evo(sp)
    rows = []
    for st, sec in result.items():
        if not sec: continue
        sec["dexEvos"] = [dict(r) for r in sec.get("dexEvos", [])]
        for r in sec["dexEvos"]:
            r["sec"] = 0
            tgt, frm = by_name.get(r["to"]), by_name.get(r["from"])
            rec = avail.get(tgt) or {}
            # the level the evolution really happens at: a held one (evo_timing)
            # waits for its move -- a held stone evolution included, which then
            # costs XP like a level-up one
            if rec.get("holdTo"):
                lv = rec["holdTo"]
            elif str(rec.get("evoMethod", "")).startswith("LEVEL"):
                lv = rec.get("evoParam")
            else:
                lv = None
            if tgt in reached or not frm or not isinstance(lv, int):
                continue
            rows.append((C.line_root(tgt), lv, frm, r))
    done = {}                       # line root -> level its grind already reached
    for root, lv, frm, r in sorted(rows, key=lambda x: (x[0], x[1])):
        start = max(TC.obtained_level(frm, avail), done.get(root, 0))
        if lv > start:
            r["sec"] = round(TC.grind_seconds(frm, start, lv, avail))
        done[root] = max(lv, done.get(root, 0))

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

# ------------------------------------------------------------------ League tactics
# In the Elite Four / Champion fights the player may spend the bag: X items and
# setup moves to boost before attacking, and potions to heal mid-fight
# (tactics.py has the decomp-read catalog). Each is one turn on which the user
# does not attack and the foe gets a free hit; stat stages last until the user
# leaves the field, so a boost is worth what it saves on the REST of the party.

def with_boosts(mon, boosts):
    """The mon carrying these stat stages (a copy; cached base mons stay neutral)."""
    return {**mon, "boosts": dict(boosts)} if boosts else mon

def _add_boosts(base, gain):
    out = dict(base)
    for st, n in gain.items():
        out[st] = max(-6, min(6, out.get(st, 0) + n))
    return {k: v for k, v in out.items() if v}

def _boost_actions(m, kit):
    """(kind, what, stat gains) a member can spend a turn on right now."""
    acts = [("move", mv, TAC.SETUP_EFFECTS[E.MOVES[mv]["effect"]])
            for mv in m.moves if TAC.is_setup(mv) and m.pp.get(mv, 0) >= 1]
    acts += [("item", it, gain) for it, gain in kit.x_items.items()]
    return acts

def _tactic_eval(m, opp, badges, H, s, k, boosts, heal, n, item_drag, rest, team, kit):
    """Price one League line for member `m` against `opp` at H HP: switch in (s),
    spend k turns boosting to `boosts`, then attack, healing n times with `heal`
    if that is what it takes to finish. Returns None if it cannot even damage."""
    p = usable(m, opp, badges, boosts)
    if not p or "table" not in p or E.MOVES[p["move"]]["effect"] == "EXPLOSION":
        return None
    mon_b = with_boosts(m.mon, boosts)
    thr0 = threat(opp, m.bmon(), badges)              # hits taken while setting up
    thr = threat(opp, mon_b, badges)
    fast = E.effective_speed(mon_b) > E.effective_speed(opp)
    t = p["table"][min(H, len(p["table"]) - 1)]
    xt, xa, uses = item_drag(t, p["turns"])
    t += xt
    hp1 = m.hp - (s + k) * thr0["avg"]
    if hp1 <= 0:
        return None                                   # dies before it ever attacks
    amt = kit.heal_amount(heal, m.maxhp) if heal else 0.0
    pool = hp1 + n * max(0.0, min(amt, m.maxhp - thr["avg"]))
    acts = acting_turns(pool, thr["avg"], n, fast)
    lands = acts >= t - 1e-9
    frac = 1.0 if lands else (acts / t if t > 0 else 0.0)
    taken = (n + max(0.0, t - (1.0 if fast else 0.0) + xa)) * thr["avg"]
    hp_end = max(1.0, pool - taken) if lands else 0.0
    # what the boost keeps paying on the foes still to come, if it stays in
    saving = 0.0
    if lands and boosts:
        run = hp_end
        for f in rest:
            pj = usable(m, f, badges, boosts)
            if not pj: break
            thj = threat(f, mon_b, badges)
            fj = E.effective_speed(mon_b) > E.effective_speed(f)
            if acting_turns(run, thj["avg"], 0, fj) < pj["turns"]: break
            base = min((q["turns"] + (0 if o is m else 1)
                        for o in team if o.alive()
                        for q in [usable(o, f, badges, {})] if q), default=pj["turns"])
            saving += max(0.0, base - pj["turns"])
            run -= max(0.0, pj["turns"] - (1.0 if fj else 0.0)) * thj["avg"]
    turns = s + k + n + (t if lands else acts)
    return {"p": p, "thr": thr, "fast": fast, "t": t, "uses": uses, "lands": lands,
            "frac": frac, "hp_end": hp_end, "turns": turns, "saving": saving,
            "left": 0 if lands else hp_after(p, H, 0, frac)}

def league_variants(team, opp, badges, active, H, item_drag, tac):
    """The League lines worth comparing with the plain plans: per member, a greedy
    boost sequence (up to tactics.MAX_BOOSTS per stint) and, for a member that
    would go down, the cheapest heals (up to MAX_HEALS) that let it finish.
    Yields (key, plan) with the same key shape as plan_vs."""
    kit, rest = tac
    out = []
    for m in team:
        if not m.alive(): continue
        s = 0 if (active is None or m is active) else 1
        base_b = dict(m.boosts) if s == 0 else {}
        acts = _boost_actions(m, kit)
        seq, boosts = [], base_b
        budget = TAC.MAX_BOOSTS - (m.stint if s == 0 else 0)
        lines = [(list(seq), dict(boosts))]
        for _ in range(max(0, budget)):
            best = None
            for a in acts:
                if a[0] == "move" and sum(1 for x in seq if x[1] == a[1]) >= m.pp.get(a[1], 0):
                    continue
                nb = _add_boosts(boosts, a[2])
                if nb == boosts: continue
                ev = _tactic_eval(m, opp, badges, H, s, len(seq) + 1, nb, None, 0,
                                  item_drag, rest, team, kit)
                if not ev or not ev["lands"]: continue
                score = ev["turns"] - ev["saving"]
                if best is None or score < best[0]:
                    best = (score, a, nb)
            if not best: break
            seq.append(best[1]); boosts = best[2]
            lines.append((list(seq), dict(boosts)))
        for seq_l, b in lines:
            for heal, n in [(None, 0)] + [(h, k) for h, _ in kit.heals for k in range(1, TAC.MAX_HEALS + 1)]:
                ev = _tactic_eval(m, opp, badges, H, s, len(seq_l), b, heal, n,
                                  item_drag, rest, team, kit)
                if not ev: continue
                if n and not ev["lands"]: continue
                if not seq_l and not n: continue        # the plain line is plan_vs's own
                cost = sum(TAC.price(x[1]) for x in seq_l if x[0] == "item") + \
                       (TAC.price(heal) * n if heal else 0)
                lands = ev["lands"]
                key = (0 if lands else 2, ev["left"],
                       round(ev["turns"] - ev["saving"], 4),
                       ev["thr"]["avg"] * ev["turns"], cost)
                pp_cost = ev["t"] * ev["frac"] / m.obey
                info = {"boosts": b, "actions": seq_l, "heal": heal, "heals": n,
                        "lands": lands, "frac": ev["frac"], "hpEnd": ev["hp_end"]}
                out.append((key, [(m, ev["p"], ev["thr"], ev["turns"], 0.0, s > 0, False,
                                   pp_cost, ev["uses"], H, 0, ev["fast"], info)]))
                if n: break          # cheapest heal that finishes is enough
    return out

# A setup move takes an attack's slot only when that attack adds less than this
# share of the moveset's total damage coverage over the section.
SETUP_SLOT_MAX_LOSS = 0.10
# How a setup move is matched to a mon: its boost to the mon's better attacking
# stat counts double, Speed once, and the defensive stages half.
SETUP_FIT_OFFENSE, SETUP_FIT_SPEED, SETUP_FIT_DEFENSE = 2.0, 1.0, 0.5

def league_moveset(pm, mvs, stage, opponents, badges):
    """Give a League member a setup move when its attack coverage is already
    saturated: take a free slot, or swap out the attack whose removal costs the
    least coverage (under SETUP_SLOT_MAX_LOSS of the moveset's damage here).
    The planner then decides whether to actually use it."""
    setup = O.setup_pool(pm["species"], pm["level"], stage, TAC.is_setup)
    if not setup: return mvs
    def cover(ms):
        return sum(max([min(o["stats"]["hp"], O.scalar_damage(pm, o, mv, badges)
                            * (E.MOVES[mv]["accuracy"] or 100) / 100) for mv in ms] or [0])
                   for o in opponents)
    # the boost that most helps this mon's own best stat
    phys = pm["stats"]["attack"] >= pm["stats"]["spAttack"]
    def fit(mv):
        g = TAC.SETUP_EFFECTS[E.MOVES[mv]["effect"]]
        return (g.get("attack" if phys else "spAttack", 0) * SETUP_FIT_OFFENSE
                + g.get("speed", 0) * SETUP_FIT_SPEED
                + (g.get("defense", 0) + g.get("spDefense", 0)) * SETUP_FIT_DEFENSE)
    pick = max(setup, key=fit)
    if fit(pick) <= 0: return mvs
    if len(mvs) < 4:
        return list(mvs) + [pick]
    total = cover(mvs)
    drop = min(mvs, key=lambda mv: total - cover([x for x in mvs if x != mv]))
    if total - cover([x for x in mvs if x != drop]) < SETUP_SLOT_MAX_LOSS * total:
        return [pick if x == drop else x for x in mvs]
    return mvs

LEAGUE_KINDS = ("elite4", "champion")

_STOCK = {}
def _league_stock():
    """What the League bag can draw on (tactics.stock), read from data/route.json
    once per process. It depends on the version's route only, never on the
    starter or trade mode, but reset_solve_caches clears it anyway."""
    if "s" not in _STOCK:
        path = f"{OUT}/route.json"
        route = json.load(open(path)) if os.path.exists(path) else {"stages": []}
        _STOCK["s"] = TAC.stock(route)
    return _STOCK["s"]

def tactic_label(info):
    """'Calm Mind ×2, Hyper Potion ×1' for a League line, or None."""
    if not info: return None
    parts, seen = [], {}
    for kind, what, _ in info["actions"]:
        nm = E.MOVES[what]["name"] if kind == "move" else TAC.name(what)
        seen[nm] = seen.get(nm, 0) + 1
    parts = [f"{k} ×{v}" for k, v in seen.items()]
    if info["heals"]:
        parts.append(f"{TAC.name(info['heal'])} ×{info['heals']}")
    return ", ".join(parts) or None

def patch_up_cost(m, kit):
    """Charge the bag for healing a member to full between League members: a
    Revive if it fainted (back at half HP), then the cheapest single potion that
    covers what is still missing. Returns False when it cannot be patched up (it
    fainted and no Revive is on sale) -- potions do not raise the fainted."""
    hp = m.hp
    if hp <= 0:
        if not kit.revive: return False
        kit.charge(kit.revive); hp = m.maxhp / 2
    miss = m.maxhp - hp
    if miss <= 0.5: return True
    fits = [h for h, a in kit.heals if a == TI.FULL or a >= miss]
    if fits:
        kit.charge(min(fits, key=TAC.price))
    return True

def acting_turns(hp, per_hit, sw, faster):
    """How many turns a member gets to ACT before the foe's hits knock it out.

    The foe needs n = ceil(hp / per_hit) hits (per_hit is its expected damage per
    turn, accuracy folded in). A switch-in eats one hit before acting. When the
    member is faster it acts before the foe's hit each turn, so it gets the turn
    the fatal hit lands; slower, it does not. A one-shot slower mon never acts:
    the Golem-into-Blastoise case, where it used to be credited with the KO."""
    if per_hit <= 0:
        return math.inf
    n = math.ceil(hp / per_hit - 1e-9)
    return max(0, n - sw - (0 if faster else 1))

def segment_outcome(m, thr, turns, switched, faster):
    """(finished?, share of its work done) for one plan segment, given the
    member's HP right now. `turns` includes the switch-in turn when switched."""
    sw = 1 if switched else 0
    need = max(0.0, turns - sw)
    acts = acting_turns(m.hp, thr["avg"], sw, faster)
    if acts >= need - 1e-9:
        return True, 1.0
    return False, (acts / need if need > 0 else 0.0)

def hp_after(p, hp_from, hp_to, frac):
    """Foe HP left after a member covers `frac` of the work of taking it from
    hp_from down to hp_to, read off the move's own expected-turns DP."""
    tbl = p["table"]
    hp_from = min(hp_from, len(tbl) - 1)
    target = tbl[hp_from] - (tbl[hp_from] - tbl[hp_to]) * max(0.0, min(1.0, frac))
    for h in range(hp_to, hp_from + 1):
        if tbl[h] >= target - 1e-9:
            return h
    return hp_from

def plan_vs(team, opp, badges, active, terrain=None, items=None, opp_hp=None,
            tac=None):
    """Cheapest way for the party to take ONE opposing Pokemon down.

    Two shapes are considered. A solo: one member does the whole thing, paying a
    turn and a hit if it has to switch in. A handover: one member softens the
    target and a second finishes it, paying a turn and a hit for the mid-fight
    switch. The handover's prefix is priced off the same DP as the solo, so
    softening for k turns costs exactly the turns it removes from the finisher.

    `items` is (slots, validMons) for a trainer battle: the trainer's remaining
    Full Restores / potions (trainer_items) and how many of its Pokemon are still
    standing. Each plan is charged the turns and hits those heals add, so a
    member that KOs from above 25% (no heal window) beats one that chips.

    `tac` (League fights only) is (kit, rest): the bag (tactics.Kit) and the foes
    still to come in this battle. It adds plans that spend X items / setup moves
    to boost first, or heal mid-fight, alongside the plain ones (league_variants).

    `opp_hp` is the foe's CURRENT HP when a previous member fainted partway
    through it (run_section re-plans the remainder); default full.

    A plan "survives" when every member gets enough turns to act (acting_turns)
    to finish its share before the foe's hits drop it. Among plans that lose a
    body, the one leaving the foe lowest is preferred -- not the "fastest" one,
    which used to pick a mon that dies before it moves.

    Returns a list of segments, each (member, profile, threat, turns, hits,
    isSwitch, selfKO, ppCost, itemUses, hpFrom, hpTo, faster), or None if
    nothing on the team can touch this Pokemon. itemUses (expected consumption
    per item slot) rides on the last segment, None on the others.
    """
    def item_drag(t, t_full=None):
        if not items or not items[0]: return 0.0, 0.0, None
        return TI.drag(items[0], t, opp["stats"]["hp"], items[1],
                       hp_frac=H / opp["stats"]["hp"], t_full=t_full)
    H = min(opp_hp, opp["stats"]["hp"]) if opp_hp else opp["stats"]["hp"]
    opts = []
    for m in team:
        if not m.alive(): continue
        p = usable(m, opp, badges)
        if not p or "table" not in p: continue
        bm = m.bmon()
        opts.append((m, p, threat(opp, bm, badges),
                     E.effective_speed(bm) > E.effective_speed(opp)))
    if not opts:
        # nothing can damage it as things stand -- in the League a boost may
        # still open a way (handled below); elsewhere it is unanswered
        if not tac: return None

    def sw(m): return 0 if (active is None or m is active) else 1
    def boom(p): return E.MOVES[p["move"]]["effect"] == "EXPLOSION"

    # ---- solo
    best, bestkey = None, None
    for m, p, thr, faster in opts:
        s, t = sw(m), p["table"][min(H, len(p["table"]) - 1)]
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
        xt, xa, uses = item_drag(t_me, p["turns"])
        t_me += xt
        hits = max(0.0, t_me - xt - (1.0 if faster else 0.0) + xa) + s
        scale = act * ((1.0 - 0.5 * burnf) if thr.get("cat") == "PHYSICAL" else 1.0)
        dmg = thr["avg"] * hits * scale + chip_in * t_me * m.maxhp
        hits_dmg = dmg / max(thr["avg"], 1e-6)     # so m.hurt() charges the real toll
        kills_self = boom(p)
        acts = acting_turns(m.hp, thr["avg"], s, faster)
        lands = acts >= t_me - 1e-9
        survives = lands and not kills_self
        # a body lost: a self-KO that lands trades one mon for the KO; a member
        # that dies first is ranked by how much of the foe it leaves standing
        left = 0 if lands else hp_after(p, H, 0, acts / max(t_me, 1e-9))
        key = (0 if survives else (1 if lands else 2), left, round(t_me + s, 4), dmg, 0)
        if bestkey is None or key < bestkey:
            bestkey = key
            best = [(m, p, thr, t_me + s, hits_dmg, s > 0, kills_self,
                     (t + xt) / m.obey, uses, H, 0, faster, None)]

    # Expected turns are very nearly linear in the target's remaining HP, so
    # prefix + switch + finish is essentially never FASTER than whichever solo
    # is best -- the switch turn is pure tax. What a handover buys is survival:
    # two bodies split the incoming damage, and a limited-PP nuke gets stretched
    # across a fight it could not have carried alone. So the search runs
    # whenever the best solo drops a Pokemon, and otherwise only for fights slow
    # enough that the tax could still be worth paying.
    if tac:
        for key, plan in league_variants(team, opp, badges, active, H, item_drag, tac):
            if bestkey is None or key < bestkey:
                bestkey, best = key, plan
    if best is None:
        return None
    if bestkey[0] == 0 and bestkey[2] <= HANDOVER_FLOOR:
        return best

    if not opts:
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
                # the trainer heals off the whole fight's clock; the finisher
                # eats the replay (a heal resets the foe after A's softening)
                xt, xa, uses = item_drag(pre + fin, pB["turns"])
                fin += xt
                if fin > ppB: continue
                total = sA + pre + 1.0 + fin
                # only prune on turns when the incumbent already keeps everyone
                # standing -- otherwise a slower plan that saves a Pokemon has
                # to be allowed to win
                if bestkey[0] == 0 and total >= bestkey[2] - 1e-9: continue
                hitsA = pre + sA                   # A never lands the KO
                hitsB = 1.0 + max(0.0, fin - xt - (1.0 if fastB else 0.0) + xa)
                dmgA, dmgB = thrA["avg"] * hitsA, thrB["avg"] * hitsB
                kills_self = boom(pB)
                okA = acting_turns(mA.hp, thrA["avg"], sA, fastA) >= pre - 1e-9
                actsB = acting_turns(mB.hp, thrB["avg"], 1, fastB)
                landsB = okA and actsB >= fin - 1e-9
                survives = landsB and not kills_self
                # only handovers whose opener lives to hand over are real plans
                if not okA: continue
                left = 0 if landsB else hp_after(pB, h, 0, actsB / max(fin, 1e-9))
                key = (0 if survives else (1 if landsB else 2), left,
                       round(total, 4), dmgA + dmgB, 0)
                if key < bestkey:
                    bestkey = key
                    best = [(mA, pA, thrA, pre + sA, hitsA, sA > 0, False,
                             pre / mA.obey, None, H, h, fastA, None),
                            (mB, pB, thrB, fin + 1.0, hitsB, True, kills_self,
                             fin / mB.obey, uses, h, 0, fastB, None)]
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
    item_uses = 0.0     # expected trainer heals (Full Restores etc.) spent on us
    # the player's bag, opened only for the League (tactics.py)
    kit = (TAC.Kit(_league_stock()) if any(b.get("kind") in LEAGUE_KINDS for b in battles)
           else None)
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
        if enc.get("catchOnly"):
            # The Safari Zone: you throw Safari Balls, you never battle, so this
            # encounter costs no turns, no PP and no HP. It stays in the section only
            # so the walk-through still fields an HM-carrying party; it isn't fought.
            log.append({"enc": enc["name"], "id": enc["id"], "kind": enc["kind"],
                        "location": enc["location"], "steps": [], "catchOnly": True,
                        "group": enc.get("group")})
            if bi in heal_after_idx:
                why = (heal_after_idx[bi] if isinstance(heal_after_idx, dict) else True)
                mark(bi, why)
                for m in team: m.restore(); m.leg_mark()
                log[-1]["healedAfter"] = why
            continue
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
        league = kit is not None and enc["kind"] in LEAGUE_KINDS
        for m in team: m.unboost()          # stat stages never outlive a battle
        # The trainer's bag (src/data/trainers.h .items): only trainers use items
        # (AI_TrySwitchOrUseItem checks BATTLE_TYPE_TRAINER), and the bag is per
        # battle -- it does not carry from one Elite Four member to the next.
        slots = (TI.new_slots(enc.get("items") or [])
                 if enc["kind"] != "wild" else [])
        n_opp = len(enc["_mons"])
        for oi, opp in enumerate(enc["_mons"]):
            # Gen 3's default battle style is SHIFT: when a trainer's Pokemon
            # goes down, the game offers a free switch before the next one
            # comes out. So against a trainer, every opponent gets a free
            # choice of who fights it -- only mid-fight handovers pay the
            # switch tax. Wild leads still carry over unpriced choices.
            # the mon still on the field from the last foe: under SHIFT you may keep
            # it in, and its stat stages stay with it; everyone else is benched
            field = active
            if enc["kind"] != "wild" and oi > 0:
                active = None
            for m in team:
                if m is not field: m.unboost()
            terrain = G.battle_terrain(enc.get("locationRaw") or enc.get("map") or "",
                                       enc.get("kind"), enc.get("method"))
            # A member that faints partway through only deals the damage it landed
            # before going down; the foe stays in at what HP it has left and the
            # next living member is planned against THAT. (This used to credit a
            # fainted member with the KO -- Golem "beating" a Blastoise whose Surf
            # one-shots it before it moves -- so "no full wipe" almost never bit.)
            rem = opp["stats"]["hp"]
            while rem > 0:
                plan = plan_vs(have, opp, badges, active, terrain,
                               items=(slots, n_opp - oi) if slots else None,
                               opp_hp=rem,
                               tac=(kit, enc["_mons"][oi + 1:]) if league else None)
                if plan is None:
                    failed += 1
                    entries.append({"opp": opp["name"], "lvl": opp["level"],
                                    "by": None, "move": None, "turns": 0.0})
                    break
                for si, (m, p, thr, turns, hits, switched, selfko, ppc, uses,
                         hp_from, hp_to, faster, tinfo) in enumerate(plan):
                    if field is not None and m is not field:
                        field.unboost(); field = None    # switched out: stages lost
                    if tinfo:
                        done, frac = tinfo["lands"], tinfo["frac"]
                    else:
                        done, frac = segment_outcome(m, thr, turns, switched, faster)
                    if uses:
                        uses = [u * frac for u in uses]
                        TI.spend(slots, uses)
                        item_uses += sum(uses)
                    if tinfo:
                        # the bag and the setup moves it spends, then its attack
                        for kind, what, _ in tinfo["actions"]:
                            if kind == "item": kit.charge(what)
                            else: m.spend(what, 1.0)
                        if tinfo["heals"]: kit.charge(tinfo["heal"], tinfo["heals"])
                        m.spend(p["move"], ppc)
                        if done:
                            m.hurt(max(0.0, m.hp - tinfo["hpEnd"]))
                            m.hp = tinfo["hpEnd"]      # mid-fight heals included
                            m.boosts = dict(tinfo["boosts"])
                            m.stint += len(tinfo["actions"])
                            rem = 0
                        else:
                            m.hurt(m.hp)
                            rem = hp_after(p, hp_from, 0, frac)
                        turns_used = turns
                    elif done:
                        m.spend(p["move"], ppc)
                        # it finishes its share before the foe's hits drop it
                        # (a KO'd foe swings no more), so it ends standing
                        m.hurt(max(0.0, min(thr["avg"] * hits, m.hp - 1.0)))
                        if selfko:   # Self-Destruct / Explosion take the user too
                            m.hp = 0.0; m.minHpPct = 0.0; m.legMinHpPct = 0.0
                        rem = hp_to
                        turns_used = turns
                    else:
                        m.spend(p["move"], ppc * frac)
                        m.hurt(m.hp)
                        rem = hp_after(p, hp_from, hp_to, frac)
                        turns_used = (1.0 if switched else 0.0) + frac * max(
                            0.0, turns - (1.0 if switched else 0.0))
                    if m.hp <= 0:
                        faints += 1; m.fainted += 1
                        m.unboost()
                        active = None      # the next send-out after a faint is free
                    else:
                        active = m
                    total_turns += turns_used
                    if enc["kind"] == "wild": wild_turns += turns_used
                    if (done and tm_value is not None and p["move"] in SCARCE
                            and scarce_spend(p["move"], stage)):
                        a = alt_turns(m, opp, badges, p["move"])
                        # no fallback at all means the TM is the only answer here
                        gain = (12.0 if a is None else max(0.0, a - p["turns"]))
                        if gain: tm_value[(p["move"], m.species)] += gain
                    entries.append({
                        "selfKO": selfko and done,
                        "_moveConst": p["move"], "_by": m.species, "_oppIdx": (bi, oi),
                        "opp": opp["name"], "lvl": opp["level"], "by": m.name,
                        "bySpecies": m.species, "move": p["name"], "moveType": p["type"],
                        "eff": p["eff"], "turns": round(turns_used, 2),
                        "switched": bool(switched),
                        # picks up a foe someone else already damaged
                        "handover": hp_from < opp["stats"]["hp"],
                        "hpLeft": round(max(0.0, m.hp) / m.maxhp * 100),
                        "dmgMin": p["min"], "dmgMax": p["max"],
                        # Danger radar: the opponent's worst single hit as a share of
                        # this mon's max HP, and whether that hit alone can KO it (one
                        # unlucky roll or a crit could end the mon). thr["max"] is the
                        # top roll of the opponent's best move (sections.threat).
                        "worstTaken": round(thr["max"] / m.maxhp * 100),
                        "ohkoRisk": 1 if thr["max"] >= m.maxhp else 0,
                        "faint": 1 if m.hp <= 0 else 0,
                        # did this member's share actually land? (False: it fainted
                        # first, and the foe stayed in at `foeHpLeft`)
                        "landed": bool(done),
                        "foeHpLeft": rem,
                        # League bag/setup use on this foe, e.g. "X Special ×2"
                        "tactics": tactic_label(tinfo),
                    })
                    if not done:
                        break          # re-plan the remainder with who is left
        log.append({"enc": enc["name"], "id": enc["id"], "kind": enc["kind"],
                    "location": enc["location"], "steps": entries,
                    "group": enc.get("group")})
        if enc["kind"] == "elite4" and bi not in heal_after_idx:
            # Between League members you may use the bag (Revives, Full Restores)
            # before walking into the next room, so every member is fought at full
            # HP. PP is the resource that carries: see Member.patch_up.
            for m in team:
                if patch_up_cost(m, kit):
                    m.patch_up()
            log[-1]["patchedAfter"] = True
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
            "itemUses": item_uses,
            "leagueKit": kit.ledger() if kit else None,
            "ppLeft": min([min(m.pp.values()) / max(1, max(E.MOVES[x]["pp"] for x in m.moves))
                           for m in team], default=1.0),
            "team": team}


# ------------------------------------------------------------------ objective
# What a party is optimized for. "turns" (the default) is the original: clear the
# section with the fewest faints, then the fewest turns. "training" adds each
# member's grind bill (training_cost.join_cost, in turns) for the levels it is
# BEHIND the section: a Pokemon fielded last section is caught up by the fights
# it took part in and pays nothing; a newcomer pays from its obtain level, and one
# returning from the bench pays from the level it was last fielded at.
OBJECTIVE = os.environ.get("LGMAX_OBJECTIVE", "training")
OBJECTIVES = ("turns", "training")
# Look-ahead solves of the whole run under the training objective: each re-solve
# spreads a line's training bill over the sections the previous solve fielded it
# in (Objective.plan). The best solve by run_cost is kept, so the first (myopic)
# one is a floor the look-ahead can only improve on.
LOOKAHEAD_PASSES = 3

# Faints stay a veto under the least-training objective (ranked before turns, as
# in the fewest-turns rule). Pricing them in turns was tried and rejected
# (2026-11-14, LeafGreen, all six starter/trade runs): at 30 and at 100 turns a
# faint, the solver fielded leaner parties that saved little training but tripled
# faints, and the drained parties they handed the next section left 1-2 runs with
# a wipe. A future price would have to see the carry into the next section.

class Objective:
    """How parties are ranked: fewest unanswered, then fewest faints, then fewest
    turns. Inactive (roster=None) that is the original rule bit for bit; active it
    adds each member's training bill (training_cost.Roster.owed) to the turns.

    `plan` (look-ahead) maps a line root to the stages the previous solve of the
    run fielded it in. A member's bill is spread over the sections from here on
    that it serves, so a Pokemon that carries the back half of the game is cheap
    to bring in -- and cheapest at the earliest stage it starts serving, when its
    level (and so its grind) is lowest. `weight` 0 ranks exactly as the
    fewest-turns rule (the wipe fallback in _solve_run) while the bill is still
    tracked and reported."""
    def __init__(self, stage=None, level=None, avail=None, roster=None, plan=None,
                 weight=1.0, levels=None):
        self.active = roster is not None
        self.stage, self.level, self.avail = stage, level, avail
        self.levels = levels or {}     # species fielded at their own level
        self.roster, self.plan, self.weight = roster, plan or {}, weight
        self._memo = {}

    def member(self, sp):
        if not self.active: return 0.0
        if sp not in self._memo:
            raw = self.roster.owed(sp, self.levels.get(sp, self.level), self.avail)[0]
            serves = sum(1 for st in self.plan.get(C.line_root(sp), ()) if st >= self.stage)
            self._memo[sp] = self.weight * raw / max(1, serves)
        return self._memo[sp]

    def bill(self, team):
        return sum(self.member(m.species) for m in team)

    def total(self, res, team):
        return res["turns"] + self.bill(team)

    def score(self, res, team):
        return (res["failed"], res["faints"], round(self.total(res, team), 2))

TURNS_ONLY = Objective()

# How many sections of this run each species earns a place in, measured on the
# unconstrained first pass. Used only to break ties: when several Pokemon clear
# a section equally well, name the one you are already carrying rather than a
# stranger you would catch for one battle and drop.
USAGE = {}

def set_usage(counts):
    USAGE.clear(); USAGE.update(counts or {})

def prune_team(team, chosen, battles, badges, heal_idx, carry, stage, obj=TURNS_ONLY,
               protected=frozenset()):
    """Drop any member whose removal costs nothing: the greedy seed can end up with
    no job once the specialists join (a solo-strong opener whose every fight gets
    taken over), and the party the page shows should be exactly the bodies the
    plan actually uses. Mutates `team` and `chosen`."""
    pruned = True
    while pruned and len(team) > 1:
        pruned = False
        base = run_section(team, battles, badges, heal_idx, carry=carry, stage=stage)
        bkey = score(base, obj, team)
        for m in list(team):
            if m.species in protected: continue
            rest = [x for x in team if x is not m]
            r = run_section(rest, battles, badges, heal_idx, carry=carry, stage=stage)
            if score(r, obj, rest) <= bkey:
                team.remove(m)
                if m.species in chosen: chosen.remove(m.species)
                pruned = True
                break

SWAP_ROUNDS = 3      # bound on swap-pass sweeps (each is |team| x |candidates| sims)

def swap_pass(team, chosen, members, battles, badges, heal_idx, carry, stage, moon_used,
              obj=TURNS_ONLY, protected=frozenset()):
    """Local search over the greedy party: replace one member at a time with the
    candidate that most improves score() (fewest unanswered, then faints, then
    turns), honoring the same legality as the greedy build (one per evolution
    line, exclusive choices, the Moon-Stone budget). Returns (team, chosen,
    history rows)."""
    team, chosen, history = list(team), list(chosen), []
    if len(team) < 2:
        return team, chosen, history
    cur = score(run_section(team, battles, badges, heal_idx, carry=carry), obj, team)
    for _ in range(SWAP_ROUNDS):
        improved = False
        for i in range(len(team)):
            out = team[i]
            if out.species in protected: continue
            others = team[:i] + team[i + 1:]
            roots = {C.line_root(m.species) for m in others}
            groups = {g for g in (C.exclusive_group(m.species) for m in others) if g}
            moon_held = (moon_used or set()) | {m.species for m in others
                                                 if m.species in MOON_EVOS}
            best = None
            for sp, (pm, mvs, pool, ob) in members.items():
                if sp == out.species or sp in chosen: continue
                if C.conflicts(sp, roots, groups): continue
                if not moon_ok(sp, stage, moon_held): continue
                cand = Member(pm, mvs, pool, ob)
                res = run_section(others + [cand], battles, badges, heal_idx, carry=carry)
                k = score(res, obj, others + [cand])
                if k < cur and (best is None or k < best[0]):
                    best = (k, sp, cand, res)
            if best:
                k, sp, cand, res = best
                team = others[:i] + [cand] + others[i:]
                chosen = [c for c in chosen if c != out.species] + [sp]
                # battle turns, like the greedy rows (k[2] also carries the bill)
                history.append({"swapped": out.name, "for": cand.name,
                                "turns": round(res["turns"], 2), "faints": k[1],
                                "failed": k[0]})
                cur, improved = k, True
        if not improved:
            break
    return team, chosen, history


def training_bill(team, level, avail, roster, levels=None):
    """The per-member grind the chosen party needs, for the guide."""
    if roster is None:
        return []
    out = []
    for m in team:
        to = (levels or {}).get(m.species, level)
        t, frm, back = roster.owed(m.species, to, avail)
        if t > 0:
            out.append({"species": m.species, "name": m.name, "from": frm, "to": to,
                        "turns": round(t, 1), "returning": back,
                        "sec": round(TC.grind_seconds(m.species, frm, to, avail))})
    return out

def pick_key(res, sp, obj=TURNS_ONLY, team=()):
    """Ordering for 'which Pokemon should join the party next' (obj.score, then
    the HP and familiarity tie-breaks)."""
    return obj.score(res, team) + (
            round(res.get("hpLost", 0.0), 3), -USAGE.get(sp, 0),
            E.SPECIES[sp]["name"])

def score(res, obj=TURNS_ONLY, team=()):
    return obj.score(res, team)

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
    # Teleport counts as a field move here too, so a party member carrying only
    # Teleport reads as a field-move slot (role "hm"), not an idle fighter.
    hm_names = {E.MOVES[mv]["name"] for mv in HM.HM_MOVE.values()} | {E.MOVES[TELE_MOVE]["name"]}
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

# Moon Stones are scarce and consumed permanently, so a run can hold only as many
# stone evolutions as it has picked stones up by a given stage (Mt. Moon gives two
# at stage 5, then Rocket Hideout at 17, Seafoam at 26). Without a budget the party
# solver will happily field three stone evolutions before the third stone exists,
# so it tracks which the run has committed and won't introduce a new one over
# budget. verify.py's Moon-Stone guard is the safety net for this.
MOON_EVOS = {"SPECIES_NIDOKING", "SPECIES_NIDOQUEEN",
             "SPECIES_CLEFABLE", "SPECIES_WIGGLYTUFF"}

# The legendary birds are caught for the Dex but kept OFF the recommended teams by
# request -- a one-of-a-kind catch sunk deep in a dungeon shouldn't be the game's
# answer to a gym. They stay in the route (catch stops) and the Dex; this only bars
# them from team selection. (Mewtwo is post-game, never on a team anyway.)
NO_TEAM_SPECIES = {"SPECIES_ARTICUNO", "SPECIES_ZAPDOS", "SPECIES_MOLTRES"}
_MOON_BUDGET = None
def moon_budget(stage):
    """How many Moon-Stone evolutions the run can hold by `stage`: the count of
    Moon Stones the route picks up on or before it (data/route.json)."""
    global _MOON_BUDGET
    if _MOON_BUDGET is None:
        _MOON_BUDGET = {}
        try:
            rt = json.load(open(f"{OUT}/route.json"))
            n = 0
            for st in rt["stages"]:
                for s in st["steps"]:
                    if "Moon Stone" in s.get("what", "") and s.get("kind") in ("item", "hidden"):
                        n += 1
                _MOON_BUDGET[st["stage"]] = n
        except Exception:
            _MOON_BUDGET = {}
    b = 0
    for s in sorted(_MOON_BUDGET):
        if s <= stage:
            b = _MOON_BUDGET[s]
    return b

def moon_ok(species, stage, moon_used):
    """A Moon-Stone evolution is allowed only if the run already fields it or
    still has an unspent stone by this stage."""
    if species not in MOON_EVOS or moon_used is None:
        return True
    return species in moon_used or len(moon_used) < moon_budget(stage)

def solve_section(stage, encs, starter, avail, tm_value=None, carry=None, moon_used=None,
                  level_override=None, heal_override=None, train_roster=None, plan=None,
                  bill_weight=1.0, core=None, specialists=True, skip_hms=frozenset(),
                  hm_helpers=(), member_levels=None):
    """One section's party. `core` (the core approach) is a list of species that
    are always fielded -- never pruned, swapped or evicted for an HM carrier --
    and `specialists` says whether anything else may join them for this section.
    `member_levels` (species -> level) fields those members at their own level
    instead of the section's: a core line is as far as its own XP has taken it,
    and a newcomer joins at the level it was caught, not ground to the team's."""
    st = P.STAGE_BY_ID[stage]
    # level_override lets the recommended-level calculator try a candidate level
    # without touching the progression curve; badges stay stage-based.
    level, badges = (level_override if level_override is not None else st["level"]), O.badges_for(stage)
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
    wild = wild_battles_for(stage)
    # The Ghost Marowak (setwildbattle SPECIES_MAROWAK, 30) blocks Pokémon Tower 6F
    # but is neither a trainer nor a wild-table slot, so nothing above it sees it.
    # It is MANDATORY -- it walls the climb to Mr. Fuji -- so unlike grass wilds it
    # IS fought: inject it as a one-off "scripted" fight grouped on 6F, where
    # interleave_wild seats it among that floor's Channelers, simulated via its _mons.
    scripted = []
    if any((b.get("locationRaw") or "").startswith("PokemonTower") for b in battles):
        mk = E.make_mon("SPECIES_MAROWAK", 30)
        scripted.append({"id": "scripted:MAROWAK", "kind": "scripted",
                         "name": "Ghost Marowak (Silph Scope)", "location": "PokemonTower_6F",
                         "locationRaw": "PokemonTower_6F", "group": "PokemonTower_6F",
                         "items": [], "_mons": [mk], "_dist": [(mk, 1.0)]})
    # Grass wild battles are OPTIONAL -- you flee or Repel past them -- so they are NOT
    # simmed against the party: simming them would wear it down (HP/PP) and pad the team
    # with bodies kept only to tank random grass (and push the survive-don't-wipe level
    # up), which is exactly the bloat we want gone. The team is chosen and levelled for
    # the TRAINERS (and the one mandatory scripted fight) you cannot avoid; the grass
    # contents stay visible as reference via wildPlan below. A section with NO forced
    # fight at all (the Safari Zone, Cerulean Cave) still has to be WALKED and wants its
    # HM carriers, so it falls back to its wild load rather than recommending nothing.
    if trainer_battles or scripted:
        battles = interleave_wild(stage, trainer_battles, scripted)
    else:
        battles = interleave_wild(stage, [], wild)
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
    # heal_override models a player who heals from the bag at set boundaries -- used
    # to ask "what level clears the Elite Four if you Full Restore between fights?"
    if heal_override is not None:
        heal_idx = dict(heal_override)
    # The Elite Four is the one gauntlet with no Pokemon Center: Lorelei -> Bruno
    # -> Agatha -> Lance -> Champion back to back. Between members you heal and
    # revive from the bag, so each member is fought at full HP -- run_section
    # patches HP (Member.patch_up) after every elite4 fight -- but PP is NOT
    # restored: a move's PP drains across all five. The League is therefore not a
    # heal point here (that would cut a leg and refill PP); heal_idx stays the
    # route's own heals.

    # candidate pool: everything obtainable by now, minus the other two starters'
    # entire evolution lines -- excluding only the base forms let Ivysaur and
    # Charmeleon slip into parties they could never legally join
    cands = [sp for sp in O.candidates(stage)
             if C.starter_of(sp) in (None, starter) and C.allowed(sp)
             and sp not in NO_TEAM_SPECIES
             and not C.outgrown(sp, stage, avail)
             and moon_ok(sp, stage, moon_used)]
    core = list(core or [])
    protected = frozenset(core)
    if core:
        core_roots = {C.line_root(sp) for sp in core}
        # a core section's level is the core's own (its XP ledger), so a
        # specialist is held in the form THAT level gives: one that would have
        # levelled into its next form by now is not offered
        cands = ([sp for sp in cands if C.line_root(sp) not in core_roots
                  and not P.level_evolved(sp, level, avail)]
                 if specialists else [])
    # everything this solve's result depends on beyond the code itself
    MEAS.note("section", (stage, level, sorted(cands), [b["id"] for b in battles],
                          carry, moon_used, dict(C.COMMITMENTS), O.TM_OWNER,
                          O.ALLOW_TRADE, USAGE, HM_HELD, heal_override))

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
    finalists = [sp for _, sp in screened[:SCREEN]] + core
    # The starter always gets a look, but as the form you would actually be
    # holding by now. Force-adding the BASE form put a Charmander on the
    # Champion's doorstep in the post-game, where it was picked purely as a
    # warm body once everything else had fainted.
    line = [sp for _, sp in screened if C.starter_of(sp) == starter]
    if line and not core and not any(sp in finalists for sp in line):
        finalists.append(line[0])

    own = dict(member_levels or {})
    members = {}
    for sp in finalists:
        lv = own.get(sp, level)
        pm = O.player_mon(sp, lv)
        pool = O.move_pool(sp, lv, stage)
        mvs = choose_moveset(pm, pool, opponents, badges)
        if not mvs: continue
        if stage == TAC.LEAGUE_STAGE:
            mvs = league_moveset(pm, mvs, stage, opponents, badges)
        members[sp] = (pm, mvs, pool, obey_factor(sp, lv, badges, avail))

    obj = Objective(stage, level, avail, train_roster, plan, bill_weight, own)

    # ---- greedy team build (a core is seated first and never leaves)
    team = [Member(*members[sp]) for sp in core if sp in members]
    chosen, history = [m.species for m in team], []
    held_roots = {C.line_root(sp) for sp in chosen}
    held_groups = {g for g in (C.exclusive_group(sp) for sp in chosen) if g}
    prev = run_section(team, battles, badges, heal_idx, carry=carry) if team else None
    while len(team) < MAX_TEAM:
        best, bestsp, bestres = None, None, None
        for sp, (pm, mvs, pool, ob) in members.items():
            if sp in chosen: continue
            if C.conflicts(sp, held_roots, held_groups): continue
            # a stone is spent per Moon-Stone evolution fielded; the candidate filter
            # only vetted each against the moon_used carried IN, so re-check here
            # against the ones already on THIS team, or the greedy build can stack
            # more Moon-Stone evos in one section than the run holds stones by then
            if not moon_ok(sp, stage, (moon_used or set())
                           | {m.species for m in team if m.species in MOON_EVOS}):
                continue
            trial = team + [Member(pm, mvs, pool, ob)]
            res = run_section(trial, battles, badges, heal_idx, carry=carry)
            k = pick_key(res, sp, obj, trial)
            if best is None or k < best:
                best, bestsp, bestres = k, sp, res
        if bestsp is None: break
        if prev is not None:
            # stop once the party already clears the section and another body
            # no longer meaningfully speeds it up (net of what it costs to train,
            # and with faints priced rather than vetoed under that objective)
            cleared = prev["failed"] == 0 and prev["faints"] == 0
            before = obj.total(prev, team)
            after = obj.total(bestres, team + [Member(*members[bestsp])])
            gain = (before - after) / max(1e-9, before)
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

    # ---- swap: the greedy build commits to its early picks, which win their round
    # as SOLO generalists (a bulky Surf user answering the most of the section on
    # its own) and are then never revisited once specialists join -- a League
    # party ended up Slowbro/Rhydon/Dewgong with Rhydon fighting one foe. Try every
    # legal candidate in every slot and keep strict improvements (swap_pass).
    greedy_sps = [m.species for m in team]
    swapped, _, swaps = swap_pass(team, chosen, members, battles, badges, heal_idx,
                                    carry, stage, moon_used, obj, protected)
    swapped_sps = [m.species for m in swapped]

    def finish(sps):
        """Prune, then place HMs, on fresh copies of a party; returns the finished
        (team, chosen, roots, groups, hm_plan, score, HM_HELD after)."""
        t = [Member(*members[sp]) for sp in sps]
        ch = list(sps)
        prune_team(t, ch, battles, badges, heal_idx, carry, stage, obj, protected)
        roots = {C.line_root(m.species) for m in t}
        groups = {g for g in (C.exclusive_group(m.species) for m in t) if g}
        # HM coverage: the party has to be able to CROSS the section, not just win
        # the battles in it. Done before the final run so that a move given up for
        # a field move is paid for in the turn count.
        plan, extra = assign_hms(t, stage, level, badges, avail, opponents,
                                 starter, roots, groups, ch,
                                 teleport=stage in teleport_stages(),
                                 moon_used=moon_used, skip=skip_hms, protected=protected,
                                 helpers=hm_helpers)
        for m in extra:
            t.append(m); ch.append(m.species)
        sc = score(run_section(t, battles, badges, heal_idx, carry=carry), obj, t)
        return t, ch, roots, groups, plan, sc, {k: set(v) for k, v in HM_HELD.items()}

    # The swap pass scores battles only, so it can trade away the one member that
    # could carry Cut; assign_hms then evicts someone vital for a carrier (a stage
    # went from 0 unanswered to a 43-opponent wipe). Finish BOTH parties the same
    # way and keep the better finished one, so swapping can never make it worse.
    # assign_hms records HMs a species is stuck with in HM_HELD, so each finish
    # starts from the same snapshot and the winner's state is the one kept.
    snap = {k: set(v) for k, v in HM_HELD.items()}
    fin_g = finish(greedy_sps)
    if swapped_sps != greedy_sps:
        HM_HELD.clear(); HM_HELD.update({k: set(v) for k, v in snap.items()})
        fin_s = finish(swapped_sps)
        use_s = fin_s[5] < fin_g[5]
    else:
        use_s = False
    team, chosen, held_roots, held_groups, hm_plan, _sc, held = fin_s if use_s else fin_g
    HM_HELD.clear(); HM_HELD.update(held)
    if use_s:
        history.extend(swaps)
    final = run_section(team, battles, badges, heal_idx, tm_value, carry=carry,
                        stage=stage)

    # ---- bench: strong candidates that didn't make the cut
    bench = []
    for sp, (pm, mvs, pool, ob) in members.items():
        if sp in chosen: continue
        res = run_section([Member(pm, mvs, pool, ob)], battles, badges,
                          heal_idx, carry=carry)
        if res["failed"]: continue      # can't clear it, so not an alternative
        # ranked the way the party is chosen: battle turns plus the training bill
        bench.append(((res["faints"], round(res["turns"] + obj.member(sp), 2),
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
        "objective": "training" if train_roster is not None else "turns",
        "training": training_bill(final["team"], level, avail, train_roster, own),
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
        # expected Full Restores / potions the trainers spend on this party
        "foeItemUses": round(final.get("itemUses", 0.0), 2),
        # League only: the player's bag spend (tactics.Kit.ledger)
        "leagueKit": final.get("leagueKit"),
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
                for p in hm_plan if p["move"] != TELE_MOVE],
        # Teleport is a field move, not an HM (level-up, no item/badge/obstacle),
        # so it's reported on its own -- who on the party carries it for the legs
        # the route teleports on. None when the party couldn't spare a slot.
        "teleport": next(({"name": E.MOVES[TELE_MOVE]["name"], "by": p["by"],
                           "species": p["species"], "how": p["how"]}
                          for p in hm_plan
                          if p["move"] == TELE_MOVE and p["by"]), None),
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
                 "SPECIES_PORYGON",
                 "SPECIES_SCYTHER" if E.VERSION == "fr" else "SPECIES_PINSIR"],
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

# ------------------------------------------------------------------ parallel solve
# The six (starter, mode) solves are independent, so each runs in its own forked
# worker. The big read-only inputs (the per-stage encounter graph, the availability
# table) are shared through these module globals — inherited across the fork rather
# than pickled per task. Mode-level state (trade flag, commitments) is set in the
# parent before the pool forks, so every worker inherits the right snapshot;
# per-starter state (usage, TM plan) is passed in and applied inside the worker.
# ProcessPoolExecutor.map preserves input order and each solve is deterministic, so
# the parallel result is identical to the serial one.
_SECTIONS_GRAPH = None
_AVAIL_TABLE = None

def _set_solve_inputs(sections, avail):
    global _SECTIONS_GRAPH, _AVAIL_TABLE
    _SECTIONS_GRAPH, _AVAIL_TABLE = sections, avail

def _solve_run(starter, tm_value, roster=None, plan=None):
    """Every stage for one starter, threading carry-over, the Moon-Stone budget
    and (least-training objective) the Roster of who is trained to what level.

    Wipe fallback (training objective only): a party chosen to save training can
    be leaner, and at a stage that starts without a heal (cold_stage_starts) the
    next section inherits its drained HP/PP -- LeafGreen/Squirtle's stage-30 party
    left Victory Road with two unanswerable foes. When a stage wipes, re-solve it
    ranking by battles alone (bill_weight 0, still billed); if it still wipes and
    starts cold, also re-solve the stage before it that way and keep whichever
    pair leaves fewer unanswered foes. Least training therefore never costs a
    wipe the fewest-turns rule would avoid at the same point."""
    result = {}
    state = {"carry": None, "moon": set()}
    stages = sorted(st["id"] for st in P.STAGES if st["id"] <= P.ROUTE_MAX_STAGE)
    snaps = {}

    def snapshot():
        return (state["carry"], set(state["moon"]),
                roster.copy() if roster is not None else None,
                {k: set(v) for k, v in HM_HELD.items()})

    def restore(sn):
        state["carry"], state["moon"] = sn[0], set(sn[1])
        if roster is not None:
            roster.last, roster.kept = dict(sn[2].last), set(sn[2].kept)
        HM_HELD.clear(); HM_HELD.update({k: set(v) for k, v in sn[3].items()})

    def run(stage, weight):
        res = solve_section(
            stage, _SECTIONS_GRAPH.get(stage, []), starter, _AVAIL_TABLE,
            tm_value, carry=state["carry"] if stage in cold_stage_starts() else None,
            moon_used=state["moon"], train_roster=roster, plan=plan, bill_weight=weight)
        state["carry"] = (res or {}).get("carryOut") or state["carry"]
        team = [t["species"] for t in (res or {}).get("team", [])]
        state["moon"].update(sp for sp in team if sp in MOON_EVOS)
        if roster is not None and res:
            roster.field(team, res["level"])
        return res

    def unans(*rs):
        return sum((r or {}).get("unanswered", 0) for r in rs)

    # stop at the routing cap -- the post-game stages are dropped while they're
    # switched off (progression.ROUTE_MAX_STAGE), so there's no route for them to
    # build a team against.
    for i, stage in enumerate(stages):
        snaps[stage] = snapshot()
        result[stage] = run(stage, 1.0)
        if roster is None or not unans(result[stage]):
            continue
        restore(snaps[stage])
        result[stage] = run(stage, 0.0)
        if not unans(result[stage]) or i == 0 or stage not in cold_stage_starts():
            continue
        prev = stages[i - 1]
        kept = (result[prev], result[stage], snapshot())
        restore(snaps[prev])
        new_prev = run(prev, 0.0)
        sn = snapshot()
        new_stage = run(stage, 1.0)
        if unans(new_stage):
            restore(sn)
            new_stage = run(stage, 0.0)
        if unans(new_prev, new_stage) < unans(kept[0], kept[1]):
            result[prev], result[stage] = new_prev, new_stage
        else:
            result[prev], result[stage] = kept[0], kept[1]
            restore(kept[2])
    return result

def run_plan(result):
    """line root -> the stages a solved run fields it in (the look-ahead input)."""
    plan = defaultdict(list)
    for stage, sec in result.items():
        for t in (sec or {}).get("team", []):
            plan[C.line_root(t["species"])].append(stage)
    return dict(plan)

def run_cost(result):
    """A whole run under the training objective, ranked the way a section's
    party is: wipes, then faints, then battle turns + the (undiscounted)
    training bill."""
    secs = [sec for sec in result.values() if sec]
    return (sum(sec.get("unanswered", 0) for sec in secs),
            sum(sec.get("faints", 0) for sec in secs),
            round(sum(sec.get("turns", 0.0) + sum(b["turns"] for b in sec.get("training", []))
                      for sec in secs), 1))

def _solve_all_stages(starter, tm_value):
    """One starter's run under OBJECTIVE. Training: a myopic solve, then up to
    LOOKAHEAD_PASSES-1 re-solves that each read the previous run's plan; the
    cheapest run wins, and the look-ahead stops at the first pass that doesn't
    beat the best so far (each is a full pass-2 solve).

    Pass 1 (tm_value collecting TM demand) always runs the fewest-turns rule: it
    decides the run's one-time choices (which fossil, which Eevee, who gets each
    single-use TM) on even footing, and those must not drift with the party
    objective. Under the training rule they did -- FireRed/Bulbasaur committed
    away the Omanyte that alone answers the stage-30 rival's L53 Charizard, and
    the run wiped there. So least training changes only who is in each party."""
    if OBJECTIVE != "training" or tm_value is not None:
        return _solve_run(starter, tm_value)
    best = best_cost = best_held = None
    plan = None
    for _ in range(LOOKAHEAD_PASSES):
        reset_hm_held()
        res = _solve_run(starter, None, roster=TC.Roster(), plan=plan)
        c = run_cost(res)
        if best is not None and c >= best_cost:
            break
        best, best_cost = res, c
        best_held = {k: set(v) for k, v in HM_HELD.items()}
        nxt = run_plan(res)
        if nxt == plan: break
        plan = nxt
    HM_HELD.clear(); HM_HELD.update(best_held)
    return best

def reset_solve_caches():
    """Clear the per-run memoization that must not leak between independent starter
    solves. Pass 1 compares each starter on even footing and pass 2 gives each its
    own TM plan, so a fight profile or move pool cached under one starter must not
    be reused for another. Forked workers start clean; calling this keeps a
    shared-process solve (single worker / a serial run) identically correct."""
    _profile_cache.clear()
    _threat_cache.clear()
    _STOCK.clear()
    O.reset_caches()

def _worker_pass1(args):
    """Pass 1 for one (starter, trade-mode), options on even footing (empty
    commitments, no TM plan). The trade flag is set HERE rather than in the parent
    so both modes' pass-1 solves can share one wide fork pool; each worker's own
    copy of the trade flag / caches stays isolated. Returns the per-move TM value."""
    starter, trading = args
    O.set_allow_trade(trading)
    C.set_commitments({})
    O.set_tm_plan(SCARCE, None)
    reset_solve_caches()
    reset_hm_held()
    tm_value = defaultdict(float)
    MEAS.reset()
    result = _solve_all_stages(starter, tm_value)
    MEAS.dump(f"p1_{starter}_{'yes' if trading else 'no'}")
    return trading, starter, result, dict(tm_value)

def _worker_pass2(args):
    """Pass 2 for one (starter, trade-mode), with that mode's commitments locked
    and this starter's own usage + TM plan applied. All mode state is set inside
    the worker so both modes run in one wide pool, matching the serial order."""
    starter, trading, picks, counts, tm_value = args
    O.set_allow_trade(trading)
    C.set_commitments(picks)
    reset_solve_caches()
    set_usage(counts)
    owners, plan = assign_tms(tm_value, SCARCE)
    O.set_tm_plan(SCARCE, owners)
    reset_hm_held()
    MEAS.reset()
    result = _solve_all_stages(starter, None)
    MEAS.dump(f"p2_{starter}_{'yes' if trading else 'no'}")
    build_party_plans(result, _AVAIL_TABLE)
    return trading, starter, result, plan

def _core_mode(trading, picks, counts):
    O.set_allow_trade(trading)
    C.set_commitments(picks)
    reset_solve_caches()
    set_usage(counts)
    O.set_tm_plan(SCARCE, None)
    reset_hm_held()

def _worker_core_pick(args):
    """The core approach, step 1, for one (starter, trade-mode): the League-viable
    sixes offered to the player (core.choose_cores, every TM open so candidates
    are judged on even footing)."""
    import core as K
    K.bind(sys.modules[__name__])
    starter, trading, picks, counts, _tm_value = args
    _core_mode(trading, picks, counts)
    return trading, starter, K.choose_cores(starter, _AVAIL_TABLE)

def _league_log(result):
    """The League section's battle log of a solved run (stage keys may be ints or,
    once through JSON, strings)."""
    sec = result.get(TAC.LEAGUE_STAGE) or result.get(str(TAC.LEAGUE_STAGE)) or {}
    return sec.get("log", [])

def _worker_core_solve(args):
    """Step 2, one offered six: its own one-time choices locked, the single-use
    TMs spent on the six alone, then the run solved with the six seated
    (core.solve_core_run)."""
    import core as K
    K.bind(sys.modules[__name__])
    starter, trading, picks, counts, tm_value, idx, team = args
    own = K.core_commitments(team, picks)
    _core_mode(trading, own, counts)
    roots = {C.line_root(t) for t in team}
    # The TMs are spent on what the SIX gains from them, measured on its own run
    # with every TM open. Pass 1's demand only counts species the maximalist
    # fielded, so a core member it never used (an Electrode) had no claim on
    # Thunderbolt and fought the League with Secret Power.
    measured = defaultdict(float)
    K.solve_core_run(starter, team, _AVAIL_TABLE, tm_value=measured)
    _core_mode(trading, own, counts)
    owners, plan = assign_tms({k: v for k, v in measured.items()
                               if C.line_root(k[1]) in roots}, SCARCE)
    O.set_tm_plan(SCARCE, owners)
    reset_solve_caches()
    reset_hm_held()
    result = K.solve_core_run(starter, team, _AVAIL_TABLE)
    build_party_plans(result, _AVAIL_TABLE)
    return trading, starter, idx, {"team": team, "picks": own, "result": result,
                                   "tmPlan": plan}

def _run_starters(fn, arglist):
    """Fan the per-starter solves across forked workers, results in input order.
    LGMAX_WORKERS caps the pool (1 forces a single worker, e.g. for debugging)."""
    import concurrent.futures as cf, multiprocessing as mp
    # default to the whole box: both trade modes' solves now share one pool, so
    # there are up to 2*len(STARTERS) independent tasks to place at once.
    workers = int(os.environ.get("LGMAX_WORKERS", os.cpu_count() or 1)) or 1
    with cf.ProcessPoolExecutor(max_workers=max(1, min(workers, len(arglist))),
                                mp_context=mp.get_context("fork")) as ex:
        return list(ex.map(fn, arglist))

def _calibrate_levels(args2):
    """Forward sweep for the recommended-level curve now that grass wilds are out of
    the sim: the LOWEST level per stage at which EVERY (starter, trade-mode) still
    clears it without a full wipe. Each context threads its own carry + Moon-Stone
    budget forward exactly as the real solve does, and a stage never falls below the
    running peak (you don't de-level). Runs serially in the parent -- it is one-time,
    invoked with LGMAX_CALIBRATE=1 -- and writes data/levelCurve.json for review
    before the numbers are baked into progression.STAGES by hand."""
    ctxs = [{"starter": s, "trading": tr, "picks": pk, "counts": ct, "tv": tv,
             "carry": None, "moon": set()} for (s, tr, pk, ct, tv) in args2]
    cold = cold_stage_starts()
    def solve_at(c, stage, L):
        O.set_allow_trade(c["trading"]); C.set_commitments(c["picks"]); set_usage(c["counts"])
        owners, _ = assign_tms(c["tv"], SCARCE); O.set_tm_plan(SCARCE, owners)
        reset_solve_caches(); reset_hm_held()
        return solve_section(stage, _SECTIONS_GRAPH.get(stage, []), c["starter"], _AVAIL_TABLE,
                             None, carry=c["carry"] if stage in cold else None,
                             moon_used=c["moon"], level_override=L)
    # search up to a high ceiling, not the current baked level: once the curve is
    # lowered and re-fed into pass 1, a stage's true floor can land ABOVE its (now
    # lowered) baked value, so capping at baked would silently under-level it. Run
    # this to a fixpoint -- bake, re-run, repeat -- until the no-wipe guard is green.
    CEIL = 70
    # The recommended level is the lowest at which EVERY context still clears the
    # stage without a full party wipe: faints are fine (you heal/revive from the bag
    # between fights), an "unanswered" opponent -- one that NO living member can beat,
    # i.e. you have been wiped down to nothing that answers it -- is not. In the
    # League you heal and revive from the bag between members (run_section patches
    # HP after each elite4 fight) but PP carries across all five.
    # LGMAX_CALIBRATE_FROM=<stage> keeps every earlier stage at its baked level
    # (still solved once, to thread carry + Moon budget forward) and searches only
    # from that stage on -- for re-tuning the endgame without re-sweeping the run.
    start = int(os.environ.get("LGMAX_CALIBRATE_FROM", "0"))
    curve, prev = {}, 0
    for stage in sorted(st["id"] for st in P.STAGES if st["id"] <= P.ROUTE_MAX_STAGE):
        baked = P.STAGE_BY_ID[stage]["level"]
        chosen = baked if stage < start else next(
            (L for L in range(max(prev, 2), CEIL + 1)
             if all((solve_at(c, stage, L) or {}).get("unanswered", 0) == 0
                    for c in ctxs)), baked)
        for c in ctxs:          # thread carry + Moon budget forward at the chosen level
            res = solve_at(c, stage, chosen)
            c["carry"] = (res or {}).get("carryOut") or c["carry"]
            for t in (res or {}).get("team", []):
                if t["species"] in MOON_EVOS:
                    c["moon"].add(t["species"])
        curve[stage] = chosen
        prev = chosen
        print(f"  stage {stage:2}: baked {baked} -> calibrated {chosen}", flush=True)
    with open(f"{OUT}/levelCurve.json", "w") as f:
        json.dump(curve, f)
    print("wrote data/levelCurve.json", flush=True)

# ------------------------------------------------------------------ run
if __name__ == "__main__":
    import time
    graph = json.load(open(f"{OUT}/encounters.json"))
    sections = build_sections(graph)
    # the evolution-timing plan (evo_timing.py): held-back evolutions move when
    # their forms can be had and which pre-evolution moves they keep
    held = P.use_evo_timing()
    O.refresh_availability()
    print(f"evolution timing: {len(held)} held-back evolution(s)", flush=True)
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
    # Solved twice over (no-trade / trade), each in two passes; the trade modes
    # share the same sections graph and availability, so all 2*len(STARTERS) pass-1
    # solves go through one wide fork pool, and likewise pass 2. Results come back
    # in input order, so the output is byte-identical to the old serial-mode loop.
    MODES = (("no", False), ("yes", True))
    mode_of = {tr: m for m, tr in MODES}
    _set_solve_inputs(sections, avail)   # shared across modes -> set once, pre-fork
    O.set_tm_plan(SCARCE, None)

    # ---- pass 1 (both modes at once): options on even footing ----
    print("\n=== pass 1: both trade modes, all starters", flush=True)
    args1 = [(st, tr) for _m, tr in MODES for st in STARTERS]
    res1 = _run_starters(_worker_pass1, args1)
    p1 = {m: {} for m, _ in MODES}
    tm_value = {m: {} for m, _ in MODES}
    for tr, st, result, tv in res1:
        p1[mode_of[tr]][st] = result
        tm_value[mode_of[tr]][st] = tv
    print(f"  pass 1 done ({time.time()-t0:.0f}s)", flush=True)

    # ---- per-mode choices + commitments (cheap, serial in the parent) ----
    picks_by_mode, args2 = {}, []
    for mode, trading in MODES:
        O.set_allow_trade(trading)     # choice evaluation reads the candidate pool
        result = p1[mode]
        totals = {k: sum((v[st] or {}).get("turns", 0) or 0 for st in v)
                  for k, v in result.items()}
        ref_starter = min(totals, key=totals.get)
        print(f"  [{mode}] section turns by starter:",
              {k: round(v, 1) for k, v in sorted(totals.items(), key=lambda kv: kv[1])})
        choices = evaluate_choices(result, avail, sections, ref_starter)
        picks = {}
        for grp in choices:
            if grp["id"] in C.COMMIT_GROUPS:
                picks[grp["id"]] = grp["rows"][0]["species"]
        picks_by_mode[mode] = picks
        all_choices[mode] = choices
        all_commits[mode] = {g: E.SPECIES[sp]["name"] for g, sp in picks.items()}
        print(f"  [{mode}] commitments:", all_commits[mode], flush=True)
        for starter in STARTERS:
            counts = collections.Counter()
            for sec in (result[starter] or {}).values():
                if not sec: continue
                for t in sec["team"]:
                    counts[t["species"]] += 1
            args2.append((starter, trading, picks, counts, tm_value[mode][starter]))

    if os.environ.get("LGMAX_CALIBRATE"):
        print("\n=== calibrating level curve (trainers only, both modes) ===", flush=True)
        _calibrate_levels(args2)
        sys.exit(0)

    # ---- pass 2 (both modes at once): re-solve with commitments + TM plans ----
    res2 = _run_starters(_worker_pass2, args2)
    p2 = {m: {} for m, _ in MODES}
    tm_plans_by_mode = {m: {} for m, _ in MODES}
    for tr, st, result, plan in res2:
        p2[mode_of[tr]][st] = result
        tm_plans_by_mode[mode_of[tr]][st] = plan
    O.set_tm_plan(SCARCE, None)
    for mode, trading in MODES:
        result, tm_plans = p2[mode], tm_plans_by_mode[mode]
        print(f"  [{mode}] re-solved — "
              f"{ {s: sum(len(p['teach']) for p in tm_plans[s]) for s in STARTERS} } "
              f"single-use TMs spent ({time.time()-t0:.0f}s)", flush=True)
        for per_stage in result.values():
            keep_lists(per_stage, avail)
            for sec in per_stage.values():
                if not sec: continue
                for log in sec["log"]:
                    for step in log["steps"]:
                        for k in ("_moveConst", "_by", "_oppIdx"): step.pop(k, None)
        for per_stage in result.values():
            price_dex_evos(per_stage, avail)
        all_sections[mode] = result
        all_plans[mode] = tm_plans
        if mode == "no":
            with open(f"{OUT}/commitments.json", "w") as f:
                json.dump({"picks": picks_by_mode["no"], "labels": all_commits["no"]}, f)
    # ---- pass 3 (core approach): a few League-viable sixes per run, each solved ----
    core_options = {m: {} for m, _ in MODES}
    if "core" in os.environ.get("LGMAX_APPROACHES", "max core").split():
        print("\n=== pass 3: core approach, both trade modes, all starters", flush=True)
        offered = {(tr, st): teams for tr, st, teams in _run_starters(_worker_core_pick, args2)}
        args3 = [a + (i, team) for a in args2
                 for i, team in enumerate(offered[(a[1], a[0])])]
        for tr, st, idx, opt in _run_starters(_worker_core_solve, args3):
            mode, result = mode_of[tr], opt["result"]
            keep_lists(result, avail)
            price_dex_evos(result, avail)
            for sec in result.values():
                if not sec: continue
                for log in sec["log"]:
                    for step in log["steps"]:
                        for k in ("_moveConst", "_by", "_oppIdx"): step.pop(k, None)
            live = [sec for sec in result.values() if sec]
            opt["summary"] = {
                "lines": len({C.line_root(t["species"]) for sec in live for t in sec["team"]}),
                "training": round(sum(b["turns"] for sec in live for b in sec.get("training", []))),
                "battle": round(sum(sec["turns"] for sec in live)),
                "faints": sum(sec["faints"] for sec in live),
                "unanswered": sum(sec["unanswered"] for sec in live),
                "leagueLevel": max((sec["level"] for sec in live), default=0),
                # sections where an HM rides on someone outside the six
                "hmHelped": sum(1 for sec in live
                                if any(h.get("species") and C.line_root(h["species"])
                                       not in {C.line_root(t) for t in opt["team"]}
                                       for h in sec.get("hms", [])))}
            core_options[mode].setdefault(st, []).append((idx, opt))
            print(f"  [{mode}] {st} #{idx}: {[E.SPECIES[t]['name'] for t in opt['team']]} "
                  f"{opt['summary']} ({time.time()-t0:.0f}s)", flush=True)
        # A six the League sim liked can still meet a section its run can't clear
        # (a Bulbasaur six wiped on Routes 12-15): offering it would hand the
        # player a dead end, so it is dropped and the next spare offered instead.
        import core as K
        for mode in core_options:
            for st in core_options[mode]:
                ranked = [o for _i, o in sorted(core_options[mode][st], key=lambda io: io[0])]
                clean = [o for o in ranked if o["summary"]["unanswered"] == 0]
                dropped = len(ranked) - len(clean)
                if dropped:
                    print(f"  [{mode}] {st}: dropped {dropped} six(es) whose run wipes", flush=True)
                # ... and one carrying a passenger: a line the League (at the run's
                # real levels) never sends in, which the pick-level screen missed
                busy = [o for o in clean
                        if not K.league_idle(o["team"], _league_log(o["result"]))]
                if len(busy) < len(clean):
                    print(f"  [{mode}] {st}: dropped {len(clean) - len(busy)} six(es) "
                          f"with a line the League never uses", flush=True)
                clean = busy
                core_options[mode][st] = clean[:K.CORE_OPTIONS]
    O.set_allow_trade(False)
    C.load_commitments(f"{OUT}/commitments.json")
    with open(f"{OUT}/sections.json", "w") as f:
        json.dump({"sections": all_sections, "choices": all_choices,
                   "core": {"options": core_options},
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
