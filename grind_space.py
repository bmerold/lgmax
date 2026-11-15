"""Queries over the wild half of the matchup space (wild_matchups.py).

The wild tables hold every one-on-one fight against a wild Pokemon; this module
rebuilds the grind simulator's per-area sweep (optimize._weighted_sweep) from
them -- the move picked per slot, turns to KO, damage taken -- with the run's
context (badges, move pool, obedience) applied at lookup time.

  python3 grind_space.py validate [N]   # tables == optimize._weighted_sweep, N samples
"""
import gzip, json, math, os, random, sys
from array import array

import engine as E
import matchups as M
import mq

OUT = M.OUT
TURN_CAP = 40                    # optimize._weighted_sweep: "can't grind here" at 40+
_IVS15 = {"ivs": {k: 15 for k in mq.IV_KEYS}}

def _physical(move):
    """Physical or special, as both sides' flat-15 IVs type Hidden Power."""
    return E.move_type_for(_IVS15, move) in E.PHYSICAL_TYPES

class WildSpace:
    def __init__(self):
        self.meta = json.load(open(os.path.join(OUT, "wild_matchups_meta.json")))
        self.species = self.meta["species"]
        self.sp_index = {s["const"]: i for i, s in enumerate(self.species)}
        self.move_index = [{m[0]: j for j, m in enumerate(s["moves"])} for s in self.species]
        raw = gzip.open(os.path.join(OUT, "wild_matchups.bin.gz")).read()
        a = array("H"); a.frombytes(raw)
        if self.meta["byteorder"] != sys.byteorder: a.byteswap()
        n_off = self.meta["sizes"]["off"]
        self.off, self.dfn = a[:n_off], a[n_off:]
        self.V = self.meta["values"]
        self.foes = self.meta["foes"]
        self.foe_index = {(f["species"], f["level"]): i for i, f in enumerate(self.foes)}
        self.starts = self.meta["starts"]
        self._fm, self._opps = {}, {}
        self.clear()

    def clear(self):
        """Drop the per-fight memo. It is keyed by the player's level, so the
        grind loop clears it as it moves to the next level -- a species' climb
        would otherwise hold every (level, move, foe) profile at once."""
        self._off, self._thr, self._ko, self._pm = {}, {}, {}, {}

    # ---- the specimens, for the stats the cells don't carry and the on-demand path
    def player(self, si, level):
        k = (si, level)
        if k not in self._pm: self._pm[k] = M.player_mon(self.species[si]["const"], level)
        return self._pm[k]

    def foe_mon(self, fi):
        if fi not in self._fm:
            f = self.foes[fi]
            self._fm[fi] = E.make_mon(f["species"], f["level"])
        return self._fm[fi]

    def _in_band(self, fi, level):
        f = self.foes[fi]
        return f["lo"] <= level <= f["hi"]

    def _values(self, fi, si, level, mi, move, offense):
        """The cell's four integers -- stored, or the identical engine call
        wild_matchups.cell makes, done on demand outside the band."""
        f = self.foes[fi]
        if self._in_band(fi, level):
            o, d = self.starts[fi][si]
            if offense:
                at = o + ((level - f["lo"]) * len(self.species[si]["moves"]) + mi) * self.V
                return self.off[at: at + self.V]
            at = d + ((level - f["lo"]) * len(f["moves"]) + mi) * self.V
            return self.dfn[at: at + self.V]
        import wild_matchups as W
        pm, foe = self.player(si, level), self.foe_mon(fi)
        if offense:
            a, b = E.with_entry_boosts(pm, foe)
        else:
            a, b = E.with_entry_boosts(foe, pm)
        return W.cell(a, b, move, offense)

    # ---- one move against one wild foe: engine.move_profile's numbers
    def offense(self, si, level, move, fi, badges):
        """(rate, stats) for the player's move, or None when it does nothing.
        rate is best_move's key: expected damage per turn spent."""
        boosted = badges["atk"] if _physical(move) else badges["spatk"]
        key = (si, level, move, fi, boosted)
        if key in self._off: return self._off[key]
        mi = self.move_index[si].get(move)
        hit = None
        if mi is not None:
            v = self._values(fi, si, level, mi, move, True)
            dn, dc = (v[2], v[3]) if boosted else (v[0], v[1])
            p = mq._move_stats(move, dn, dc, self.species[si]["ability"], self.foes[fi]["ability"])
            if p is not None:
                hit = (p["avg"] * p["acc"] / 100.0 / p["tpu"], p)
        self._off[key] = hit
        return hit

    def threat(self, fi, si, level, badges):
        """The wild foe's best move back (best_move over its four), as
        (expected damage per use incl. accuracy, stats) or None."""
        key = (fi, si, level, badges["def"], badges["spdef"])
        if key in self._thr: return self._thr[key]
        f = self.foes[fi]
        best = rate_best = None
        for mi, mv in enumerate(f["moves"]):
            if mv not in E.MOVES: continue
            boosted = badges["def"] if _physical(mv) else badges["spdef"]
            v = self._values(fi, si, level, mi, mv, False)
            dn, dc = (v[2], v[3]) if boosted else (v[0], v[1])
            p = mq._move_stats(mv, dn, dc, f["ability"], self.species[si]["ability"])
            if p is None: continue
            rate = p["avg"] * p["acc"] / 100.0 / p["tpu"]
            if rate_best is None or rate > rate_best:
                best, rate_best = p, rate
        hit = (best["avg"] * best["acc"] / 100.0, best) if best else None
        self._thr[key] = hit
        return hit

    # ---- one wild area: optimize._weighted_sweep, from the tables
    def sweep(self, si, level, opps, pool, badges, obey=1.0):
        """opps: [(foe index, slot chance 0..1)]. Returns the fields
        training._score_sweep reads, or None when some slot can't be KOed
        (or would take TURN_CAP+ turns), exactly as the simulator does."""
        pm = self.player(si, level)
        hp, spe = pm["stats"]["hp"], pm["stats"]["speed"]
        turns = taken = 0.0
        per = []
        for fi, w in opps:
            best = None
            for mv in pool:                      # pool order breaks ties, as best_move
                if mv not in E.MOVES: continue
                got = self.offense(si, level, mv, fi, badges)
                if got and (best is None or got[0] > best[0]):
                    best = got
            if best is None: return None
            p = best[1]
            kk = (si, level, p["move"], fi,
                  badges["atk"] if _physical(p["move"]) else badges["spatk"])
            if kk not in self._ko:
                self._ko[kk] = mq._turn_cost(p["move"], E.expected_turns_to_ko(
                    p["dist"], p["acc"], self.foes[fi]["stats"]["hp"]))
            t = self._ko[kk]
            if not math.isfinite(t) or t >= TURN_CAP: return None
            t *= obey
            thr = self.threat(fi, si, level, badges)
            # raw Speed is the effective Speed here: the only entry-ability stage
            # (Intimidate) is Attack, so no Speed stage is in play
            faster = spe > self.foes[fi]["stats"]["speed"]
            swings = max(0.0, t - (1.0 if faster else 0.0))
            tk = thr[0] * swings if thr else 0.0
            turns += w * t
            taken += w * tk
            f = self.foes[fi]
            per.append({"fi": fi, "opp": E.SPECIES[f["species"]]["name"], "oppLevel": f["level"],
                        "chance": round(w * 100, 1), "moveConst": p["move"],
                        "move": p["name"], "turns": round(t, 2), "takenHere": round(tk, 1)})
        return {"turns": turns, "hp": hp, "damageTaken": taken, "survives": taken < hp,
                "perOpponent": per}

    def opps_of(self, node):
        """An encounters.json wild node's slots as (foe index, chance), the way
        training._area_opponents weights them."""
        import wild_matchups as W
        # keyed by the node object (held, so its id() stays unique): the two
        # versions give one map's id different slots
        hit = self._opps.get(id(node))
        if hit is not None and hit[0] is node: return hit[1]
        out = []
        for s in node.get("wildMons") or []:
            w = (s.get("chance") or 0) / 100.0
            if w <= 0 or s["species"] not in E.SPECIES: continue
            out.append((self.foe_index[(s["species"], W.wild_level(s))], w))
        self._opps[id(node)] = (node, out)
        return out

