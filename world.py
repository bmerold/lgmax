#!/usr/bin/env python3
"""The game as one walkable graph: every tile of every map, joined the way the
player actually moves.

Nodes are (map, x, y) tiles. Edges are the four walking directions (1 step),
one-way ledge hops (MB_JUMP_*), spin-tile slides (the Rocket Hideout arrow
floors, resolved to wherever the slide deposits you), warps (a directed edge
from the warp tile to its destination warp's tile), and outdoor map
connections with their offsets. Everything is read from the decomp: collision
bits from map.bin, behaviors from metatile_attributes.bin (attr & 0x1FF, per
src/fieldmap.c), warps and object events from map.json.

The graph is stage-gated the same way the rest of the model is:

  * a map opens at its progression stage (progression.MAP_STAGE);
  * surfable water opens once Surf is usable (progression.HM_STAGE);
  * a cut tree, smashable rock or Strength boulder blocks its tile until
    max(map stage, that HM's stage) — the same rule hms.py applies;
  * elevator warps (MAP_DYNAMIC) are skipped: every elevator building also
    has stairs, and the elevator's return warp is set at runtime.

Approximations, all small and all deliberate: object events (NPCs, item
balls, trainers) are treated as walkable rather than modelling that you talk
to them from an adjacent tile; the Seafoam current puzzle and the Strength
boulder puzzles are not state-modelled (currents count as surfable water once
Surf is live); collapsing floors and other script tricks are ignored.
"""
import json, os, struct, functools, collections, glob
import route_order as R
import walking as W
import progression as P
import hms as HM

REPO = R.REPO
NUM_METATILES_IN_PRIMARY = 640

# metatile behaviors (include/constants/metatile_behaviors.h)
MB_JUMP = {0x38: (1, 0), 0x39: (-1, 0), 0x3A: (0, -1), 0x3B: (0, 1)}  # E W N S
MB_SPIN = {0x54: (1, 0), 0x55: (-1, 0), 0x56: (0, -1), 0x57: (0, 1)}
MB_STOP_SPINNING = 0x58
MB_WATERFALL = 0x13
# water currents (Seafoam) ride like surf once Surf is live -- the boulder
# puzzle that stills them is not state-modelled
MB_CURRENT = {0x50, 0x51, 0x52, 0x53}
SURFABLE = set(HM.SURFABLE_MB) | MB_CURRENT | {MB_WATERFALL}
# Directional walls: MB_IMPASSABLE_* tiles have collision 0 but block
# movement across one edge -- Mt. Moon's ridge strips are exactly these.
# Moving in a direction is blocked if the SOURCE tile walls that edge or the
# DESTINATION tile walls the opposite edge (IsMetatileDirectionallyImpassable).
_BLOCK_N = {0x32, 0x34, 0x35}
_BLOCK_S = {0x33, 0x36, 0x37}
_BLOCK_E = {0x30, 0x34, 0x36}
_BLOCK_W = {0x31, 0x35, 0x37}

# Cycling Road (0xD0/0xD1) pulls you south when coasting, but bike.c has a
# real BIKE_TRANS_UPHILL for it -- pedalling up is allowed, so those tiles
# are ordinary ground to the router.

DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))

# ------------------------------------------------------------------ per-map grids
@functools.lru_cache(maxsize=1)
def _layouts():
    data = json.load(open(f"{REPO}/data/layouts/layouts.json"))["layouts"]
    return {l["id"]: l for l in data if l and "id" in l}

# Four maps swap to a different layout as the story advances
# (setmaplayoutindex), and in all four the final state is the open one:
# Seafoam's currents stop once the boulders drop, the Dunsparce tunnel gets
# dug out post-National-Dex, the Seven Island back room unlocks. Route with
# the final layout.
ALT_LAYOUT = {
    "SeafoamIslands_B3F": "LAYOUT_SEAFOAM_ISLANDS_B3F_CURRENT_STOPPED",
    "SeafoamIslands_B4F": "LAYOUT_SEAFOAM_ISLANDS_B4F_CURRENT_STOPPED",
    "ThreeIsland_DunsparceTunnel": "LAYOUT_THREE_ISLAND_DUNSPARCE_TUNNEL_DUG_OUT",
    "SevenIsland_House_Room1": "LAYOUT_SEVEN_ISLAND_HOUSE_ROOM1_DOOR_OPEN",
}

