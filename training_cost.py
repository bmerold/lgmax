"""What it costs to bring a Pokemon up to a section's level -- the price of the
"least training" objective.

The section simulator assumes every party member is already at the section's
level. For a Pokemon that has been fighting all along that is (roughly) true:
trainer battles paid its way. A NEWCOMER, or one coming back off the bench, has to
be ground up from where it actually is. This module prices that grind in the same
currency the simulator uses for battles -- turns -- so a newcomer earns its slot
only when it saves more battle turns than its training costs.

Sources (every fact from the decomp or from the grind simulator built on it):
  * The level you obtain a Pokemon at:
      - wild: the highest level its earliest encounter slot offers (progression
        records min/max from src/data/wild_encounters.json);
      - gifts, prizes, fossils, the starter: `givemon <species>, <level>` in the
        map scripts (data/maps/*/scripts.inc), including the variable form used by
        the Game Corner prize counter (`switch` / `case SPECIES_X, Label` ->
        `givemon VAR_TEMP_1, N`, with .ifdef FIRERED/LEAFGREEN) and the Dojo/Oak's
        Lab (`setvar VAR, SPECIES_X` ... `givemon VAR, N`);
      - static encounters: `setwildbattle <species>, <level>`;
      - in-game trades: the level of the Pokemon you hand over
        (CreateInGameTradePokemonInternal, src/trade_scene.c, reads
        gPlayerParty[playerSlot]'s MON_DATA_LEVEL);
      - evolutions: the level the line's base was obtained at (you train the base).
  * Turns to gain each level: the grind simulator's itineraries
    (data/training_<v>.json, journey mode, renewable TMs) -- the best wild area
    reachable at that point, the moves it would use, turns per level. Built by
    training.py (opt-in); the regular build reuses the cached file.
  * An in-game trade Pokemon earns 1.5x experience (the "traded mon" boost in
    Cmd_getexp, src/battle_script_commands.c), so its grind takes 2/3 the turns.
"""
import json, os, re
import engine as E
import progression as P
import constraints as C

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TRADED_EXP = 1.5
GRIND_MODE, GRIND_TM = "journey", "renew"   # the app's default grind view

# ------------------------------------------------------------------ obtain levels
def _version_lines(text):
    """Drop the lines inside the other version's .ifdef block."""
    keep, out, stack = True, [], []
    mine = "FIRERED" if E.VERSION == "fr" else "LEAFGREEN"
    for line in text.splitlines():
        s = line.strip()
        m = re.match(r"\.ifdef\s+(\w+)", s)
        if m:
            stack.append(keep); keep = keep and m.group(1) == mine; continue
        if s == ".else":
            parent = stack[-1] if stack else True
            keep = parent and not keep; continue
        if s == ".endif":
            keep = stack.pop() if stack else True; continue
        if keep: out.append(line)
    return out

def _script_levels():
    """species -> level from givemon / setwildbattle across every map script."""
    base = os.path.join(E.REPO, "data/maps")
    out = {}
    for mp in sorted(os.listdir(base)):
        path = os.path.join(base, mp, "scripts.inc")
        if not os.path.exists(path): continue
        lines = _version_lines(open(path).read())
        alias = {m.group(1): m.group(2) for m in
                 (re.match(r"\.equ\s+(\w+),\s*(\w+)", l.strip()) for l in lines) if m}
        def var(v): return alias.get(v, v)
        # label -> the givemon level inside it
        label, give_in = None, {}
        setvars, gives = {}, []
        cases = []
        for l in lines:
            s = l.strip()
            m = re.match(r"^(\w+)::?$", s)
            if m: label = m.group(1); continue
            m = re.match(r"(?:givemon|setwildbattle)\s+(\w+)\s*,\s*(\d+)", s)
            if m:
                who, lv = m.group(1), int(m.group(2))
                if who.startswith("SPECIES_"):
                    out.setdefault(who, lv)
                else:
                    gives.append((var(who), lv))
                    if label: give_in[label] = lv
                continue
            m = re.match(r"setvar\s+(\w+)\s*,\s*(SPECIES_\w+)", s)
            if m: setvars.setdefault(var(m.group(1)), set()).add(m.group(2)); continue
            m = re.match(r"case\s+(SPECIES_\w+)\s*,\s*(\w+)", s)
            if m: cases.append((m.group(1), m.group(2)))
        # a `case SPECIES_X, Label` dispatch names the exact block per species
        for sp, lab in cases:
            if lab in give_in: out.setdefault(sp, give_in[lab])
        # otherwise every species set into the given variable gets its level
        for v, lv in gives:
            for sp in setvars.get(v, ()):
                out.setdefault(sp, lv)
    return out

