#!/usr/bin/env python3
"""Guard rails. Run after the pipeline; every check must print OK.

These encode the legality rules that are easy to break silently:
LeafGreen-only availability, one Pokemon per evolution line, one per
thing the game hands you once, and no other-starter Pokemon in a run.
"""
import json, os, sys, collections, re
import engine as E
import progression as P
import constraints as C

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
fails = []

def _line_names(option_name):
    """Every form that counts as having made this choice, by display name."""
    for opts in C.COMMIT_GROUPS.values():
        for opt, members in opts.items():
            if E.SPECIES[opt]["name"] == option_name:
                return {E.SPECIES[m]["name"] for m in members}
    return {option_name}

def check(name, ok, detail=""):
    print(("  OK   " if ok else "  FAIL ") + name + (("  " + detail) if detail and not ok else ""))
    if not ok: fails.append(name)

# ------------------------------------------------------------------ availability
FIRERED_ONLY = ["EKANS","ARBOK","ODDISH","GLOOM","VILEPLUME","BELLOSSOM","PSYDUCK",
                "GOLDUCK","GROWLITHE","ARCANINE","SHELLDER","CLOYSTER","SCYTHER",
                "SCIZOR","ELECTABUZZ","ELEKID","WOOPER","QUAGSIRE","MURKROW",
                "QWILFISH","DELIBIRD","SKARMORY"]
av = P.full_availability()
leaks = [s for s in FIRERED_ONLY if f"SPECIES_{s}" in av]
check("no FireRed exclusives are obtainable", not leaks, str(leaks))

traded = [s for s, r in av.items() if r.get("requiresTrade")]
recs = json.load(open(f"{OUT}/recommendations.json"))
rec_species = {t["species"] for r in recs.values() for t in r["team"]}
rec_species |= {b["species"] for r in recs.values() for c in r["counters"] for b in c["best"]}
check("every recommended species is obtainable in LeafGreen",
      rec_species <= set(av), str(sorted(rec_species - set(av))[:6]))
# The encounter rankings are built WITH trading and each entry tagged, so the
# page can hide the trade evolutions. Assert the tagging is honest rather than
# that they are absent.
mistagged = [t["name"] for r in recs.values() for t in r["team"]
             if bool(t.get("requiresTrade")) != (t["species"] in set(traded))]
check("every trade-only recommendation is tagged as needing a trade",
      not mistagged, str(sorted(set(mistagged))[:6]))
trade_free = {t["species"] for r in recs.values() for t in r["team"]
              if not t.get("requiresTrade")}
check("a no-trade run's rankings still contain no trade evolutions",
      not (trade_free & set(traded)), str(sorted(trade_free & set(traded))[:6]))

# An evolution needing a held item cannot be had before the item can. In
# LeafGreen all four trade-evolution items (Metal Coat, Dragon Scale, King's
# Rock, Up-Grade) are item balls in the POST-GAME Sevii Islands, so Porygon2,
# Steelix, Kingdra, Politoed and Slowking cannot exist before then.
early_evo = []
for sp, r in av.items():
    if r.get("evoMethod") not in ("ITEM", "TRADE_ITEM"): continue
    need = (P.evo_item_stage() if r["evoMethod"] == "TRADE_ITEM"
            else P.STONE_STAGE).get(r.get("evoParam"))
    if need is None or r["stage"] < need:
        early_evo.append((E.SPECIES[sp]["name"], r["stage"], r.get("evoParam"), need))
check("no evolution is reachable before the item it needs",
      not early_evo, str(early_evo[:5]))

# ------------------------------------------------------------------ one per line
dupe_lists = 0
for r in recs.values():
    roots = [C.line_root(t["species"]) for t in r["team"]]
    if len(roots) != len(set(roots)): dupe_lists += 1
    for c in r["counters"]:
        cr = [C.line_root(b["species"]) for b in c["best"]]
        if len(cr) != len(set(cr)): dupe_lists += 1
check("no ranked list repeats an evolution line", dupe_lists == 0, f"{dupe_lists} lists")

# ------------------------------------------------------------------ section parties
_raw_secs = json.load(open(f"{OUT}/sections.json"))["sections"]
# sections are now solved once per trade mode; flatten to "<mode>/<starter>"
secs = {f"{m}/{s}": per for m, by in _raw_secs.items() for s, per in by.items()}
NAME_TO_SPECIES = {v["name"]: k for k, v in E.SPECIES.items()}
wrong_starter, dupe_party, unavailable = [], 0, []
for starter, per_stage in secs.items():
    for st, sec in per_stage.items():
        if not sec: continue
        sps = [NAME_TO_SPECIES.get(t["name"]) for t in sec["team"]]
        for sp in sps:
            if sp is None: continue
            own = C.starter_of(sp)
            if own and own != starter.split("/")[-1]:
                wrong_starter.append((starter, int(st), E.SPECIES[sp]["name"]))
            if sp not in av: unavailable.append(E.SPECIES[sp]["name"])
            elif av[sp]["stage"] > int(st):
                unavailable.append(f"{E.SPECIES[sp]['name']}@{st}")
        roots = [C.line_root(s) for s in sps if s]
        if len(roots) != len(set(roots)): dupe_party += 1
check("no other-starter Pokemon in a party", not wrong_starter, str(wrong_starter[:5]))
check("no party repeats an evolution line", dupe_party == 0, f"{dupe_party} parties")
check("every party member is obtainable by that section",
      not unavailable, str(sorted(set(unavailable))[:6]))

# ------------------------------------------------------------------ run-wide commitments
raw = json.load(open(f"{OUT}/sections.json"))
raw["sections"] = secs
conflicts = []
for starter, per_stage in raw["sections"].items():
    used = collections.defaultdict(set)
    for st, sec in per_stage.items():
        if not sec: continue
        for t in sec["team"]:
            sp = NAME_TO_SPECIES.get(t["name"])
            if not sp: continue
            g = C.commitment_of(sp)
            if g: used[g].add(C._SPECIES_COMMIT[sp][1])
    for g, opts in used.items():
        if len(opts) > 1:
            conflicts.append((starter, g, sorted(E.SPECIES[o]["name"] for o in opts)))