# Thin ice (MB_THIN_ICE) cracks underfoot and drops you to the floor below
# (warphole, landing on the same x/y). Icefall Cave is the one place the
# route needs it -- it is the intended way down.
HOLE_DROP = {"FourIsland_IcefallCave_1F": "FourIsland_IcefallCave_B1F"}
MB_THIN_ICE = 0x26

class Grid:
    """One map's tiles: walkability, behavior, and the events standing on it."""
    __slots__ = ("name", "w", "h", "beh", "coll", "enc", "elev", "warps",
                 "warp_tiles", "obstacles", "scripted_open", "blockers",
                 "barrier_gates", "bodies")
    def __init__(self, name):
        mj = R.maps()[name]
        lay = _layouts()[ALT_LAYOUT.get(name) or mj["layout"]]
        self.name = name
        self.w, self.h = lay["width"], lay["height"]
        raw = open(f"{REPO}/{lay['blockdata_filepath']}", "rb").read()
        cells = struct.unpack(f"<{self.w * self.h}H", raw[:self.w * self.h * 2])
        prim, sec = lay.get("primary_tileset", ""), lay.get("secondary_tileset", "")
        pa, sa = W.tileset_attrs(prim), W.tileset_attrs(sec)
        beh = bytearray(self.w * self.h)
        coll = bytearray(self.w * self.h)
        enc = bytearray(self.w * self.h)
        elev = bytearray(self.w * self.h)
        for i, cell in enumerate(cells):
            coll[i] = (cell >> 10) & 3
            elev[i] = (cell >> 12) & 0xF
            mt = cell & 0x3FF
            attrs, idx = (pa, mt) if mt < NUM_METATILES_IN_PRIMARY else \
                         (sa, mt - NUM_METATILES_IN_PRIMARY)
            if idx < len(attrs):
                beh[i] = attrs[idx] & 0xFF  # behaviors fit a byte
                enc[i] = (attrs[idx] & 0x07000000) >> 24
        self.beh, self.coll, self.enc, self.elev = beh, coll, enc, elev
        self.warps = [(w.get("x", 0), w.get("y", 0), w.get("dest_map", ""),
                       w.get("dest_warp_id", "0"))
                      for w in mj.get("warp_events", [])]
        # doors carry collision 1 and the game special-cases stepping into
        # them, so a warp's own tile is always enterable
        self.warp_tiles = {(w[0], w[1]) for w in self.warps}
        # HM obstacles standing on tiles, with the stage each melts away
        self.obstacles = {}
        # A trainer or NPC is a body you never stand on: you talk to it from the
        # tile beside it, or trip its sight line -- it never steps aside. `bodies`
        # is every such tile (trainer/NPC, mover or not); the drawn walk stops one
        # tile short of it. `blockers` is the narrower set that also walls through-
        # traffic (standing bodies only -- a pacer only holds its spawn tile, so it
        # can be slipped past). Item balls vanish on pickup, so they're neither.
        self.blockers = set()
        self.bodies = set()
        for o in mj.get("object_events", []):
            hm = HM.OBSTACLE_SCRIPT.get(o.get("script"))
            if hm:
                st = max(_map_stage(name) or 0, P.HM_STAGE[hm])
                self.obstacles[(o.get("x", 0), o.get("y", 0))] = st
                continue
            gfx = o.get("graphics_id", "")
            if "ITEM_BALL" in gfx: continue
            xy = (o.get("x", 0), o.get("y", 0))
            self.bodies.add(xy)
            mt = o.get("movement_type", "") or ""
            moves = ("WANDER" in mt or "RUN" in mt
                     or ("WALK" in mt and "IN_PLACE" not in mt))
            if not moves:
                self.blockers.add(xy)

        # tiles a script ever swaps to a passable metatile: switch-opened
        # barriers (Mansion switches, the Rocket Hideout door, the Elite Four
        # chambers). The story opens them at the right moment; the map's own
        # stage gate is the approximation of when.
        self.scripted_open = _scripted_open(name)
        # trainer-gated barrier tiles: passable only once their fight is won.
        # RemoveBarrier lists them as `setmetatile _, 0` too, so they'd otherwise
        # land in scripted_open (always-open) -- keep them out of it and gate them.
        self.barrier_gates = {}
        for tiles, gates in gated_barriers().get(name, ()):
            for t in tiles:
                self.barrier_gates[t] = gates
        if self.barrier_gates:
            self.scripted_open = frozenset(self.scripted_open) - self.barrier_gates.keys()
        if name.startswith("PokemonLeague_"):
            # entering a chamber, the game force-walks you up through the
            # sealed entry door (Common_Movement_WalkUp5) -- scripted moves
            # ignore collision, so the strip above each warp is passable
            extra = set(self.scripted_open)
            for wx, wy, _, _ in self.warps:
                extra.update((wx, wy - dy) for dy in range(1, 5) if wy - dy >= 0)
            self.scripted_open = frozenset(extra)

    def inb(self, x, y): return 0 <= x < self.w and 0 <= y < self.h
    def b(self, x, y): return self.beh[y * self.w + x]
    def c(self, x, y): return self.coll[y * self.w + x]

