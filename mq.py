"""Queries over the matchup space (matchups.py).

The tables hold every one-on-one fight's raw damage; this module rebuilds the
same per-move profile and per-foe threat the section simulator uses (sections.
profile / sections.threat) from them, then combines fights along a route leg
with HP and PP carried between them.

  python3 mq.py validate [N]                    # tables == the simulator, N samples
  python3 mq.py solo RATTATA [--version lg|fr]  # min level for one mon to clear each stage

The version flag only picks the ROUTE (which trainers, in what order, which TMs
are in hand); the tables themselves are version-free.
"""
import gzip, json, math, os, random, sys
from array import array

# the route and TM timing are version data; the engine binds it at import
if "--version" in sys.argv:
    os.environ["LGMAX_VERSION"] = sys.argv[sys.argv.index("--version") + 1]

import engine as E
import trainer_items as TI

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TURN_CAP = 40                     # engine.turn_table's cap: "can't KO it" beyond this

# ------------------------------------------------------------------ the tables
class Space:
    def __init__(self):
        self.meta = json.load(open(os.path.join(OUT, "matchups_meta.json")))
        raw = gzip.open(os.path.join(OUT, "matchups.bin.gz")).read()
        a = array("H"); a.frombytes(raw)
        if self.meta["byteorder"] != sys.byteorder: a.byteswap()
        n_off, n_def = self.meta["sizes"]["off"], self.meta["sizes"]["def"]
        self.off, self.dfn, self.stats = a[:n_off], a[n_off:n_off + n_def], a[n_off + n_def:]
        self.species = self.meta["species"]
        self.sp_index = {s["const"]: i for i, s in enumerate(self.species)}
        self.move_index = [{m[0]: j for j, m in enumerate(s["moves"])} for s in self.species]
        self.foes = self.meta["foes"]
        self.pair = {(f, s): (o, d) for f, s, o, d in self.meta["pairs"]}
        self.by_trainer = {}
        for fi, f in enumerate(self.foes):
            for const, i in f["trainers"]:
                self.by_trainer[(const, i)] = fi
        self._prof, self._thr, self._mons = {}, {}, {}

    def stat(self, si, level, k):
        """k: 0 hp, 1 atk, 2 def, 3 spe, 4 spa, 5 spd."""
        return self.stats[(si * 101 + level) * 6 + k]

    def form_at(self, si, level):
        """The evolution line's form at this level: follow level-up evolutions
        (EVOS method LEVEL) whose level is reached. Stone, trade and friendship
        evolutions are a choice, so a line query names that form directly."""
        const = self.species[si]["const"]
        while True:
            nxt = [e for e in E.EVOS.get(const, []) if e["method"] == "LEVEL"
                   and isinstance(e["param"], int) and e["param"] <= level
                   and e["to"] in self.sp_index]
            if len(nxt) != 1: return self.sp_index[const]
            const = nxt[0]["to"]

    def in_band(self, fi, level):
        f = self.foes[fi]
        return f["lo"] <= level <= f["hi"]

    def _block(self, fi, si):
        return self.pair.get((fi, si))

    def _cell(self, fi, si, level, move, offense):
        """(normal, crit) for a cell outside the stored band, or for a species the
        table skipped because the story can't have it by this foe: the identical
        engine call matchups.py makes, done on demand."""
        f = self.foes[fi]
        if fi not in self._mons:
            const, i = f["trainers"][0]
            self._mons[fi] = E.realize_trainer_mon(const, i)
        foe = self._mons[fi]
        pm = E.make_mon(self.species[si]["const"], level, ivs_flat=PLAYER_IV, nature=0, moves=[])
        if offense:
            a, b = E.with_entry_boosts(pm, foe)
            bd = {"atk": f["badges"]["atk"], "spatk": f["badges"]["spatk"]}
        else:
            a, b = E.with_entry_boosts(foe, pm)
            bd = {"def": f["badges"]["def"], "spdef": f["badges"]["spdef"]}
        return (E.damage_rolls(a, b, move, False, bd)[0],
                E.damage_rolls(a, b, move, True, bd)[0])

    # ---- one move against one foe: sections.profile, from the table
    def profile(self, si, level, move, fi):
        key = (si, level, move, fi)
        if key in self._prof: return self._prof[key]
        blk = self._block(fi, si)
        f = self.foes[fi]
        mi = self.move_index[si].get(move)
        hit = None
        if mi is not None:
            if blk is not None and self.in_band(fi, level):
                nm = len(self.species[si]["moves"])
                at = blk[0] + ((level - f["lo"]) * nm + mi) * 2
                dn, dc = self.off[at], self.off[at + 1]
            else:
                dn, dc = self._cell(fi, si, level, move, True)
            hit = _profile(move, dn, dc, self.species[si]["ability"], f["ability"],
                           f["stats"]["hp"])
        self._prof[key] = hit
        return hit

    # ---- the foe's best move against us: sections.threat, from the table
    def threat(self, fi, si, level):
        key = (fi, si, level)
        if key in self._thr: return self._thr[key]
        blk = self._block(fi, si)
        f = self.foes[fi]
        hit = None
        if True:
            nm = len(f["moves"])
            stored = blk is not None and self.in_band(fi, level)
            best, rate_best = None, None
            for mi, mv in enumerate(f["moves"]):
                if mv not in E.MOVES: continue
                if stored:
                    at = blk[1] + ((level - f["lo"]) * nm + mi) * 2
                    dn, dc = self.dfn[at], self.dfn[at + 1]
                else:
                    dn, dc = self._cell(fi, si, level, mv, False)
                p = _move_stats(mv, dn, dc, f["ability"], self.species[si]["ability"], f["iv"])
                if p is None: continue
                rate = p["avg"] * p["acc"] / 100.0 / p["tpu"]
                if rate_best is None or rate > rate_best:
                    best, rate_best = p, rate
            hit = ({"name": best["name"], "avg": best["avg"] * best["acc"] / 100.0,
                    "max": best["max"], "const": best["move"],
                    "cat": E.MOVES[best["move"]]["category"]}
                   if best else {"name": None, "avg": 0.0, "max": 0, "const": None,
                                 "cat": None})
        self._thr[key] = hit
        return hit

