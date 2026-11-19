"""Which sixes can beat the League, and at what shared level -- a standalone
analysis (like mq.py), not a pipeline stage.

  1. Every line the player can hold before the League (trades on, the legendary
     birds included, no starter required) fights every Elite Four and Champion
     Pokemon one on one at each level 45..60, each fight with the single best
     move the line could possibly know: its own and its pre-evolutions'
     level-up moves, every TM/HM, the Two Island tutors.
  2. The lines that win at least one of those fights are the pool.
  3. Every legal six (one starter line, one Eevee, one fossil, one Dojo prize,
     the Moon-Stone budget) that covers every foe at some level <= 60 is found,
     with its minimum shared level: the lowest level at which, all six at that
     level, each foe is beaten by somebody. The Champion's team follows your
     starter, so a six without a starter line takes the starter whose Champion
     it beats lowest (reported).
  4. The best of those sixes are re-run through the real League simulation
     (sections.solve_section: cumulative damage, PP, the League's items and
     tactics), then replayed with real rolls (clear_rate): a six clears at the
     lowest shared level that wins CLEAR_RATE of its replays.

Usage:  LGMAX_VERSION=lg python3 league_sets.py [--sim N]   (N sixes simulated)
Writes data/league_sets_<v>.json.
"""
import json, os, sys, time, itertools, collections, math, random
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor
import engine as E
import progression as P
import optimize as O
import constraints as C
import sections as S
import tactics as TAC
import tms as TM
import core as K

LEVELS = range(45, 61)
STAGE = TAC.LEAGUE_STAGE
STARTERS = ("Bulbasaur", "Charmander", "Squirtle")
SIM_MAX_LEVEL = 75          # stop raising a six here


# ------------------------------------------------------------------ every move
_PRE = {ev["to"]: f for f, evs in E.EVOS.items() for ev in evs}

def _levelup_any(species, level):
    """Level-up moves of this form and every earlier form, up to `level`: a
    pre-evolution can be held back to learn one (delayed evolution), and the
    Move Maniac teaches the form's own."""
    out, sp = set(), species
    while sp:
        out.update(E.learnable_by(sp, level))
        sp = _PRE.get(sp)
    return out

# The League simulation (step 4) must field the same moves the screen assumed.
_orig_levelup = O._levelup_pool
O._levelup_pool = lambda species, level, stage: list(_levelup_any(species, level))


# ------------------------------------------------------------------ setup
def setup():
    O.set_allow_trade(True)
    C.set_commitments({})
    graph = json.load(open(f"{S.OUT}/encounters.json"))
    S._set_solve_inputs(S.build_sections(graph), P.full_availability())
    supply = TM.extract()
    S.SCARCE.update(TM.scarce_moves(supply))
    S.set_repeatable(supply)
    O.set_tm_plan(S.SCARCE, _owners({}))   # one-copy TMs: nobody's until handed out


# One-copy TMs (tms.scarce_moves: Psychic, Earthquake, Thunder...) go to at most
# their number of copies per six; the Dept. Store and Game Corner TMs are
# re-buyable by the League and free to all. A line's fights are scored without
# the scarce ones, then once with each it could learn; a six covers only when
# some hand-out of the copies beats every foe.
def league_scarce():
    """move -> copies for the TMs still one-copy at the League."""
    return {mv: rec["copies"] for mv, rec in S.SCARCE.items()
            if not (rec.get("shopStage") is not None and STAGE >= rec["shopStage"])}


def league_foes():
    """[(label, starterVariant or None, realized mon)] for every League Pokemon."""
    out = []
    for e in S._SECTIONS_GRAPH.get(STAGE, []):
        if e.get("kind") not in ("elite4", "champion"): continue
        for i in range(len(e["party"])):
            mon = E.realize_trainer_mon(e["trainerConst"], i)
            out.append((f"{e['name']}: {E.SPECIES[mon['species']]['name']} L{mon['level']}",
                        e.get("starterVariant"), mon))
    return out