import re as _re
_SETMETA = _re.compile(r"setmetatile\s+(\d+),\s*(\d+),\s*\S+,\s*0\b")

@functools.lru_cache(maxsize=1)
def _mansion_open():
    """The Mansion's secret switches live in one shared script file, with the
    floor encoded in the label (PressSwitch_1F / ResetSwitch_B1F...). Union
    both switch states: every tile that can ever be floor counts as floor --
    the walk between re-presses is not modelled."""
    path = f"{REPO}/data/scripts/pokemon_mansion.inc"
    out = collections.defaultdict(set)
    cur = None
    for line in open(path):
        lbl = _re.match(r"(\w+)::", line)
        if lbl:
            fl = _re.search(r"Switch_(B?\dF)$", lbl.group(1))
            cur = f"PokemonMansion_{fl.group(1)}" if fl else cur
        m = _SETMETA.search(line)
        if m and cur:
            out[cur].add((int(m.group(1)), int(m.group(2))))
    return {k: frozenset(v) for k, v in out.items()}

@functools.lru_cache(maxsize=1)
def _league_open():
    """Every Elite Four chamber opens its exit door the same way, from one
    shared script -- the same tiles in every room."""
    out = set()
    for m in _SETMETA.finditer(
            open(f"{REPO}/data/scripts/pokemon_league.inc").read()):
        out.add((int(m.group(1)), int(m.group(2))))
    return frozenset(out)

@functools.lru_cache(maxsize=None)
def _scripted_open(name):
    """Tiles this map's scripts ever set passable: `setmetatile x, y, _, 0`."""
    out = set(_mansion_open().get(name, ()))
    if name.startswith("PokemonLeague_"):
        out |= _league_open()
    path = f"{REPO}/data/maps/{name}/scripts.inc"
    if os.path.exists(path):
        for m in _SETMETA.finditer(open(path).read()):
            out.add((int(m.group(1)), int(m.group(2))))
    return frozenset(out)

