#!/usr/bin/env python3
"""Strength-boulder puzzle solver, step by step, from the decomp geometry.

Two caves gate progress behind Strength boulders, and the walkthrough has to say
how to clear them, not just that they exist:

  * Seafoam Islands stills its B3F current only when both of its slot boulders are
    present (SeafoamIslands_B3F scripts.inc: FLAG_STOPPED_SEAFOAM_B3F_CURRENT at
    NUM_BOULDERS_PRESENT == 2). Those slot boulders are the bottom of a four-floor
    cascade: each PUSHABLE_BOULDER object carries a reveal flag (its `trainer_type`)
    that HandleBoulderFallThroughHole clears when the boulder is pushed onto a
    MB_FALL_WARP hole -- revealing the linked boulder at its own fixed spot one floor
    down (src/field_control_avatar.c, src/event_object_movement.c). So you push the
    1F pair into holes, then the B1F pair, then the B2F pair, and the B3F pair lands
    in the current slots.

  * Victory Road holds three barriers open with floor switches; a boulder parked on a
    switch keeps it pressed (copyobjectxytoperm in each VictoryRoad_*_EventScript_
    FloorSwitch). So you push a boulder onto each switch.

Both reduce to one task: push a single boulder across a cave floor to a target tile.
solve_push is a one-box Sokoban BFS -- a push is legal when the destination tile is
clear and the player can reach the tile on the near side to shove from. Everything
here is read from the maps; nothing is hand-listed.
"""
import collections
import engine as E
import world as W

DIRS = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
MB_FALL_WARP = 0x66
_STAGE = 99                       # both caves are late-game; Surf/Strength long live


def _boulders(mapname):
    """The map's pushable boulders: (x, y, own-hide-flag, reveal-flag-below)."""
    out = []
    for o in W.R.maps()[mapname].get("object_events", []):
        if o.get("graphics_id") == "OBJ_EVENT_GFX_PUSHABLE_BOULDER":
            out.append((o.get("x"), o.get("y"), o.get("flag"),
                        o.get("trainer_type")))
    return out


def _passable(g, x, y):
    """Can a boulder occupy / the player stand on (x, y)? Open floor or ridable
    water; a fall-hole counts (the boulder sits there a moment, then drops)."""
    if not g.inb(x, y):
        return False
    if g.b(x, y) == MB_FALL_WARP:
        return True
    return W._tile_open(g, x, y, _STAGE, True)


def _reachable(g, start, blocked):
    """Every tile the player can walk/surf to from `start`, treating `blocked`
    (the boulder) as a wall."""
    seen = {start}
    stack = [start]
    while stack:
        x, y = stack.pop()
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n not in seen and n != blocked and _passable(g, n[0], n[1]):
                seen.add(n)
                stack.append(n)
    return seen


def solve_push(g, start, targets, player_start):
    """Fewest pushes to shove the boulder at `start` onto any tile in `targets`.
    One-box Sokoban: BFS over boulder positions; a push in direction d is legal
    when the destination is clear and the player can reach the opposite side.
    Returns a list of (direction, tiles) runs, or None if unsolved."""
    targets = set(targets)
    q = collections.deque([(start, player_start, [])])
    seen = {start}
    while q:
        b, p, pushes = q.popleft()
        if b in targets:
            return _runs(pushes)
        reg = _reachable(g, p, blocked=b)
        for d, (dx, dy) in DIRS.items():
            near = (b[0] - dx, b[1] - dy)         # where the player shoves from
            dest = (b[0] + dx, b[1] + dy)
            if (near in reg and dest not in seen
                    and _passable(g, dest[0], dest[1])):
                seen.add(dest)
                q.append((dest, b, pushes + [d]))
    return None


def _runs(pushes):
    """Collapse a push list into (direction, count) runs."""
    out = []
    for d in pushes:
        if out and out[-1][0] == d:
            out[-1][1] += 1
        else:
            out.append([d, 1])
    return [(d, n) for d, n in out]


def _trajectory(start, runs):
    """The boulder's path as a flat [x0,y0,x1,y1,...] of its corner tiles (start,
    each turn, the hole) -- the same shape the app's svgPath draws."""
    x, y = start
    pts = [x, y]
    for d, n in runs:
        dx, dy = DIRS[d]
        x += dx * n; y += dy * n
        pts += [x, y]
    return pts


def _player_start(g, boulder):
    """Where to seed the player for the first push: any open tile next to the
    boulder. The player can walk up to it from anywhere on the floor, so starting
    beside it makes reachability the floor's whole walkable region (seeding from a
    far entrance can land in a disconnected pocket and wrongly fail the solve)."""
    bx, by = boulder
    for dx, dy in DIRS.values():
        if _passable(g, bx + dx, by + dy):
            return (bx + dx, by + dy)
    return (bx, by)