def lines(avail):
    """Most evolved form of every line obtainable by the League (birds included)."""
    cands = [sp for sp in O.candidates(STAGE, allow_trade=True) if sp in E.SPECIES]
    have = set(cands)
    return sorted(sp for sp in cands if not any(e["to"] in have for e in E.EVOS.get(sp, [])))


# ------------------------------------------------------------------ step 1
def _fight_cost(pm, opp, pool, badges):
    """HP share lost beating `opp` one on one with the best move (>= 1: loses)."""
    return S.duel_cost(pm, opp, pool, badges)


def _costs(pm, pool, foes, badges):
    costs, moves = [], []
    for _l, _v, opp in foes:
        c, mv = _fight_cost(pm, opp, pool, badges) if pool else (9.0, None)
        costs.append(round(c, 3)); moves.append(mv)
    return costs, moves


def _line_rows(target):
    """{level: (form, [cost per foe], [best move per foe], {scarce TM: [cost per
    foe with it]})} for one line; a TM is listed only where it helps a fight."""
    avail = S._AVAIL_TABLE
    foes = league_foes()
    badges = O.badges_for(STAGE)
    scarce = league_scarce()
    out = {}
    for lv in LEVELS:
        form = K.core_form(target, STAGE, lv, avail) or target
        pm = O.player_mon(form, lv)
        pool = O.move_pool(form, lv, STAGE)
        costs, moves = _costs(pm, pool, foes, badges)
        learn = O.learnable(form, lv, STAGE)
        with_tm = {}
        for mv in sorted(scarce):
            if mv not in learn or mv in pool: continue
            c2, _m2 = _costs(pm, pool + [mv], foes, badges)
            if any(b < a for a, b in zip(costs, c2)):
                with_tm[mv] = c2
        out[lv] = (form, costs, moves, with_tm)
    return target, out


def fight_table(targets):
    workers = int(os.environ.get("LGMAX_WORKERS", os.cpu_count() or 4))
    # fork, so workers inherit the loaded graph and TM tables (as sections.py does)
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork")) as ex:
        return dict(ex.map(_line_rows, targets))


# ------------------------------------------------------------------ steps 2-3
LIST_CAP = 5000      # sixes listed in full; later levels are only counted

def _bits(costs):
    return sum(1 << i for i, c in enumerate(costs) if c < 1.0)

def masks(table, foes):
    """line -> {level: (base mask, {scarce TM: mask with it}, optimistic mask)}:
    the foes it beats 1v1 without one-copy TMs, with each, and with all."""
    out = {}
    for t, rows in table.items():
        out[t] = {}
        for lv in LEVELS:
            base = _bits(rows[lv][1])
            tms = {mv: _bits(c) | base for mv, c in rows[lv][3].items()}
            opt = base
            for m in tms.values(): opt |= m
            out[t][lv] = (base, tms, opt)
    return out


def assign_tms(six, level, mk, need, copies):
    """A hand-out of one-copy TMs ({move: (lines...)}) under which the six, all
    at `level`, beats every foe in `need`; None when no hand-out does."""
    got = 0
    for t in six: got |= mk[t][level][0]
    missing = need & ~got
    if not missing: return {}
    useful = sorted({mv for t in six for mv, m in mk[t][level][1].items() if m & missing})
    def go(i, covered, plan):
        if not (missing & ~covered): return plan
        if i == len(useful): return None
        mv = useful[i]
        holders = [t for t in six if mk[t][level][1].get(mv, 0) & missing & ~covered]
        for k in range(min(copies.get(mv, 1), len(holders)), 0, -1):
            for pick in itertools.combinations(holders, k):
                gain = 0
                for t in pick: gain |= mk[t][level][1][mv]
                r = go(i + 1, covered | gain, {**plan, mv: pick})
                if r is not None: return r
        return go(i + 1, covered, plan)
    return go(0, got, {})


def required(foes):
    """starter -> bitmask of every League foe you face having picked it."""
    return {st: sum(1 << i for i, (_l, v, _m) in enumerate(foes) if v in (None, st))
            for st in STARTERS}


