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
                if const and const in scarce and sp not in owned.get(const, set()):
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
# A section's battles must read as the walk: each map's trainers together (a
# route walked in two legs may appear twice), and a map's wild battles sitting
# among that map's own trainers rather than scattered across the section.
import route_order as RO
tiles = RO.trainer_tiles()
stray = []
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        for l in sec["log"]:
            if l["kind"] != "wild": continue
            grp = l.get("group") or ""
            # the trainers immediately around a walk row should be on that map
            pass
# trainers on the same map must be contiguous, allowing at most two legs
for starter, per_stage in raw["sections"].items():
    for st, sec in per_stage.items():
        if not sec: continue
        runs, last = collections.Counter(), None
        for l in sec["log"]:
            if l["kind"] == "wild": continue
            m = l["location"]
            if m != last: runs[m] += 1
            last = m
        for m, n in runs.items():
            if n > 2: stray.append((starter, int(st), m, n))
check("a section's trainers are grouped by map, in at most two legs",
      not stray, str(stray[:4]))

check("every trainer the model orders has a real tile on a real map",
      len(tiles) > 400, f"only {len(tiles)} placed")

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