_LEVELS = None
def obtained_level(species, avail, _seen=None):
    """The level the run first holds this species at (see the module doc)."""
    global _LEVELS
    if _LEVELS is None: _LEVELS = _script_levels()
    rec = avail.get(species) or {}
    kind = rec.get("kind")
    if kind == "wild":
        return rec.get("maxLevel") or rec.get("minLevel") or 5
    if kind == "evolution":
        seen = (_seen or set()) | {species}
        frm = rec.get("from")
        return obtained_level(frm, avail, seen) if frm and frm not in seen else 5
    if kind == "ingame_trade":
        given = next((g for r, g, _st, _n in P.INGAME_TRADES if r == species), None)
        return obtained_level(given, avail, (_seen or set()) | {species}) if given else 5
    if species in _LEVELS:
        return _LEVELS[species]
    return 5

def exp_factor(species, avail):
    return TRADED_EXP if (avail.get(species) or {}).get("kind") == "ingame_trade" else 1.0

# ------------------------------------------------------------------ grind turns
_TPL = None
def _turns_per_level():
    """species name -> {level: turns to gain that level}, from the itineraries."""
    global _TPL
    if _TPL is not None: return _TPL
    _TPL = {}
    path = os.path.join(OUT, f"training_{E.VERSION}.json")
    if not os.path.exists(path):
        path = os.path.join(OUT, "training.json")
    data = json.load(open(path)) if os.path.exists(path) else {"itineraries": {}}
    for name, modes in data["itineraries"].items():
        per = {}
        for seg in (modes.get(GRIND_MODE) or {}).get(GRIND_TM) or []:
            tpl = (seg["tplLo"] + seg["tplHi"]) / 2.0
            for lv in range(seg["from"], seg["to"] + 1):
                per[lv] = tpl
        _TPL[name] = per
    return _TPL

_PRE = None
def _pre_evo(species):
    global _PRE
    if _PRE is None:
        _PRE = {ev["to"]: frm for frm, evs in E.EVOS.items() for ev in evs}
    return _PRE.get(species)

def _level_turns(species, lv):
    """Turns for `species` (or, below its evolution level, its pre-evolution) to
    gain level lv -> lv+1; None when the grind simulator has no spot for it."""
    tpl = _turns_per_level()
    sp = species
    while sp:
        v = tpl.get(E.SPECIES[sp]["name"], {}).get(lv)
        if v is not None: return v
        sp = _pre_evo(sp)
    return None

_FALLBACK = {}
def _fallback(lv):
    """The median species' turns at this level -- for the rare level a species'
    itinerary skips (no reachable spot then)."""
    if lv not in _FALLBACK:
        vals = sorted(per[lv] for per in _turns_per_level().values() if lv in per)
        _FALLBACK[lv] = vals[len(vals) // 2] if vals else 0.0
    return _FALLBACK[lv]

def grind_turns(species, frm, to):
    """Turns to grind `species` from level frm up to level to."""
    total = 0.0
    for lv in range(max(1, int(frm)), int(to)):
        v = _level_turns(species, lv)
        total += v if v is not None else _fallback(lv)
    return total

def join_cost(species, level, have_level, avail):
    """Turns of training to field `species` at `level` when it is at have_level."""
    if have_level >= level: return 0.0
    return grind_turns(species, have_level, level) / exp_factor(species, avail)

# ------------------------------------------------------------------ the roster
class Roster:
    """Who the run has trained, and to what level -- the one place that decides
    what a party member owes (the solver's objective, the guide's bill and the
    verify guard all ask it).

    A line fielded in the previous section that had a party has kept pace by
    fighting, so it owes nothing. A line that sat out owes the levels since it
    was last fielded; one never fielded owes them from its obtain level."""
    def __init__(self):
        self.last = {}        # line root -> level it was last fielded at
        self.kept = set()     # roots in the previous section's party

    def copy(self):
        r = Roster(); r.last = dict(self.last); r.kept = set(self.kept)
        return r

    def owed(self, species, level, avail):
        """(grind turns, from level, returning?) to field `species` at `level`."""
        root = C.line_root(species)
        if root in self.kept:
            return 0.0, level, False
        have = self.last.get(root)
        frm = have if have is not None else obtained_level(species, avail)
        return join_cost(species, level, frm, avail), frm, have is not None

    def field(self, species_list, level):
        """Record a section's party (skip sections without one: a party that
        crossed no battles neither kept pace nor fell behind)."""
        if not species_list: return
        self.kept = {C.line_root(sp) for sp in species_list}
        for root in self.kept:
            self.last[root] = level