# A barrier wall the game drops until a specific fight is won, then unlocks
# (playse SE_UNLOCK). The Rocket Hideout's two doors are the only ones gated on
# beating a trainer: B1F opens when Grunt 12 falls, B4F when both "door grunts"
# (16 and 17) do -- data/maps/RocketHideout_B{1,4}F/scripts.inc, the SetBarrier
# / call_if[_not]_defeated pair. Modelled as tiles that stay impassable until
# their gate trainers are in the fought set, so the walk can't clip the closed
# door before earning it. (Victory Road's and Cinnabar's barriers are boulder-
# and quiz-gated, a different mechanism, so they carry no call_if_defeated and
# are not caught here.)
_BARRIER_TILE = _re.compile(
    r"setmetatile\s+(\d+),\s*(\d+),\s*METATILE_\w*Barrier\w*,\s*1")
_BARRIER_GATE = _re.compile(r"call_if(?:_not)?_defeated\s+(TRAINER_\w+)")

@functools.lru_cache(maxsize=1)
def gated_barriers():
    """map -> [(frozenset of barrier tiles, frozenset of gate trainer consts)].
    A tile is passable only once every gate trainer of its barrier is beaten."""
    out = {}
    for f in glob.glob(f"{REPO}/data/maps/*/scripts.inc"):
        txt = open(f).read()
        tiles = {(int(a), int(b)) for a, b in _BARRIER_TILE.findall(txt)}
        gates = frozenset(_BARRIER_GATE.findall(txt))
        if tiles and gates:
            out[os.path.basename(os.path.dirname(f))] = [(frozenset(tiles), gates)]
    return out

# Trainer consts whose defeat has opened a gated barrier so far. None means
# "every gate open" -- the default, so reachability, the distance matrix and
# every non-tour caller behave exactly as before. tour.py sets it to the set of
# gate trainers fought so far while it draws a leg, so a pre-fight segment routes
# around a still-closed door and a post-fight one walks through it.
_OPEN_GATES = None

@functools.lru_cache(maxsize=None)
def grid(name):
    return Grid(name)

# Which stage each map opens at. The progression tables cover the maps that
# matter to battles; the connective tissue between them -- gate houses,
# entrance floors, underground paths, harbors -- carries no stage of its own
# and used to default to "post-game", walling off everything behind it. Here
# a map with no explicit stage inherits the earliest stage of anything it
# touches (warps or connections), to a fixpoint. Event-only islands stay
# closed: no boat in this model sails to Navel Rock or Birth Island.
_CLOSED_PREFIXES = ("NavelRock", "BirthIsland", "TrainerTower_",
                    "BattleColosseum", "TradeCenter", "RecordCorner",
                    "UnionRoom")

# Maps whose battles come later but whose GROUND is crossed earlier: the
# stage-5 walk into Mt. Moon passes the west stub of Route 4 (that's where
# the cave mouth and the Center are), even though Route 4's own trainers are
# stage 6. Same reading sections.py takes via center_between().
_OPEN_EARLY = {"Route4": 5}

@functools.lru_cache(maxsize=1)
def stage_table():
    explicit, adj = {}, collections.defaultdict(set)
    names = [n for n in R.maps()
             if not n.startswith(_CLOSED_PREFIXES)]
    folder = _const_to_folder()
    for name in names:
        mj = R.maps()[name]
        cid = (mj.get("id") or "").replace("MAP_", "")
        st = P.MAP_STAGE.get(cid)
        if st is None: st = P.LOCATION_STAGE.get(name)
        if st is None:
            if cid.startswith(("FOUR_ISLAND", "FIVE_ISLAND", "SIX_ISLAND",
                               "SEVEN_ISLAND")):
                st = 33
            elif cid.startswith(("ONE_ISLAND", "TWO_ISLAND", "THREE_ISLAND",
                                 "MT_EMBER")):
                st = 28
        if name in _OPEN_EARLY:
            st = _OPEN_EARLY[name] if st is None else min(st, _OPEN_EARLY[name])
        if st is not None: explicit[name] = st
        for w in mj.get("warp_events", []):
            nb = folder.get(w.get("dest_map", ""))
            if nb and not nb.startswith(_CLOSED_PREFIXES):
                adj[name].add(nb); adj[nb].add(name)
        for c in (mj.get("connections") or []):
            nb = R._const_name(c["map"])
            if nb and not nb.startswith(_CLOSED_PREFIXES):
                adj[name].add(nb); adj[nb].add(name)
    table = dict(explicit)
    changed = True
    while changed:
        changed = False
        for name in names:
            if name in explicit: continue
            best = min((table[nb] for nb in adj[name] if nb in table),
                       default=None)
            if best is not None and table.get(name) != best:
                if name not in table or best < table[name]:
                    table[name] = best; changed = True
    return table

