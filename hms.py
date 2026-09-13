#!/usr/bin/env python3
"""Which HMs a section actually needs on the party, read from the maps.

Field obstacles come from the ROM in two shapes and both are read here:

* **Object events.** A cuttable tree, a smashable rock and a pushable boulder are
  each an object on the map whose `script` is `EventScript_CutTree`,
  `EventScript_RockSmash` or `EventScript_StrengthBoulder`. Counting those gives
  49 trees, 97 rocks and 54 boulders across the game.
* **Metatile behaviours.** Surfable water and waterfalls are terrain, not
  objects, so they come from the layout's blockdata cross-referenced against the
  tileset's `metatile_attributes.bin` -- the same pair `walking.py` already
  reads for encounter tiles. Surfable is exactly the ROM's `sBehaviorSurfable`
  table; a waterfall is MB_WATERFALL.

Flash is simpler still: a map declares `requires_flash` in its own JSON, and
exactly two do -- both floors of Rock Tunnel.

Fly is the exception to all of that: it is gated on no obstacle at all. Nothing
in the game is unreachable without it, but it is the move you actually use most,
so it is treated as a standing requirement from the moment HM02 is in the bag --
the party must always be able to field it, everywhere, forever.
"""
import json, os, re, struct, functools, collections
import build_graph as G
import progression as P
import engine as E
import walking as W

REPO = G.REPO

# ------------------------------------------------------------------ the HMs
HM_MOVE = {
    "CUT": "MOVE_CUT", "FLY": "MOVE_FLY", "SURF": "MOVE_SURF",
    "STRENGTH": "MOVE_STRENGTH", "FLASH": "MOVE_FLASH",
    "ROCK_SMASH": "MOVE_ROCK_SMASH", "WATERFALL": "MOVE_WATERFALL",
}
HM_ITEM = {
    "CUT": "ITEM_HM01", "FLY": "ITEM_HM02", "SURF": "ITEM_HM03",
    "STRENGTH": "ITEM_HM04", "FLASH": "ITEM_HM05",
    "ROCK_SMASH": "ITEM_HM06", "WATERFALL": "ITEM_HM07",
}
# The badge each field move is gated on, from the field-move scripts.
HM_BADGE = {
    "FLASH": "Boulder", "CUT": "Cascade", "FLY": "Thunder", "STRENGTH": "Rainbow",
    "SURF": "Soul", "ROCK_SMASH": "Marsh", "WATERFALL": "Volcano",
}
# Fly answers no obstacle -- it is wanted everywhere, from the moment you own it.
ALWAYS = {"FLY"}

OBSTACLE_SCRIPT = {
    "EventScript_CutTree": "CUT",
    "EventScript_RockSmash": "ROCK_SMASH",
    "EventScript_StrengthBoulder": "STRENGTH",
}

# sBehaviorSurfable, verbatim from src/metatile_behavior.c
MB_WATERFALL = 0x13
SURFABLE_MB = {0x10, 0x11, 0x12, 0x13, 0x15, 0x1A, 0x1B, 0x1C, 0x1D, 0x1E, 0x1F}

def behavior(metatile_id, primary, secondary):
    """The metatile's behaviour byte -- attribute bits 0-8, masked to a byte the
    way the ROM's `MapGridGetMetatileBehaviorAt` hands it to these predicates."""
    if metatile_id < W.NUM_METATILES_IN_PRIMARY:
        attrs = W.tileset_attrs(primary); idx = metatile_id
    else:
        attrs = W.tileset_attrs(secondary); idx = metatile_id - W.NUM_METATILES_IN_PRIMARY
    if idx >= len(attrs): return 0
    return attrs[idx] & 0xFF