def covers_at(level, cand, mk, need, moon_cap, copies):
    """Every legal six of `cand` whose members, all at `level`, beat every foe
    in `need` under some hand-out of the one-copy TMs: [(six, hand-out)].
    Depth-first in a fixed order, pruned by what the remaining lines could still
    cover even with every TM."""
    m = [mk[t][level][2] for t in cand]
    n = len(cand)
    reach = [0] * (n + 1)                   # union of masks from i on
    for i in range(n - 1, -1, -1): reach[i] = reach[i + 1] | m[i]
    root = [C.line_root(t) for t in cand]
    grp = [C.exclusive_group(t) for t in cand]
    moon = [t in S.MOON_EVOS for t in cand]
    out = []
    def dfs(i, picked, got, roots, groups, moons):
        left = 6 - len(picked)
        if left == 0:
            if got & need == need:
                six = tuple(cand[j] for j in picked)
                plan = assign_tms(six, level, mk, need, copies)
                if plan is not None: out.append((six, plan))
            return
        if n - i < left or (got | reach[i]) & need != need: return
        for j in range(i, n - left + 1):
            if root[j] in roots or (grp[j] and grp[j] in groups): continue
            if moon[j] and moons >= moon_cap: continue
            if (got | m[j] | reach[j + 1]) & need != need: continue
            picked.append(j)
            dfs(j + 1, picked, got | m[j], roots | {root[j]},
                groups | ({grp[j]} if grp[j] else set()), moons + moon[j])
            picked.pop()
    dfs(0, [], 0, frozenset(), frozenset(), 0)
    return out


def all_covers(table, foes, starter, exclude=frozenset()):
    """{six (sorted tuple): min shared level} against the League you face having
    picked `starter` (its Champion team), listed level by level until LIST_CAP.
    A six holds this starter's line or none, and none of the `exclude` lines.
    Returns (sets, counts, last level)."""
    mk = masks(table, foes)
    need = required(foes)[starter]
    moon_cap = S.moon_budget(STAGE)
    copies = league_scarce()
    found, counts, last = {}, {}, None
    for lv in LEVELS:
        cand = [t for t in table if C.starter_of(t) in (None, starter) and mk[t][lv][2]
                and t not in exclude]
        new = 0
        for six, plan in covers_at(lv, cand, mk, need, moon_cap, copies):
            key = tuple(sorted(six))
            if key not in found:
                found[key] = (lv, plan); new += 1
        counts[lv] = new
        last = lv
        print(f"  [{starter}] L{lv}: {new} new sixes ({len(found)} in all)", flush=True)
        if len(found) >= LIST_CAP: break
    return found, counts, last


# ------------------------------------------------------------------ step 4
SIM_TOP = int(os.environ.get("LGMAX_SIM_TOP", 1200))   # sixes simulated, split per starter
RANK_LEVEL = 50      # the 1v1 margin that orders sixes for the full simulation
# Share of each starter's simulated sixes held for teams without a legendary bird.
# Ranking by screen level alone let the birds crowd them out: picking Squirtle,
# the Champion's Venusaur (L63) is beaten one on one only by Moltres until L48
# and by no other line until L50, so all 33k sixes listed by the L46 cap held a
# bird and the League tab's "no birds" filter showed nothing for that path.
BIRDLESS_SHARE = 0.25

def pick_for_sim(ranked, per_top, birdless):
    """The top `per_top` of `ranked` (best first), with up to BIRDLESS_SHARE of
    them held for the best bird-free sixes (fewer when fewer exist); rank order kept."""
    keep = {kv[0] for kv in [kv for kv in ranked if birdless(kv[0])][:int(per_top * BIRDLESS_SHARE)]}
    for kv in ranked:
        if len(keep) >= per_top: break
        keep.add(kv[0])
    return [kv for kv in ranked if kv[0] in keep]

def _member_costs(t, lv, table, plan):
    """A line's per-foe costs at lv with the one-copy TMs `plan` gives it."""
    row = table[t][lv]
    best = list(row[1])
    for mv, holders in plan.items():
        if t in holders and mv in row[3]:
            best = [min(a, b) for a, b in zip(best, row[3][mv])]
    return best


