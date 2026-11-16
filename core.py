"""The core approach: one fixed team of up to six lines, run from the start.

The maximalist solve (sections._solve_run) picks the best party for every
section on its own, so a one-fight specialist joins whenever it saves a turn --
26 lines in a LeafGreen/Charmander run. Trainer experience is a fixed budget
(about 680k XP over the whole route, src/battle_script_commands.c: expYield *
level / 7, x1.5 in a trainer battle; splitting it between participants or with
an Exp. Share divides the same total), and one Medium Fast line costs ~166k XP
from L5 to L55, so the route only feeds a handful of lines on its own.

This module:
  * choose_cores  -- offers a few distinct sixes that clear the League (the
                     hardest test), each with every HM the route needs learnable
                     inside it and no legendary bird; the player picks one;
  * CoreLedger    -- the core's levels come from the XP it actually earns (not
                     the maximalist curve), topped up by grinding only where a
                     section can't be cleared at the level the ledger reached;
  * solve_core_run -- the per-stage driver: the core alone first, and only where
                     it can't clear a section, either specialists or a higher
                     level -- whichever costs fewer turns (battle + training).

Fly answers no obstacle (hms.ALWAYS), so it is required only once a core
member can carry it; until then the run walks.
"""
import itertools
import engine as E
import progression as P
import optimize as O
import constraints as C
import hms as HM
import training_cost as TC
import tactics as TAC
import sections as S

def bind(sections_module):
    """Work against the RUNNING solver. sections.py runs as __main__, so a plain
    `import sections` here would load a second copy with none of the solve
    inputs (_SECTIONS_GRAPH, HM_HELD, the caches) set; its workers call this."""
    global S
    S = sections_module

CORE_SIZE = 6
# Six lines reach about L50 on trainer XP alone by the Elite Four (~620k XP
# banked before Lorelei, split six ways), and the runs then raise to clear it:
# solved cores fight the League at L51-55. The pick screens and simulates at
# that fighting level, each line as the form it holds there (core_form) -- an
# L50 screen with final forms judged Dragonites that are still Dragonairs, and
# one with real forms clears too few sixes to offer a choice.
CORE_FIGHT_LEVEL = 55
CORE_SCREEN = 24        # lines promoted from the 1v1 screen to the combination search
CORE_OPTIONS = 4        # League-viable sixes offered to the player per run
CORE_SPARES = 6         # extra sixes solved in full: an option whose whole run wipes
                        # somewhere, or carries a line the League never sends in, is
                        # dropped and a spare takes its place (the passenger rule drops
                        # up to five of six in some runs, so two spares were too few)
OPTION_SPREAD = 2       # lines each option must change from every other one
CORE_SIM_BUDGET = 150   # League simulations spent finding them
CORE_GATHER = 3         # League-clearing sixes gathered per one offered, before
                        # the HM-coverage ranking picks the offered ones
RAISE_MAX = 12          # levels the core may grind above its ledger for one section
TRAINER_BONUS = 1.5     # src/battle_script_commands.c: BATTLE_TYPE_TRAINER exp x150/100

def _exp():
    import training as T      # lazily: training.py does work at import
    return T

# ------------------------------------------------------------------ forms
def chain(target):
    """The evolution chain from the line's base form to `target`, in order."""
    out = [target]
    while S._pre_evo(out[0]):
        out.insert(0, S._pre_evo(out[0]))
    return out

def _evo(frm, to):
    return next((e for e in E.EVOS.get(frm, []) if e["to"] == to), None)