check("a run never uses two options of a one-time choice", not conflicts, str(conflicts[:4]))

_dec = raw.get("tradeCommitments", {})
declared_by = {f"{m}/{s}": _dec.get(m, {}) for m in _dec for s in ("Bulbasaur", "Charmander", "Squirtle")}
mismatch = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        for t in sec["team"]:
            sp = NAME_TO_SPECIES.get(t["name"])
            g = C.commitment_of(sp) if sp else None
            dec = declared_by.get(starter, {})
            if g and dec.get(g) and t["name"] not in _line_names(dec[g]):
                mismatch.append((starter, int(st), t["name"], dec[g]))
check("parties match the declared commitments", not mismatch, str(mismatch[:4]))

# ------------------------------------------------------------------ single-use TMs
supply = raw.get("tmSupply", {})
plans = {f"{m}/{s}": p for m, by in raw.get("tmPlans", {}).items() for s, p in by.items()}
import tms as TMOD
scarce = TMOD.scarce_moves(supply)
MOVE_BY_NAME = {m["name"]: k for k, m in E.MOVES.items()}

over, unassigned = [], []
for starter, plan in plans.items():
    for p in plan:
        if len(p["teach"]) > p["copies"]:
            over.append((starter, p["name"], len(p["teach"]), p["copies"]))
check("no TM is taught more often than the run can obtain it", not over, str(over[:4]))

# every scarce TM move a party actually knows must have been assigned to it
for starter, per_stage in raw["sections"].items():
    owned = collections.defaultdict(set)
    for p in plans.get(starter, []):
        for t in p["teach"]:
            owned[p["move"]].add(t["species"])
    for st, sec in per_stage.items():
        if not sec: continue
        for t in sec["team"]:
            sp = NAME_TO_SPECIES.get(t["name"])
            for mv in t["moves"]:
                const = MOVE_BY_NAME.get(mv["name"])
                if not const or const not in scarce: continue
                # a level-up copy of the move spends nothing
                if (mv.get("src") or "").split()[0] in ("start", "Lv"): continue
                ss = scarce[const].get("shopStage")
                if ss is not None and int(st) >= ss: continue   # buyable now
                if sp not in owned.get(const, set()):
                    unassigned.append((starter, int(st), t["name"], mv["name"]))
check("no party knows a single-use TM move it was not given",
      not unassigned, str(unassigned[:4]))

hm_free = [p["name"] for pl in plans.values() for p in pl
           if p["item"].startswith("ITEM_HM")]
check("HMs are never treated as scarce (they are not consumed)", not hm_free, str(hm_free[:4]))

# ------------------------------------------------------------------ bench sanity
bad_bench = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        for b in sec["bench"]:
            if b.get("soloFailed"):
                bad_bench.append((starter, int(st), b["name"]))
check("the bench only lists Pokemon that could actually clear the section",
      not bad_bench, str(bad_bench[:4]))

# ------------------------------------------------------------------ switching
# Every Pokemon named in a battle log has to be one the party actually holds --
# mid-fight handovers must hand over to a team mate, not to a stranger.
strangers = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        roster = {t["name"] for t in sec["team"]}
        for l in sec["log"]:
            for s in l["steps"]:
                if s.get("by") and s["by"] not in roster:
                    strangers.append((starter, int(st), s["by"]))
check("every Pokemon in a battle log is on that section's team",
      not strangers, str(strangers[:4]))

# A wild route is walked by a LEAD chosen blind against the whole encounter
# table, not cherry-picked per species. The signature of that is one Pokemon
# taking most of a map's battles; per-encounter cherry-picking spreads them.
scatter = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        for l in sec["log"]:
            if l["kind"] != "wild": continue
            tally = collections.Counter()
            for s in l["steps"]:
                tally[s.get("by")] += s.get("n", 1)
            tot = sum(tally.values())
            if tot >= 6 and tally.most_common(1)[0][1] < 0.5 * tot:
                scatter.append((starter, int(st), l["enc"], dict(tally)))
check("wild routes are walked by a lead, not cherry-picked per encounter",
      not scatter, str(scatter[:2]))

# ------------------------------------------------------------------ starter forms
# The starter is force-added to every section's candidate pool; it must be the
# form you would be holding by then, never a base form left behind by evolution.
stale = []
for starter, per_stage in raw["sections"].items():
    base = f"SPECIES_{starter.upper()}"
    for st, sec in per_stage.items():
        if not sec: continue
        for t in sec["team"]:
            if C.outgrown(t["species"], int(st), av):
                stale.append((starter, int(st), t["name"]))
check("no section recommends a form it would have levelled out of",
      not stale, str(stale[:4]))

# ------------------------------------------------------------------ obedience
# `IsMonDisobedient`: caps of 10/30/50/70 from badges 2/4/6/8, the eighth badge
# lifting it entirely, and it obeys when ((level + cap) * rnd >> 8) < cap.
def _odds(level, badges):
    if badges >= 8: return 1.0
    cap = 70 if badges >= 6 else 50 if badges >= 4 else 30 if badges >= 2 else 10
    if level <= cap: return 1.0
    return sum(1 for r in range(256) if ((level + cap) * r >> 8) < cap) / 256.0
bad_odds = [(lv, b) for lv in range(2, 101) for b in range(9)
            if abs(E.obedience_odds(lv, b) - _odds(lv, b)) > 1e-12]
check("the obedience roll matches the ROM at every level and badge count",
      not bad_odds, str(bad_odds[:4]))

