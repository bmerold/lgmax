#!/usr/bin/env python3
"""Guard rails. Run after the pipeline; every check must print OK.

These encode the legality rules that are easy to break silently:
LeafGreen-only availability, one Pokemon per evolution line, one per
thing the game hands you once, and no other-starter Pokemon in a run.
"""
import json, os, sys, collections
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
        seq = [RO.battle_key(_const_by_id.get(l["id"], ""))
               for l in sec["log"] if l["kind"] != "wild"]
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

prev_end, disc = None, []
for st in routej["stages"]:
    if prev_end is not None and st["startsAt"] != prev_end:
        disc.append(st["stage"])
    prev_end = st["endsAt"]
check("the route is one continuous walk", not disc, str(disc[:5]))

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
import world as WVIS
_FACE = {"MOVEMENT_TYPE_FACE_RIGHT": [(1,0)], "MOVEMENT_TYPE_FACE_LEFT": [(-1,0)],
         "MOVEMENT_TYPE_FACE_UP": [(0,-1)], "MOVEMENT_TYPE_FACE_DOWN": [(0,1)]}
_fought = {}
for _st in _routej["stages"]:
    for _s in _st["steps"]:
        if _s["kind"] == "trainer" and _s.get("enc"):
            _fought[RO.battle_key(_s["enc"].split(":")[-1])] = _st["stage"]
_t2c = {v: k for k, v in RO.trainer_tiles().items()}
_cones = []
for _name, _mj in RO.maps().items():
    if WVIS._map_stage(_name) is None: continue
    try: _g = WVIS.grid(_name)
    except Exception: continue
    for _o in _mj.get("object_events", []):
        if _o.get("trainer_type", "TRAINER_TYPE_NONE") == "TRAINER_TYPE_NONE": continue
        _sight = int(_o.get("trainer_sight_or_berry_tree_id", "0") or 0)
        if _sight <= 0: continue
        _tiles = set()
        for _dx, _dy in _FACE.get(_o.get("movement_type"), [(1,0),(-1,0),(0,1),(0,-1)]):
            _x, _y = _o["x"], _o["y"]
            for _ in range(_sight):
                _x += _dx; _y += _dy
                if not _g.inb(_x, _y) or _g.c(_x, _y): break
                _tiles.add((_x, _y))
        _cones.append((_name, _tiles, (_o["x"], _o["y"])))
_seen_early = []
for _st in _routej["stages"]:
    _walked = {}
    for _s in _st["steps"]:
        _prev = None
        for _m, _x, _y in _s["path"]:
            if _prev and _prev[0] == _m:
                _n = max(abs(_x-_prev[1]), abs(_y-_prev[2]), 1)
                for _t in range(_n+1):
                    _walked.setdefault(_m, set()).add(
                        (round(_prev[1]+(_x-_prev[1])*_t/_n),
                         round(_prev[2]+(_y-_prev[2])*_t/_n)))
            _prev = (_m, _x, _y)
    for _name, _tiles, _pos in _cones:
        if _name not in _walked or not (_tiles & _walked[_name]): continue
        _const = _t2c.get((_name, _pos[0], _pos[1]))
        _fs = _fought.get(RO.battle_key(_const)) if _const else None
        if _fs is not None and _fs > _st["stage"]:
            _seen_early.append((_st["stage"], _const, _fs))
check("the walk never crosses an unfought trainer's sight line",
      not _seen_early, str(sorted(set(_seen_early))[:4]))

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

# ------------------------------------------------------------------ graph sanity
graph = json.load(open(f"{OUT}/encounters.json"))
ids = [e["id"] for e in graph]
check("encounter ids are unique", len(ids) == len(set(ids)))
check("every encounter has an analysis",
      all(e["id"] in recs for e in graph),
      str([e["id"] for e in graph if e["id"] not in recs][:4]))

print()
if fails:
    print(f"{len(fails)} check(s) FAILED")
    sys.exit(1)
print("all checks passed")