def core_form(target, stage, level, avail):
    """The form of `target`'s line held at `stage` with the core at `level`, or
    None before any form can be held. A LEVEL evolution follows the ledger's
    level (the core may be ahead of the curve availability was derived from);
    stone, trade and the rest follow availability."""
    held = None
    for sp in chain(target):
        rec = avail.get(sp)
        own = bool(rec) and rec["stage"] <= stage and rec.get("kind") != "evolution"
        e = _evo(held, sp) if held else None
        if e is not None:
            if e["method"] == "LEVEL" and isinstance(e["param"], int):
                grown = P.evolve_level(held, sp, e["param"]) <= level and sp in avail
            else:
                grown = bool(rec) and rec["stage"] <= stage
                # a held stone/trade evolution also waits for the move's level
                grown = grown and (P.evo_delay(held, sp) or 0) <= level
            if rec and rec.get("requiresTrade") and not O.ALLOW_TRADE:
                grown = False
            # a LEVEL evolution follows the ledger, but none may cross into the
            # National Dex before it's granted (progression.NATIONAL_DEX_STAGE)
            if P.NAT_DEX.get(sp, 0) > 151 and stage < P.NATIONAL_DEX_STAGE:
                grown = False
            grown = grown and C.allowed(sp)
        else:
            grown = False
        if (grown or own) and C.allowed(sp):
            held = sp
        elif held is not None:
            break
    return held

# ------------------------------------------------------------------ the pick
def _league():
    """The League's battles and its opposing Pokemon, built the way
    solve_section builds them."""
    encs = [e for e in S._SECTIONS_GRAPH.get(TAC.LEAGUE_STAGE, [])]
    return encs

def _field_hms():
    """Every HM move some stage of the route requires."""
    out = set()
    for st in P.STAGES:
        if st["id"] <= P.ROUTE_MAX_STAGE:
            out.update(HM.moves_here(st["id"]))
    return sorted(out)

def _line_learns(target, mv):
    return any(S.field_can_learn(sp, mv) for sp in chain(target))

def _final_candidates(starter, avail):
    stage = TAC.LEAGUE_STAGE
    cands = [sp for sp in O.candidates(stage)
             if C.starter_of(sp) in (None, starter) and C.allowed(sp)
             and sp not in S.NO_TEAM_SPECIES]
    have = set(cands)
    # the most evolved reachable form only: Kadabra and Alakazam are one line
    return [sp for sp in cands
            if not any(e["to"] in have for e in E.EVOS.get(sp, []))]

def _cost_row(sp, opponents, badges, level):
    """HP share `sp` loses beating each League foe one on one (>= 1: it loses)."""
    stage = TAC.LEAGUE_STAGE
    pm = O.player_mon(sp, level)
    pool = O.move_pool(sp, level, stage)
    mvs = S.choose_moveset(pm, pool, opponents, badges) if pool else []
    return [S.duel_cost(pm, opp, mvs, badges)[0] for opp in opponents]

def core_commitments(team, picks):
    """The run's one-time choices with this core's own made: a core holding
    Jolteon has taken the Eevee as a Jolteon whatever the maximalist run chose
    (the same for the fossil and the Dojo prize)."""
    out = dict(picks or {})
    for sp in team:
        for f in chain(sp):
            hit = C._SPECIES_COMMIT.get(f)
            if hit: out[hit[0]] = hit[1]
    return out

def league_idle(team, log):
    """Core lines the League never credits with a KO. The six is picked backwards
    from the League, so a line that is never sent in there (or only trades itself
    away with Explosion / Self-Destruct) is a passenger: it still takes a sixth of
    the pooled XP and is billed every grind, and a line that fights would earn it.
    `log` is a section's battle log (sections.simulate_team)."""
    hit = {C.line_root(s["bySpecies"]) for e in log if e.get("kind") in S.LEAGUE_KINDS
           for s in e.get("steps", []) if s.get("landed") and not s.get("selfKO")}
    return sorted(C.line_root(sp) for sp in team if C.line_root(sp) not in hit)

def _differs(team, chosen):
    """A new option must change at least two lines from every one already kept,
    so the choice offered is a real one, not one swapped member."""
    roots = {C.line_root(sp) for sp in team}
    return all(len(roots - {C.line_root(sp) for sp in t}) >= OPTION_SPREAD for t in chosen)

def _forms_at(team, stage, level, avail):
    """The six as the forms held at `level` (core_form), for a League sim."""
    return [core_form(sp, stage, level, avail) or sp for sp in team]

