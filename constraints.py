#!/usr/bin/env python3
"""What a single playthrough can actually hold at once.

Three rules the raw rankings don't know about:

1. An evolution line is ONE Pokemon. Listing Charmander and Charmeleon side by
   side, or Vaporeon and Flareon (both are the one Eevee you are given), offers
   a choice that doesn't exist. Branching lines collapse to their shared root,
   which is exactly what makes the three Eeveelutions mutually exclusive.

2. Some separate lines are still one-or-the-other because the game hands you a
   single item or prize: the starter, the Mt. Moon fossil, the Fighting Dojo.

3. Those choices are permanent and apply to the WHOLE RUN, not to one fight.
   Solving each section on its own produced a Vaporeon in Saffron City and a
   Jolteon in the Saffron Gym next door -- impossible, because there is one
   Eevee and one stone. A run therefore commits to a single option per group,
   and every section from then on lives with it.
"""
import json, os, functools as _ft
import engine as E

# ------------------------------------------------------------------ evolution lines
def _build_roots():
    pre = {}
    for src, evos in E.EVOS.items():
        for ev in evos:
            pre[ev["to"]] = src
    roots = {}
    for sp in E.SPECIES:
        cur, seen = sp, set()
        while cur in pre and cur not in seen:
            seen.add(cur)
            cur = pre[cur]
        roots[sp] = cur
    return roots

LINE_ROOT = _build_roots()

def line_root(species):
    """The base form of this species' evolution line. Vaporeon, Jolteon and
    Flareon all return Eevee, so only one of them can hold a slot."""
    return LINE_ROOT.get(species, species)

@_ft.lru_cache(maxsize=None)
def line_members(root):
    """Every form in an evolution line, given its base form."""
    return tuple(sorted(sp for sp in LINE_ROOT if LINE_ROOT[sp] == root)) or (root,)

def form_at(root, stage, avail):
    """The form of this line you would actually be holding at this section --
    the most evolved one that is reachable and that you have not levelled past."""
    # `allowed` matters for branching lines: Hitmonlee and Hitmonchan share the
    # root Tyrogue but are alternatives, not stages, so the run's commitment is
    # what says which one you are actually holding. Same for the Eeveelutions.
    have = [sp for sp in line_members(root)
            if sp in avail and avail[sp]["stage"] <= stage
            and not outgrown(sp, stage, avail) and allowed(sp)]
    if not have: return None
    return max(have, key=lambda sp: sum(E.SPECIES[sp][k] for k in
               ("baseHP", "baseAttack", "baseDefense",
                "baseSpAttack", "baseSpDefense", "baseSpeed")))

def outgrown(species, stage, avail):
    """True if a LEVEL evolution of this Pokemon is already reachable by this
    section, i.e. you would be holding the evolved form and not this one.

    Only level evolutions count. Declining a Thunder Stone to keep Pikachu's
    level-up moves is a real decision a player makes; staying a Bulbasaur past
    level 16 is not one, and letting the run-wide continuity tiebreak prefer the
    Bulbasaur you were already carrying over the Ivysaur it became was simply
    wrong.
    """
    for e in E.EVOS.get(species, []):
        if e.get("method") != "LEVEL": continue
        rec = avail.get(e["to"])
        if rec and rec["stage"] <= stage: return True
    return False

# ------------------------------------------------------------------ one-of groups
STARTER_LINES = {
    "Bulbasaur": "SPECIES_BULBASAUR",
    "Charmander": "SPECIES_CHARMANDER",
    "Squirtle": "SPECIES_SQUIRTLE",
}
_ROOT_TO_STARTER = {v: k for k, v in STARTER_LINES.items()}

# Keyed by LINE ROOT. Hitmonlee and Hitmonchan already share the root Tyrogue,
# so the line rule alone keeps them apart -- the group only supplies the label.
EXCLUSIVE_GROUPS = {
    # the Mt. Moon fossil: Helix or Dome, never both
    "fossil": {"SPECIES_OMANYTE", "SPECIES_KABUTO"},
    # the Saffron Dojo hands over one of the two
    "dojo": {"SPECIES_TYROGUE"},
}
# The three Eeveelutions need no group: they share the root Eevee, and the game
# gives you exactly one Eevee.
GROUP_LABEL = {
    "fossil": "the Mt. Moon fossil",
    "dojo": "the Fighting Dojo prize",
    "starter": "your starter",
}

_ROOT_GROUP = {}
for gid, roots in EXCLUSIVE_GROUPS.items():
    for r in roots:
        _ROOT_GROUP[r] = gid
for r in _ROOT_TO_STARTER:
    _ROOT_GROUP[r] = "starter"