def margin(six, table, need_mask, foes, plan, lv=RANK_LEVEL):
    """Sum over the League foes of the six's best one-on-one HP loss at `lv`
    (capped), with its TM hand-out: lower is a six with room to spare."""
    rows = [_member_costs(t, lv, table, plan) for t in six]
    return round(sum(min(min(r[i] for r in rows), 1.5)
                     for i in range(len(foes)) if need_mask >> i & 1), 3)


def full_plan(six, plan, table, need_mask, foes, lv=RANK_LEVEL):
    """The screen's required hand-out, then every other one-copy TM the six can
    use, each to whichever member it helps most (greedy, copies respected)."""
    plan = {mv: tuple(h) for mv, h in plan.items()}
    copies = league_scarce()
    while True:
        base = margin(six, table, need_mask, foes, plan, lv)
        best = None
        for mv in sorted(copies):
            if len(plan.get(mv, ())) >= copies[mv]: continue
            for t in six:
                if t in plan.get(mv, ()) or mv not in table[t][lv][3]: continue
                trial = {**plan, mv: plan.get(mv, ()) + (t,)}
                gain = base - margin(six, table, need_mask, foes, trial, lv)
                if gain > 1e-6 and (best is None or gain > best[0]):
                    best = (gain, mv, t)
        if best is None: return plan
        _g, mv, t = best
        plan[mv] = plan.get(mv, ()) + (t,)


def _owners(plan):
    """sections' TM owner table: each one-copy move to the forms of its holders."""
    owners = {mv: set() for mv in S.SCARCE}
    for mv, holders in plan.items():
        for t in holders:
            sp = t
            while sp:
                owners[mv].add(sp); sp = _PRE.get(sp)
    return owners


def _clears(six, starter, lv, plan):
    avail = S._AVAIL_TABLE
    C.set_commitments(K.core_commitments(list(six), {}))
    O.set_tm_plan(S.SCARCE, _owners(plan))
    S.LAST_SOLVE.clear()      # clear_rate replays whatever this solve leaves here
    try:
        forms = [K.core_form(t, STAGE, lv, avail) or t for t in six]
        res = S.solve_section(STAGE, S._SECTIONS_GRAPH.get(STAGE, []), starter, avail,
                              core=forms, specialists=False, level_override=lv)
    finally:
        C.set_commitments({})
        O.set_tm_plan(S.SCARCE, _owners({}))
    return res["unanswered"] == 0, res


def team_detail(res):
    """Each member of a cleared League run: the four moves it carried (with how
    often the League sim used each) and every foe it was sent against, in fight
    order, as [foe, level, move, outcome] -- what the tab shows to say why a member
    is in the six. outcome 1: it landed the KO (an Explosion finish counts);
    0: it wore the foe down before going out, a role as real as the KO; -1: it was
    knocked out before it moved -- fodder that buys the next member a free switch-in."""
    beats = collections.defaultdict(list)
    for e in res.get("log", []):
        if e.get("kind") not in S.LEAGUE_KINDS: continue
        for s in e.get("steps", []):
            if not s.get("bySpecies"): continue      # an unanswered foe: no one fought it
            outcome = 1 if s.get("landed") else (0 if s.get("turns", 0) > 0 else -1)
            beats[s["bySpecies"]].append([s["opp"], s["lvl"], s["move"], outcome])
    return [{"name": m["name"], "species": m["species"], "fainted": m.get("fainted", 0),
             "moves": [[mv["name"], round(mv.get("used", 0), 1)] for mv in m["moves"]],
             "beats": beats.get(m["species"], [])}
            for m in res.get("team", [])]


