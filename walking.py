#!/usr/bin/env python3
"""How many wild battles a section actually costs you.

Fully clearing an area means walking it: every item ball, every hidden item,
every trainer. That walking is where wild encounters come from, and those
battles spend PP that the section's trainer fights then don't have.

Nothing here is guessed at where the ROM can answer it:

* **Encounter chance per step** is the game's own formula
  (`src/wild_encounter.c`). `DoWildEncounterRateTest` multiplies the map's
  encounter rate by 16 and rolls against `MAX_ENCOUNTER_RATE` (1600), so the
  base chance is simply `rate / 100` per step. Failed checks add the rate to a
  pity buff worth `buff * 16 / 200`, which is modelled exactly below.
* **Which tiles can trigger** comes from the real map: each map's `map.bin`
  gives a metatile id per tile, and the tileset's `metatile_attributes.bin`
  gives that metatile's `METATILE_ATTRIBUTE_ENCOUNTER_TYPE` (bits 24-26).
  So the share of walkable ground that is grass or cave floor is counted, not
  assumed.
* **How far you walk** is a nearest-neighbour tour over the real x/y of every
  item, trainer and exit on the map, taken from `map.json`.

The one genuine estimate is that the walk is spread over the map evenly, so the
share of steps landing on encounter tiles equals that tile share.
"""
import json, os, re, struct, functools
import engine as E

REPO = E.REPO
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
NUM_METATILES_IN_PRIMARY = 640
MAX_ENCOUNTER_RATE = 1600
TILE_ENCOUNTER_LAND = 1

# ------------------------------------------------------------------ tilesets
def _tileset_dir(name):
    stem = name.replace("gTileset_", "")
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", stem).lower()
    snake = re.sub(r"_(\d)", r"\1", snake)          # generic_building_1 -> generic_building1
    for sub in ("primary", "secondary"):
        p = f"{REPO}/data/tilesets/{sub}/{snake}/metatile_attributes.bin"
        if os.path.exists(p): return p
    return None