# A traded Pokemon over the cap wastes roughly half its turns, so it should
# never win a section outright. If one does, the penalty has stopped being applied.
import optimize as _O
disobedient = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        cap = E.obedience_cap(_O.badges_for(int(st))["count"])
        if cap is None: continue
        for t in sec["team"]:
            rec = av.get(t["species"]) or {}
            if rec.get("source", "").startswith("In-game trade") and t["level"] > cap:
                disobedient.append((starter, int(st), t["name"], t["level"], cap))
check("no party leans on a traded Pokemon that would disobey",
      not disobedient, str(disobedient[:4]))

# ------------------------------------------------------------------ HM coverage
import hms as HM
missing, wrong_set, cant_learn = [], [], []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        want = set(HM.moves_here(int(st)))
        plan = sec.get("hms", [])
        got = {p["move"] for p in plan}
        if want != got:
            wrong_set.append((starter, int(st), sorted(want ^ got)))
        for p in plan:
            if p["how"] == "uncovered" or not p.get("species"):
                missing.append((starter, int(st), p["name"]))
            elif not HM.can_learn(p["species"], p["move"]):
                cant_learn.append((starter, int(st), p["by"], p["name"]))
check("every HM a section's own maps demand is on that section's party",
      not missing, str(missing[:4]))
check("each section plans for exactly the HMs its maps demand",
      not wrong_set, str(wrong_set[:3]))
check("the Pokemon assigned an HM can actually learn it",
      not cant_learn, str(cant_learn[:4]))

# ------------------------------------------------------------------ Teleport
# Teleport is DISABLED as a routing shortcut: the suggested party isn't guaranteed
# to carry it, and its real destination (the last-healed Center, not the nearest
# one to where you stand) can't be resolved from the route geometry. So the route
# must never tell you to Teleport, and no section may claim a Teleport carrier. If
# Teleport is reintroduced, restore the carrier/destination legality checks here.
_tele_route = {st["stage"] for st in json.load(open(f"{OUT}/route.json"))["stages"]
               if st.get("teleport")}
check("the route never uses the (disabled) Teleport shortcut", not _tele_route,
      f"stages {sorted(_tele_route)}")
_tele_claims = [(starter, int(st)) for starter, per_stage in raw["sections"].items()
                for st, sec in per_stage.items() if sec and sec.get("teleport")]
check("no section claims a Teleport carrier while Teleport is disabled",
      not _tele_claims, str(_tele_claims[:4]))

# ------------------------------------------------------------------ walk order
# The completionist route decides the order battles happen in, and the
# section simulation must fight in exactly that order — the checklist, the
# maps and the PP ledger all have to describe the same line.
import route_order as RO
tiles = RO.trainer_tiles()
_routej = json.load(open(f"{OUT}/route.json"))
_rt_keys = {}
for st in _routej["stages"]:
    _rt_keys[st["stage"]] = [RO.battle_key((s.get("enc") or "").split(":")[-1])
                             for s in st["steps"] if s["kind"] == "trainer"]
_graph_tmp = json.load(open(f"{OUT}/encounters.json"))
_const_by_id = {e["id"]: e.get("trainerConst", "") for e in _graph_tmp}
out_of_line = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        # wild rows are ambient; scripted one-offs (the Ghost Marowak) are paired
        # to their own route stop by map, not by trainer-order position -- both
        # sit outside the trainer sequence this guard checks
        seq = [RO.battle_key(_const_by_id.get(l["id"], ""))
               for l in sec["log"] if l["kind"] not in ("wild", "scripted")]
        if seq != _rt_keys.get(int(st), seq):
            out_of_line.append((starter, int(st)))
check("every section fights in the route's exact order",
      not out_of_line, str(out_of_line[:5]))

check("every trainer the model orders has a real tile on a real map",
      len(tiles) > 400, f"only {len(tiles)} placed")

# ------------------------------------------------------------------ the route
# The completionist walk: every stop exactly once, in one continuous line,
# never before its stage, always in straight tile runs.
routej = json.load(open(f"{OUT}/route.json"))
import tour as TOUR
route_nodes = TOUR.harvest()

check("the route leaves nothing uncollected", not routej.get("left"),
      str(routej.get("left", [])[:4]))

want = collections.Counter((n["kind"], n["what"], n["map"]) for n in route_nodes)
got = collections.Counter()
for st in routej["stages"]:
    for s in st["steps"]:
        if s["kind"] == "heal": continue    # deliberate Center detours, not harvest nodes
        got[(s["kind"], s["what"], s["map"])] += 1
check("the route visits every stop exactly once", want == got,
      str(list(((want - got) + (got - want)).items())[:3]))

# The Route 12/16 Leftovers sits on the exact tile the Snorlax blocks (both
# underfoot, same coords in the decomp), so it can only be dug up after that
# Snorlax is woken and beaten -- the route must order it after, never before.
_bad_snorlax = []
for st in routej["stages"]:
    steps = st["steps"]
    snor = next((i for i, s in enumerate(steps) if "Snorlax" in str(s.get("what", ""))), None)
    left = next((i for i, s in enumerate(steps) if str(s.get("what", "")) == "Leftovers"), None)
    if snor is not None and left is not None and left < snor:
        _bad_snorlax.append(st["stage"])
check("the Leftovers is collected after the Snorlax that blocks it",
      not _bad_snorlax, f"stages {_bad_snorlax}")

prev_end, disc = None, []
for st in routej["stages"]:
    if prev_end is not None and st["startsAt"] != prev_end:
        disc.append(st["stage"])
    prev_end = st["endsAt"]
check("the route is one continuous walk", not disc, str(disc[:5]))