def _phrase(runs):
    return ", ".join(f"{n} {'tile' if n == 1 else 'tiles'} {d}" for d, n in runs)


# ------------------------------------------------------------------ Seafoam
# The cascade floors, top to bottom, and the base hide-flag that names a floor's
# boulders. A floor's boulders are pushed into its holes; each reveals the boulder
# one floor down whose own hide-flag equals this one's reveal-flag.
SEAFOAM_FLOORS = ["SeafoamIslands_1F", "SeafoamIslands_B1F", "SeafoamIslands_B2F"]


def seafoam_steps():
    """Per-floor push instructions that cascade the boulders down to B3F's current
    slots. Returns [(floor_map, [push phrases], [boulder trajectories])] where each
    trajectory is a flat [x0,y0,...] the app can draw on that floor's map."""
    out = []
    # which boulders are 'live' on the current floor: start with 1F's two, then
    # follow each floor's reveal-flag to the next floor's matching hide-flag.
    live = None
    for fl in SEAFOAM_FLOORS:
        g = W.grid(fl)
        holes = [(x, y) for y in range(g.h) for x in range(g.w)
                 if g.b(x, y) == MB_FALL_WARP]
        bs = _boulders(fl)
        if live is not None:
            bs = [b for b in bs if b[2] in live]   # only the revealed ones
        phrases, paths, reveal_next = [], [], set()
        for (bx, by, _flag, reveal) in bs:
            runs = solve_push(g, (bx, by), holes, _player_start(g, (bx, by)))
            where = (f"push it {_phrase(runs)} into the hole" if runs
                     else "push it into the nearest hole")
            phrases.append(f"the boulder at ({bx},{by}): {where}")
            if runs:
                paths.append(_trajectory((bx, by), runs))
            reveal_next.add(reveal)
        out.append((fl, phrases, paths))
        live = reveal_next
    return out


# ------------------------------------------------------------------ Victory Road
# Floor switches (coord_events) and the boulder pushed onto each. The switch's own
# tile is the target; the nearest boulder that can reach it is the one to use.
VICTORY_FLOORS = ["VictoryRoad_1F", "VictoryRoad_2F", "VictoryRoad_3F"]


def _switches(mapname):
    return [(c.get("x"), c.get("y")) for c in
            W.R.maps()[mapname].get("coord_events", [])
            if "FloorSwitch" in (c.get("script") or "")]


def victory_steps():
    """Which boulder to push onto each Victory Road floor switch. Victory Road is
    laced with one-way ledges and elevation changes that a flat push-path solver
    would route a boulder straight over, so this does NOT dictate a tile-by-tile
    path (it would risk a route the game forbids). It names the verified facts: the
    switch tile to hold down, and the nearest boulder to shove onto it -- the short
    push itself is in plain sight once you are there. Returns [(floor, [lines])]."""
    out = []
    for fl in VICTORY_FLOORS:
        sw = _switches(fl)
        lines = []
        for (sx, sy) in sw:
            lines.append(f"push a Strength boulder onto the floor switch at "
                         f"({sx},{sy}) to hold its barrier open")
        out.append((fl, lines))
    return out


# ------------------------------------------------------------------ Mt Ember
# Mt Ember's exterior is gated by plain Strength boulders (EventScript_StrengthBoulder)
# -- no holes, no switches: you shove them aside to open the climb. Two clusters block
# the route and with every boulder solid NEITHER is passable (verified): a lower pair
# walls off the Summit Path stairs, and so the whole upper mountain (Moltres, the items),
# and an upper trio walls off the Fire Stone ledge you reach coming back out of Summit
# Path 3F. The pushes are on flat ground (no ledges/elevation like Victory Road), so the
# exact solve is safe to dictate; a multi-box Sokoban over the boulders finds it.
MT_EMBER = "MtEmber_Exterior"


def _warp_tile(g, dest_const):
    """The exterior tile whose warp leads to dest_const (a Summit Path stair)."""
    for wx, wy, dmap, _id in g.warps:
        if dmap == dest_const:
            return (wx, wy)
    return None


def _object_tile(mapname, script_sub):
    """The tile of the object whose script contains script_sub (an item ball)."""
    for o in W.R.maps()[mapname].get("object_events", []):
        if script_sub in (o.get("script") or ""):
            return (o.get("x"), o.get("y"))
    return None