@functools.lru_cache(maxsize=None)
def tileset_attrs(name):
    path = _tileset_dir(name)
    if not path: return ()
    raw = open(path, "rb").read()
    return struct.unpack(f"<{len(raw)//4}I", raw[:len(raw)//4*4])

def encounter_type(metatile_id, primary, secondary):
    if metatile_id < NUM_METATILES_IN_PRIMARY:
        attrs = tileset_attrs(primary); idx = metatile_id
    else:
        attrs = tileset_attrs(secondary); idx = metatile_id - NUM_METATILES_IN_PRIMARY
    if idx >= len(attrs): return 0
    return (attrs[idx] & 0x07000000) >> 24

# ------------------------------------------------------------------ layouts
@functools.lru_cache(maxsize=1)
def layouts():
    data = json.load(open(f"{REPO}/data/layouts/layouts.json"))["layouts"]
    return {l["id"]: l for l in data if l and "id" in l}

def tile_profile(layout_id):
    """(walkable tiles, tiles that can start a land encounter) for a layout."""
    lay = layouts().get(layout_id)
    if not lay or "blockdata_filepath" not in lay: return (0, 0)
    path = os.path.join(REPO, lay["blockdata_filepath"])
    if not os.path.exists(path): return (0, 0)
    raw = open(path, "rb").read()
    grid = struct.unpack(f"<{len(raw)//2}H", raw[:len(raw)//2*2])
    primary = lay.get("primary_tileset", ""); secondary = lay.get("secondary_tileset", "")
    walkable = land = 0
    for cell in grid:
        if (cell & 0x0C00) >> 10:      # collision bits set -> not walkable
            continue
        walkable += 1
        if encounter_type(cell & 0x03FF, primary, secondary) == TILE_ENCOUNTER_LAND:
            land += 1
    return (walkable, land)

# ------------------------------------------------------------------ objectives
ITEM_GFX = "OBJ_EVENT_GFX_ITEM_BALL"

def objectives(mapjson):
    """Everywhere you must stand to clear the map: items, hidden items,
    trainers, and the ways in and out."""
    pts, items, trainers, hidden = [], 0, 0, 0
    for o in mapjson.get("object_events", []):
        p = (o.get("x", 0), o.get("y", 0))
        if o.get("graphics_id") == ITEM_GFX:
            pts.append(p); items += 1
        elif o.get("trainer_type", "TRAINER_TYPE_NONE") != "TRAINER_TYPE_NONE":
            pts.append(p); trainers += 1
    for b in mapjson.get("bg_events", []):
        if b.get("type", "").startswith("hidden_item"):
            pts.append((b.get("x", 0), b.get("y", 0))); hidden += 1
    warps = [(w.get("x", 0), w.get("y", 0)) for w in mapjson.get("warp_events", [])]
    return pts, warps, {"items": items, "trainers": trainers, "hidden": hidden}

def tour_length(start, pts, end=None):
    """Nearest-neighbour walk over every objective, in tiles (Manhattan)."""
    if not pts:
        return abs(start[0] - end[0]) + abs(start[1] - end[1]) if end else 0
    remaining, cur, total = list(pts), start, 0
    while remaining:
        nxt = min(remaining, key=lambda p: abs(p[0] - cur[0]) + abs(p[1] - cur[1]))
        total += abs(nxt[0] - cur[0]) + abs(nxt[1] - cur[1])
        remaining.remove(nxt); cur = nxt
    if end: total += abs(end[0] - cur[0]) + abs(end[1] - cur[1])
    return total

# ------------------------------------------------------------------ encounter odds
@functools.lru_cache(maxsize=None)
def steps_per_encounter(rate):
    """Expected steps on encounter ground before a battle starts, using the
    game's rate test including the pity buff that builds on failed checks."""
    if rate <= 0: return None
    total, survive = 0.0, 1.0
    for k in range(0, 4000):
        r = rate * 16 + (k * rate * 16) // 200
        p = min(1.0, r / MAX_ENCOUNTER_RATE)
        total += survive * (k + 1) * p
        survive *= (1 - p)
        if survive < 1e-9: break
    return total if total > 0 else None

# ------------------------------------------------------------------ per map
def estimate_map(map_name, land_rate):
    """Expected wild battles from clearing this map once."""
    mp = f"{REPO}/data/maps/{map_name}/map.json"
    if not os.path.exists(mp) or not land_rate: return None
    mj = json.load(open(mp))
    walkable, land = tile_profile(mj.get("layout", ""))
    if not walkable: return None
    pts, warps, counts = objectives(mj)
    start = warps[0] if warps else (pts[0] if pts else (0, 0))
    end = warps[-1] if len(warps) > 1 else None
    steps = tour_length(start, pts, end)
    if steps == 0 and not pts:
        # a corridor you only pass through: crossing it still costs steps
        lay = layouts().get(mj.get("layout", ""), {})
        steps = (lay.get("width", 0) + lay.get("height", 0)) // 2
    share = land / walkable if walkable else 0.0
    enc_steps = steps * share
    spe = steps_per_encounter(land_rate)
    battles = enc_steps / spe if spe else 0.0
    return {
        "map": map_name, "steps": int(steps), "encounterShare": round(share, 3),
        "encounterSteps": round(enc_steps, 1), "rate": land_rate,
        "stepsPerEncounter": round(spe, 2) if spe else None,
        "battles": round(battles, 1), "requiresFlash": bool(mj.get("requires_flash")),
        **counts,
    }

# ------------------------------------------------------------------ per section
def section_wild_load():
    """Expected wild battles per section, and which maps they come from."""
    import progression as P
    import engine as E
    by_stage = {}
    land_rate, slots = {}, {}
    for enc in E.WILD["encounters"]:
        if enc["version"] == "FireRed": continue
        tbl = enc["tables"].get("land_mons")
        if not tbl or not tbl["slots"]: continue
        raw = enc["map"].replace("MAP_", "")
        land_rate[raw] = tbl["encounterRate"]
        slots[raw] = tbl["slots"]

    # map constant -> the map folder name used by data/maps/
    folder = {}
    for path in os.listdir(f"{REPO}/data/maps"):
        mp = f"{REPO}/data/maps/{path}/map.json"
        if os.path.exists(mp):
            try: folder[json.load(open(mp))["id"].replace("MAP_", "")] = path
            except Exception: pass

    for raw, rate in land_rate.items():
        name = folder.get(raw)
        if not name: continue
        est = estimate_map(name, rate)
        if not est: continue
        stage = P.map_stage("MAP_" + raw)
        est["stage"] = stage
        est["species"] = _slot_summary(slots[raw])
        by_stage.setdefault(stage, []).append(est)
    return by_stage

def _slot_summary(slots):
    agg = {}
    total = sum(s["rate"] for s in slots) or 1
    for s in slots:
        a = agg.setdefault(s["species"], {"rate": 0, "min": 99, "max": 0})
        a["rate"] += s["rate"]; a["min"] = min(a["min"], s["minLevel"])
        a["max"] = max(a["max"], s["maxLevel"])
    return [{"species": k, "share": v["rate"] / total,
             "level": round((v["min"] + v["max"]) / 2)}
            for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["rate"])]

if __name__ == "__main__":
    import engine as E, progression as P
    loads = section_wild_load()
    with open(f"{OUT}/wildLoad.json", "w") as f:
        json.dump({str(k): v for k, v in loads.items()}, f)
    print(f"{sum(len(v) for v in loads.values())} maps with land encounters\n")
    tot = 0
    for stage in sorted(loads):
        maps = loads[stage]
        n = sum(m["battles"] for m in maps)
        tot += n
        st = P.STAGE_BY_ID.get(stage, {})
        print(f"  [{stage:2}] {st.get('name','?')[:34]:36} {n:6.1f} wild battles "
              f"over {len(maps)} map(s)")
    print(f"\n  whole game: {tot:.0f} wild battles to clear every area")
    print("\n  worked example — Mt. Moon 1F:")
    for stage, maps in loads.items():
        for m in maps:
            if m["map"] == "MtMoon_1F":
                print(f"    walk {m['steps']} tiles; {m['encounterShare']*100:.0f}% of the floor "
                      f"triggers encounters ({m['encounterSteps']} steps)")
                print(f"    rate {m['rate']} -> one battle per {m['stepsPerEncounter']} steps "
                      f"=> {m['battles']} battles")