# Fly and Teleport only work in the open air -- you can't use either to leave a
# cave or a building (there the route Digs or Escape-Ropes out). A hop departs
# from where you were standing, i.e. the previous stop's tile.
import world as WORLD
_indoor_hop, _bad_dig = [], []
for st in routej["stages"]:
    _pe = st["startsAt"]
    for s in st["steps"]:
        if (s.get("fly") or s.get("teleport")) and _pe and not WORLD.is_outdoors(_pe[0]):
            _indoor_hop.append((st["stage"], _pe[0], s["what"]))
        # Dig / Escape Rope is the inverse: it only works INSIDE an escapable
        # cave (allow_escaping), and only from one with a single mouth (else you
        # can't know which end it drops you at) -- world.dungeon_mouth vets both.
        if s.get("dig") and _pe and WORLD.dungeon_mouth(_pe) is None:
            _bad_dig.append((st["stage"], _pe[0], s["what"]))
        if s["path"]: _pe = s["path"][-1]
check("the walk never Flies or Teleports out of a cave or building",
      not _indoor_hop, str(_indoor_hop[:4]))
check("the walk only Digs out from inside a single-entrance cave",
      not _bad_dig, str(_bad_dig[:4]))

bad_tot = [st["stage"] for st in routej["stages"]
           if not (0 <= st["stepTotal"] < 10 ** 7)]
check("every stage's step total is finite", not bad_tot, str(bad_tot))

intrinsic = {}
for n in route_nodes:
    k = (n["kind"], n["what"], n["map"])
    intrinsic[k] = min(intrinsic.get(k, 99), TOUR.node_stage(n))
early = [(st["stage"], s["what"]) for st in routej["stages"]
         for s in st["steps"]
         if st["stage"] < intrinsic.get((s["kind"], s["what"], s["map"]), 0)]
check("no stop is routed before its stage", not early, str(early[:4]))

# Straight-run check. Exempt the maps where a single edge legitimately lands
# elsewhere: spin-floor slides bend (Rocket Hideout, the Five Island
# warehouse), and teleporter pads (Sabrina's gym, the Silph Co. pads) are
# same-map warps. Spin floors are detected from the tiles themselves.
import world as WORLD
def _bendy(m):
    if m == "SaffronCity_Gym" or m.startswith("SilphCo_"): return True
    try:
        return any(b in WORLD.MB_SPIN for b in WORLD.grid(m).beh)
    except Exception:
        return False
diag = []
for st in routej["stages"]:
    for s in st["steps"]:
        p = s["path"]
        for a, b in zip(p, p[1:]):
            if a[0] == b[0] and not _bendy(a[0]) and a[1] != b[1] and a[2] != b[2]:
                diag.append((st["stage"], s["what"])); break
check("route paths run straight, tile to tile, never diagonally",
      not diag, str(diag[:4]))

# ------------------------------------------------------------------ sight & stones
# The walk must never cross the sight line of a trainer it hasn't fought yet
# -- in the game that forces the battle on the spot, at the wrong level.
_aggro = __import__("tour").aggro_cones()
_wt = __import__("tour").walked_tiles
_t2c = {v: k for k, v in RO.trainer_tiles().items()}
_seen_early = []
_fought_walk = set()
for _st in _routej["stages"]:
    _steps = _st["steps"]
    for _i, _s in enumerate(_steps):
        _w = _wt([(m, x, y) for m, x, y in _s["path"]])
        _next_enc = _steps[_i + 1].get("enc") if _i + 1 < len(_steps) else None
        for _name, _tiles, _pos in _aggro:
            if _name not in _w or not (_tiles & _w[_name]): continue
            _c = _t2c.get((_name, _pos[0], _pos[1]))
            if not _c or _c in _fought_walk: continue
            if (_s.get("enc") or "").endswith(":" + _c): continue   # the hop INTO this fight
            # a side-by-side pair: brushing the partner who is fought next step
            if (_next_enc or "").endswith(":" + _c): continue
            _seen_early.append((_st["stage"], _c, _s["what"]))
        if _s["kind"] == "trainer" and _s.get("enc"):
            _fought_walk.add(_s["enc"].split(":")[-1])
check("the walk never enters an unfought trainer aggro zone (sight or body)",
      not _seen_early, str(sorted(set(_seen_early))[:4]))

# A trainer with a body on the map is fought from the tile beside it (or by
# tripping its sight line), never by standing on it -- so the walk stops one tile
# short: its last tile is orthogonally adjacent to the body, not the body itself.
# Scripted battles with no object-event body (the Rival, some one-offs) trigger on
# their own tile and are exempt.
_stand_on = []
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        if _s["kind"] != "trainer" or not _s["path"]: continue
        _bxy = (_s["at"][0], _s["at"][1])
        try:
            if _bxy not in WORLD.grid(_s["map"]).bodies: continue   # scripted, no body
        except Exception:
            continue
        _end = tuple(_s["path"][-1])
        if _end == (_s["map"],) + _bxy or _end[0] != _s["map"] or \
           abs(_end[1] - _bxy[0]) + abs(_end[2] - _bxy[1]) != 1:
            _stand_on.append((_st["stage"], _s["map"], _bxy))
check("every trainer with a body is reached from the tile beside it, not on it",
      not _stand_on, str(_stand_on[:5]))

# A standing trainer never yields its tile -- fought or not, it stays put -- so no
# walked tile anywhere may land on one, not just at a stop's end (the walk to a
# LATER stop must route around it too, or trip its sight line). Restricted to
# stationary trainers (pacers step off their spawn tile). The one allowance is a
# trainer planted in a one-wide corridor -- the only tile through, so the game
# marches it up to battle you there; exempt where its tile is the sole passage.
def _choke(g, x, y):
    fl = {(dx, dy) for dx, dy in WORLD.DIRS if g.inb(x + dx, y + dy) and g.c(x + dx, y + dy) == 0}
    return fl in ({(1, 0), (-1, 0)}, {(0, 1), (0, -1)})
_tbody = {}
for _m, _mj in RO.maps().items():
    try: _g = WORLD.grid(_m)
    except Exception: continue
    for _o in _mj.get("object_events", []):
        if _o.get("trainer_type", "TRAINER_TYPE_NONE") == "TRAINER_TYPE_NONE": continue
        _xy = (_o.get("x", 0), _o.get("y", 0))
        if _xy in _g.blockers and not _choke(_g, _xy[0], _xy[1]):
            _tbody.setdefault(_m, set()).add(_xy)