# "Clears at" is a chance, not a single run. The section sim draws each fight as
# its median outcome; a six that wins its median League can still lose it a third
# of the time (the 80%-accurate one-shot, the Full Restore that comes at the wrong
# moment), and the median level jumped about from one level to the next. So the
# chosen party is replayed TRIALS times with real rolls (rolls.py) and a six
# clears at the lowest level that wins at least CLEAR_RATE of them. The replays
# use the same seeds at every level and for every six (common random numbers), so
# the level-to-level comparison is not swamped by luck.
CLEAR_RATE = 0.9
TRIALS = 60
ABOVE_SLACK = 0.1    # the levels above may dip this far under CLEAR_RATE (noise)

def clear_rate(six, starter, lv, plan, need=CLEAR_RATE, n=TRIALS):
    """Solve the League at `lv`, then replay it n times with real rolls.
    Stops early once the six can no longer reach `need`. -> dict with the share
    won, whether it reached `need`, the mean faints/turns of the replays, and
    the median-outcome solve (its team detail is what the tab shows)."""
    ok, res = _clears(six, starter, lv, plan)
    if not S.LAST_SOLVE:
        return {"rate": 0.0, "passed": False, "res": res, "n": 0}
    z = dict(S.LAST_SOLVE)
    allowed = n - math.ceil(need * n - 1e-9)
    wins = losses = 0
    faints = turns = 0.0
    for i in range(n):
        rr = S.run_section(z["team"], z["battles"], z["badges"], z["heal_idx"],
                           carry=z["carry"], stage=z["stage"], rng=random.Random(i))
        if rr["failed"]: losses += 1
        else: wins += 1
        faints += rr["faints"]; turns += rr["turns"]
        if losses > allowed:
            break
    k = wins + losses
    return {"rate": wins / k, "passed": losses <= allowed, "res": res, "n": k,
            "faints": faints / k, "turns": turns / k}

def _holds(rate, lv, proven):
    """The six still wins its replays every ABOVE_STEPS level above `lv`, at the
    slack the above-clear check allows. A level in `proven` already passed at
    the full rate. The planner is myopic across the League's five fights (it
    spends HP on one that a later fight needs), so a higher level can pick a
    worse line: LG Bulbasaur's Jynx six wins 91% at L46 and 72% at L48, where
    Lance's line leaves Jynx too low to take Exeggutor. A clear level must not
    sit on that kind of dip."""
    return all(lv + d > SIM_MAX_LEVEL or lv + d in proven
               or rate(lv + d, CLEAR_RATE - ABOVE_SLACK)["passed"]
               for d in ABOVE_STEPS)


def _sim_one(args):
    """Lowest shared level at which this six wins CLEAR_RATE of its League
    replays and keeps winning them above (_holds), or None. The median-outcome
    sim's own clear level (binary search) is the starting point; the replays
    then walk down while they keep passing, or up until they do."""
    six, starter, lo, plan = args
    S.reset_solve_caches()
    ok, res = _clears(six, starter, SIM_MAX_LEVEL, plan)
    if not ok: return six, None, None
    hi = SIM_MAX_LEVEL
    while lo < hi:
        mid = (lo + hi) // 2
        if _clears(six, starter, mid, plan)[0]: hi = mid
        else: lo = mid + 1
    memo = {}
    def rate(lv, need=CLEAR_RATE):
        # each is a full solve plus up to TRIALS replays; the walk and _holds
        # revisit levels, and the same seeds make a rerun identical
        if (lv, need) not in memo:
            memo[lv, need] = clear_rate(six, starter, lv, plan, need=need)
        return memo[lv, need]
    lv, best = hi, rate(hi)
    proven = set()
    if best["passed"]:
        proven.add(lv)
        while lv - 1 >= LEVELS[0]:
            down = rate(lv - 1)
            if not down["passed"]: break
            lv, best = lv - 1, down
            proven.add(lv)
    passed = best["passed"] and _holds(rate, lv, proven)
    while not passed:
        lv += 1
        if lv > SIM_MAX_LEVEL: return six, None, None
        best = rate(lv)
        if best["passed"]: proven.add(lv)
        passed = best["passed"] and _holds(rate, lv, proven)
    res = best["res"]
    return six, lv, {"faints": round(best["faints"], 1), "turns": round(best["turns"], 1),
                     "clearRate": round(best["rate"] * 100), "trials": best["n"],
                     "team": team_detail(res)}


