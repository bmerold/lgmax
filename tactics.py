"""League tactics: X items, bought healing, and setup moves -- the player's side of
the bag, used ONLY in the Elite Four / Champion fights.

Every fact is read from the decomp:
  * What you can buy: each Poke Mart's stock is a `pokemart <label>` list in its
    map's scripts.inc (data/maps/*/scripts.inc). A mart counts once the route has
    reached its town (the map name's first segment, e.g. CeladonCity for the
    Dept. Store's 5F X-item counter) by the League stage.
  * X items: src/data/pokemon/item_effects.h. Byte 0 low nibble is X Attack's
    stage gain, byte 1 high/low are X Defend / X Speed, byte 2 low is X Special
    (ITEM0_X_ATTACK etc., include/constants/item_effects.h); PokemonUseItemEffects
    (src/pokemon.c) adds exactly that many stages, capped at +6. X Accuracy,
    Dire Hit and Guard Spec. affect accuracy / crits / Mist, which the 1v1 model
    does not price, so they are left out.
  * Healing: the same table's ITEM4_HEAL_HP amounts (trainer_items.EFFECTS).
  * Prices: items.json (src/data/items.json).
  * Setup moves: the move's battle effect (moves.json `effect`, the EFFECT_*
    constant from src/data/battle_moves.h); EFFECT_ATTACK_UP_2 is +2 Attack, etc.

An item or a setup move costs the user its turn (items act before moves), so the
foe gets a free hit; stat stages last until the user switches out or faints.
"""
import os, re, json
import engine as E
import trainer_items as TI

LEAGUE_STAGE = 32
# Bag discipline per fight, in lieu of a money budget (the run's TM shopping list
# already outruns its prize income -- see economy.py): at most this many boost
# actions per Pokemon per stint in, and this many heals per foe.
MAX_BOOSTS = 3
MAX_HEALS = 2

# EFFECT_* -> stat stages it gives the user (src/data/battle_moves.h effects;
# the _2 effects are the sharp +2 versions in data/battle_scripts_1.s).
SETUP_EFFECTS = {
    "ATTACK_UP":           {"attack": 1},
    "ATTACK_UP_2":         {"attack": 2},
    "DEFENSE_UP":          {"defense": 1},
    "DEFENSE_UP_2":        {"defense": 2},
    "SPECIAL_ATTACK_UP":   {"spAttack": 1},     # Growth in Gen 3
    "SPECIAL_ATTACK_UP_2": {"spAttack": 2},     # Tail Glow
    "SPECIAL_DEFENSE_UP_2": {"spDefense": 2},   # Amnesia
    "SPEED_UP_2":          {"speed": 2},        # Agility
    "BULK_UP":             {"attack": 1, "defense": 1},
    "CALM_MIND":           {"spAttack": 1, "spDefense": 1},
    "DRAGON_DANCE":        {"attack": 1, "speed": 1},
}

def is_setup(move):
    return E.MOVES.get(move, {}).get("effect") in SETUP_EFFECTS

def _x_items():
    """item const -> stat stages, from the item effect bytes."""
    src = open(os.path.join(E.REPO, "src/data/pokemon/item_effects.h")).read()
    out = {}
    for item, eff in re.findall(r"\[(ITEM_\w+)\s*-\s*ITEM_POTION\]\s*=\s*(sItemEffect_\w+)", src):
        m = re.search(r"static const u8 %s\[\d*\]\s*=\s*\{(.*?)\};" % eff, src, re.S)
        if not m: continue
        b = {int(i): v for i, v in re.findall(r"\[(\d)\]\s*=\s*([^,\n/]+)", m.group(1))}
        def val(i):
            v = b.get(i, "0").strip()
            mm = re.match(r"(\d+)\s*<<\s*4", v)
            return (int(mm.group(1)) << 4) if mm else (int(v) if v.isdigit() else 0)
        boosts = {}
        if val(0) & 0x0F: boosts["attack"] = val(0) & 0x0F
        if val(1) & 0xF0: boosts["defense"] = (val(1) & 0xF0) >> 4
        if val(1) & 0x0F: boosts["speed"] = val(1) & 0x0F
        if val(2) & 0x0F: boosts["spAttack"] = val(2) & 0x0F
        if boosts:
            out[item] = boosts
    return out

# Story-staged shops: Two Island's clerk sells `_ShopInitial` until flags (the
# Lostelle rescue, beating the Champion, the Hoenn link -- data/maps/TwoIsland/
# scripts.inc) switch it to `_ShopExpandedN`. Only the initial stock is counted.
STAGED_EXPANSION = re.compile(r"_ShopExpanded\d+$")

def _marts():
    """map name -> item consts it sells (every `pokemart` list in the map, minus
    story-gated expansions)."""
    base = os.path.join(E.REPO, "data/maps")
    out = {}
    for mp in sorted(os.listdir(base)):
        path = os.path.join(base, mp, "scripts.inc")
        if not os.path.exists(path): continue
        txt = open(path).read()
        for label in re.findall(r"pokemart\s+(\w+)", txt):
            if STAGED_EXPANSION.search(label): continue
            m = re.search(r"^%s::?\s*\n((?:\s*\.2byte\s+\w+\s*\n)+)" % re.escape(label), txt, re.M)
            if m:
                items = [i for i in re.findall(r"\.2byte\s+(\w+)", m.group(1)) if i != "ITEM_NONE"]
                out.setdefault(mp, []).extend(items)
    return out

X_ITEMS = _x_items()
MARTS = _marts()

def _town(mp):
    return mp.split("_")[0]

def buyable_by(route, stage=LEAGUE_STAGE):
    """Item consts sold at a mart in a town the route reaches by `stage`."""
    towns = {_town(s.get("map") or "") for st in route.get("stages", [])
             if st["stage"] <= stage for s in st.get("steps", [])}
    return {i for mp, items in MARTS.items() if _town(mp) in towns for i in items}

def price(item):
    return E.ITEMS.get(item, {}).get("price", 0)

def name(item):
    return E.ITEMS.get(item, {}).get("name", item)

def stock(route):
    """What the League bag can draw on, from the marts the route reaches: X items
    (stat gains), heals cheapest first (so a plan reaches for the Super Potion
    before the Full Restore), and the Revive. Fixed per version -- compute once."""
    have = buyable_by(route)
    heals = []
    for i in have:
        eff = TI.EFFECTS.get(name(i), {})
        if eff.get("kind") in ("full_restore", "heal_hp") and "REVIVE" not in i:
            heals.append((i, eff["amount"]))
    return {"x_items": {i: b for i, b in X_ITEMS.items() if i in have},
            "heals": sorted(heals, key=lambda h: price(h[0])),
            "revive": "ITEM_REVIVE" if "ITEM_REVIVE" in have else None}

class Kit:
    """One run's League bag: the shared stock plus a fresh ledger of what was spent."""
    def __init__(self, stock):
        self.x_items = stock["x_items"]
        self.heals = stock["heals"]
        self.revive = stock["revive"]
        self.spent = 0
        self.used = {}

    def charge(self, item, n=1.0):
        if n <= 0: return
        self.spent += price(item) * n
        self.used[item] = self.used.get(item, 0.0) + n

    def heal_amount(self, item, maxhp):
        amt = dict(self.heals).get(item)
        return maxhp if amt == TI.FULL else amt

    def ledger(self):
        return {"spent": round(self.spent),
                "items": {name(i): round(n, 2) for i, n in sorted(self.used.items())}}