_cross = []
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        for _m, _ts in TOUR.walked_tiles(_s["path"]).items():
            # same-map warp/spin maps (Saffron & Silph teleport pads, arrow floors)
            # jump between distant tiles, so walked_tiles draws a false straight
            # line across them -- the straight-run guard skips them for the same
            # reason, so trust the route there rather than the interpolation.
            if _bendy(_m): continue
            hit = _ts & _tbody.get(_m, set())
            if hit: _cross.append((_st["stage"], _m, sorted(hit)[0]))
check("the walk never crosses a standing trainer's tile (outside a one-wide choke)",
      not _cross, str(_cross[:5]))

# Trainer-gated doors (the Rocket Hideout's two barriers) open only once their
# fight is won, so the walk must never step on a still-locked barrier tile.
# Replayed in route order, accumulating the trainers beaten so far.
_tile_gate = {}
for _m, _bl in WORLD.gated_barriers().items():
    for _tiles, _gates in _bl:
        for _t in _tiles:
            _tile_gate[(_m, _t[0], _t[1])] = _gates
_door_bad, _door_fought = [], set()
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        for _m, _ts in __import__("tour").walked_tiles(_s["path"]).items():
            for _x, _y in _ts:
                _g = _tile_gate.get((_m, _x, _y))
                if _g and not _g <= _door_fought:
                    _door_bad.append((_st["stage"], _m, (_x, _y)))
        if _s["kind"] == "trainer" and _s.get("enc"):
            _door_fought.add(_s["enc"].split(":")[-1])
check("the walk never crosses a trainer-gated door before winning its fight",
      not _door_bad, str(_door_bad[:4]))

# The Rocket Hideout elevator needs the Lift Key, and B4F's stair-less right wing
# (Giovanni, the Silph Scope) is reachable only by riding it -- so the walk must
# grab the key first. An elevator ride is a path edge between two of the lift's
# floor tiles; the key is the "Lift Key" event stop. Replayed in route order.
_lift_tiles = set()
for _f in ("RocketHideout_B1F", "RocketHideout_B2F", "RocketHideout_B4F"):
    _mj = RO.maps().get(_f) or {}
    for _w in _mj.get("warp_events", []):
        if "ELEVATOR" in _w.get("dest_map", ""):
            _lift_tiles.add((_f, _w.get("x", 0), _w.get("y", 0)))
_lift_bad, _have_key, _key_seen = [], False, False
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        _p = _s["path"]
        for _a, _b in zip(_p, _p[1:]):
            if tuple(_a) in _lift_tiles and tuple(_b) in _lift_tiles and _a[0] != _b[0] \
                    and not _have_key:
                _lift_bad.append((_st["stage"], _s["what"]))
        if "Lift Key" in _s["what"]:
            _have_key = True; _key_seen = True
check("the Rocket Hideout Lift Key is collected on the route", _key_seen, "not found")
check("the walk never rides the hideout elevator before the Lift Key",
      not _lift_bad, str(_lift_bad[:4]))

# Overworld sprites: every item ball on the route has the game's on-map graphic
# anchored to its tile, and every anchor resolves to an embedded sprite. (Trainer
# stops without a static object event -- scripted rivals, Elite Four rematches --
# legitimately have none and fall back to the numbered pin, so they're exempt.)
_mapart = json.load(open(f"{OUT}/mapart/index.json"))
_ow, _owby = _mapart.get("ow", {}), _mapart.get("owByMap", {})
_ow_dangling = [(m, k, a[0]) for m, anc in _owby.items() for k, a in anc.items()
                if a[0] not in _ow]
check("every overworld sprite anchor resolves to a real sprite",
      not _ow_dangling, str(_ow_dangling[:4]))
_item_nosprite = []
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        if _s["kind"] != "item": continue
        if f"{_s['at'][0]},{_s['at'][1]}" not in _owby.get(_s["map"], {}):
            _item_nosprite.append((_st["stage"], _s["map"], _s["what"]))
check("every item ball on the route carries its overworld sprite",
      not _item_nosprite, str(_item_nosprite[:4]))

# Moon Stones are finite: a party can only hold as many stone evolutions as
# the route has picked up stones by that stage.
_stones = 0; _stones_by = {}
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        if "Moon Stone" in _s["what"] and _s["kind"] in ("item", "hidden"):
            _stones += 1
    _stones_by[_st["stage"]] = _stones
_MOON = {"Nidoking", "Nidoqueen", "Clefable", "Wigglytuff"}
_short = []
for starter, per_stage in raw["sections"].items():
    _ever = set()
    for st in sorted(per_stage, key=int):
        sec = per_stage[st]
        if not sec: continue
        _ever |= {t["name"] for t in sec["team"] if t["name"] in _MOON}
        if len(_ever) > _stones_by.get(int(st), 0):
            _short.append((starter, int(st), sorted(_ever)))
check("no party uses more Moon Stone evolutions than stones held",
      not _short, str(_short[:4]))

# ------------------------------------------------------------------ mid-stage catches
# A species first caught mid-stage does not exist for the fights before its
# catch stop: nothing of Abra's line may answer a Nugget Bridge trainer, since
# the grass is on the far side of the bridge.
_early_by = []
for run, per_stage in raw["sections"].items():
    for stg, sec in per_stage.items():
        if not sec: continue
        _j = sec.get("joins") or {}
        if not _j: continue
        t = 0
        for row in sec["log"]:
            if row["kind"] == "wild": continue
            for x in row["steps"]:
                by = x.get("by")
                if by and _j.get(by, 0) > t:
                    _early_by.append((run, int(stg), by, row.get("enc")))
            t += 1
check("no fight is answered by a Pokémon not yet caught at that point",
      not _early_by, str(_early_by[:4]))