@functools.lru_cache(maxsize=None)
def _map_stage(name):
    return stage_table().get(name)

@functools.lru_cache(maxsize=1)
def _const_to_folder():
    out = {}
    for name, mj in R.maps().items():
        cid = mj.get("id", "")
        if cid: out[cid] = name
    return out

# ------------------------------------------------------------------ ferries
# The Seagallop is a scripted boat, not a warp: without these edges the Sevii
# Islands are unreachable. Anchored on the pier tiles; a trip costs a nominal
# fare of steps. The chain mirrors the in-game ferry stops.
FERRY_COST = 40
_FERRIES = [
    ("VermilionCity", (23, 34), "OneIsland_Harbor", None, 28),
    ("OneIsland_Harbor", None, "TwoIsland_Harbor", None, 28),
    ("TwoIsland_Harbor", None, "ThreeIsland_Harbor", None, 28),
    ("OneIsland_Harbor", None, "FourIsland_Harbor", None, 33),
    ("FourIsland_Harbor", None, "FiveIsland_Harbor", None, 33),
    ("FiveIsland_Harbor", None, "SixIsland_Harbor", None, 33),
    ("SixIsland_Harbor", None, "SevenIsland_Harbor", None, 33),
]

# Elevators are MAP_DYNAMIC warps -- the car itself has no static return
# edge, but the set of floors an elevator serves is static: every warp that
# points into the same elevator map is one stop. Link the stops directly.
# Rocket Hideout genuinely needs this: B4F's right wing has no stairs.
ELEVATOR_COST = 8

@functools.lru_cache(maxsize=1)
def _elevator_edges():
    folder = _const_to_folder()
    stops = collections.defaultdict(list)
    for name, mj in R.maps().items():
        for w in mj.get("warp_events", []):
            dname = folder.get(w.get("dest_map", ""))
            if dname and dname.endswith("_Elevator"):
                stops[dname].append((name, w.get("x", 0), w.get("y", 0)))
    out = collections.defaultdict(list)
    for group in stops.values():
        for a in group:
            for b in group:
                if a != b: out[a].append(b)
    return dict(out)

@functools.lru_cache(maxsize=1)
def _ferry_edges():
    def anchor(name, xy):
        if xy is None:
            w = grid(name).warps[0]
            xy = (w[0], w[1])
        return (name, xy[0], xy[1])
    out = collections.defaultdict(list)
    for a, axy, b, bxy, st in _FERRIES:
        na, nb = anchor(a, axy), anchor(b, bxy)
        out[na].append((nb, st))
        out[nb].append((na, st))
    return dict(out)

# ------------------------------------------------------------------ movement rules
# Reachability (what is collectable, and the TSP distances) ignores bodies --
# the game is always completable, and some rooms sit behind an NPC you talk past
# or one that paces out of the way. Only path DRAWING honors bodies, so the line
# the guide shows routes AROUND standing trainers and NPCs and stops on the tile
# beside them (reach_tile), never on the body; if honoring them leaves no route
# it falls back to the straight one rather than the path vanishing.
_DRAW_BLOCKERS = False
def draw_path(a, b, stage):
    global _DRAW_BLOCKERS
    _DRAW_BLOCKERS = True
    try:
        p = path_between(a, b, stage)
    finally:
        _DRAW_BLOCKERS = False
    return p or path_between(a, b, stage)

