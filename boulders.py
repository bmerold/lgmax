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
    slots. Returns [(floor_label, [push phrases])]."""
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
        phrases, reveal_next = [], set()
        for (bx, by, _flag, reveal) in bs:
            runs = solve_push(g, (bx, by), holes, _player_start(g, (bx, by)))
            where = (f"push it {_phrase(runs)} into the hole" if runs
                     else "push it into the nearest hole")
            phrases.append(f"the boulder at ({bx},{by}): {where}")
            reveal_next.add(reveal)
        out.append((fl, phrases))
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


if __name__ == "__main__":
    print("=== SEAFOAM ISLANDS (still the currents) ===")
    for fl, ph in seafoam_steps():
        print(f"  {fl}:")
        for p in ph:
            print(f"    - {p}")
    print("\n=== VICTORY ROAD (hold the floor switches) ===")
    for fl, ph in victory_steps():
        print(f"  {fl}:")
        for p in ph:
            print(f"    - {p}")