# ------------------------------------------------------------------ rebuild a profile
SPECIAL = set(E.FIXED_DAMAGE_EFFECTS) | E.LEVEL_DAMAGE_EFFECTS | {"SUPER_FANG"}

def rolls(move, d):
    """The 16 damage rolls engine.damage_rolls returns, from its first roll.
    Fixed and level-based damage never rolls; everything else went through
    ApplyRandomDmgMultiplier, which is invertible from its r=0 value."""
    if E.MOVES[move]["effect"] in SPECIAL:
        return [d] * 16
    if d == 0:
        return [0] * 16
    return [max(1, d * (100 - r) // 100) for r in range(16)]

IV_KEYS = ("hp", "attack", "defense", "speed", "spAttack", "spDefense")
PLAYER_IV = 15                    # optimize.player_mon: flat 15s

def _accuracy(move, att_ability, att_iv):
    """engine.move_profile's accuracy with no accuracy/evasion stages in play.
    The IVs matter only to Hidden Power, whose type decides physical/special."""
    mv = E.MOVES[move]
    if not mv["accuracy"]: return 100
    physical = E.move_type_for({"ivs": {k: att_iv for k in IV_KEYS}}, move) in E.PHYSICAL_TYPES
    return min(100, int(mv["accuracy"] * E.A.accuracy_mult(att_ability, physical)))

def _move_stats(move, dn, dc, att_ability, def_ability, att_iv=PLAYER_IV):
    """The parts of engine.move_profile the simulator reads, or None when the move
    does nothing to this target (an immunity, an absorbing ability, no power)."""
    mv = E.MOVES[move]
    eff = mv["effect"]
    if eff in E.UNUSABLE_EFFECTS: return None
    if dn == 0 and eff not in SPECIAL: return None
    cc = E.crit_chance(move, def_ability)
    hits = E.MULTI_HIT_EFFECTS.get(eff, 1.0)
    dist = {}
    for v in rolls(move, dn): dist[int(v * hits)] = dist.get(int(v * hits), 0) + (1 - cc) / 16
    for v in rolls(move, dc): dist[int(v * hits)] = dist.get(int(v * hits), 0) + cc / 16
    nc = [int(v * hits) for v in rolls(move, dn)]
    tpu = 2.0 if eff in E.CHARGE_EFFECTS or eff in E.RECHARGE_EFFECTS else 1.0
    return {"move": move, "name": mv["name"], "dist": dist, "acc": _accuracy(move, att_ability, att_iv),
            "avg": sum(k * p for k, p in dist.items()), "min": min(nc), "max": max(nc),
            "tpu": tpu}

def _turn_cost(move, hits):
    """optimize._turn_cost: a charge move's wind-up, Hyper Beam's recharge."""
    e = E.MOVES[move]["effect"]
    if e in E.RECHARGE_EFFECTS: return max(1.0, 2.0 * hits - 1.0)
    if e in E.CHARGE_EFFECTS: return 2.0 * hits
    return hits

def _profile(move, dn, dc, att_ability, def_ability, foe_hp):
    p = _move_stats(move, dn, dc, att_ability, def_ability)
    if p is None: return None
    raw = E.turn_table(p["dist"], p["acc"], foe_hp)
    if raw is None: return None
    tbl = [0.0] + [_turn_cost(move, h) for h in raw[1:]]
    t = tbl[foe_hp]
    return {"move": move, "name": p["name"], "turns": t if math.isfinite(t) else 999.0,
            "pp": E.MOVES[move]["pp"], "avg": p["avg"], "acc": p["acc"],
            "min": p["min"], "max": p["max"], "table": tbl}

# ------------------------------------------------------------------ validate
def validate(n=3000, seed=1):
    """Draw random (species, level, move, foe) cells and check the table's profile
    and threat equal the simulator's own (sections.profile / sections.threat)."""
    import sections as S, optimize as O
    sp = Space()
    rng = random.Random(seed)
    pairs = sp.meta["pairs"]
    bad = checked = live = 0
    for _ in range(n):
        fi, si, _o, _d = pairs[rng.randrange(len(pairs))]
        f, s = sp.foes[fi], sp.species[si]
        lv = rng.randint(f["lo"], f["hi"])
        const, i = f["trainers"][0]
        opp = E.realize_trainer_mon(const, i)
        pm = O.player_mon(s["const"], lv)
        b = f["badges"]
        for mv, _lv, _src in s["moves"]:
            want = S.profile(pm, opp, mv, b)
            got = sp.profile(si, lv, mv, fi)
            checked += 1
            live += want is not None
            if (want is None) != (got is None) or (want and (
                    abs(want["turns"] - got["turns"]) > 1e-9 or want["max"] != got["max"])):
                bad += 1
                if bad <= 5:
                    print("  OFF MISMATCH", s["name"], lv, mv, "vs", f["species"], f["level"],
                          want and want["turns"], got and got["turns"])
        want, got = S.threat(opp, pm, b), sp.threat(fi, si, lv)
        checked += 1
        if want["const"] != got["const"] or abs(want["avg"] - got["avg"]) > 1e-9:
            bad += 1
            if bad <= 5:
                print("  DEF MISMATCH", s["name"], lv, "vs", f["species"], want, got)
    print(f"validate: {checked} cells checked ({live} with a usable move), {bad} mismatches")
    return bad == 0

# ------------------------------------------------------------------ solo a route
def route_battles(version):
    """Each stage's trainer battles in route order, with whether the walk to the
    NEXT battle passes a heal (a Center, a heal zone, a flight)."""
    import sections as S
    rt = json.load(open(os.path.join(OUT, f"route_{version}.json")))
    doors, zones = S._center_doors(), S._heal_zones()
    out = {}
    for st in rt["stages"]:
        fights = []
        for s in st["steps"]:
            if fights and S._step_heal(s, doors, zones):
                fights[-1]["healAfter"] = True
            if s["kind"] != "trainer": continue
            const = (s.get("enc") or "").split(":")[-1]
            if const in E.TRAINERS:
                fights.append({"const": const, "healAfter": False})
        out[st["stage"]] = fights
    return out

def known_moves(sp, si, level, stage):
    """Damaging moves this species could have at `level` by `stage`: level-up
    moves learned by then, TMs/HMs in hand (build_graph's item timing,
    progression.HM_STAGE for HMs), tutors from Two Island (stage 28)."""
    import build_graph as G, progression as P
    tms = G.tm_moves_by_stage(stage)
    out = []
    for mv, lv, src in sp.species[si]["moves"]:
        if src == "level": ok = lv <= level
        elif src.startswith("HM"): ok = stage >= P.HM_STAGE.get(mv.replace("MOVE_", ""), 99)
        elif src.startswith("TM"): ok = mv in tms
        else: ok = stage >= 28
        if ok: out.append(mv)
    return out

def pick_moves(sp, si, level, pool, fis):
    """Four moves that minimise the summed turns across these foes (greedy;
    the objective is submodular, as in sections.choose_moveset)."""
    chosen, best = [], {fi: TURN_CAP for fi in fis}
    while len(chosen) < 4:
        gain, pick = 0.0, None
        for mv in pool:
            if mv in chosen: continue
            g = 0.0
            for fi in fis:
                p = sp.profile(si, level, mv, fi)
                if p: g += max(0.0, best[fi] - min(p["turns"], TURN_CAP))
            if g > gain: gain, pick = g, mv
        if pick is None: break
        chosen.append(pick)
        for fi in fis:
            p = sp.profile(si, level, pick, fi)
            if p: best[fi] = min(best[fi], p["turns"])
    # Once one move covers everything the marginal gain is zero, but a long leg
    # with no Center still needs the PP: fill the free slots with the next-best
    # standalone attacks (fewest summed turns), which are what it falls back to.
    def total(mv):
        return sum(min(TURN_CAP, (sp.profile(si, level, mv, fi) or {"turns": TURN_CAP})["turns"])
                   for fi in fis)
    for mv in sorted((m for m in pool if m not in chosen), key=total):
        if len(chosen) >= 4: break
        if total(mv) < TURN_CAP * len(fis): chosen.append(mv)
    return chosen

def run_leg(sp, si, level, moves, fights):
    """One Pokemon, no switching, no bag: walk the stage's battles in order,
    carrying HP and PP, healing where the route passes a Center. Returns
    (cleared, detail). Expected-value model, like the section simulator."""
    maxhp = sp.stat(si, level, 0)
    spe = sp.stat(si, level, 3)
    hp, pp = float(maxhp), {m: float(E.MOVES[m]["pp"]) for m in moves}
    turns = 0.0
    for fight in fights:
        t = E.TRAINERS[fight["const"]]
        party = [sp.by_trainer.get((fight["const"], i)) for i in range(len(t["party"]))]
        slots = TI.new_slots(t.get("items") or [])
        for k, fi in enumerate(party):
            if fi is None:
                return False, {"at": fight["const"], "why": "foe missing from the table"}
            foe = sp.foes[fi]
            left = 1.0                      # share of the foe's HP still to take
            used = 0.0
            for _ in range(4):              # fall back move by move as PP runs dry
                opts = [(sp.profile(si, level, m, fi), m) for m in moves if pp[m] >= 1.0]
                opts = [(p, m) for p, m in opts if p and p["turns"] < TURN_CAP]
                if not opts: break
                p, m = min(opts, key=lambda o: o[0]["turns"])
                need = p["turns"] * left
                if need <= pp[m]:
                    pp[m] -= need; used += need; left = 0.0; break
                left -= pp[m] / p["turns"]; used += pp[m]; pp[m] = 0.0
            if left > 1e-9:
                return False, {"at": fight["const"], "foe": foe["species"], "why": "out of PP or no damaging move"}
            thr = sp.threat(fi, si, level)
            fspe = foe["stats"]["speed"]
            first = 1.0 if spe > fspe else (0.5 if spe == fspe else 0.0)
            ex_t, ex_a, uses = TI.drag(slots, used, foe["stats"]["hp"],
                                       len(party) - k, 1.0, used)
            TI.spend(slots, uses)
            attacks = max(0.0, used - first) + ex_a
            hp -= attacks * thr["avg"]
            turns += used + ex_t
            if hp <= 0:
                return False, {"at": fight["const"], "foe": foe["species"], "why": "fainted"}
        if fight["healAfter"]:
            hp, pp = float(maxhp), {m: float(E.MOVES[m]["pp"]) for m in moves}
    return True, {"turns": round(turns, 1), "hpLeft": round(100 * hp / maxhp)}

def solo(name, version):
    sp = Space()
    const = "SPECIES_" + name.upper().replace(" ", "_")
    si = sp.sp_index.get(const)
    if si is None:
        sys.exit(f"{name}: not in the matchup space")
    legs = route_battles(version)
    have = sp.species[si]["versions"].get(version)
    print(f"{sp.species[si]['name']} solo, {version.upper()} route "
          f"(obtainable from stage {have['stage'] if have else '-'})")
    for stage in sorted(legs):
        fights = legs[stage]
        if not fights: continue
        if have is None or have["stage"] > stage:
            print(f"  [{stage:2}] not obtainable yet"); continue
        fis = sorted({sp.by_trainer[(f['const'], i)] for f in fights
                      for i in range(len(E.TRAINERS[f["const"]]["party"]))
                      if (f["const"], i) in sp.by_trainer})
        lo = max(sp.meta["band"]["min"], min(sp.foes[fi]["level"] for fi in fis) - 10)
        found = None
        for lv in range(lo, 101):
            fsi = sp.form_at(si, lv)
            pool = known_moves(sp, fsi, lv, stage)
            moves = pick_moves(sp, fsi, lv, pool, fis)
            ok, det = run_leg(sp, fsi, lv, moves, fights)
            if ok:
                found = (lv, moves, det, sp.species[fsi]["name"]); break
        if found:
            lv, moves, det, form = found
            print(f"  [{stage:2}] L{lv:<3} {form:<10} {', '.join(E.MOVES[m]['name'] for m in moves):<48}"
                  f" {det['turns']:>6} turns, {det['hpLeft']}% HP at the end")
        else:
            print(f"  [{stage:2}] can't clear it even at L100")

if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    if "--version" in args:
        i = args.index("--version"); del args[i:i + 2]
    cmd = args[0] if args else "help"
    if cmd == "validate":
        sys.exit(0 if validate(int(args[1]) if len(args) > 1 else 3000) else 1)
    elif cmd == "solo":
        solo(args[1], os.environ.get("LGMAX_VERSION", "lg")[:2].lower())
    else:
        print(__doc__)