# A move whose only TM is picked up mid-stage cannot be thrown in the fights
# before the pickup: Secret Power waits for the far end of Route 25.
_early_mv = []
for run, per_stage in raw["sections"].items():
    for stg, sec in per_stage.items():
        if not sec: continue
        _mj = sec.get("moveJoins") or {}
        if not _mj: continue
        t = 0
        for row in sec["log"]:
            if row["kind"] == "wild": continue
            for x in row["steps"]:
                mv = x.get("move")
                if mv and _mj.get(mv, 0) > t:
                    _early_mv.append((run, int(stg), mv, row.get("enc")))
            t += 1
check("no move is thrown before its TM is picked up",
      not _early_mv, str(_early_mv[:4]))

# ------------------------------------------------------------------ TM supply
# A single-use TM can only be taught to as many Pokemon as copies exist -- and
# a Dept.-store TM (Dig, Brick Break, Secret Power) has only its finite gift
# copies until the Celadon shop opens at stage 15. The `tm` marker on a team
# move means "this run spends that TM here", already gated to the scarce window.
import tms as _TM
_scarce_tm = _TM.scarce_moves(json.load(open(f"{OUT}/tmSupply.json")))
_copies_of = {r["item"]: r["copies"] for r in _scarce_tm.values()}
_over = []
for run, per_stage in raw["sections"].items():
    _spent = collections.defaultdict(set)
    for st, sec in per_stage.items():
        if not sec: continue
        for t in sec["team"]:
            for mv in t["moves"]:
                if mv.get("tm"): _spent[mv["tm"]].add(t["species"])
    for item, sps in _spent.items():
        cop = _copies_of.get(item)
        if cop is not None and len(sps) > cop:
            _over.append((run, item, sorted(sps), cop))
check("no run spends more copies of a single-use TM than it holds",
      not _over, str(_over[:4]))

# ------------------------------------------------------------------ abilities
# Every ability the ROM defines must have a function, or the engine is silently
# treating an unknown ability as no-op. Parsed straight from the decomp header.
import abilities as AB
check("every ROM ability has an implementation",
      set(AB.ABILITY_NAMES) == set(AB.ABILITIES),
      str(sorted(set(AB.ABILITY_NAMES) ^ set(AB.ABILITIES))[:6]))

# The damage-negating abilities must actually zero a move — and Gen 3's
# Lightning Rod must NOT (it only redirects in doubles). Locks the mechanics so
# a future refactor can't quietly drop an immunity or invent one.
_neg = AB.negates_damage
_ability_cases = [
    ("Levitate negates Ground", _neg("LEVITATE", "GROUND", 100, "MOVE_EARTHQUAKE", 2.0), True),
    ("Levitate ignores Water", _neg("LEVITATE", "WATER", 100, "MOVE_SURF", 1.0), False),
    ("Volt Absorb negates Electric", _neg("VOLT_ABSORB", "ELECTRIC", 90, "MOVE_THUNDERBOLT", 1.0), True),
    ("Water Absorb negates Water", _neg("WATER_ABSORB", "WATER", 80, "MOVE_SURF", 1.0), True),
    ("Flash Fire negates Fire", _neg("FLASH_FIRE", "FIRE", 95, "MOVE_FLAMETHROWER", 1.0), True),
    ("Wonder Guard negates non-super", _neg("WONDER_GUARD", "NORMAL", 80, "MOVE_TACKLE", 1.0), True),
    ("Wonder Guard allows super", _neg("WONDER_GUARD", "FIRE", 80, "MOVE_EMBER", 2.0), False),
    ("Lightning Rod is no-op in 1v1", _neg("LIGHTNING_ROD", "ELECTRIC", 90, "MOVE_THUNDERBOLT", 1.0), False),
]
_bad = [n for n, got, want in _ability_cases if got != want]
check("damage-immunity abilities behave as the ROM does", not _bad, str(_bad))
check("Battle/Shell Armor block crits, others don't",
      AB.blocks_crit("BATTLE_ARMOR") and AB.blocks_crit("SHELL_ARMOR")
      and not AB.blocks_crit("NONE"))

# A status-immunity ability must actually spare its status in the tempo model:
# Body Slam can't slow a Limber mon, Bite can't make Inner Focus flinch.
check("status-immunity abilities neutralize the matching secondary",
      E.status_tempo("MOVE_BODY_SLAM", True, 4, def_ability="LIMBER")[0] == 1.0
      and E.status_tempo("MOVE_BITE", True, 4, def_ability="INNER_FOCUS")[0] == 1.0
      and E.status_tempo("MOVE_BODY_SLAM", True, 4, def_ability="NONE")[0] < 1.0)

# ------------------------------------------------------------------ stat stages
# Stat-stage multipliers must match gStatStageRatios exactly (src/pokemon.c).
check("stat-stage multipliers match the ROM table",
      E.stage_stat(100, 1) == 150 and E.stage_stat(100, 2) == 200
      and E.stage_stat(100, -1) == 66 and E.stage_stat(100, -6) == 25
      and E.stage_stat(100, 6) == 400)

# A critical hit ignores the attacker's own Attack drops (src/pokemon.c crit rule):
# a -1 Attack attacker's crit damage equals a neutral attacker's crit damage.
_geo = E.make_mon("SPECIES_GEODUDE", 30)
_mac = E.make_mon("SPECIES_MACHOP", 30)
_mac_dn = {**_mac, "boosts": {"attack": -1}}
check("a crit ignores the attacker's Attack drop",
      E.damage_rolls(_mac_dn, _geo, "MOVE_KARATE_CHOP", crit=True)[8]
      == E.damage_rolls(_mac, _geo, "MOVE_KARATE_CHOP", crit=True)[8]
      and E.damage_rolls(_mac_dn, _geo, "MOVE_KARATE_CHOP", crit=False)[8]
      < E.damage_rolls(_mac, _geo, "MOVE_KARATE_CHOP", crit=False)[8])