def choose_cores(starter, avail, n=CORE_OPTIONS + CORE_SPARES, level=CORE_FIGHT_LEVEL):
    """Up to `n` sixes (starter line included) that clear the League at `level`,
    best first, each with every field HM learnable inside it and no legendary
    bird (sections.NO_TEAM_SPECIES). The one-time choices (Eevee, fossil, Dojo)
    are opened for the pick -- each core makes its own (core_commitments).
    Returns [final-form species lists, starter first]."""
    stage = TAC.LEAGUE_STAGE
    badges = O.badges_for(stage)
    keep = dict(C.COMMITMENTS)
    C.set_commitments({})
    try:
        encs = [e for e in _league() if S.variant_ok(e, starter)]
        opponents = [E.realize_trainer_mon(e["trainerConst"], i)
                     for e in encs for i in range(len(e["party"]))]
        cands = _final_candidates(starter, avail)
        head = next(sp for sp in cands if C.starter_of(sp) == starter)
        # each line as the form it is AT the pick level (a Dragonite line is a
        # Dragonair at L50), as solve_core_run will field it
        rows = {sp: _cost_row(core_form(sp, stage, level, avail) or sp, opponents, badges, level)
                for sp in cands}
    finally:
        C.set_commitments(keep)
    wins = lambda sp: sum(1 for x in rows[sp] if x < 1.0)
    ranked = sorted((sp for sp in cands if sp != head),
                    key=lambda sp: (-wins(sp), sum(min(x, 1.5) for x in rows[sp]),
                                    E.SPECIES[sp]["name"]))
    pool = ranked[:CORE_SCREEN]
    hms = _field_hms()
    moon_cap = S.moon_budget(stage)
    # Once all six are held the party is full, so a field move the section needs
    # has to be learnable by a form held THEN -- a Dragonite that would carry Cut
    # is still a Dragonair at Silph Co. and leaves no slot for a carrier. Forms
    # are taken at the maximalist curve's level (the ledger only runs ahead of it).
    C.set_commitments({})
    try:
        route = [st["id"] for st in P.STAGES if st["id"] <= P.ROUTE_MAX_STAGE]
        need = {st: {mv for mv in HM.moves_here(st) if mv != "MOVE_FLY"} for st in route}
        # every field move incl. Fly, for the gap count that ranks the options
        need_all = {st: set(HM.moves_here(st)) for st in route}
        held_hm = {}
        for sp in [head] + pool:
            row = {}
            for st in route:
                f = core_form(sp, st, P.STAGE_BY_ID[st]["level"], avail)
                row[st] = None if f is None else {mv for mv in need_all[st] if S.field_can_learn(f, mv)}
            held_hm[sp] = row
    finally:
        C.set_commitments(keep)

    def full_party_ok(team):
        for st in route:
            if not need[st]: continue
            rows = [held_hm[sp][st] for sp in team]
            if any(r is None for r in rows): continue      # a slot is free for a carrier
            if not need[st] <= set().union(*rows): return False
        return True

    def hm_gaps(team):
        """(stage, field move) pairs no form of the six HELD then can learn --
        each is a section that needs an outside carrier (Fly included: walking
        is the cost). A Dragonite that only exists at L55 covers nothing before."""
        n = 0
        for st in route:
            rows = [held_hm[sp][st] for sp in team if held_hm[sp][st] is not None]
            n += len(need_all[st] - set().union(*rows)) if rows else len(need_all[st])
        return n
    def legal(team):
        roots, groups = set(), set()
        for sp in team:
            if C.conflicts(sp, roots, groups): return False
            roots.add(C.line_root(sp))
            g = C.exclusive_group(sp)
            if g: groups.add(g)
        if sum(1 for sp in team if sp in S.MOON_EVOS) > moon_cap: return False
        return (all(any(_line_learns(sp, mv) for sp in team) for mv in hms)
                and full_party_ok(team))

    def cost(team):
        un = tot = 0.0
        for k in range(len(opponents)):
            b = min(rows[sp][k] for sp in team)
            un += b >= 1.0
            tot += min(b, 1.5)
        return (un, round(tot, 4))

    scored = sorted((cost((head,) + rest), (head,) + rest)
                    for rest in itertools.combinations(pool, CORE_SIZE - 1)
                    if legal((head,) + rest))
    # the 1v1 screen ignores switching, PP and the League's items: walk the
    # screen's order, re-score each distinct-enough six with the section
    # simulator itself (under that six's own one-time choices), keep the ones
    # that clear, and rank those by the simulator
    # gather League-clearing sixes (more than offered), then offer the ones that
    # lean least on outside HM carriers, best League result first among equals
    found, sims = [], 0
    for _c, team in scored:
        if len(found) >= n * CORE_GATHER or sims >= CORE_SIM_BUDGET: break
        if not _differs(team, [t for _k, t in found]): continue
        C.set_commitments(core_commitments(team, keep))
        try:
            res = S.solve_section(stage, S._SECTIONS_GRAPH.get(stage, []), starter, avail,
                                  core=_forms_at(team, stage, level, avail),
                                  specialists=False, level_override=level)
        finally:
            C.set_commitments(keep)
        sims += 1
        # must clear, and every line must land a KO (no passenger)
        if res["unanswered"] == 0 and not league_idle(team, res["log"]):
            found.append(((hm_gaps(team), res["faints"], res["turns"]), list(team)))
    found.sort(key=lambda kt: kt[0])
    kept = []
    for k, team in found:
        if len(kept) >= n: break
        if _differs(team, [t for _k, t in kept]):
            kept.append((k, team))
    if not kept:
        kept = [((0, 0.0), list(scored[0][1] if scored else [head] + pool[:CORE_SIZE - 1]))]
    kept.sort(key=lambda kt: kt[0])
    return [t for _k, t in kept]