def _multi_region(g, player, boxes):
    """Every tile reachable from player with the whole box set treated as walls."""
    seen = {player}
    stack = [player]
    while stack:
        x, y = stack.pop()
        for dx, dy in DIRS.values():
            n = (x + dx, y + dy)
            if n not in seen and n not in boxes and _passable(g, n[0], n[1]):
                seen.add(n)
                stack.append(n)
    return seen


def solve_push_multi(g, boxes, player_start, goal):
    """Fewest boulder pushes (over several boulders) to make `goal` walk-reachable.
    Multi-box Sokoban: BFS over frozenset box layouts. A push of box b in direction d
    is legal when the near side (b-d) is in the player's current region and the far
    side (b+d) is clear; the player ends on b's old tile, so the next region is seeded
    there. Returns the pushes in order as (before, after), or None if unsolvable."""
    boxes0 = frozenset(boxes)
    init = _multi_region(g, player_start, boxes0)
    if goal in init:
        return []
    q = collections.deque([(boxes0, init, [])])
    seen = {(boxes0, min(init))}
    while q:
        bxs, reg, pushes = q.popleft()
        if goal in reg:
            return pushes
        for b in bxs:
            for dx, dy in DIRS.values():
                near, dest = (b[0] - dx, b[1] - dy), (b[0] + dx, b[1] + dy)
                if near in reg and dest not in bxs and _passable(g, dest[0], dest[1]):
                    nb = frozenset((bxs - {b}) | {dest})
                    nreg = _multi_region(g, near, nb)
                    key = (nb, min(nreg))
                    if key not in seen:
                        seen.add(key)
                        q.append((nb, nreg, pushes + [(b, dest)]))
    return None


def _per_boulder(boulders, pushes):
    """Replay the ordered pushes to recover each boulder's own trajectory (tracking
    identity by current position, so two boulders crossing the same tile don't mix).
    Returns [(start, runs, flat_path)] for the boulders that actually move."""
    pos = {i: b for i, b in enumerate(boulders)}
    steps = {i: [] for i in pos}
    for before, after in pushes:
        i = next(k for k, p in pos.items() if p == before)
        d = next(name for name, (dx, dy) in DIRS.items()
                 if (before[0] + dx, before[1] + dy) == after)
        steps[i].append(d)
        pos[i] = after
    out = []
    for i, b in enumerate(boulders):
        if not steps[i]:
            continue
        runs = _runs(steps[i])
        out.append((b, runs, _trajectory(b, runs)))
    return out


def mt_ember_steps():
    """The Mt Ember exterior boulder pushes, lower cluster then upper, each solved as
    its own sub-puzzle (they sit in regions the warp network joins, so a single
    exterior Sokoban can't see across them). Returns (phrases, trajectories) for
    MtEmber_Exterior, the same shape a Seafoam floor yields."""
    g = W.grid(MT_EMBER)
    boulders = [(x, y) for x, y, _f, _r in _boulders(MT_EMBER)]
    entrance = _warp_tile(g, "MAP_ONE_ISLAND_KINDLE_ROAD")
    start = (entrance[0], entrance[1] - 1)          # stand just inside the entrance
    # (goal tile, player start) for each sub-puzzle, in the order you meet them
    legs = [
        (_warp_tile(g, "MAP_MT_EMBER_SUMMIT_PATH_1F"), start),       # open the climb
        (_object_tile(MT_EMBER, "ItemFireStone"),                   # open the Fire Stone
         _warp_tile(g, "MAP_MT_EMBER_SUMMIT_PATH_3F")),
    ]
    phrases, trajs = [], []
    for goal, pstart in legs:
        if not goal or not pstart:
            continue
        pushes = solve_push_multi(g, boulders, pstart, goal)
        if not pushes:
            continue
        for b, runs, flat in _per_boulder(boulders, pushes):
            phrases.append(f"the boulder at ({b[0]},{b[1]}): push it {_phrase(runs)}")
            trajs.append(flat)
    return phrases, trajs


if __name__ == "__main__":
    print("=== SEAFOAM ISLANDS (still the currents) ===")
    for fl, ph, paths in seafoam_steps():
        print(f"  {fl}:")
        for p in ph:
            print(f"    - {p}")
        for tr in paths:
            print(f"      path: {tr}")
    print("\n=== VICTORY ROAD (hold the floor switches) ===")
    for fl, ph in victory_steps():
        print(f"  {fl}:")
        for p in ph:
            print(f"    - {p}")
    print("\n=== MT EMBER (shove the boulders aside) ===")
    ph, trajs = mt_ember_steps()
    for p in ph:
        print(f"    - {p}")
    for tr in trajs:
        print(f"      path: {tr}")