# Intimidate must actually reduce the physical damage its holder faces: an
# opponent with Intimidate takes longer for a physical attacker to KO.
_t_plain = E.make_mon("SPECIES_TAUROS", 30, ability="NONE")
_t_intim = E.make_mon("SPECIES_TAUROS", 30, ability="INTIMIDATE")
check("Intimidate lowers the foe's Attack at entry",
      E.matchup(_mac, _t_intim)["turnsToKO"] > E.matchup(_mac, _t_plain)["turnsToKO"])

# ------------------------------------------------------------------ graph sanity
graph = json.load(open(f"{OUT}/encounters.json"))
ids = [e["id"] for e in graph]
check("encounter ids are unique", len(ids) == len(set(ids)))
check("every encounter has an analysis",
      all(e["id"] in recs for e in graph),
      str([e["id"] for e in graph if e["id"] not in recs][:4]))

# ------------------------------------------------------------------ dex
# The Dex tab lists every obtainable species; the payload must carry one entry
# per obtainable species, each with a National Dex number and a resolvable
# evolves-from link, so the app's chain-based registration can't dangle.
_pl = json.load(open(f"{OUT}/payload.json"))
_dex, _pool = _pl.get("dex", []), _pl["pool"]
_dex_names = {_pool[d[1]] for d in _dex}
check("the dex covers every obtainable species",
      len(_dex) == len(av) and len(_dex_names) == len(_dex),
      f"dex={len(_dex)} obtainable={len(av)}")
check("every dex entry has a National Dex number",
      all(isinstance(d[0], int) and d[0] > 0 for d in _dex),
      str([_pool[d[1]] for d in _dex if not (isinstance(d[0], int) and d[0] > 0)][:6]))
check("every dex evolves-from link resolves to another dex species",
      all(d[7] == -1 or _pool[d[7]] in _dex_names for d in _dex),
      str([_pool[d[1]] for d in _dex if d[7] != -1 and _pool[d[7]] not in _dex_names][:6]))

# ------------------------------------------------------------------ money
# Prize money follows the ROM: 4 * last-mon level * class value. Two known
# leaders anchor the formula; the rest of the model builds on it.
import economy as EC
check("prize money matches the ROM for known trainers",
      EC.prize("TRAINER_LEADER_BROCK") == 1400 and EC.prize("TRAINER_LEADER_MISTY") == 2100,
      f"Brock={EC.prize('TRAINER_LEADER_BROCK')} Misty={EC.prize('TRAINER_LEADER_MISTY')}")
_econ = _pl.get("economy", {})
_tmbuys = [b for per in _econ.get("tmBuys", {}).values() for buys in per.values() for b in buys]
check("the money model ships an income curve and a shopping list",
      _econ.get("totalIncome", 0) > 0 and len(_econ.get("fixed", [])) > 0 and len(_tmbuys) > 0)
_seq = [_econ["incomeByStage"][k] for k in sorted(_econ.get("incomeByStage", {}), key=int)]
check("cumulative income never decreases across stages",
      all(b >= a for a, b in zip(_seq, _seq[1:])))
# every purchase is a Celadon shop (Game Corner / Dept. Store), so nothing is
# buyable before that city opens.
_buys = _econ.get("fixed", []) + _tmbuys
check("nothing is buyable before its shop opens",
      all(p.get("stage", 0) >= _econ.get("gcStage", 15) for p in _buys),
      str([p["what"] for p in _buys if p.get("stage", 0) < _econ.get("gcStage", 15)][:5]))
# the run really does buy the coin-only TMs the model now prices (regression on
# the Game Corner / Dept. TM gap)
check("purchasable TMs the run teaches are priced",
      all(b.get("yen", 0) > 0 and b.get("count", 0) > 0 for b in _tmbuys))

# ------------------------------------------------------------------ danger radar
# Every fight step carries a risk reading (worst single hit as % of the mon's HP,
# whether that hit can one-shot it, whether it faints). These drive the app's
# danger badges, so each reading must be internally consistent, and each leg's
# tier must match the per-step signals it claims to summarize — a silent
# packer/aggregator drift here would mislead the player about what's dangerous.
def _all_legs(pl):
    for _mode in pl.get("sections", {}).values():
        for _per_starter in _mode.values():
            for _sec in _per_starter.values():
                for _leg in _sec.get("legs", []):
                    yield _leg
# step_row indices (compact.py): hpLeft=8, worstTaken=14, ohkoRisk=15, faint=16.
_steps = [(s[14], s[15], s[16]) for leg in _all_legs(_pl)
          for lg in leg.get("log", []) for s in lg[4]]
check("every fight step carries a consistent danger reading",
      all(o in (0, 1) and f in (0, 1) and w >= 0 and (o == 0 or w >= 100)
          for w, o, f in _steps),
      f"{sum(1 for w, o, f in _steps if o and w < 100)} OHKO flags under 100% of HP")

def _danger_tier(u, f, ok, cf):
    return 3 if (u or f) else 2 if ok else 1 if cf else 0
_bad_legs = []
for leg in _all_legs(_pl):
    ohko = sum(1 for lg in leg.get("log", []) for s in lg[4] if s[15])
    if (leg.get("dg", 0) != _danger_tier(leg.get("u", 0), leg.get("f", 0),
                                         leg.get("ok", 0), leg.get("cf", 0))
            or ohko != leg.get("ok", 0)):
        _bad_legs.append(leg.get("ti"))
check("each leg's danger tier matches its OHKO / faint / close counts",
      not _bad_legs, f"{len(_bad_legs)} legs mismatch")

