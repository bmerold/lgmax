#!/usr/bin/env python3
"""The order you actually meet trainers.

The model used to sort a section's battles by a hand-written floor table and
then by party level, which is only a proxy for "further in". Levels are not
monotonic along a route, so Route 3's eight trainers came out in an order that
matched nothing you would walk.

This orders them by where they physically stand, in two levels:

* **Which map first.** Maps are ordered by the curated floor table where one
  exists, and otherwise by hop distance through the real map graph -- route
  `connections` and interior `warp_events`, both read from the map JSON -- from
  whichever map of the section you enter by.
* **Where in the map.** Every trainer is an object event with real x/y. Given
  the edge you come in by and the edge you leave by, each trainer is projected
  onto that line, so a route reads west to east or south to north exactly as you
  walk it. Where a map has no single clean exit (a cave with four staircases),
  it falls back to distance from the entrance, i.e. you meet what is nearest the
  way in first.
"""
import json, os, re, glob, functools, collections
import build_graph as G
import progression as P

REPO = G.REPO

# ------------------------------------------------------------------ raw map data
@functools.lru_cache(maxsize=1)
def maps():
    out = {}
    for f in glob.glob(f"{REPO}/data/maps/*/map.json"):
        name = os.path.basename(os.path.dirname(f))
        out[name] = json.load(open(f))
    return out

@functools.lru_cache(maxsize=1)
def layout_size():
    data = json.load(open(f"{REPO}/data/layouts/layouts.json"))["layouts"]
    return {l["id"]: (l.get("width", 1), l.get("height", 1)) for l in data if l and "id" in l}

def size(mapname):
    mj = maps().get(mapname) or {}
    return layout_size().get(mj.get("layout", ""), (1, 1))

def _const_name(map_const):
    """MAP_ROCK_TUNNEL_1F -> RockTunnel_1F, matching the folder names."""
    raw = map_const.replace("MAP_", "")
    flat = raw.replace("_", "")
    for name in maps():
        if name.upper().replace("_", "") == flat: return name
    return None

# ------------------------------------------------------------------ trainer -> tile
@functools.lru_cache(maxsize=1)
def trainer_tiles():
    """trainer const -> (map, x, y).

    An object event names a script label; that label's body contains the
    `trainerbattle_*` line naming the trainer. Joining the two puts every
    trainer on the tile it is standing on.
    """
    label_to_trainer = {}
    for path in glob.glob(f"{REPO}/data/maps/*/scripts.inc") + \
                glob.glob(f"{REPO}/data/scripts/*.inc"):
        txt = open(path, encoding="utf-8", errors="replace").read()
        cur = None
        for line in txt.splitlines():
            lm = re.match(r"^(\w+)::", line)
            if lm: cur = lm.group(1); continue
            tb = re.search(r"trainerbattle\w*\s+(TRAINER_[A-Z0-9_]+)", line)
            if tb and cur and cur not in label_to_trainer:
                label_to_trainer[cur] = tb.group(1)
    out = {}
    for name, mj in maps().items():
        for o in mj.get("object_events", []):
            if o.get("trainer_type", "TRAINER_TYPE_NONE") == "TRAINER_TYPE_NONE": continue
            t = label_to_trainer.get(o.get("script"))
            if t and t not in out:
                out[t] = (name, o.get("x", 0), o.get("y", 0))
    return out

# ------------------------------------------------------------------ map graph
EDGE = {"left": lambda w, h: (0, h / 2), "right": lambda w, h: (w, h / 2),
        "up": lambda w, h: (w / 2, 0), "down": lambda w, h: (w / 2, h)}

@functools.lru_cache(maxsize=1)
def graph():
    """map -> [(neighbour, the point on THIS map you leave through)]."""
    g = collections.defaultdict(list)
    for name, mj in maps().items():
        w, h = size(name)
        for c in (mj.get("connections") or []):
            nb = _const_name(c["map"])
            if nb and c["direction"] in EDGE:
                g[name].append((nb, EDGE[c["direction"]](w, h)))
        for wp in mj.get("warp_events", []):
            nb = _const_name(wp.get("dest_map", ""))
            if nb: g[name].append((nb, (wp.get("x", 0), wp.get("y", 0))))
    return {k: v for k, v in g.items()}

def link_point(a, b):
    """Where on map `a` you cross over to map `b`, or None if they don't touch."""
    for nb, pt in graph().get(a, ()):
        if nb == b: return pt
    return None

@functools.lru_cache(maxsize=1)
def center_maps():
    """Maps holding a Pokemon Center door, from the warps themselves."""
    out = set()
    for name, mj in maps().items():
        for w in mj.get("warp_events", []):
            if "POKEMON_CENTER" in (w.get("dest_map") or ""): out.add(name)
    return out