# ------------------------------------------------------------------ the XP ledger
class CoreLedger:
    """Each core line's experience total. Trainer XP is credited to whoever was
    sent in against a foe (Gen 3 splits a KO between participants), then pooled
    across the core and spread to the lowest-levelled first -- a player who leads
    with the laggard, or hands it the Exp. Share, moves XP exactly this way, and
    the pool is conserved. A line joins at its obtain level."""
    def __init__(self, targets):
        self.targets = list(targets)
        self.exp = {}            # target -> total experience
        self.pool = 0.0          # earned XP not yet absorbed by a level

    def growth(self, t):
        return E.SPECIES[t]["growthRate"]

    def level(self, t):
        if t not in self.exp: return None
        T, g, lv = _exp(), self.growth(t), 1
        while T.exp_at_level(g, lv + 1) <= self.exp[t]: lv += 1
        return lv

    def join(self, t, level):
        if t not in self.exp:
            self.exp[t] = float(_exp().exp_at_level(self.growth(t), level))

    def raise_to(self, t, level):
        """Grinding (billed by the roster) brings a line up to `level`."""
        need = _exp().exp_at_level(self.growth(t), level)
        if self.exp.get(t, 0) < need: self.exp[t] = float(need)

    def credit(self, xp):
        self.pool += xp
        held = [t for t in self.targets if t in self.exp]
        T = _exp()
        while held and self.pool > 0:
            t = min(held, key=lambda x: (self.level(x), self.targets.index(x)))
            lv = self.level(t)
            step = T.exp_at_level(self.growth(t), lv + 1) - self.exp[t]
            if step > self.pool:
                self.exp[t] += self.pool; self.pool = 0.0
            else:
                self.exp[t] += step; self.pool -= step

    def copy(self):
        c = CoreLedger(self.targets); c.exp = dict(self.exp); c.pool = self.pool
        return c

_NAME_TO_SP = None
def _species_by_name(name):
    global _NAME_TO_SP
    if _NAME_TO_SP is None:
        _NAME_TO_SP = {v["name"]: k for k, v in E.SPECIES.items()}
    return _NAME_TO_SP.get(name)