@functools.lru_cache(maxsize=None)
def water_profile(layout_id):
    """(surfable tiles, waterfall tiles) on this layout."""
    lay = W.layouts().get(layout_id)
    if not lay or "blockdata_filepath" not in lay: return (0, 0)
    path = os.path.join(REPO, lay["blockdata_filepath"])
    if not os.path.exists(path): return (0, 0)
    raw = open(path, "rb").read()
    grid = struct.unpack(f"<{len(raw)//2}H", raw[:len(raw)//2*2])
    primary = lay.get("primary_tileset", ""); secondary = lay.get("secondary_tileset", "")
    surf = falls = 0
    for cell in grid:
        b = behavior(cell & 0x03FF, primary, secondary)
        if b in SURFABLE_MB: surf += 1
        if b == MB_WATERFALL: falls += 1
    return (surf, falls)

# A handful of maps hold decorative water you never actually ride -- a fountain
# tile in a city, the edge of a pier. Requiring Surf for a town because it has a
# pond would be wrong, so a map has to carry a real body of water to count.
MIN_SURF_TILES = 12

@functools.lru_cache(maxsize=1)
def per_map():
    """map name -> set of HMs its terrain and objects demand."""
    out = collections.defaultdict(set)
    for path in sorted(G.map_json_paths()) if hasattr(G, "map_json_paths") else []:
        pass
    import glob
    for f in glob.glob(f"{REPO}/data/maps/*/map.json"):
        mj = json.load(open(f))
        name = os.path.basename(os.path.dirname(f))
        for o in mj.get("object_events", []):
            hm = OBSTACLE_SCRIPT.get(o.get("script"))
            if hm: out[name].add(hm)
        if mj.get("requires_flash"): out[name].add("FLASH")
        surf, falls = water_profile(mj.get("layout", ""))
        if surf >= MIN_SURF_TILES: out[name].add("SURF")
        if falls: out[name].add("WATERFALL")
    return dict(out)

# ------------------------------------------------------------------ by section
@functools.lru_cache(maxsize=1)
def _stage_index():
    """MAP_STAGE keyed on a form no spelling can disagree with.

    The map folders are CamelCase with floors already underscored
    (`SeafoamIslands_B3F`), and every camel-to-const helper in this codebase
    splits before a capital -- which turns `_B3F` into `_B_3_F` and misses
    `SEAFOAM_ISLANDS_B3F` entirely. Dropping the separators on both sides makes
    the two spellings meet: SEAFOAMISLANDSB3F.
    """
    return {k.replace("_", ""): v for k, v in P.MAP_STAGE.items()}

def _map_stage(name):
    """The section a map belongs to. Falls back to the parent area so that, say,
    SilphCo_7F inherits Silph Co."""
    idx = _stage_index()
    flat = name.upper().replace("_", "")
    if flat in idx: return idx[flat]
    st = P.location_stage(name) or P.location_stage(name.split("_")[0])
    if st is not None: return st
    base = name.split("_")[0].upper()
    if base in idx: return idx[base]
    return P.map_stage("MAP_" + G._camel_to_const(name))

@functools.lru_cache(maxsize=1)
def by_section():
    """stage -> {HM: [maps in THIS section that need it]}.

    An obstacle becomes live at `max(map's stage, HM's stage)` -- Route 2's cut
    trees sit there from the first walk through, but they are not your problem
    until you own HM01 and the Cascade Badge. This is what the party has to be
    able to field while it is standing in the section, so it is keyed on the
    maps of that section and nothing else. `kit()` handles the separate
    question of what you should be carrying around by now.
    """
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for name, hms in per_map().items():
        st = _map_stage(name)
        if st is None: continue
        for hm in hms:
            out[max(st, P.HM_STAGE[hm])][hm].append(name)
    return {st: {hm: sorted(v) for hm, v in d.items()} for st, d in out.items()}

@functools.lru_cache(maxsize=None)
def here(stage):
    """The HMs the party standing in this section must be able to use: the ones
    this section's own maps demand, plus the ones wanted everywhere (Fly), which
    answer no obstacle and so appear with no spots against them."""
    out = {hm: list(v) for hm, v in by_section().get(stage, {}).items()}
    for hm in ALWAYS:
        if stage >= P.HM_STAGE[hm]: out.setdefault(hm, [])
    return out

@functools.lru_cache(maxsize=None)
def kit(stage):
    """Everything you should own by now, cumulative, because a playthrough keeps
    walking back through where it has already been."""
    out = {}
    for st in sorted(by_section()):
        if st > stage: break
        for hm, maps in by_section()[st].items():
            out.setdefault(hm, st)
    for hm in ALWAYS:
        if stage >= P.HM_STAGE[hm]: out.setdefault(hm, P.HM_STAGE[hm])
    return out

def moves_here(stage):
    """HM moves the party must be able to field in this section, earliest first."""
    return [HM_MOVE[h] for h in sorted(here(stage), key=lambda h: P.HM_STAGE[h])]

@functools.lru_cache(maxsize=None)
def needed(stage):
    """Every HM the party has to be able to field at this section: what this
    section's own maps demand, plus everything demanded earlier -- a playthrough
    keeps walking back through the places it has already been, and you do not
    get to leave Cut in the PC because Route 2 is behind you.

    HM -> {firstNeeded, here (maps in THIS section), new}
    """
    out = {}
    for st in sorted(by_section()):
        if st > stage: break
        for hm, maps in by_section()[st].items():
            if hm not in out:
                out[hm] = {"firstNeeded": st, "here": [], "new": st == stage}
            if st == stage:
                out[hm]["here"] = maps
    return out

def required_moves(stage):
    """The HM moves a party at this stage has to be able to hold, in the order
    you first need them."""
    return [HM_MOVE[h] for h in sorted(needed(stage), key=lambda h: P.HM_STAGE[h])]

# ------------------------------------------------------------------ who can hold what
def can_learn(species, move):
    """HM compatibility straight from the TM/HM table."""
    item = HM_ITEM.get(_hm_of(move))
    if not item: return False
    tag = item.replace("ITEM_", "")
    return any(t.startswith(tag) for t in E.TMHM.get(species, []))

def _hm_of(move):
    for k, v in HM_MOVE.items():
        if v == move: return k
    return None

if __name__ == "__main__":
    pm = per_map()
    tot = collections.Counter()
    for hms in pm.values():
        for h in hms: tot[h] += 1
    print("maps carrying each field obstacle:", dict(tot))
    print()
    for st in sorted(by_section()):
        d = by_section()[st]
        nm = P.STAGE_BY_ID.get(st, {}).get("name", "?")
        print(f"  {st:2d} {nm[:34]:34s} " +
              ", ".join(f"{h}({len(v)})" for h, v in sorted(d.items())))
