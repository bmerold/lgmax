#!/usr/bin/env python3
"""Render every map the sections walk to a PNG, straight from the decomp.

No wiki screenshots and no external imaging library: the layouts (map.bin),
tilesets (tiles.png), palettes (JASC .pal) and metatile definitions
(metatiles.bin) are read from pokefirered and composed exactly the way the
GBA does it — two layers of 8x8 tiles per 16x16 metatile, primary tileset
below 640, secondary above, palettes 0-6 primary and 7-12 secondary.

Also drops every trainer's standing tile (from the maps' object events, the
same join route_order.py walks) into the index, so the page can pin each
trainer battle onto the ground it happens on.

Outputs:
    data/mapart/<MapName>.png       one image per map, 16 px per metatile
    data/mapart/index.json          {"maps": {name: {w, h}}, "trainers": {...}}

Incremental: an existing PNG is only re-rendered with --force.
"""
import json, os, re, struct, sys, zlib
import build_graph as G
import route_order as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
ART = os.path.join(OUT, "mapart")
REPO = G.REPO

# FRLG fieldmap constants (include/fieldmap.h)
NUM_TILES_IN_PRIMARY = 640
NUM_METATILES_IN_PRIMARY = 640
NUM_PALS_IN_PRIMARY = 7
NUM_PALS_TOTAL = 13

# ------------------------------------------------------------------ PNG codec
# The tileset sheets are plain indexed PNGs (4bpp, no interlace); zlib is in
# the stdlib, so a reader/writer here keeps the whole pipeline dependency-free.
def read_indexed_png(path):
    """-> (width, height, flat list of palette indices)."""
    d = open(path, "rb").read()
    assert d[:8] == b"\x89PNG\r\n\x1a\n", path
    pos, w, h, depth, idat = 8, 0, 0, 0, b""
    while pos < len(d):
        ln, typ = struct.unpack(">I4s", d[pos:pos + 8])
        body = d[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
            assert ctype == 3 and inter == 0, f"{path}: not plain indexed"
        elif typ == b"IDAT":
            idat += body
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = (w * depth + 7) // 8
    px, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        row = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = row[x - 1] if x else 0
            b = prev[x]
            c = prev[x - 1] if x else 0
            if f == 1:   row[x] = (row[x] + a) & 0xFF
            elif f == 2: row[x] = (row[x] + b) & 0xFF
            elif f == 3: row[x] = (row[x] + (a + b) // 2) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                row[x] = (row[x] + (a if pa <= pb and pa <= pc
                                    else b if pb <= pc else c)) & 0xFF
        prev = row
        if depth == 8:
            px.extend(row[:w])
        else:                                   # 4bpp, high nibble first
            for x in range(w):
                byte = row[x >> 1]
                px.append(byte >> 4 if x % 2 == 0 else byte & 0xF)
    return w, h, px

def write_indexed_png(path, w, h, pixels, palette, transparent0=False):
    """pixels: flat palette indices; palette: [(r,g,b), ...]. With
    transparent0, palette entry 0 renders fully transparent (sprites)."""
    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body
                + struct.pack(">I", zlib.crc32(typ + body)))
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw.extend(pixels[y * w:(y + 1) * w])
    plte = b"".join(bytes(c) for c in palette)
    out = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0))
           + chunk(b"PLTE", plte))
    if transparent0:
        out += chunk(b"tRNS", b"\x00")
    out += (chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))
    open(path, "wb").write(out)

# ------------------------------------------------------------------ tilesets
# A tileset header does not have to own its graphics: gTileset_SilphCo keeps
# its own metatiles but points .tiles and .palettes at Condominiums'. So the
# lookup follows the symbols in headers.h rather than assuming one directory.
def _headers():
    txt = open(f"{REPO}/src/data/tilesets/headers.h").read()
    out = {}
    for m in re.finditer(
            r"const struct Tileset (gTileset_\w+)\s*=\s*\{(.*?)\};", txt, re.S):
        body = m.group(2)
        f = lambda k: re.search(rf"\.{k} = g\w+?_(\w+)", body).group(1)
        out[m.group(1)] = {"tiles": f("tiles"), "palettes": f("palettes"),
                           "metatiles": f("metatiles")}
    return out

def _dir_of(symbol):
    """'PewterCity' -> data/tilesets/{primary,secondary}/pewter_city."""
    want = symbol.replace("_", "").lower()
    for kind in ("primary", "secondary"):
        base = f"{REPO}/data/tilesets/{kind}"
        for name in os.listdir(base):
            if name.replace("_", "").lower() == want:
                return os.path.join(base, name)
    raise KeyError(symbol)