def earned_xp(res, core_species, avail):
    """Trainer XP the core members of a solved section gained. A foe's steps run
    until its HP reaches 0; everyone sent in against it shares the KO (integer
    split, then the trainer and traded multipliers, as the battle script does)."""
    T = _exp()
    total = 0.0
    for log in res.get("log", []):
        bonus = TRAINER_BONUS if log.get("kind") not in ("wild", "scripted") else 1.0
        by, foe_key = [], None
        for st in log.get("steps", []):
            # a foe left unanswered never reaches the KO below, so start each
            # new foe's participant list afresh (its fainted attackers earn
            # nothing from the next foe's KO)
            key = st.get("_oppIdx") or (st.get("opp"), st.get("lvl"))
            if key != foe_key:
                by, foe_key = [], key
            by.append(st.get("bySpecies"))
            if st.get("foeHpLeft", 1) <= 0 or st.get("selfKO"):
                foe = _species_by_name(st["opp"])
                if foe:
                    base = T.exp_on_ko(E.SPECIES[foe]["expYield"], st["lvl"])
                    who = [b for b in dict.fromkeys(by) if b]
                    share = base // max(1, len(who))
                    for b in who:
                        if b in core_species:
                            total += share * bonus * TC.exp_factor(b, avail)
                by = []
    return total

# ------------------------------------------------------------------ the run
RAISE_STEP = 3   # between the laggard and the leader, raises are tried this coarsely

def over_obey_cap(form, level, stage, avail):
    """True for an in-game-trade Pokemon above the obedience cap its stage's
    badges set (engine.obedience_cap, from IsMonDisobedient)."""
    rec = avail.get(form) or {}
    if not str(rec.get("source", "")).startswith("In-game trade"): return False
    cap = E.obedience_cap(O.badges_for(stage)["count"])
    return cap is not None and level > cap

def raise_floors(own, base):
    """Floors to grind the core up to, cheapest first: through the gap between
    the laggard and the leader in RAISE_STEP strides (a revived fossil can sit 40
    levels behind), then level by level above the leader up to RAISE_MAX."""
    lo = min(own.values()) if own else base
    floors = list(range(lo + 1, base, RAISE_STEP)) + list(range(base, base + RAISE_MAX + 1))
    return [f for f in floors if f > lo]

