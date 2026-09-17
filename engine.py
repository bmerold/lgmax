#!/usr/bin/env python3
"""Gen 3 (FireRed/LeafGreen) battle engine.

Every formula here is transcribed from the pokefirered decompilation:
  - CalculateBaseDamage / CalculateMonStats  -> src/pokemon.c
  - crit, STAB, type mult, random roll       -> src/battle_script_commands.c
  - trainer party generation                 -> src/battle_main.c
"""
import json, os, re
from functools import lru_cache

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# Path to a checkout of the pret/pokefirered decompilation -- the single source
# of truth for every number in this project. Override with LGMAX_POKEFIRERED.
REPO = os.environ.get("LGMAX_POKEFIRERED",
                      os.path.expanduser("~/pokefirered"))

def _load(name):
    with open(os.path.join(DATA, f"{name}.json")) as f:
        return json.load(f)

SPECIES     = _load("species")
MOVES       = _load("moves")
LEVELUP     = _load("levelUpLearnsets")
TMHM        = _load("tmhmLearnsets")
TUTOR       = _load("tutorLearnsets")
EVOS        = _load("evolutions")
TYPECHART   = _load("typeChart")
TRAINERS    = _load("trainers")
WILD        = _load("wild")
ITEMS       = _load("items")
EXPTABLES   = _load("expTables")
WEIGHTS     = _load("weights")

PHYSICAL_TYPES = {"NORMAL","FIGHTING","FLYING","POISON","GROUND","ROCK","BUG","GHOST","STEEL"}

# ------------------------------------------------------------------ natures
NATURE_NAMES = ["Hardy","Lonely","Brave","Adamant","Naughty","Bold","Docile","Relaxed",
    "Impish","Lax","Timid","Hasty","Serious","Jolly","Naive","Modest","Mild","Quiet",
    "Bashful","Rash","Calm","Gentle","Sassy","Careful","Quirky"]
# columns: Attack, Defense, Speed, SpAtk, SpDef  (src/pokemon.c sNatureStatTable)
NATURE_TABLE = [
    (0,0,0,0,0), (1,-1,0,0,0), (1,0,-1,0,0), (1,0,0,-1,0), (1,0,0,0,-1),
    (-1,1,0,0,0), (0,0,0,0,0), (0,1,-1,0,0), (0,1,0,-1,0), (0,1,0,0,-1),
    (-1,0,1,0,0), (0,-1,1,0,0), (0,0,0,0,0), (0,0,1,-1,0), (0,0,1,0,-1),
    (-1,0,0,1,0), (0,-1,0,1,0), (0,0,-1,1,0), (0,0,0,0,0), (0,0,0,1,-1),
    (-1,0,0,0,1), (0,-1,0,0,1), (0,0,-1,0,1), (0,0,0,-1,1), (0,0,0,0,0),
]
STAT_ORDER = ["attack","defense","speed","spAttack","spDefense"]

def modify_by_nature(nature, stat, stat_name):
    if stat_name not in STAT_ORDER: return stat
    d = NATURE_TABLE[nature][STAT_ORDER.index(stat_name)]
    if d == 1:  return (stat * 110) // 100
    if d == -1: return (stat * 90) // 100
    return stat

# ------------------------------------------------------------------ charmap (for trainer nature derivation)
@lru_cache(maxsize=1)
def charmap():
    m = {}
    with open(os.path.join(REPO, "charmap.txt"), encoding="utf-8") as f:
        for line in f:
            # charmap.txt escapes the apostrophe as '\'' — matching only a single
            # character here silently dropped it, corrupting the name hash for
            # every trainer whose party contains Farfetch'd.
            mm = re.match(r"^'(\\'|.)'\s*=\s*([0-9A-Fa-f]{2})", line)
            if mm:
                ch = mm.group(1)
                if ch == "\\'": ch = "'"
                m[ch] = int(mm.group(2), 16)
    return m