def _read_pal(d, p):
    jasc = os.path.join(d, "palettes", f"{p:02}.pal")
    if os.path.exists(jasc):
        # JASC-PAL header is 3 tokens ("JASC-PAL", "0100", "16"), then 16 RGB
        vals = [int(x) for x in open(jasc).read().split()[3:3 + 48]]
        return [tuple(vals[i:i + 3]) for i in range(0, 48, 3)]
    raw = open(os.path.join(d, "palettes", f"{p:02}.gbapal"), "rb").read()
    out = []
    for v in struct.unpack("<16H", raw[:32]):
        out.append(((v & 31) * 255 // 31, ((v >> 5) & 31) * 255 // 31,
                    ((v >> 10) & 31) * 255 // 31))
    return out

HEADERS, _TILES, _METAS, _PALS = {}, {}, {}, {}

def _tiles(symbol):
    if symbol not in _TILES:
        w, h, px = read_indexed_png(os.path.join(_dir_of(symbol), "tiles.png"))
        tiles = []
        for t in range((w // 8) * (h // 8)):
            tx, ty = (t % (w // 8)) * 8, (t // (w // 8)) * 8
            tiles.append([px[(ty + r) * w + tx + c]
                          for r in range(8) for c in range(8)])
        _TILES[symbol] = tiles
    return _TILES[symbol]

def _metatiles(symbol):
    if symbol not in _METAS:
        mt = open(os.path.join(_dir_of(symbol), "metatiles.bin"), "rb").read()
        _METAS[symbol] = [struct.unpack("<8H", mt[i:i + 16])
                          for i in range(0, len(mt), 16)]
    return _METAS[symbol]

def _palettes(symbol):
    if symbol not in _PALS:
        d = _dir_of(symbol)
        _PALS[symbol] = [_read_pal(d, p) for p in range(16)]
    return _PALS[symbol]

def tileset(const):
    """-> (tiles as 64-int lists, metatiles as 8-u16 tuples, 16 palettes)."""
    h = HEADERS[const]
    return _tiles(h["tiles"]), _metatiles(h["metatiles"]), _palettes(h["palettes"])

# ------------------------------------------------------------------ rendering
def render_map(mapname):
    """-> (width px, height px, pixels) or None if the layout is missing."""
    mj = R.maps().get(mapname)
    if not mj: return None
    layout = _LAYOUTS.get(mj.get("layout", ""))
    if not layout: return None
    w, h = layout["width"], layout["height"]
    blocks = open(f"{REPO}/{layout['blockdata_filepath']}", "rb").read()
    ptiles, pmetas, ppals = tileset(layout["primary_tileset"])
    stiles, smetas, spals = tileset(layout["secondary_tileset"])
    pals = ppals[:NUM_PALS_IN_PRIMARY] + spals[NUM_PALS_IN_PRIMARY:NUM_PALS_TOTAL]
    pals += [[(0, 0, 0)] * 16] * (16 - len(pals))

    W, H = w * 16, h * 16
    dst = bytearray(W * H)
    for i in range(w * h):
        block = struct.unpack("<H", blocks[i * 2:i * 2 + 2])[0] & 0x3FF
        if block < NUM_METATILES_IN_PRIMARY:
            mt = pmetas[block] if block < len(pmetas) else None
        else:
            s = block - NUM_METATILES_IN_PRIMARY
            mt = smetas[s] if s < len(smetas) else None
        if mt is None: continue
        bx, by = (i % w) * 16, (i // w) * 16
        for layer in (0, 1):                    # bottom under, top over
            for q in range(4):
                e = mt[layer * 4 + q]
                tid, pal = e & 0x3FF, (e >> 12) & 0xF
                if tid < NUM_TILES_IN_PRIMARY:
                    tile = ptiles[tid] if tid < len(ptiles) else None
                else:
                    s = tid - NUM_TILES_IN_PRIMARY
                    tile = stiles[s] if s < len(stiles) else None
                if tile is None: continue
                hf, vf = e & 0x400, e & 0x800
                ox, oy = bx + (q % 2) * 8, by + (q // 2) * 8
                base = pal * 16
                for r in range(8):
                    sr = 7 - r if vf else r
                    dr = (oy + r) * W + ox
                    tr = sr * 8
                    for c in range(8):
                        ci = tile[tr + (7 - c if hf else c)]
                        if layer and ci == 0: continue
                        dst[dr + c] = base + ci
    palette = [c for p in pals for c in p]
    return W, H, dst, palette

_LAYOUTS = {}
def load_layouts():
    data = json.load(open(f"{REPO}/data/layouts/layouts.json"))["layouts"]
    for l in data:
        if l and l.get("id"): _LAYOUTS[l["id"]] = l

# ------------------------------------------------------------------ what to draw
def maps_needed():
    """Every map a section actually crosses: the ground under each trainer
    battle, plus every map the wild-load walk clears."""
    need = set()
    for e in json.load(open(f"{OUT}/encounters.json")):
        if e["kind"] == "wild": continue
        raw = e.get("locationRaw")
        if raw: need.add(raw)
    for per_map in json.load(open(f"{OUT}/wildLoad.json")).values():
        for m in per_map: need.add(m["map"])
    # every map the completionist route acts on (items in houses, gift rooms)
    rt = f"{OUT}/route.json"
    if os.path.exists(rt):
        for st in json.load(open(rt))["stages"]:
            for s in st["steps"]:
                need.add(s["map"])
    return sorted(m for m in need if m in R.maps())

def team_sprites():
    """Front sprites for every Pokémon any leg's team fields, recolored with
    the game's own normal.pal, keyed by display name."""
    import engine as _E
    path = f"{OUT}/sections.json"
    if not os.path.exists(path): return {}
    secs = json.load(open(path))["sections"]
    species = set()
    for mode in secs.values():
        for per in mode.values():
            for sec in per.values():
                if not sec: continue
                for t in sec.get("team", []): species.add(t["species"])
                for leg in sec.get("legs", []):
                    for t in leg.get("team", []): species.add(t["species"])
    sdir = os.path.join(ART, "sprites")
    os.makedirs(sdir, exist_ok=True)
    out = {}
    for sp in sorted(species):
        folder = sp.replace("SPECIES_", "").lower()
        base = f"{REPO}/graphics/pokemon/{folder}"
        if not os.path.isdir(base): continue
        name = _E.SPECIES[sp]["name"]
        dest = os.path.join(sdir, f"{sp}.png")
        try:
            w, h, px = read_indexed_png(os.path.join(base, "front.png"))
            vals = [int(x) for x in
                    open(os.path.join(base, "normal.pal")).read().split()[3:3 + 48]]
            pal = [tuple(vals[i:i + 3]) for i in range(0, 48, 3)]
            write_indexed_png(dest, w, h, px, pal, transparent0=True)
            out[name] = f"sprites/{sp}.png"
        except Exception as ex:
            print(f"  sprite skip {sp}: {ex}")
    return out

def main():
    force = "--force" in sys.argv
    os.makedirs(ART, exist_ok=True)
    load_layouts()
    HEADERS.update(_headers())
    index, drawn, kept = {}, 0, 0
    for m in maps_needed():
        out = os.path.join(ART, f"{m}.png")
        r = render_map(m)
        if r is None:
            print(f"  skip {m}: no layout"); continue
        W, H, dst, palette = r
        if force or not os.path.exists(out):
            write_indexed_png(out, W, H, dst, palette); drawn += 1
        else:
            kept += 1
        index[m] = {"w": W, "h": H}
    trainers = {const: [mp, x, y]
                for const, (mp, x, y) in R.trainer_tiles().items()
                if mp in index}
    # outdoor connections with their offsets, so adjacent maps can be
    # stitched back into one continuous picture
    conns = {}
    for m in index:
        mj = R.maps().get(m) or {}
        rows = []
        for c in (mj.get("connections") or []):
            nb = R._const_name(c["map"])
            if nb in index:
                rows.append([c["direction"], int(c.get("offset", 0)), nb])
        if rows: conns[m] = rows
    sprites = team_sprites()
    with open(os.path.join(ART, "index.json"), "w") as f:
        json.dump({"maps": index, "trainers": trainers,
                   "connections": conns, "sprites": sprites}, f)
    size = sum(os.path.getsize(os.path.join(ART, f"{m}.png")) for m in index)
    print(f"maps: {len(index)} ({drawn} drawn, {kept} kept), "
          f"{size/1e6:.2f} MB of PNG; trainers placed: {len(trainers)}; "
          f"sprites: {len(sprites)}")

if __name__ == "__main__":
    main()