def _tile_open(g, x, y, stage, surf_ok):
    """Can the player occupy (x, y) at this stage?"""
    if not g.inb(x, y): return False
    st = g.obstacles.get((x, y))
    if st is not None and stage < st: return False
    gates = g.barrier_gates.get((x, y))
    if gates is not None:
        # the barrier's floor is drawn by the unlock script, not baked into the
        # layout, so decide it here outright: open once every gate trainer is
        # beaten (or when gates aren't being tracked), a wall until then.
        return _OPEN_GATES is None or gates <= _OPEN_GATES
    if (x, y) in g.warp_tiles: return True
    if _DRAW_BLOCKERS and (x, y) in g.blockers: return False
    if (x, y) in g.scripted_open: return True
    beh = g.b(x, y)
    if beh in SURFABLE:
        return surf_ok
    return g.c(x, y) == 0

def _edge_ok(src_beh, dst_beh, dx, dy):
    if dy > 0:  return src_beh not in _BLOCK_S and dst_beh not in _BLOCK_N
    if dy < 0:  return src_beh not in _BLOCK_N and dst_beh not in _BLOCK_S
    if dx > 0:  return src_beh not in _BLOCK_E and dst_beh not in _BLOCK_W
    if dx < 0:  return src_beh not in _BLOCK_W and dst_beh not in _BLOCK_E
    return True

def _elev_ok(g, x, y, gn, nx, ny):
    """The game blocks walking between mismatched elevations even where
    collision is clear -- a raised platform's cliff edge (the Mt. Moon fossil
    ridge) is a wall unless one side is a transition (0) or bridge (15)
    tile. Ledge hops, warps and surf mounts have their own rules."""
    a = g.elev[y * g.w + x]
    b = gn.elev[ny * gn.w + nx]
    return a == b or a in (0, 15) or b in (0, 15)

def _surf_ok(g, stage):
    """Surf works here once you own HM03 + badge and the map is at/past it."""
    return stage >= P.HM_STAGE["SURF"]

def neighbours(node, stage):
    """All moves out of (map, x, y): walking, ledges, spins, warps, edges."""
    name, x, y = node
    g = grid(name)
    surf = _surf_ok(g, stage)
    out = []

    for dest, st in _ferry_edges().get(node, ()):
        if stage >= st and _open_map(dest[0], stage):
            out.append((dest, FERRY_COST))

    for dest in _elevator_edges().get(node, ()):
        if _open_map(dest[0], stage):
            out.append((dest, ELEVATOR_COST))

    # standing on thin ice: it cracks, you land on the floor below
    below = HOLE_DROP.get(name)
    if below and g.b(x, y) == MB_THIN_ICE and _open_map(below, stage):
        bg = grid(below)
        if bg.inb(x, y) and _tile_open(bg, x, y, stage, _surf_ok(bg, stage)):
            out.append(((below, x, y), 1))

    # warps fire from the tile itself
    for wx, wy, dmap, dwid in g.warps:
        if (wx, wy) != (x, y) or dmap == "MAP_DYNAMIC": continue
        dname = _const_to_folder().get(dmap)
        if not dname or not _open_map(dname, stage): continue
        dg = grid(dname)
        try: dw = dg.warps[int(dwid)]
        except (ValueError, IndexError): continue
        out.append(((dname, dw[0], dw[1]), 1))

    for dx, dy in DIRS:
        nx, ny = x + dx, y + dy
        if not g.inb(nx, ny):
            hop = _cross_connection(g, nx, ny, stage)
            if hop: out.append((hop, 1))
            continue
        beh = g.b(nx, ny)
        jump = MB_JUMP.get(beh)
        if jump:
            # a ledge is hopped only in its own direction, landing beyond it
            if (dx, dy) == jump:
                lx, ly = nx + dx, ny + dy
                if g.inb(lx, ly) and _tile_open(g, lx, ly, stage, surf):
                    out.append(((name, lx, ly), 2))
            continue
        if beh in MB_SPIN:
            slide = _spin_slide(g, nx, ny, stage, surf)
            if slide: out.append((slide[0], 1 + slide[1]))
            continue
        if _tile_open(g, nx, ny, stage, surf):
            if not _edge_ok(g.b(x, y), beh, dx, dy):
                continue
            if (_elev_ok(g, x, y, g, nx, ny)
                    or g.b(x, y) in SURFABLE or beh in SURFABLE
                    or (nx, ny) in g.warp_tiles or (x, y) in g.warp_tiles):
                out.append(((name, nx, ny), 1))
    return out