def encode_text(s):
    cm = charmap()
    missing = [c for c in s if c not in cm]
    if missing:
        raise ValueError(f"charmap has no encoding for {missing!r} in {s!r}")
    return [cm[c] for c in s]

# ------------------------------------------------------------------ stats
def calc_stats(species_const, level, ivs=None, evs=None, nature=0):
    """CalculateMonStats from src/pokemon.c"""
    sp = SPECIES[species_const]
    ivs = ivs or {}
    evs = evs or {}
    def iv(k): return ivs.get(k, 0)
    def ev(k): return evs.get(k, 0)
    hp = ((2 * sp["baseHP"] + iv("hp") + ev("hp") // 4) * level) // 100 + level + 10
    out = {"hp": hp}
    for key, base in (("attack","baseAttack"), ("defense","baseDefense"), ("speed","baseSpeed"),
                      ("spAttack","baseSpAttack"), ("spDefense","baseSpDefense")):
        n = 2 * sp[base] + iv(key)
        v = ((n + ev(key) // 4) * level) // 100 + 5
        out[key] = modify_by_nature(nature, v, key)
    return out

# ------------------------------------------------------------------ movesets
def default_moveset(species_const, level):
    """GiveBoxMonInitialMoveset from src/pokemon.c: walk the level-up learnset in
    order; when 4 moves are held, drop the oldest and append."""
    moves = []
    for lv, mv in LEVELUP.get(species_const, []):
        if lv > level: break
        if mv in moves: continue
        if len(moves) == 4: moves.pop(0)
        moves.append(mv)
    return moves

def learnable_by(species_const, level):
    """Every level-up move a species knows at or below `level`."""
    return [mv for lv, mv in LEVELUP.get(species_const, []) if lv <= level]

# ------------------------------------------------------------------ trainer party realization
def trainer_nature_sequence(trainer_const):
    """Trainer mon personalities are deterministic (src/battle_main.c CreateNPCTrainerParty):
    a running nameHash accumulates the trainer name plus each species name, and the
    per-mon personality = base + (nameHash << 8). nature = personality % 25."""
    t = TRAINERS[trainer_const]
    if t["doubleBattle"]:
        base = 0x80
    else:
        # encounterMusic_gender F_TRAINER_FEMALE bit; recover from the raw source
        base = 0x78 if _is_female(trainer_const) else 0x88
    name_bytes = encode_text(t["name"].upper()) if t["name"] else []
    name_hash = 0
    natures = []
    for mon in t["party"]:
        name_hash += sum(name_bytes)
        sp_name = SPECIES[mon["species"]]["name"].upper()
        # species names are stored uppercase, no spaces except Mr. Mime etc.
        name_hash += sum(encode_text(_species_stored_name(mon["species"])))
        personality = (base + (name_hash << 8)) & 0xFFFFFFFF
        natures.append(personality % 25)
    return natures

@lru_cache(maxsize=1)
def _female_set():
    txt = open(os.path.join(REPO, "src/data/trainers.h"), encoding="utf-8").read()
    out = set()
    for m in re.finditer(r"\[(TRAINER_[A-Z0-9_]+)\]\s*=\s*\{(.*?)\n    \}", txt, re.S):
        if "F_TRAINER_FEMALE" in m.group(2):
            out.add(m.group(1))
    return out

def _is_female(trainer_const):
    return trainer_const in _female_set()

@lru_cache(maxsize=1)
def _stored_names():
    txt = open(os.path.join(REPO, "src/data/text/species_names.h"), encoding="utf-8").read()
    return dict(re.findall(r'\[(SPECIES_[A-Z0-9_]+)\]\s*=\s*_\("([^"]*)"\)', txt))

def _species_stored_name(const):
    return _stored_names().get(const, "")

def realize_trainer_mon(trainer_const, index):
    """Produce the exact battle-ready Pokemon a trainer sends out."""
    t = TRAINERS[trainer_const]
    mon = t["party"][index]
    fixed_iv = (mon["iv"] * 31) // 255
    ivs = {k: fixed_iv for k in ("hp","attack","defense","speed","spAttack","spDefense")}
    natures = trainer_nature_sequence(trainer_const)
    nature = natures[index] if index < len(natures) else 0
    moves = mon["moves"] if mon["moves"] else default_moveset(mon["species"], mon["lvl"])
    return {
        "species": mon["species"],
        "name": SPECIES[mon["species"]]["name"],
        "level": mon["lvl"],
        "ivs": ivs, "evs": {}, "nature": nature,
        "natureName": NATURE_NAMES[nature],
        "moves": moves,
        "item": mon.get("item") if mon.get("item") not in (None, "ITEM_NONE") else None,
        "types": SPECIES[mon["species"]]["types"],
        "ability": SPECIES[mon["species"]]["abilities"][0],
        "stats": calc_stats(mon["species"], mon["lvl"], ivs, {}, nature),
    }

def make_mon(species_const, level, ivs_flat=15, evs=None, nature=0, moves=None,
             item=None, ability=None):
    ivs = {k: ivs_flat for k in ("hp","attack","defense","speed","spAttack","spDefense")}
    sp = SPECIES[species_const]
    return {
        "species": species_const, "name": sp["name"], "level": level,
        "ivs": ivs, "evs": evs or {}, "nature": nature, "natureName": NATURE_NAMES[nature],
        "moves": moves if moves is not None else default_moveset(species_const, level),
        "item": item, "types": sp["types"],
        "ability": ability or sp["abilities"][0],
        "stats": calc_stats(species_const, level, ivs, evs or {}, nature),
    }

# ------------------------------------------------------------------ type effectiveness
def type_mult(attack_type, defender_types):
    mult = 1.0
    seen = []
    for dt in defender_types:
        if dt in seen or dt == "MYSTERY": continue
        seen.append(dt)
        mult *= TYPECHART.get(attack_type, {}).get(dt, 1.0)
    return mult

# ------------------------------------------------------------------ damage
# Cmd_critcalc counts exactly these four effects — Razor Wind is NOT among them.
HIGH_CRIT_EFFECTS = {"HIGH_CRITICAL", "SKY_ATTACK", "BLAZE_KICK", "POISON_TAIL"}
HIGH_CRIT_MOVES = {"MOVE_CRABHAMMER","MOVE_KARATE_CHOP","MOVE_RAZOR_LEAF","MOVE_SLASH",
                   "MOVE_AEROBLAST","MOVE_CROSS_CHOP","MOVE_LEAF_BLADE","MOVE_SKY_ATTACK",
                   "MOVE_BLAZE_KICK","MOVE_POISON_TAIL"}

def crit_chance(move_const):
    return 1/8 if (move_const in HIGH_CRIT_MOVES or
                   MOVES[move_const]["effect"] in HIGH_CRIT_EFFECTS) else 1/16

FIXED_DAMAGE_EFFECTS = {"DRAGON_RAGE": 40, "SONICBOOM": 20}
LEVEL_DAMAGE_EFFECTS = {"LEVEL_DAMAGE", "PSYWAVE"}   # Night Shade / Seismic Toss / Psywave

# Moves whose usefulness can't be represented as "pick it and hit every turn":
# one-hit KOs (30% accuracy, fail on higher-level targets), moves that need the
# opponent to have acted first, and moves needing a state we don't model.
UNUSABLE_EFFECTS = {
    "OHKO", "COUNTER", "MIRROR_COAT", "BIDE", "DREAM_EATER", "SPIT_UP",
    "FOCUS_PUNCH",      # -3 priority; only connects if the user isn't hit
    "REVENGE", "ENDEAVOR", "BEAT_UP", "FUTURE_SIGHT", "RAGE", "PURSUIT",
    "FRUSTRATION",      # a playthrough Pokemon has high friendship, so ~1 BP
    "RAMPAGE",          # Thrash/Outrage/Petal Dance lock the user in for 2-3
                        # turns and confuse it after: no retargeting, no
                        # switching, no move choice -- the plan can't honor
                        # any of that, so it never recommends them
}

# Effective turns per use. Charge moves spend a turn winding up; Hyper Beam
# spends one recharging, but not after the hit that faints the target.
CHARGE_EFFECTS = {"SOLAR_BEAM", "SKY_ATTACK", "RAZOR_WIND", "SKULL_BASH",
                  "SEMI_INVULNERABLE"}
RECHARGE_EFFECTS = {"RECHARGE"}

# ------------------------------------------------------------------ status tempo
# Secondary effects of damaging moves, folded as expected value. Gen 3
# numbers: thaw 20% per turn, full paralysis 25%, confusion self-hit 50%
# lasting 1-4 turns. One major status per target, they don't stack.
TEMPO_EFFECTS = {"FLINCH_HIT", "FREEZE_HIT", "PARALYZE_HIT", "BURN_HIT",
                 "POISON_HIT", "CONFUSE_HIT", "TWINEEDLE", "POISON_FANG"}
def status_tempo(move_const, attacker_faster, turns):
    """-> (act, burn, chip): the fraction of its turns the defender actually
    gets to act, the average probability it sits burned (physical attack
    halved), and its expected residual damage per turn as a fraction of its
    max HP. A tiny EV walk over the fight's length -- no state machine."""
    m = MOVES.get(move_const) or {}
    eff = m.get("effect")
    p = (m.get("secondaryChance") or 0) / 100.0 * ((m.get("accuracy") or 100) / 100.0)
    if eff not in TEMPO_EFFECTS or p <= 0:
        return 1.0, 0.0, 0.0
    T = max(1, min(int(turns + 0.999), 10))
    frozen = para = burn = psn = conf = 0.0
    acts = burn_avg = chip = 0.0
    for _ in range(T):
        healthy = max(0.0, 1.0 - frozen - para - burn - psn)
        if eff == "FREEZE_HIT":     frozen += healthy * p
        elif eff == "PARALYZE_HIT": para += healthy * p
        elif eff == "BURN_HIT":     burn += healthy * p
        elif eff in ("POISON_HIT", "TWINEEDLE", "POISON_FANG"):
            psn += healthy * p
        elif eff == "CONFUSE_HIT":  conf = min(1.0, conf + (1.0 - conf) * p)
        act = (1.0 - frozen) * (1.0 - 0.25 * para) * (1.0 - 0.5 * conf)
        if eff == "FLINCH_HIT" and attacker_faster:
            act *= (1.0 - p)
        acts += act
        burn_avg += burn
        chip += (burn + psn) * 0.125
        frozen *= 0.8
        conf *= 0.6
    return acts / T, burn_avg / T, chip / T

# Gen 3 Low Kick power brackets, by target weight in hectograms.
LOW_KICK_BRACKETS = [(100, 20), (250, 40), (500, 60), (1000, 80), (2000, 100)]

def hidden_power(ivs):
    """Gen 3 Hidden Power type and power, from the six IVs."""
    order = ["hp", "attack", "defense", "speed", "spAttack", "spDefense"]
    bit0 = sum(((ivs.get(k, 0) & 1) << i) for i, k in enumerate(order))
    bit1 = sum((((ivs.get(k, 0) >> 1) & 1) << i) for i, k in enumerate(order))
    types = ["FIGHTING","FLYING","POISON","GROUND","ROCK","BUG","GHOST","STEEL",
             "FIRE","WATER","GRASS","ELECTRIC","PSYCHIC","ICE","DRAGON","DARK"]
    return types[(bit0 * 15) // 63], (bit1 * 40) // 63 + 30

def effective_power(attacker, defender, move_const):
    """Resolve variable-power moves to the power they'd actually have here.
    Returns None if the move can't be sensibly used as a damage option."""
    mv = MOVES[move_const]
    eff = mv["effect"]
    if eff in UNUSABLE_EFFECTS: return None
    if eff == "RETURN":        return 102      # max friendship
    if eff == "HIDDEN_POWER":  return hidden_power(attacker["ivs"])[1]
    if eff == "MAGNITUDE":     return 71       # probability-weighted mean
    if eff == "PRESENT":       return 52       # 40/80/120 BP at 40/30/10%
    if eff in ("FLAIL", "REVERSAL"): return 20 # modelled at full HP
    if eff == "ERUPTION":      return 150      # modelled at full HP
    if eff == "ROLLOUT":       return 30       # first turn of the sequence
    if eff == "FURY_CUTTER":   return 10
    if eff == "TRIPLE_KICK":   return 20       # 10+20+30 over three hits
    if eff == "LOW_KICK":
        w = WEIGHTS.get(defender["species"], {}).get("weightHg")
        if w is None: return None
        for limit, p in LOW_KICK_BRACKETS:
            if w < limit: return p
        return 120
    return mv["power"]

def nominal_power(attacker, move_const):
    """Power to print in a moveset listing. Variable-power moves store a
    placeholder of 1 in the ROM table, so resolve the ones that don't depend on
    the target; return 0 for the ones that genuinely do."""
    mv = MOVES[move_const]
    eff = mv["effect"]
    if eff == "RETURN":        return 102
    if eff == "HIDDEN_POWER":  return hidden_power(attacker["ivs"])[1]
    if eff == "MAGNITUDE":     return 71
    if eff == "PRESENT":       return 52
    if eff in ("FLAIL", "REVERSAL"): return 20
    if eff == "ERUPTION":      return 150
    if eff == "ROLLOUT":       return 30
    if eff == "FURY_CUTTER":   return 10
    if eff == "TRIPLE_KICK":   return 20
    if eff in ("LOW_KICK", "SUPER_FANG", "DRAGON_RAGE", "SONICBOOM"): return 0
    if eff in LEVEL_DAMAGE_EFFECTS: return 0
    return mv["power"]

def move_type_for(attacker, move_const):
    mv = MOVES[move_const]
    if mv["effect"] == "HIDDEN_POWER":
        return hidden_power(attacker["ivs"])[0]
    return mv["type"]

def base_damage(attacker, defender, move_const, crit=False,
                atk_badge=False, def_badge=False, spatk_badge=False, spdef_badge=False):
    """CalculateBaseDamage, src/pokemon.c — integer arithmetic preserved exactly."""
    mv = MOVES[move_const]
    power = effective_power(attacker, defender, move_const)
    if power is None: return 0
    mtype = move_type_for(attacker, move_const)
    if power == 0: return 0
    physical = mtype in PHYSICAL_TYPES

    atk_stats, def_stats = attacker["stats"], defender["stats"]
    attack, defense = atk_stats["attack"], def_stats["defense"]
    spatk, spdef = atk_stats["spAttack"], def_stats["spDefense"]

    # FRLG-only: badges boost the PLAYER's stats (ShouldGetStatBadgeBoost)
    if atk_badge:   attack  = (110 * attack) // 100
    if def_badge:   defense = (110 * defense) // 100
    if spatk_badge: spatk   = (110 * spatk) // 100
    if spdef_badge: spdef   = (110 * spdef) // 100

    # --- modifiers, in the same order as CalculateBaseDamage (integer
    # truncation makes the order observable, so it is preserved exactly)
    ab = attacker.get("ability", "NONE")
    dab = defender.get("ability", "NONE")
    item = attacker.get("item")

    if ab in ("HUGE_POWER", "PURE_POWER"):
        attack *= 2
    # (badge boosts were applied above, matching their position in the C)
    if item == "ITEM_CHOICE_BAND":
        attack = (150 * attack) // 100
    if item == "ITEM_LIGHT_BALL" and attacker["species"] == "SPECIES_PIKACHU":
        spatk *= 2
    if item == "ITEM_THICK_CLUB" and attacker["species"] in ("SPECIES_CUBONE", "SPECIES_MAROWAK"):
        attack *= 2
    if dab == "THICK_FAT" and mtype in ("FIRE", "ICE"):
        spatk //= 2
    if ab == "HUSTLE":
        attack = (150 * attack) // 100
    if ab == "GUTS" and attacker.get("status"):
        attack = (150 * attack) // 100
    if dab == "MARVEL_SCALE" and defender.get("status"):
        defense = (150 * defense) // 100

    # pinch abilities boost POWER, not the attacking stat
    if attacker.get("pinch") and (
        (mtype == "GRASS" and ab == "OVERGROW") or (mtype == "FIRE" and ab == "BLAZE")
        or (mtype == "WATER" and ab == "TORRENT") or (mtype == "BUG" and ab == "SWARM")):
        power = (150 * power) // 100

    # Self-Destruct / Explosion halve the target's Defense
    if mv["effect"] == "EXPLOSION":
        defense //= 2

    lvl_term = (2 * attacker["level"] // 5 + 2)
    if physical:
        d = attack
        d = d * power
        d = d * lvl_term
        d = d // max(1, defense)
        d = d // 50
        if attacker.get("burned") and ab != "GUTS": d //= 2
        if d == 0: d = 1
    else:
        d = spatk
        d = d * power
        d = d * lvl_term
        d = d // max(1, spdef)
        d = d // 50
    return d + 2

def damage_rolls(attacker, defender, move_const, crit=False, badges=None):
    """Every possible damage value for one connecting hit (the 16 random rolls),
    returned as a list of 16 integers."""
    mv = MOVES[move_const]
    eff = mv["effect"]
    if eff in FIXED_DAMAGE_EFFECTS: return [FIXED_DAMAGE_EFFECTS[eff]] * 16
    if eff in LEVEL_DAMAGE_EFFECTS: return [attacker["level"]] * 16
    if eff == "SUPER_FANG":
        return [max(1, defender["stats"]["hp"] // 2)] * 16
    if eff in UNUSABLE_EFFECTS: return [0] * 16
    if mv["power"] == 0: return [0] * 16

    badges = badges or {}
    d = base_damage(attacker, defender, move_const, crit,
                    badges.get("atk", False), badges.get("def", False),
                    badges.get("spatk", False), badges.get("spdef", False))
    if d == 0: return [0] * 16

    mtype = move_type_for(attacker, move_const)
    d = d * (2 if crit else 1)
    if mtype in attacker["types"]:
        d = d * 15 // 10
    for dt in dict.fromkeys(defender["types"]):
        if dt == "MYSTERY": continue
        m = TYPECHART.get(mtype, {}).get(dt, 1.0)
        if m == 0: return [0] * 16
        # ModulateDmgByType clamps to 1; a true immunity is the only zero
        d = max(1, int(d * (m * 10)) // 10)

    # ApplyRandomDmgMultiplier: a roll that truncates to 0 still deals 1
    return [max(1, d * (100 - r) // 100) for r in range(16)]

MULTI_HIT_EFFECTS = {"MULTI_HIT": 3.0, "DOUBLE_HIT": 2.0,
                     "TWINEEDLE": 2.0, "TRIPLE_KICK": 3.0}

def move_profile(attacker, defender, move_const, badges=None):
    """Full statistical profile of using one move against one target."""
    mv = MOVES[move_const]
    special = set(FIXED_DAMAGE_EFFECTS) | LEVEL_DAMAGE_EFFECTS | {"SUPER_FANG"}
    if mv["effect"] in UNUSABLE_EFFECTS: return None
    rp = effective_power(attacker, defender, move_const)
    if (rp is None or rp == 0) and mv["effect"] not in special:
        return None
    mtype = move_type_for(attacker, move_const)
    eff_mult = type_mult(mtype, defender["types"])
    if eff_mult == 0 and mv["effect"] not in special:
        return {"move": move_const, "name": mv["name"], "immune": True,
                "avg": 0.0, "avgNoCrit": 0.0, "avgPerTurn": 0.0,
                "min": 0, "max": 0, "critMin": 0, "critMax": 0,
                "effectiveness": 0.0, "turnsPerUse": 1.0,
                "accuracy": mv["accuracy"] or 100, "dist": {0: 1.0}, "type": mtype,
                "category": mv["category"], "power": rp or 0, "pp": mv["pp"]}

    normal = damage_rolls(attacker, defender, move_const, False, badges)
    crits  = damage_rolls(attacker, defender, move_const, True, badges)
    cc = crit_chance(move_const)
    hits = MULTI_HIT_EFFECTS.get(mv["effect"], 1.0)

    dist = {}
    for v in normal: dist[int(v * hits)] = dist.get(int(v * hits), 0) + (1 - cc) / 16
    for v in crits:  dist[int(v * hits)] = dist.get(int(v * hits), 0) + cc / 16

    acc = mv["accuracy"] if mv["accuracy"] else 100
    avg = sum(k * p for k, p in dist.items())
    nc = [int(v * hits) for v in normal]
    cr = [int(v * hits) for v in crits]
    if mv["effect"] in CHARGE_EFFECTS:     tpu = 2.0
    elif mv["effect"] in RECHARGE_EFFECTS: tpu = 2.0    # corrected in the turn DP
    else:                                  tpu = 1.0
    return {
        "move": move_const, "name": mv["name"], "type": mtype,
        "turnsPerUse": tpu,
        "category": ("PHYSICAL" if mtype in PHYSICAL_TYPES else "SPECIAL"),
        "power": rp if rp is not None else mv["power"], "pp": mv["pp"],
        "accuracy": acc, "effectiveness": eff_mult, "immune": False,
        # `min`/`max` follow damage-calc convention: the non-critical range.
        # Critical hits are reported separately rather than widening the band.
        "min": min(nc), "max": max(nc),
        "critMin": min(cr), "critMax": max(cr),
        "avg": avg, "avgNoCrit": sum(nc) / len(nc),
        "avgPerTurn": avg * acc / 100, "dist": dist,
    }

# ------------------------------------------------------------------ turns to KO
def obedience_cap(badges):
    """The level a TRADED Pokemon obeys up to, from `IsMonDisobedient`.

    Only badges 2/4/6/8 move it, and the eighth removes the cap entirely.
    Pokemon you caught yourself always obey -- the check is gated on
    `IsOtherTrainer(otId, otName)`, so this touches only the in-game trades.
    """
    if badges >= 8: return None          # no cap at all
    if badges >= 6: return 70
    if badges >= 4: return 50
    if badges >= 2: return 30
    return 10

def obedience_odds(level, badges):
    """P(a traded Pokemon actually uses the move you picked), exactly as the ROM
    rolls it: rnd = Random() & 255; calc = (level + cap) * rnd >> 8; it obeys
    when calc < cap. Enumerated over all 256 values rather than approximated.
    """
    cap = obedience_cap(badges)
    if cap is None or level <= cap: return 1.0
    ok = sum(1 for rnd in range(256) if ((level + cap) * rnd >> 8) < cap)
    return ok / 256.0

def turn_table(dist, accuracy, target_hp, cap=40):
    """The whole DP, not just its last cell: E[h] for every remaining HP h.

    E[h] = 1/acc + sum_d P(d) * E[h-d],  E[h<=0] = 0
    (derived from E[h] = 1 + (1-acc)E[h] + acc*sum_d P(d)E[h-d])

    Keeping the table is what lets a fight be split between two Pokemon: the
    turns one spends taking the target from H down to h is E[H] - E[h], priced
    by the same DP that prices the whole fight, so a handover can never look
    cheap for the wrong reason.
    """
    acc = max(0.01, accuracy / 100.0)
    dist = {d: p for d, p in dist.items() if d > 0}
    if not dist: return None
    tot = sum(dist.values())
    dist = {d: p / tot for d, p in dist.items()}
    E = [0.0] * (target_hp + 1)
    for h in range(1, target_hp + 1):
        s = 0.0
        for d, p in dist.items():
            s += p * (E[h - d] if h - d > 0 else 0.0)
        E[h] = 1.0 / acc + s
        if E[h] > cap: E[h] = cap
    return E

def expected_turns_to_ko(dist, accuracy, target_hp, cap=40):
    """Exact expected number of turns to reduce target_hp to 0."""
    E = turn_table(dist, accuracy, target_hp, cap)
    return float("inf") if E is None else E[target_hp]

def ko_probability(dist, accuracy, target_hp, turns):
    """P(target is KOed within `turns` uses of this move)."""
    acc = accuracy / 100.0
    states = {target_hp: 1.0}
    dead = 0.0
    for _ in range(turns):
        nxt = {}
        for hp, p in states.items():
            nxt[hp] = nxt.get(hp, 0.0) + p * (1 - acc)
            for d, pd in dist.items():
                nh = hp - d
                if nh <= 0: dead += p * acc * pd
                else: nxt[nh] = nxt.get(nh, 0.0) + p * acc * pd
        states = nxt
    return dead

def best_move(attacker, defender, badges=None, moves=None):
    """The attacker's best damaging option against this defender, by expected damage."""
    cands = moves if moves is not None else attacker["moves"]
    best, prof = None, None
    for mv in cands:
        if mv not in MOVES: continue
        p = move_profile(attacker, defender, mv, badges)
        if not p or p["immune"]: continue
        # rank by damage per TURN, so a charge move is judged on what it
        # actually delivers per turn spent, not per use
        rate = p["avgPerTurn"] / p.get("turnsPerUse", 1.0)
        if best is None or rate > best:
            best, prof = rate, p
    return prof

def matchup(player, opponent, badges=None):
    """Full two-sided analysis of one player mon vs one opposing mon."""
    pb = badges or {}
    off = best_move(player, opponent, badges={"atk": pb.get("atk", False),
                                              "spatk": pb.get("spatk", False)})
    # the opponent gets no badge boosts (player side only)
    deff = best_move(opponent, player, badges={"def": pb.get("def", False),
                                               "spdef": pb.get("spdef", False)})
    php, ohp = player["stats"]["hp"], opponent["stats"]["hp"]
    res = {
        "player": player["name"], "playerLevel": player["level"],
        "opponent": opponent["name"], "opponentLevel": opponent["level"],
        "playerHP": php, "opponentHP": ohp,
        "playerSpeed": player["stats"]["speed"], "opponentSpeed": opponent["stats"]["speed"],
        "outspeeds": player["stats"]["speed"] > opponent["stats"]["speed"],
    }
    if off:
        res["bestMove"] = {k: off[k] for k in ("move","name","type","category","power",
                                               "accuracy","effectiveness","min","max","avg","pp")}
        res["turnsToKO"] = expected_turns_to_ko(off["dist"], off["accuracy"], ohp)
        res["ohkoChance"] = ko_probability(off["dist"], off["accuracy"], ohp, 1)
        res["pctPerHit"] = off["avg"] / ohp * 100
    else:
        res["bestMove"] = None; res["turnsToKO"] = float("inf")
        res["ohkoChance"] = 0.0; res["pctPerHit"] = 0.0
    if deff:
        res["threatMove"] = {k: deff[k] for k in ("move","name","type","category","power",
                                                  "accuracy","effectiveness","min","max","avg")}
        res["turnsToBeKOed"] = expected_turns_to_ko(deff["dist"], deff["accuracy"], php)
        res["damageTakenPct"] = deff["avg"] / php * 100
        res["worstCaseTakenPct"] = deff["max"] / php * 100
    else:
        res["threatMove"] = None; res["turnsToBeKOed"] = float("inf")
        res["damageTakenPct"] = 0.0; res["worstCaseTakenPct"] = 0.0
    return res