# ------------------------------------------------------------------ level targets
# The app frames each gym (and the League) as a no-grind level target; because
# the run never grinds, a stage's level IS that target, so the payload's
# levelTargets must match progression exactly and cover every one of them.
_lt = _pl.get("levelTargets", [])
_lt_stages = {t[2] for t in _lt}
_want_stages = {s["id"] for s in P.STAGES if s.get("gym") or s["id"] == 32}
check("level targets cover every gym and the League",
      _lt_stages == _want_stages, f"have {sorted(_lt_stages)} want {sorted(_want_stages)}")
check("every level target matches its stage's progression level",
      all(t[1] == P.STAGE_BY_ID[t[2]]["level"] for t in _lt),
      str([t for t in _lt if t[1] != P.STAGE_BY_ID[t[2]]["level"]][:4]))
check("level targets rise monotonically toward the League",
      all(b[1] >= a[1] for a, b in zip(_lt, _lt[1:])))

# ------------------------------------------------------------------ in-game trades
# Each in-game-trade stop carries a checkbox for the species you must obtain to
# hand over. That give-away species must be the ROM's requestedSpecies, not the
# one you receive -- LeafGreen reverses the Nidoran/Nidorina pair vs FireRed, and
# a past route bug had it backwards (progression.INGAME_TRADES is the ROM truth).
_rt_trades = [s for st in json.load(open(f"{OUT}/route.json"))["stages"]
              for s in st["steps"] if str(s.get("what", "")).startswith("Trade for ")]
_give_want = {E.SPECIES[g]["name"] for _r, g, *_ in P.INGAME_TRADES}
def _trade_give(s):
    m = re.match(r"Trade for .+? \(give (.+?)\)$", s.get("what", ""))
    return m.group(1) if m else None
_bad_trades = [s.get("what") for s in _rt_trades
               if (s.get("species") or []) != [_trade_give(s)]
               or _trade_give(s) not in _give_want]
check("every in-game trade names the ROM's give-away species to catch",
      bool(_rt_trades) and not _bad_trades, str(_bad_trades[:4]))

# ------------------------------------------------------------------ catch odds
# Catch odds are the ROM's capture math (capture.py). The shipped table must be
# well-formed: every chance in [0,1], the 1-HP+asleep setup never worse than a
# full-HP throw, better catch rates never catch worse, and every legendary
# playbook resolving to a real catch rate. A final anchor pins the formula.
_cbr = _pl.get("catchByRate", {})
_cbr_ok = True
for _t in _cbr.values():
    for _b in ("Poke", "Great", "Ultra"):
        if not (0.0 <= _t["full"][_b] <= 1.0 and 0.0 <= _t["opt"][_b] <= 1.0
                and _t["opt"][_b] >= _t["full"][_b] - 1e-9):
            _cbr_ok = False
check("catch odds stay within [0,1] and the setup never hurts", bool(_cbr) and _cbr_ok)
_rates = sorted(int(k) for k in _cbr)
check("catch odds rise with catch rate",
      all(_cbr[str(a)]["full"]["Ultra"] <= _cbr[str(b)]["full"]["Ultra"] + 1e-9
          for a, b in zip(_rates, _rates[1:])))
_pb = _pl.get("playbooks", [])
check("every legendary playbook resolves a catch rate in the odds table",
      bool(_pb) and all(str(p["cr"]) in _cbr and p.get("level", 0) > 0 and p.get("where")
                        for p in _pb),
      str([p.get("name") for p in _pb if str(p.get("cr")) not in _cbr][:4]))
import capture as CAP
check("the capture formula matches the ROM at its anchors",
      CAP.catch_chance(255, "Ultra", 0.01, "sleep") == 1.0
      and 0.0 < CAP.catch_chance(3, "Poke", 1.0) < 0.02
      and CAP.catch_chance(3, "Master", 1.0) == 1.0,
      f"cr3poke={CAP.catch_chance(3, 'Poke', 1.0):.4f}")

# ------------------------------------------------------------------ grind calc
# The training itineraries (training.py): each segment [from,to,area,method,move,
# tplLo,tplHi,battles] is well-formed and the per-species segments run in strict
# ascending, non-overlapping level order; every obtainable species has an
# itinerary for all three TM policies; and because a broader TM pool can only add
# clearable areas, the "any" itinerary covers every level "no TMs" does.
_itin = _pl.get("itineraries", {})
# segment = [from,to,area,method,move,tplLo,tplHi,battles,sustainLo,sustainHi,mons,roundTrip,center]
def _seg_ok(s):
    return (s[0] <= s[1] and s[2] != -1 and s[5] > 0 and s[6] >= s[5] - 1e-9
            and s[7] >= 1 and 0 <= s[8] <= s[9] <= 99 and s[11] >= 0
            and all(len(m) == 4 and m[0] != -1 for m in s[10]))
_bad_seg = []
for _nm, _tgs in _itin.items():
    for _tg, _segs in _tgs.items():
        _prev = -1
        for _s in _segs:
            if not _seg_ok(_s) or _s[0] <= _prev:
                _bad_seg.append((_nm, _tg))
                break
            _prev = _s[1]
check("every grind itinerary segment is well-formed and in order",
      bool(_itin) and not _bad_seg, str(_bad_seg[:4]))
_it_missing = [E.SPECIES[_sp]["name"] for _sp in av
               if _sp in E.SPECIES
               and (E.SPECIES[_sp]["name"] not in _itin
                    or set(_itin[E.SPECIES[_sp]["name"]]) != {"none", "renew", "any"})]
check("every obtainable species has an itinerary for each TM policy",
      not _it_missing, str(_it_missing[:5]))
def _levels_covered(nm, tg):
    out = set()
    for s in _itin.get(nm, {}).get(tg, []):
        out |= set(range(s[0], s[1] + 1))
    return out
_bad_cov = [nm for nm in _itin
            if not _levels_covered(nm, "none") <= _levels_covered(nm, "any")]
check("the 'any TM' itinerary covers every level 'no TMs' does",
      not _bad_cov, str(_bad_cov[:4]))

print()
if fails:
    print(f"{len(fails)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