# A six's clear chance should hold as it levels; a planner flaw once broke that
# (sixes cleared at L49 and failed at L50-52: a foe parked in its Full Restore
# window ranked as nearly beaten), so a sample is replayed above its clear level
# and the result kept for verify.py.
ABOVE_STEPS = (1, 2)

def _above_one(args):
    six, starter, lv, plan = args
    S.reset_solve_caches()
    out = []
    for d in ABOVE_STEPS:
        if lv + d > SIM_MAX_LEVEL: continue
        r = clear_rate(six, starter, lv + d, plan, need=CLEAR_RATE - ABOVE_SLACK)
        out.append([lv + d, r["passed"], round(r["rate"] * 100)])
    return out

def above_clear(cleared, starter):
    """[[six names, clear level, [[level, still clears?, % won]...]]] for the
    first, middle and last cleared sixes (`cleared`: (six, plan, level) best first)."""
    pick = sorted({0, len(cleared) // 2, len(cleared) - 1}) if cleared else []
    out = []
    for i in pick:
        six, plan, lv = cleared[i]
        out.append([[E.SPECIES[t]["name"] for t in six], lv, _above_one((six, starter, lv, plan))])
    return out


def simulate(ranked):
    workers = int(os.environ.get("LGMAX_WORKERS", os.cpu_count() or 4))
    out = []
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("fork")) as ex:
        for i, r in enumerate(ex.map(_sim_one, ranked, chunksize=4), 1):
            out.append(r)
            if i % 50 == 0 or i == len(ranked):
                print(f"    simulated {i}/{len(ranked)}", flush=True)
    return out


# ------------------------------------------------------------------ caching
# A full run is ~20 minutes per version, so build.sh's step reuses the last
# result when nothing it reads has changed (training.py does the same).
INPUT_MODULES = ("league_sets.py", "engine.py", "abilities.py", "sections.py", "optimize.py",
                 "core.py", "progression.py", "constraints.py", "tms.py", "tactics.py",
                 "trainer_items.py", "rolls.py", "hms.py", "measure.py", "training_cost.py", "build_graph.py")
INPUT_DATA = ("encounters.json", "route.json", "species.json", "moves.json",
              "levelUpLearnsets.json", "tmhmLearnsets.json", "tutorLearnsets.json",
              "evolutions.json", "typeChart.json", "items.json")

def inputs_hash():
    import hashlib
    h = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in INPUT_MODULES:
        p = os.path.join(here, name)
        if os.path.exists(p): h.update(name.encode()); h.update(open(p, "rb").read())
    for name in INPUT_DATA:
        p = os.path.join(S.OUT, name)
        if os.path.exists(p): h.update(name.encode()); h.update(open(p, "rb").read())
    h.update(f"{E.VERSION}|{SIM_TOP}|{LIST_CAP}|{BIRDLESS_SHARE}|{CLEAR_RATE}|{TRIALS}|{ABOVE_SLACK}|{os.environ.get('LGMAX_POKEFIRERED', '')}".encode())
    return h.hexdigest()


def flags(six, starter, avail):
    """What a six asks of the player: trades, a legendary bird, the starter line."""
    return {"trade": any((avail.get(t) or {}).get("requiresTrade") for t in six),
            "bird": any(t in S.NO_TEAM_SPECIES for t in six),
            "starterLine": any(C.starter_of(t) == starter for t in six)}


if __name__ == "__main__":
    t0 = time.time()
    out_path = f"{S.OUT}/league_sets_{E.VERSION}.json"
    want = inputs_hash()
    if os.environ.get("LGMAX_FORCE_LEAGUE") != "1" and os.path.exists(out_path):
        try:
            if json.load(open(out_path)).get("inputsHash") == want:
                print(f"league sets: inputs unchanged, reusing {out_path} "
                      f"(LGMAX_FORCE_LEAGUE=1 to rebuild)")
                sys.exit(0)
        except ValueError:
            pass
    setup()
    avail = S._AVAIL_TABLE
    foes = league_foes()
    targets = lines(avail)
    print(f"{len(targets)} lines, {len(foes)} League Pokemon "
          f"({sum(1 for f in foes if f[1] is None)} Elite Four)", flush=True)
    table = fight_table(targets)
    print(f"fight table: {time.time() - t0:.0f}s", flush=True)
    json.dump({"foes": [[l, v] for l, v, _m in foes],
               "table": {t: {str(lv): r for lv, r in rows.items()} for t, rows in table.items()}},
              open(f"{S.OUT}/league_table_{E.VERSION}.json", "w"))
    need = required(foes)
    tbl = {t: {lv: rows[lv] for lv in LEVELS} for t, rows in table.items()}
    name = lambda t: E.SPECIES[t]["name"]
    per_top = max(1, SIM_TOP // len(STARTERS))
    report = {"version": E.VERSION, "inputsHash": want, "levels": [LEVELS[0], LEVELS[-1]],
              "starters": {}}
    order = os.environ.get("LGMAX_LEAGUE_STARTERS", " ".join(STARTERS)).split()
    for st in order:
        found, counts, last = all_covers(table, foes, st)
        birds = frozenset(t for t in table if t in S.NO_TEAM_SPECIES)
        birdless = lambda six: not birds.intersection(six)
        listed = len(found)
        n_free = sum(1 for six in found if birdless(six))
        if n_free < per_top * BIRDLESS_SHARE:
            # the cap stopped the listing before bird-free sixes appear: list them
            # on their own (levels already listed find nothing new; earlier wins)
            free, _c, free_last = all_covers(table, foes, st, exclude=birds)
            for six, v in free.items():
                found.setdefault(six, v)
            n_free = sum(1 for six in found if birdless(six))
            print(f"[{st}] bird-free sixes: {n_free} (listed through L{free_last})", flush=True)
        # rank by the screen's own hand-out (cheap), then complete the hand-out
        # only for the sixes that go on to the full simulation
        ranked = sorted(found.items(), key=lambda kv: (
            kv[1][0], margin(kv[0], tbl, need[st], foes, kv[1][1]), kv[0]))
        top = [(six, st, lv, full_plan(six, plan, tbl, need[st], foes))
               for six, (lv, plan) in pick_for_sim(ranked, per_top, birdless)]
        print(f"[{st}] {len(found)} sixes through L{last}; simulating the top {len(top)}", flush=True)
        sims = simulate(top)
        rows = [{"six": [name(t) for t in six], "species": list(six), "screenLevel": lv1,
                 "margin": margin(six, tbl, need[st], foes, plan),
                 "tms": {E.MOVES[mv]["name"]: [name(t) for t in h] for mv, h in plan.items()},
                 "clearLevel": lv, **flags(six, st, avail), **(info or {})}
                for (six, _st, lv1, plan), (_s, lv, info) in zip(top, sims)]
        rows.sort(key=lambda r: (r["clearLevel"] is None, r["clearLevel"] or 0,
                                 r.get("faints", 0), r.get("turns", 0)))
        cleared = sorted(((six, plan, lv) for (six, _st, _l1, plan), (_s, lv, _i)
                          in zip(top, sims) if lv), key=lambda c: (c[2], c[0]))
        report["starters"][st] = {"listed": listed, "listedThrough": last,
                                  "birdless": n_free,
                                  "countsByLevel": counts, "simulated": len(top), "sixes": rows,
                                  "aboveClear": above_clear(cleared, st)}
        print(f"[{st}] best:", flush=True)
        for r in rows[:15]:
            tms = "; ".join(f"{m}: {', '.join(h)}" for m, h in r["tms"].items())
            print(f"  L{r['clearLevel']}  {', '.join(r['six'])}  (faints {r.get('faints')}, "
                  f"screen L{r['screenLevel']}; TMs {tms or 'none'})", flush=True)
    json.dump(report, open(out_path, "w"), indent=1)
    print(f"done ({time.time() - t0:.0f}s)", flush=True)