@functools.lru_cache(maxsize=None)
def center_between(a, b):
    """If the walk from map `a` to map `b` passes a Pokemon Center, name it.

    This is how the Center at Mt. Moon's entrance gets found. Route 3 does not
    touch Mt. Moon directly -- it runs UP into the west end of Route 4, and that
    is where both the Center and the cave mouth are. Nothing on Route 4 is a
    stage-5 battle, so keying heals off battle locations alone missed it
    entirely; you have to look at the ground you cross between the two.
    """
    if a == b: return None
    seen, q = {a}, collections.deque([(a, [])])
    while q:
        m, path = q.popleft()
        if m == b:
            for step in path:
                if step in center_maps():
                    return G.pretty_location(step) + " Pokémon Center"
            return None
        if len(path) > 3: continue
        for nb, _ in graph().get(m, ()):
            if nb not in seen:
                seen.add(nb); q.append((nb, path + [nb] if nb != b else path))
    return None

def hops(start, targets):
    """Breadth-first hop count from `start` to every map in `targets`."""
    seen, out, q = {start}, {}, collections.deque([(start, 0)])
    want = set(targets)
    while q and want:
        m, d = q.popleft()
        if m in want: out[m] = d; want.discard(m)
        for nb, _ in graph().get(m, ()):
            if nb not in seen:
                seen.add(nb); q.append((nb, d + 1))
    return out

# ------------------------------------------------------------------ ordering
def order_maps(stage, mapnames, floor_order):
    """Which of a section's maps you walk first.

    The curated floor table wins where it has an opinion -- it encodes things
    the geometry cannot, like which of Silph Co's eleven identical floors comes
    next. Everything else is ordered by hop distance from the map you enter the
    section by, which is whichever of them touches an earlier section.
    """
    mapnames = list(dict.fromkeys(mapnames))
    if not mapnames: return []

    def earlier(m):
        st = _map_stage(m)
        return st is not None and st < stage

    entry = None
    for m in mapnames:
        if any(earlier(nb) for nb, _ in graph().get(m, ())):
            entry = m; break
    if entry is None: entry = mapnames[0]
    dist = hops(entry, mapnames)
    return sorted(mapnames, key=lambda m: (floor_order.get(m, 0),
                                           dist.get(m, 99), m))

def _map_stage(name):
    idx = {k.replace("_", ""): v for k, v in P.MAP_STAGE.items()}
    return idx.get(name.upper().replace("_", ""))

def within_map(mapname, trainers, entry, exit_pt):
    """Sort trainers by how far along the walk they stand.

    With a distinct entry and exit, project onto the line between them -- that
    is the direction of travel. With only an entrance, use distance from it, so
    the ones nearest the way in come first.
    """
    tiles = trainer_tiles()
    def key(const):
        t = tiles.get(const)
        if not t: return (1, 0.0, const)
        _, x, y = t
        # a cave is entered and pushed through; there is no single line of
        # travel across it, so nearest-the-entrance-first is the honest reading
        if entry and exit_pt and entry != exit_pt and is_route(mapname):
            ax, ay = exit_pt[0] - entry[0], exit_pt[1] - entry[1]
            n = (ax * ax + ay * ay) or 1.0
            proj = ((x - entry[0]) * ax + (y - entry[1]) * ay) / n
            perp = abs((x - entry[0]) * ay - (y - entry[1]) * ax) / (n ** 0.5)
            return (0, round(proj, 4), round(perp, 2))
        if entry:
            return (0, round(((x - entry[0]) ** 2 + (y - entry[1]) ** 2) ** 0.5, 2), 0.0)
        return (0, float(y), float(x))
    return sorted(trainers, key=key)

def entry_point(prev, m):
    """Where you step onto map `m` coming from map `prev`.

    Not always a direct link: Route 3 does not touch Mt. Moon, it runs up into
    Route 4 and the cave mouth is there. So walk the map graph and take the last
    hop, which is the door you actually come through.
    """
    direct = link_point(m, prev)
    if direct: return direct
    seen, q = {prev}, collections.deque([[prev]])
    while q:
        path = q.popleft()
        if path[-1] == m and len(path) > 1:
            return link_point(m, path[-2])
        if len(path) > 4: continue
        for nb, _ in graph().get(path[-1], ()):
            if nb not in seen:
                seen.add(nb); q.append(path + [nb])
    return None

def is_route(m):
    """An outdoor map you walk across, as opposed to an interior you push into.
    Routes have edge connections; caves and buildings only have doors."""
    return bool((maps().get(m) or {}).get("connections"))