# ------------------------------------------------------------------ run-wide commitments
# Choices a playthrough makes once and keeps. Each option lists every species
# that counts as having made that choice, so evolved forms follow the pick.
# Eevee itself is deliberately absent: holding an unevolved Eevee commits to
# nothing yet.
COMMIT_GROUPS = {
    "eevee": {
        "SPECIES_VAPOREON": {"SPECIES_VAPOREON"},
        "SPECIES_JOLTEON":  {"SPECIES_JOLTEON"},
        "SPECIES_FLAREON":  {"SPECIES_FLAREON"},
    },
    "fossil": {
        "SPECIES_OMANYTE": {"SPECIES_OMANYTE", "SPECIES_OMASTAR"},
        "SPECIES_KABUTO":  {"SPECIES_KABUTO", "SPECIES_KABUTOPS"},
    },
    "dojo": {
        "SPECIES_HITMONLEE":  {"SPECIES_HITMONLEE"},
        "SPECIES_HITMONCHAN": {"SPECIES_HITMONCHAN"},
    },
}
_SPECIES_COMMIT = {}          # species -> (group id, option id)
for _g, _opts in COMMIT_GROUPS.items():
    for _opt, _members in _opts.items():
        for _sp in _members:
            _SPECIES_COMMIT[_sp] = (_g, _opt)

COMMITMENTS = {}              # group id -> chosen option id

def set_commitments(picks):
    """Lock the run's one-time choices. `picks` maps group id to option species."""
    COMMITMENTS.clear()
    COMMITMENTS.update({g: o for g, o in (picks or {}).items() if g in COMMIT_GROUPS})

def load_commitments(path):
    if os.path.exists(path):
        with open(path) as f:
            set_commitments(json.load(f).get("picks", {}))
    return dict(COMMITMENTS)

def allowed(species):
    """False when this species contradicts a choice the run already made."""
    hit = _SPECIES_COMMIT.get(species)
    if not hit: return True
    group, option = hit
    picked = COMMITMENTS.get(group)
    return picked is None or picked == option

def commitment_of(species):
    hit = _SPECIES_COMMIT.get(species)
    return hit[0] if hit else None

def starter_of(species):
    """Which starter's line this species belongs to, or None."""
    return _ROOT_TO_STARTER.get(line_root(species))

def exclusive_group(species):
    """Group id when this species competes with another line for one slot."""
    return _ROOT_GROUP.get(line_root(species))

def conflicts(species, held_roots, held_groups, allow_starter_group=True):
    """True when this species can't join a party that already holds these."""
    r = line_root(species)
    if r in held_roots: return True
    g = exclusive_group(species)
    if g and g in held_groups:
        if g == "starter" and not allow_starter_group: return False
        return True
    return False

def dedupe_ranked(rows, key=lambda r: r["species"], keep_starter_variants=True):
    """Collapse a ranked list to choices a real playthrough could make.

    Keeps the best-placed member of each evolution line and of each one-of group,
    recording what it displaced so the page can say "or Jolteon, or Flareon".
    Starter lines are kept separately when `keep_starter_variants` is set, because
    the page filters those by whichever starter the reader picked.
    """
    rows = _committed_first(rows, key)
    out, seen_roots, seen_groups = [], set(), set()
    for r in rows:
        sp = key(r)
        root, grp = line_root(sp), exclusive_group(sp)
        if root in seen_roots:
            continue          # a lesser form of something already listed
        if grp and grp in seen_groups and not (grp == "starter" and keep_starter_variants):
            _note_alt(out, root, grp, r, key)
            continue
        seen_roots.add(root)
        if grp and not (grp == "starter" and keep_starter_variants):
            seen_groups.add(grp)
        r = dict(r)
        r["lineRoot"] = root
        r["starterLine"] = starter_of(sp)
        r["exclusiveGroup"] = grp
        r["alts"] = []
        out.append(r)
    return out

def _committed_first(rows, key):
    """Float the run's committed option to the front of its own group, so the
    collapse keeps Vaporeon (which the run picked) and files Flareon under
    "or ...", rather than keeping whichever happened to win this one fight."""
    if not COMMITMENTS: return list(rows)
    ranked, deferred = [], []
    for r in rows:
        sp = key(r)
        g = commitment_of(sp)
        (deferred if (g and not allowed(sp)) else ranked).append(r)
    return ranked + deferred

def _note_alt(out, root, grp, dropped, key):
    """Attach a displaced option to whichever kept row displaced it."""
    for r in out:
        same_line = r["lineRoot"] == root
        same_grp = grp and r.get("exclusiveGroup") == grp
        if same_line or same_grp:
            name = dropped.get("name") or key(dropped)
            if name not in r["alts"]:
                r["alts"].append(name)
            return