def _spin_slide(g, x, y, stage, surf):
    """Ride the arrow floor until something stops you."""
    seen = 0
    while g.inb(x, y):
        beh = g.b(x, y)
        d = MB_SPIN.get(beh)
        if d is None:
            if _tile_open(g, x, y, stage, surf):
                return ((g.name, x, y), seen)
            return None
        x, y = x + d[0], y + d[1]
        seen += 1
        if seen > 80: return None
    return None

@functools.lru_cache(maxsize=None)
def _connection_list(name):
    mj = R.maps()[name]
    out = []
    for c in (mj.get("connections") or []):
        nb = R._const_name(c["map"])
        if nb: out.append((c["direction"], int(c.get("offset", 0)), nb))
    return out

def _cross_connection(g, x, y, stage):
    """Step off a map edge onto the connected map, honouring the offset."""
    for direction, off, nb in _connection_list(g.name):
        if not _open_map(nb, stage): continue
        ng = grid(nb)
        if direction == "down" and y >= g.h:
            tx, ty = x - off, 0
        elif direction == "up" and y < 0:
            tx, ty = x - off, ng.h - 1
        elif direction == "right" and x >= g.w:
            tx, ty = 0, y - off
        elif direction == "left" and x < 0:
            tx, ty = ng.w - 1, y - off
        else:
            continue
        if ng.inb(tx, ty) and _tile_open(ng, tx, ty, stage, _surf_ok(ng, stage)):
            sx = min(max(x, 0), g.w - 1); sy = min(max(y, 0), g.h - 1)
            if (_elev_ok(g, sx, sy, ng, tx, ty)
                    or g.b(sx, sy) in SURFABLE or ng.b(tx, ty) in SURFABLE):
                return (nb, tx, ty)
    return None

@functools.lru_cache(maxsize=None)
def _open_map(name, stage):
    st = _map_stage(name)
    return st is not None and st <= stage

def encounter_anchor(name, mode="land"):
    """A tile that stands for this map's encounter ground: the land-encounter
    (or surfable, or shoreline for fishing) tile nearest the way in — the
    middle of a cave can be a pocket the corridors never touch."""
    g = grid(name)
    if g.warps:
        cx, cy = float(g.warps[0][0]), float(g.warps[0][1])
    else:
        cx, cy = g.w / 2.0, g.h / 2.0
    best, bestd = None, 1e18
    for y in range(g.h):
        for x in range(g.w):
            if mode == "water":
                ok = g.b(x, y) in SURFABLE
            elif mode == "shore":
                ok = (g.c(x, y) == 0 and g.b(x, y) not in SURFABLE
                      and any(g.inb(x + dx, y + dy)
                              and g.b(x + dx, y + dy) in SURFABLE
                              for dx, dy in DIRS))
            else:
                ok = g.c(x, y) == 0 and g.enc[y * g.w + x] == 1
            if ok:
                d = (x - cx) ** 2 + (y - cy) ** 2
                if d < bestd: best, bestd = (x, y), d
    return best

def first_open_tile(name):
    """Any plainly walkable tile, nearest the middle — a last-resort anchor
    for a scripted battle on a map with no warps and no known tile."""
    g = grid(name)
    cx, cy = g.w / 2.0, g.h / 2.0
    best, bestd = None, 1e18
    for y in range(g.h):
        for x in range(g.w):
            if g.c(x, y) == 0 and g.b(x, y) == 0:
                d = (x - cx) ** 2 + (y - cy) ** 2
                if d < bestd: best, bestd = (x, y), d
    return best

