#!/usr/bin/env python3
"""Extract Gen 3 (FireRed/LeafGreen) game data from the pret/pokefirered decompilation
into normalized JSON. Source of truth is the actual game code, not wiki transcription.
"""
import json, os, re, sys
from collections import defaultdict

# Path to a checkout of pret/pokefirered. extract.py runs before data/ exists,
# so it cannot import engine for this -- it resolves the path itself.
REPO = os.environ.get("LGMAX_POKEFIRERED",
                      os.path.expanduser("~/pokefirered"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(OUT, exist_ok=True)

def read(p):
    with open(os.path.join(REPO, p), encoding="utf-8", errors="replace") as f:
        return f.read()

def strip_comments(s):
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    s = re.sub(r"//[^\n]*", "", s)
    return s

# ---------------------------------------------------------------- constants
def parse_enum_constants(path, prefix):
    """Parse `#define PREFIX_X n` sequences, honoring simple arithmetic on prior names."""
    txt = strip_comments(read(path))
    vals = {}
    for m in re.finditer(r"^#define\s+(" + prefix + r"[A-Z0-9_]*)\s+(.+)$", txt, re.M):
        name, expr = m.group(1), m.group(2).strip()
        expr = expr.split("//")[0].strip()
        # resolve references to already-defined names
        def sub(mm):
            k = mm.group(0)
            return str(vals[k]) if k in vals else k
        e = re.sub(r"[A-Z_][A-Z0-9_]*", sub, expr)
        try:
            v = int(eval(e, {"__builtins__": {}}, {}))
        except Exception:
            continue
        vals[name] = v
    return vals

SPECIES = parse_enum_constants("include/constants/species.h", "SPECIES_")
MOVES = parse_enum_constants("include/constants/moves.h", "MOVE_")
ITEMS = parse_enum_constants("include/constants/items.h", "ITEM_")

TYPES = ["NORMAL","FIGHTING","FLYING","POISON","GROUND","ROCK","BUG","GHOST","STEEL",
         "MYSTERY","FIRE","WATER","GRASS","ELECTRIC","PSYCHIC","ICE","DRAGON","DARK"]
TYPE_ID = {f"TYPE_{t}": i for i, t in enumerate(TYPES)}

# In Gen 3 the physical/special split is per TYPE, not per move.
PHYSICAL_TYPES = {"NORMAL","FIGHTING","FLYING","POISON","GROUND","ROCK","BUG","GHOST","STEEL"}

def titlecase(s):
    s = s.replace("$", "").strip()
    parts = []
    for w in s.split():
        parts.append(w.capitalize())
    return " ".join(parts)

# ---------------------------------------------------------------- names
def parse_name_table(path, pattern):
    txt = read(path)
    out = {}
    for m in re.finditer(pattern, txt):
        out[m.group(1)] = m.group(2)
    return out

species_names_raw = parse_name_table("src/data/text/species_names.h",
    r'\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\("([^"]*)"\)')
move_names_raw = parse_name_table("src/data/text/move_names.h",
    r'\[(MOVE_[A-Z0-9_]+)\]\s*=\s*_\("([^"]*)"\)')

SPECIAL_NAMES = {
    "SPECIES_NIDORAN_F": "Nidoran♀", "SPECIES_NIDORAN_M": "Nidoran♂",
    "SPECIES_FARFETCHD": "Farfetch'd", "SPECIES_MR_MIME": "Mr. Mime",
    "SPECIES_HO_OH": "Ho-Oh", "SPECIES_PORYGON2": "Porygon2",
}
MOVE_NAME_FIX = {
    "DOUBLESLAP": "Double Slap", "SONICBOOM": "Sonic Boom", "BUBBLEBEAM": "Bubble Beam",
    "SOLARBEAM": "Solar Beam", "THUNDERPUNCH": "Thunder Punch", "THUNDERSHOCK": "Thunder Shock",
    "DOUBLE-EDGE": "Double-Edge", "POISONPOWDER": "Poison Powder", "VICEGRIP": "Vice Grip",
    "SMOKESCREEN": "Smokescreen", "DRAGONBREATH": "Dragon Breath", "EXTREMESPEED": "Extreme Speed",
    "ANCIENTPOWER": "Ancient Power", "FEATHERDANCE": "Feather Dance", "SAND-ATTACK": "Sand-Attack",
    "SELFDESTRUCT": "Self-Destruct", "LOCK-ON": "Lock-On", "WILL-O-WISP": "Will-O-Wisp",
    "MUD-SLAP": "Mud-Slap", "FAINT ATTACK": "Faint Attack",
}

def species_display(const):
    if const in SPECIAL_NAMES: return SPECIAL_NAMES[const]
    raw = species_names_raw.get(const, const.replace("SPECIES_", ""))
    return titlecase(raw)

def move_display(const):
    raw = move_names_raw.get(const, const.replace("MOVE_", "")).replace("$", "")
    if raw in MOVE_NAME_FIX: return MOVE_NAME_FIX[raw]
    return titlecase(raw)

# ---------------------------------------------------------------- species info
def parse_species_info():
    txt = strip_comments(read("src/data/pokemon/species_info.h"))
    body = txt[txt.index("gSpeciesInfo[]"):]
    # `[SPECIES_NONE] = {0},` is a one-line entry; skipping past it stops the
    # non-greedy block regex from swallowing Bulbasaur's entry along with it.
    body = body[body.index("[SPECIES_NONE] = {0},") + len("[SPECIES_NONE] = {0},"):]
    out = {}
    # each entry: [SPECIES_X] = { ... },
    for m in re.finditer(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*\{(.*?)\n    \}", body, re.S):
        const, blk = m.group(1), m.group(2)
        if const == "SPECIES_NONE": continue
        def f(key, default=0):
            mm = re.search(r"\." + key + r"\s*=\s*([A-Za-z0-9_]+)", blk)
            if not mm: return default
            v = mm.group(1)
            return int(v) if v.isdigit() else v
        tm = re.search(r"\.types\s*=\s*\{\s*(TYPE_[A-Z_]+)\s*,\s*(TYPE_[A-Z_]+)\s*\}", blk)
        if not tm: continue
        t1, t2 = tm.group(1), tm.group(2)
        am = re.search(r"\.abilities\s*=\s*\{\s*(ABILITY_[A-Z0-9_]+)\s*,\s*(ABILITY_[A-Z0-9_]+)\s*\}", blk)
        out[const] = {
            "id": SPECIES.get(const),
            "const": const,
            "name": species_display(const),
            "baseHP": f("baseHP"), "baseAttack": f("baseAttack"),
            "baseDefense": f("baseDefense"), "baseSpeed": f("baseSpeed"),
            "baseSpAttack": f("baseSpAttack"), "baseSpDefense": f("baseSpDefense"),
            "types": [t1.replace("TYPE_", ""), t2.replace("TYPE_", "")],
            "catchRate": f("catchRate"), "expYield": f("expYield"),
            "growthRate": str(f("growthRate", "GROWTH_MEDIUM_FAST")).replace("GROWTH_", ""),
            "abilities": [am.group(1).replace("ABILITY_", ""), am.group(2).replace("ABILITY_", "")] if am else ["NONE","NONE"],
        }
    return out

# ---------------------------------------------------------------- moves
def parse_moves():
    txt = strip_comments(read("src/data/battle_moves.h"))
    out = {}
    for m in re.finditer(r"\[(MOVE_[A-Z0-9_]+)\]\s*=\s*\{(.*?)\n    \}", txt, re.S):
        const, blk = m.group(1), m.group(2)
        def f(key, default=0):
            mm = re.search(r"\." + key + r"\s*=\s*(-?[A-Za-z0-9_]+)", blk)
            if not mm: return default
            v = mm.group(1)
            try: return int(v)
            except ValueError: return v
        typ = str(f("type", "TYPE_NORMAL")).replace("TYPE_", "")
        eff = str(f("effect", "EFFECT_HIT")).replace("EFFECT_", "")
        flags = re.search(r"\.flags\s*=\s*([^,\n]*)", blk)
        out[const] = {
            "id": MOVES.get(const), "const": const, "name": move_display(const),
            "power": f("power"), "type": typ, "accuracy": f("accuracy"),
            "pp": f("pp"), "effect": eff,
            "secondaryChance": f("secondaryEffectChance"),
            "priority": f("priority"),
            "category": "PHYSICAL" if typ in PHYSICAL_TYPES else "SPECIAL",
            "flags": (flags.group(1).strip() if flags else ""),
        }
        if out[const]["power"] == 0:
            out[const]["category"] = "STATUS"
    return out

# ---------------------------------------------------------------- learnsets
def parse_level_up_learnsets():
    txt = strip_comments(read("src/data/pokemon/level_up_learnsets.h"))
    tables = {}
    for m in re.finditer(r"static const u16 (s\w+LevelUpLearnset)\[\]\s*=\s*\{(.*?)\};", txt, re.S):
        name, blk = m.group(1), m.group(2)
        moves = [(int(a), b) for a, b in re.findall(r"LEVEL_UP_MOVE\(\s*(\d+)\s*,\s*(MOVE_[A-Z0-9_]+)\s*\)", blk)]
        tables[name] = moves
    ptr = strip_comments(read("src/data/pokemon/level_up_learnset_pointers.h"))
    out = {}
    for m in re.finditer(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*(s\w+LevelUpLearnset)", ptr):
        out[m.group(1)] = tables.get(m.group(2), [])
    return out

def parse_tmhm():
    txt = strip_comments(read("src/data/pokemon/tmhm_learnsets.h"))
    out = {}
    for m in re.finditer(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*TMHM_LEARNSET\((.*?)\),\s*\n", txt, re.S):
        const, blk = m.group(1), m.group(2)
        out[const] = re.findall(r"TMHM\((\w+)\)", blk)
    return out

def parse_tutor():
    txt = strip_comments(read("src/data/pokemon/tutor_learnsets.h"))
    out = {}
    for m in re.finditer(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*TUTOR_LEARNSET\((.*?)\),\s*\n", txt, re.S):
        out[m.group(1)] = re.findall(r"TUTOR\((\w+)\)", m.group(2))
    return out

def parse_evolutions():
    """Split on the `[SPECIES_X] =` markers rather than trying to brace-match.
    Multi-branch entries (Eevee's three stones, Gloom, Poliwhirl) nest braces and
    span lines, which defeats a non-greedy block regex."""
    txt = strip_comments(read("src/data/pokemon/evolution.h"))
    marks = [(m.start(), m.group(1)) for m in
             re.finditer(r"\[(SPECIES_[A-Z0-9_]+)\]\s*=", txt)]
    out = defaultdict(list)
    for i, (pos, const) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(txt)
        blk = txt[pos:end]
        for e in re.finditer(r"\{\s*(EVO_[A-Z_]+)\s*,\s*([A-Za-z0-9_]+)\s*,\s*(SPECIES_[A-Z0-9_]+)\s*\}", blk):
            method, param, target = e.group(1), e.group(2), e.group(3)
            out[const].append({
                "method": method.replace("EVO_", ""),
                "param": int(param) if param.isdigit() else param,
                "to": target,
            })
    return dict(out)

# ---------------------------------------------------------------- type chart
def parse_type_chart():
    txt = read("src/battle_main.c")
    blk = txt[txt.index("gTypeEffectiveness[336]"):]
    blk = blk[:blk.index("};")]
    chart = defaultdict(dict)
    for m in re.finditer(r"(TYPE_[A-Z_]+),\s*(TYPE_[A-Z_]+),\s*TYPE_MUL_([A-Z_]+)", blk):
        atk, dfn, mul = m.group(1).replace("TYPE_",""), m.group(2).replace("TYPE_",""), m.group(3)
        val = {"SUPER_EFFECTIVE": 2.0, "NOT_EFFECTIVE": 0.5, "NO_EFFECT": 0.0, "NORMAL": 1.0}[mul]
        chart[atk][dfn] = val
    return {k: dict(v) for k, v in chart.items()}

# ---------------------------------------------------------------- trainers
def parse_trainer_parties():
    txt = strip_comments(read("src/data/trainer_parties.h"))
    # expand the DUMMY macros so placeholder RS parties still parse
    dummies = {
        "DUMMY_TRAINER_MON": [{"lvl": 5, "species": "SPECIES_EKANS", "iv": 0, "moves": None, "item": None}],
        "DUMMY_TRAINER_MON_IV": [{"lvl": 5, "species": "SPECIES_EKANS", "iv": 100, "moves": None, "item": None}],
        "DUMMY_TRAINER_STARMIE": [{"lvl": 38, "species": "SPECIES_STARMIE", "iv": 0, "moves": None, "item": None}],
    }
    out = {}
    for m in re.finditer(r"static const struct (TrainerMon\w+) (sParty_\w+)\[\]\s*=\s*\{(.*?)\n?\};", txt, re.S):
        kind, name, blk = m.group(1), m.group(2), m.group(3)
        blk_s = blk.strip()
        if blk_s in dummies:
            out[name] = {"kind": kind, "mons": json.loads(json.dumps(dummies[blk_s]))}
            continue
        mons = []
        for e in re.finditer(r"\{(.*?)\}(?=\s*,\s*(?:\{|$)|\s*$)", blk, re.S):
            eb = e.group(1)
            if "species" not in eb: continue
            def g(k):
                mm = re.search(r"\." + k + r"\s*=\s*([A-Za-z0-9_]+)", eb)
                return mm.group(1) if mm else None
            mv = re.search(r"\.moves\s*=\s*\{([^}]*)\}", eb)
            moves = None
            if mv:
                moves = [x.strip() for x in mv.group(1).split(",") if x.strip() and x.strip() != "MOVE_NONE"]
            lvl = g("lvl"); spc = g("species"); iv = g("iv"); itm = g("heldItem")
            if spc is None: continue
            mons.append({
                "lvl": int(lvl) if lvl and lvl.isdigit() else 0,
                "species": spc,
                "iv": int(iv) if iv and iv.isdigit() else 0,
                "moves": moves,
                "item": itm,
            })
        out[name] = {"kind": kind, "mons": mons}
    return out

def parse_trainers(parties):
    txt = strip_comments(read("src/data/trainers.h"))
    out = {}
    for m in re.finditer(r"\[(TRAINER_[A-Z0-9_]+)\]\s*=\s*\{(.*?)\n    \}", txt, re.S):
        const, blk = m.group(1), m.group(2)
        nm = re.search(r'\.trainerName\s*=\s*_\("([^"]*)"\)', blk)
        pic = re.search(r"\.trainerPic\s*=\s*TRAINER_PIC_([A-Z0-9_]+)", blk)
        cls = re.search(r"\.trainerClass\s*=\s*(TRAINER_CLASS_[A-Z0-9_]+)", blk)
        pty = re.search(r"\.party\s*=\s*(\w+)\((sParty_\w+)\)", blk)
        dbl = "TRUE" in (re.search(r"\.doubleBattle\s*=\s*(\w+)", blk) or re.Match).__str__() if False else bool(re.search(r"\.doubleBattle\s*=\s*TRUE", blk))
        items = re.findall(r"(ITEM_[A-Z0-9_]+)", (re.search(r"\.items\s*=\s*\{([^}]*)\}", blk) or type("x",(),{"group":lambda s,i:""})()).group(1) or "")
        ai = re.search(r"\.aiFlags\s*=\s*([^,\n]*)", blk)
        p = parties.get(pty.group(2)) if pty else None
        out[const] = {
            "const": const,
            "pic": pic.group(1) if pic else None,
            "name": titlecase(nm.group(1)) if nm and nm.group(1) else "",
            "class": cls.group(1).replace("TRAINER_CLASS_", "").replace("_", " ").title() if cls else "",
            # the raw class constant, so the prize-money table (keyed by it) resolves
            "classConst": cls.group(1).replace("TRAINER_CLASS_", "") if cls else None,
            "partyLabel": pty.group(2) if pty else None,
            "doubleBattle": dbl,
            "items": items,
            "aiFlags": ai.group(1).strip() if ai else "",
            "party": p["mons"] if p else [],
            "partyKind": p["kind"] if p else None,
        }
    return out

def parse_trainer_locations():
    """Map trainer constant -> the map/area where the battle happens, using
    trainerbattle_* script commands and their label prefixes."""
    loc = defaultdict(set)
    import glob
    files = [os.path.join(REPO, "data/scripts/trainers.inc")]
    files += glob.glob(os.path.join(REPO, "data/maps/*/scripts.inc"))
    files += glob.glob(os.path.join(REPO, "data/scripts/*.inc"))
    for path in files:
        try:
            txt = open(path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        mapname = None
        if "/maps/" in path:
            mapname = path.split("/maps/")[1].split("/")[0]
        cur_label = None
        for line in txt.splitlines():
            lm = re.match(r"^(\w+)::", line)
            if lm:
                cur_label = lm.group(1)
            tb = re.search(r"trainerbattle\w*\s+(TRAINER_[A-Z0-9_]+)", line)
            if tb:
                t = tb.group(1)
                area = mapname
                if area is None and cur_label:
                    area = cur_label.split("_EventScript_")[0] if "_EventScript_" in cur_label else cur_label
                if area:
                    loc[t].add(area)
    return {k: sorted(v) for k, v in loc.items()}

# ---------------------------------------------------------------- wild
def parse_wild():
    j = json.loads(read("src/data/wild_encounters.json"))
    groups = j["wild_encounter_groups"]
    main = next(g for g in groups if g["label"] == "gWildMonHeaders")
    rates = {f["type"]: f.get("encounter_rates", []) for f in main["fields"]}
    fishing_groups = next((f.get("groups") for f in main["fields"] if f["type"] == "fishing_mons"), {})
    out = []
    for enc in main["encounters"]:
        label = enc.get("base_label", "")
        version = "LeafGreen" if label.endswith("_LeafGreen") else ("FireRed" if label.endswith("_FireRed") else "Both")
        rec = {"map": enc["map"], "label": label, "version": version, "tables": {}}
        for field in ("land_mons", "water_mons", "rock_smash_mons", "fishing_mons"):
            if field not in enc: continue
            data = enc[field]
            slots = []
            for i, mon in enumerate(data["mons"]):
                slots.append({
                    "slot": i,
                    "species": mon["species"],
                    "minLevel": mon["min_level"],
                    "maxLevel": mon["max_level"],
                    "rate": rates.get(field, [])[i] if i < len(rates.get(field, [])) else 0,
                })
            rec["tables"][field] = {"encounterRate": data.get("encounter_rate", 0), "slots": slots}
        out.append(rec)
    return {"encounters": out, "rates": rates, "fishingGroups": fishing_groups}

# ---------------------------------------------------------------- items
def parse_items():
    j = json.loads(read("src/data/items.json"))
    out = {}
    for it in j["items"]:
        out[it["itemId"]] = {
            "const": it["itemId"], "name": titlecase(it["english"]),
            "price": it.get("price", 0), "pocket": it.get("pocket", ""),
            "holdEffect": it.get("holdEffect", ""),
            "holdEffectParam": it.get("holdEffectParam", 0),
            "description": it.get("description_english", "").replace("\\n", " ").replace("\\p", " "),
            "secondaryId": it.get("secondaryId", 0),
        }
    return out

def parse_weights():
    """Pokedex weight in hectograms, keyed by species const (Low Kick needs it)."""
    txt = strip_comments(read("src/data/pokemon/pokedex_entries.h"))
    out = {}
    for m in re.finditer(r"\[NATIONAL_DEX_([A-Z0-9_]+)\]\s*=\s*\{(.*?)\n    \}", txt, re.S):
        name, blk = m.group(1), m.group(2)
        w = re.search(r"\.weight\s*=\s*(\d+)", blk)
        h = re.search(r"\.height\s*=\s*(\d+)", blk)
        const = f"SPECIES_{name}"
        if w:
            out[const] = {"weightHg": int(w.group(1)),
                          "heightDm": int(h.group(1)) if h else 0}
    return out

def parse_exp_tables():
    txt = strip_comments(read("src/data/pokemon/experience_tables.h"))
    out = {}
    for m in re.finditer(r"\[GROWTH_([A-Z_]+)\]\s*=\s*\{(.*?)\}", txt, re.S):
        nums = [int(x) for x in re.findall(r"\d+", m.group(2))]
        out[m.group(1)] = nums
    return out

# ---------------------------------------------------------------- run
if __name__ == "__main__":
    print("parsing species...");   species = parse_species_info()
    print("parsing moves...");     moves = parse_moves()
    print("parsing learnsets..."); levelup = parse_level_up_learnsets()
    print("parsing tmhm...");      tmhm = parse_tmhm()
    print("parsing tutor...");     tutor = parse_tutor()
    print("parsing evos...");      evos = parse_evolutions()
    print("parsing typechart...");  chart = parse_type_chart()
    print("parsing parties...");   parties = parse_trainer_parties()
    print("parsing trainers...");  trainers = parse_trainers(parties)
    print("parsing locations..."); tloc = parse_trainer_locations()
    print("parsing wild...");      wild = parse_wild()
    print("parsing items...");     items = parse_items()
    print("parsing exp...");       exp = parse_exp_tables()
    print("parsing weights...");   weights = parse_weights()

    for t, locs in tloc.items():
        if t in trainers: trainers[t]["locations"] = locs
    for t in trainers.values():
        t.setdefault("locations", [])

    bundle = {
        "species": species, "moves": moves, "levelUpLearnsets": levelup,
        "tmhmLearnsets": tmhm, "tutorLearnsets": tutor, "evolutions": evos,
        "typeChart": chart, "trainers": trainers, "wild": wild,
        "items": items, "expTables": exp, "weights": weights,
        "constants": {"species": SPECIES, "moves": MOVES, "items": ITEMS},
    }
    for k, v in bundle.items():
        with open(os.path.join(OUT, f"{k}.json"), "w") as f:
            json.dump(v, f, separators=(",", ":"))
    print("\n--- counts ---")
    print("species:", len(species), "moves:", len(moves), "learnsets:", len(levelup))
    print("trainers:", len(trainers), "with party:", sum(1 for t in trainers.values() if t["party"]))
    print("trainers with location:", sum(1 for t in trainers.values() if t["locations"]))
    print("wild maps:", len(wild["encounters"]), "items:", len(items))
    print("evolutions:", len(evos), "tmhm:", len(tmhm), "weights:", len(weights))