def solve_core_run(starter, targets, avail, tm_value=None):
    """Every stage with the core seated. Returns {stage: section result} like
    sections._solve_run, each result tagged with the option that cleared it.
    `tm_value`, when given, collects each single-use TM's gain for the six (the
    measuring run, every TM open)."""
    result = {}
    stages = sorted(st["id"] for st in P.STAGES if st["id"] <= P.ROUTE_MAX_STAGE)
    carry, moon = None, set()
    roster, ledger = TC.Roster(), CoreLedger(targets)
    helpers = []     # line roots brought in to carry an HM, earliest first
    roots = {C.line_root(t) for t in targets}

    for stage in stages:
        encs = S._SECTIONS_GRAPH.get(stage, [])
        # the levels the ledger had BEFORE this section: a line that joins here
        # has none (it joins at its obtain level)
        prior = {t: ledger.level(t) for t in targets if ledger.level(t) is not None}
        for t in targets:
            if t in prior: continue
            # the form you catch it in: level evolutions not yet taken
            f = core_form(t, stage, 1, avail) or core_form(t, stage, 100, avail)
            if f: ledger.join(t, TC.obtained_level(f, avail))
        # Each held line fights at the level its OWN experience reached. One
        # shared level forced a grind on someone every section: the leader's level
        # billed the other five a catch-up (the six levelled as fast as one mon
        # earning all the XP -- League at L54-69), and any shared level ground a
        # newcomer (Snorlax met at L30, an Omastar revived at L5) up to the team
        # before it could fight. A player leads with the newcomer and lets the
        # pooled XP catch it up; trainer XP split six ways is ~L45 at the League.
        own = {t: ledger.level(t) for t in targets
               if ledger.level(t) is not None and core_form(t, stage, ledger.level(t), avail)}
        base = max(own.values()) if own else P.STAGE_BY_ID[0]["level"]
        snap_hm = {k: set(v) for k, v in S.HM_HELD.items()}

        def levels_at(floor):
            """Every held line at its own level, the ones below `floor` ground up
            to it (a raise lifts the laggards first -- the cheapest levels)."""
            return {t: max(lv, floor) for t, lv in own.items()}

        def attempt(floor, specialists):
            S.HM_HELD.clear(); S.HM_HELD.update({k: set(v) for k, v in snap_hm.items()})
            lv_of = levels_at(floor)
            forms = {t: core_form(t, stage, lv, avail) for t, lv in lv_of.items()}
            forms = {t: f for t, f in forms.items() if f}
            # an in-game-trade member over the badge obedience cap sits this
            # section out (IsMonDisobedient: it would waste half its turns)
            forms = {t: f for t, f in forms.items()
                     if not over_obey_cap(f, lv_of[t], stage, avail)}
            level = max(lv_of.values()) if lv_of else base
            r = roster.copy()
            for t, lv in prior.items():
                r.last[C.line_root(t)] = lv
            for t in targets:
                if t not in prior: r.last.pop(C.line_root(t), None)
            r.kept -= roots
            fly_ok = any(S.field_can_learn(f, "MOVE_FLY") for f in forms.values())
            res = S.solve_section(
                stage, encs, starter, avail, tm_value,
                carry=carry if stage in S.cold_stage_starts() else None,
                moon_used=moon, level_override=level, train_roster=r,
                core=list(forms.values()), specialists=specialists,
                hm_helpers=tuple(helpers),
                skip_hms=frozenset() if fly_ok else frozenset({"MOVE_FLY"}),
                member_levels={f: lv_of[t] for t, f in forms.items()})
            cost = res["turns"] + sum(b["turns"] for b in res.get("training", []))
            held = {k: set(v) for k, v in S.HM_HELD.items()}
            return (res["unanswered"], round(cost, 1)), res, held, lv_of, specialists

        tries = [attempt(0, False)]
        if tries[0][0][0]:
            tries.append(attempt(0, True))
            for floor in raise_floors(own, base):
                t = attempt(floor, False)
                tries.append(t)
                if not t[0][0]: break
        key, res, held, lv_of, spec = min(tries, key=lambda x: x[0])
        level = max(lv_of.values()) if lv_of else base
        S.HM_HELD.clear(); S.HM_HELD.update(held)
        res["approach"] = {"level": level, "ledgerLevel": base,
                           # traded lines benched here for disobedience (verify.py)
                           "obeyBench": sorted(C.line_root(t) for t, lv in lv_of.items()
                                               if core_form(t, stage, lv, avail)
                                               and over_obey_cap(core_form(t, stage, lv, avail),
                                                                 lv, stage, avail)),
                           # line root -> the level it was fielded at here
                           "levels": {C.line_root(t): lv for t, lv in lv_of.items()},
                           "specialists": [m["species"] for m in res["team"]
                                           if C.line_root(m["species"]) not in roots],
                           "options": len(tries),
                           # the core alone at its ledger level: what had to be fixed
                           "coreAlone": tries[0][0][0],
                           # line root -> the ledger's level going in (verify.py
                           # re-derives the core's training bill from it)
                           "ledger": {C.line_root(t): lv for t, lv in prior.items()}}
        result[stage] = res
        for h in res.get("hms", []):
            sp = h.get("species")
            if sp and h.get("how") in ("carrier", "swap") and C.line_root(sp) not in roots \
                    and C.line_root(sp) not in helpers:
                helpers.append(C.line_root(sp))
        carry = res.get("carryOut") or carry
        team = [m["species"] for m in res["team"]]
        moon.update(sp for sp in team if sp in S.MOON_EVOS)
        roster.field(team, level)
        core_sp = {m["species"] for m in res["team"] if C.line_root(m["species"]) in roots}
        for t, lv in lv_of.items():
            ledger.raise_to(t, lv)
        ledger.credit(earned_xp(res, core_sp, avail))
        res["approach"]["levelAfter"] = max(ledger.level(t) for t in targets
                                            if ledger.level(t) is not None)
        for t in targets:
            lv = ledger.level(t)
            if lv is not None: roster.last[C.line_root(t)] = lv
        roster.kept -= roots
    return result