@functools.lru_cache(maxsize=1)
def center_nodes():
    """Every heal spot in the game -- where Fly puts you down."""
    hl = json.load(open(f"{REPO}/src/data/heal_locations.json"))["heal_locations"]
    out = []
    for h in hl:
        name = _const_to_folder().get(h["map"])
        if name and _map_stage(name) is not None:
            out.append((name, h["x"], h["y"]))
    return out

# ------------------------------------------------------------------ search
def bfs(start, stage, targets=None, want_paths=False):
    """Dijkstra (ledges cost 2, slides more) from one tile over the whole
    stage-gated world. Returns {node: dist}; with want_paths, also parents."""
    import heapq
    dist, parent = {start: 0}, {start: None}
    pq = [(0, start)]
    remaining = set(targets) if targets is not None else None
    if remaining is not None: remaining.discard(start)
    while pq:
        d, node = heapq.heappop(pq)
        if d > dist.get(node, 1 << 30): continue
        if remaining is not None:
            remaining.discard(node)
            if not remaining: break
        for nxt, cost in neighbours(node, stage):
            nd = d + cost
            if nd < dist.get(nxt, 1 << 30):
                dist[nxt] = nd
                parent[nxt] = node
                heapq.heappush(pq, (nd, nxt))
    return (dist, parent) if want_paths else dist

def bfs_multi(starts, stage):
    """Dijkstra from many seeds at once: distance to the NEAREST seed, with
    parents for path reconstruction. This is what Fly costs: land at the best
    Pokémon Center and walk from there."""
    import heapq
    dist, parent = {}, {}
    pq = []
    for s in starts:
        dist[s] = 0; parent[s] = None
        heapq.heappush(pq, (0, s))
    while pq:
        d, node = heapq.heappop(pq)
        if d > dist.get(node, 1 << 30): continue
        for nxt, cost in neighbours(node, stage):
            nd = d + cost
            if nd < dist.get(nxt, 1 << 30):
                dist[nxt] = nd
                parent[nxt] = node
                heapq.heappush(pq, (nd, nxt))
    return dist, parent

def walk_back(parent, node):
    path, cur = [], node
    while cur is not None:
        path.append(cur); cur = parent.get(cur)
    return path[::-1]

def path_between(a, b, stage):
    """Actual tile path a -> b at this stage, or None."""
    dist, parent = bfs(a, stage, targets=[b], want_paths=True)
    if b not in dist: return None
    return walk_back(parent, b)

def reach_tile(node, stage):
    """Interactables sit ON solid tiles; you stand next to them. The node we
    route to is the object's own tile if enterable, else any open neighbour.
    (Standing-next-to a trainer/NPC is handled when the path is DRAWN -- the last
    tile is trimmed so the walk stops beside the body, not on it; here we only
    need a tile reachability can key on, which the object's own tile gives even on
    the spin-floor puzzles where a fixed adjacent tile would not be reachable.)"""
    name, x, y = node
    g = grid(name)
    surf = _surf_ok(g, stage)
    if _tile_open(g, x, y, stage, surf): return node
    for dx, dy in DIRS:
        nx, ny = x + dx, y + dy
        if g.inb(nx, ny) and _tile_open(g, nx, ny, stage, surf):
            return (name, nx, ny)
    return node

# ------------------------------------------------------------------ start point
def spawn():
    """Your bedroom door, effectively: the Pallet Town heal spot."""
    hl = json.load(open(f"{REPO}/src/data/heal_locations.json"))["heal_locations"]
    for h in hl:
        if "PALLET" in h["map"]:
            name = _const_to_folder().get(h["map"])
            if name: return (name, h["x"], h["y"])
    return ("PalletTown", 6, 8)

if __name__ == "__main__":
    s = spawn()
    print("spawn:", s)
    for stage in (0, 5, 11, 20):
        d = bfs(s, stage)
        maps = {n[0] for n in d}
        print(f"stage {stage:2}: {len(d):7} tiles reachable across {len(maps):3} maps")