# ------------------------------------------------------------------ validate
def validate(n=2000, seed=1, ws=None, areas=None, grind=None):
    """Random (species, level, area, stage, TM policy) draws: the table sweep must
    equal optimize._weighted_sweep -- same move per slot, same turns, same damage
    taken, same survival -- with the simulator's own pool, badges and obedience."""
    import optimize as O, progression as P
    T = grind
    if T is None:
        import training as T
    ws = ws or WildSpace()
    rng = random.Random(seed)
    if areas is None:
        areas = json.load(open(os.path.join(OUT, "encounters.json")))
    areas = [e for e in areas if e.get("kind") == "wild" and e.get("wildMons")]
    names = [s["const"] for s in ws.species]
    bad = checked = live = 0
    for _ in range(n):
        sp = names[rng.randrange(len(names))]
        lv = rng.randint(5, 99)
        node = areas[rng.randrange(len(areas))]
        stage = rng.randint(0, P.MAX_STAGE)
        pool = T.build_pool(sp, lv, stage, rng.choice(T.TOGGLES))
        if not pool: continue
        badges = O.badges_for(stage)
        player = E.make_mon(sp, lv)
        obey = O.obey_mult(player, badges)
        want = O._weighted_sweep(player, T._area_opponents(node)[0], pool, badges, obey)
        got = ws.sweep(ws.sp_index[sp], lv, ws.opps_of(node), pool, badges, obey)
        checked += 1
        ok = (want is None) == (got is None)
        if ok and want is not None:
            live += 1
            ok = (abs(want["turns"] - got["turns"]) < 1e-9
                  and abs(want["damageTaken"] - got["damageTaken"]) < 1e-9
                  and want["survives"] == got["survives"] and want["hp"] == got["hp"]
                  and [(p["moveConst"], p["turns"], p["takenHere"], p["opp"], p["oppLevel"],
                        p["chance"]) for p in want["perOpponent"]]
                      == [(p["moveConst"], p["turns"], p["takenHere"], p["opp"], p["oppLevel"],
                           p["chance"]) for p in got["perOpponent"]])
        if not ok:
            bad += 1
            if bad <= 5:
                print("  MISMATCH", sp, lv, node["id"], stage,
                      want and (want["turns"], want["damageTaken"]),
                      got and (got["turns"], got["damageTaken"]))
    print(f"validate: {checked} area sweeps checked ({live} grindable), {bad} mismatches")
    return bad == 0

if __name__ == "__main__":
    args = sys.argv[1:]
    cmd = args[0] if args else "help"
    if cmd == "validate":
        sys.exit(0 if validate(int(args[1]) if len(args) > 1 else 2000) else 1)
    else:
        print(__doc__)