def onward(m, stage):
    """Where you eventually leave this map for the next section."""
    for nb, pt in graph().get(m, ()):
        st = _map_stage(nb)
        if st is not None and st > stage: return pt
    return None

def _legs(m, trainers, entry, exit_pt, onward_pt):
    """Split a map's trainers into the ones you pass on the way IN and the ones
    that are behind you when you get there.

    Route 10 is the clear case: you come in from Route 9 halfway up it, walk
    NORTH to Rock Tunnel, and only meet the southern half on the way out the
    other side. Projecting onto the entry-to-exit line makes those trainers
    negative, and a negative projection means exactly that -- behind the way in,
    so on a later leg. The later leg is then ordered by closing on whatever this
    map eventually leads to.
    """
    tiles = trainer_tiles()
    # Only an outdoor route can have a second leg, and only when it leaves the
    # section through a door rather than off its own edge -- that is the shape
    # of "walk in, duck into a cave, come back out and carry on". An interior is
    # pushed through once, so everything in it is on the way in.
    detour = is_route(m) and exit_pt is not None and exit_pt != onward_pt
    if not (entry and exit_pt and entry != exit_pt) or not detour:
        return within_map(m, trainers, entry, exit_pt), []
    ax, ay = exit_pt[0] - entry[0], exit_pt[1] - entry[1]
    n = (ax * ax + ay * ay) or 1.0
    fwd, back = [], []
    for const in trainers:
        t = tiles.get(const)
        if not t: fwd.append(const); continue
        proj = ((t[1] - entry[0]) * ax + (t[2] - entry[1]) * ay) / n
        (fwd if proj >= 0 else back).append(const)
    fwd = within_map(m, fwd, entry, exit_pt)
    if back and onward_pt:
        back.sort(key=lambda c: -((tiles[c][1] - onward_pt[0]) ** 2 +
                                  (tiles[c][2] - onward_pt[1]) ** 2)
                  if c in tiles else 0)
    return fwd, back

def battle_key(trainer_const):
    """A rival/Champion fight exists as three constants, one per starter the
    RIVAL holds. Drop that suffix so the same fight matches across runs."""
    parts = trainer_const.split("_")
    if parts[-1] in ("BULBASAUR", "CHARMANDER", "SQUIRTLE"):
        return "_".join(parts[:-1])
    return trainer_const

def section_order(stage, encs, floor_order):
    """One section's trainer battles, in the order you meet them, plus the
    points along the way where you walk past a Pokemon Center."""
    by_map = collections.defaultdict(list)
    for e in encs:
        by_map[e.get("locationRaw") or ""].append(e)
    ordered_maps = order_maps(stage, list(by_map), floor_order)

    out, tail, heal_after = [], [], {}
    for i, m in enumerate(ordered_maps):
        prev = ordered_maps[i - 1] if i else None
        nxt = ordered_maps[i + 1] if i + 1 < len(ordered_maps) else None
        entry = entry_point(prev, m) if prev else None
        if entry is None and prev is None:
            for nb, pt in graph().get(m, ()):
                st = _map_stage(nb)
                if st is not None and st < stage: entry = pt; break
        exit_pt = link_point(m, nxt) if nxt else None
        if exit_pt is None: exit_pt = onward(m, stage)
        group = {e["trainerConst"]: e for e in by_map[m]}
        fwd, back = _legs(m, list(group), entry, exit_pt, onward(m, stage))
        for const in fwd: out.append(group[const])
        # a Pokemon Center on the way from this map to the next one
        if nxt:
            why = center_between(m, nxt)
            if why and out: heal_after[out[-1]["id"]] = why
        for const in back: tail.append(group[const])
    return out + tail, heal_after

if __name__ == "__main__":
    tiles = trainer_tiles()
    print(f"trainers placed on a tile: {len(tiles)}")
    graph_ = json.load(open(f"{os.path.dirname(os.path.abspath(__file__))}/data/encounters.json"))
    import sections as S
    for stage in (5, 3, 13, 23):
        encs = [e for e in graph_ if e["stage"] == stage and e["kind"] not in ("wild", "rematch")]
        encs = [e for e in encs if e.get("starterVariant") in (None, "Charmander")]
        print(f"\n=== section {stage}: {P.STAGE_BY_ID[stage]['name']}")
        ordered, heals = section_order(stage, encs, S.FLOOR_ORDER)
        for j, e in enumerate(ordered):
            t = tiles.get(e["trainerConst"])
            where = f"{t[0]} ({t[1]},{t[2]})" if t else "?"
            print(f"   {e['name'][:38]:38s} L{e['maxLevel']:<3d} {where}")
            if j in heals: print(f"      ---- heal: {heals[j]} ----")
