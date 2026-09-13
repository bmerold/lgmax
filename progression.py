#!/usr/bin/env python3
"""Progression model for Pokemon LeafGreen.

Defines the ordered stages of a normal playthrough, assigns every map to the
earliest stage at which it can be reached, gates encounter methods behind the
HMs and rods that enable them, and computes the pool of Pokemon a player could
actually have in hand at each point.

Gate facts (badge requirements, HM locations, the post-Blaine Sevii trigger)
were verified against the pokefirered decompilation.
"""
import json, os, functools as _functools
from collections import defaultdict
import engine as E

# ------------------------------------------------------------------ stages
# badges: number held ON ARRIVAL at the stage.
STAGES = [
    dict(id=0,  name="Pallet Town",              chapter="Getting started", badges=0, level=5),
    dict(id=1,  name="Route 1 & Viridian City",  chapter="Getting started", badges=0, level=7),
    dict(id=2,  name="Route 22 (early)",         chapter="Getting started", badges=0, level=9),
    dict(id=3,  name="Route 2 & Viridian Forest",chapter="Getting started", badges=0, level=10),
    dict(id=4,  name="Pewter City — Brock",      chapter="Boulder Badge",   badges=0, level=13, gym="Brock"),
    dict(id=5,  name="Route 3 & Mt. Moon",       chapter="Boulder Badge",   badges=1, level=15),
    dict(id=6,  name="Route 4 & Cerulean City",  chapter="Boulder Badge",   badges=1, level=17),
    dict(id=7,  name="Nugget Bridge & Routes 24-25", chapter="Boulder Badge", badges=1, level=19),
    dict(id=8,  name="Cerulean Gym — Misty",     chapter="Cascade Badge",   badges=1, level=21, gym="Misty"),
    dict(id=9,  name="Routes 5-6 & Vermilion City", chapter="Cascade Badge",badges=2, level=22),
    dict(id=10, name="S.S. Anne",                chapter="Cascade Badge",   badges=2, level=23),
    dict(id=11, name="Vermilion Gym — Lt. Surge",chapter="Thunder Badge",   badges=2, level=25, gym="Lt. Surge"),
    dict(id=12, name="Route 11 & Diglett's Cave",chapter="Thunder Badge",   badges=3, level=26),
    dict(id=13, name="Routes 9-10 & Rock Tunnel",chapter="Thunder Badge",   badges=3, level=28),
    dict(id=14, name="Lavender Town & Routes 7-8", chapter="Thunder Badge", badges=3, level=29),
    dict(id=15, name="Celadon City",             chapter="Thunder Badge",   badges=3, level=30),
    dict(id=16, name="Celadon Gym — Erika",      chapter="Rainbow Badge",   badges=3, level=31, gym="Erika"),
    dict(id=17, name="Rocket Hideout",           chapter="Rainbow Badge",   badges=4, level=32),
    dict(id=18, name="Pokémon Tower",            chapter="Rainbow Badge",   badges=4, level=33),
    dict(id=19, name="Routes 12-15 (Snorlax)",   chapter="Rainbow Badge",   badges=4, level=35),
    dict(id=20, name="Routes 16-18 (Cycling Road)", chapter="Rainbow Badge",badges=4, level=36),
    dict(id=21, name="Fuchsia City & Safari Zone", chapter="Rainbow Badge", badges=4, level=37),
    dict(id=22, name="Fuchsia Gym — Koga",       chapter="Soul Badge",      badges=4, level=38, gym="Koga"),
    dict(id=23, name="Saffron City & Silph Co.", chapter="Soul Badge",      badges=5, level=40),
    dict(id=24, name="Saffron Gym — Sabrina",    chapter="Marsh Badge",     badges=5, level=43, gym="Sabrina"),
    dict(id=25, name="Sea routes 19-21 & Seafoam Islands", chapter="Marsh Badge", badges=6, level=44),
    dict(id=26, name="Cinnabar Island & Pokemon Mansion",  chapter="Marsh Badge", badges=6, level=45),
    dict(id=27, name="Cinnabar Gym — Blaine",    chapter="Volcano Badge",   badges=6, level=46, gym="Blaine"),
    dict(id=28, name="Sevii Islands 1-3",        chapter="Volcano Badge",   badges=7, level=47),
    dict(id=29, name="Viridian Gym — Giovanni",  chapter="Earth Badge",     badges=7, level=48, gym="Giovanni"),
    dict(id=30, name="Route 22 (late) & Route 23", chapter="Victory Road",  badges=8, level=50),
    dict(id=31, name="Victory Road",             chapter="Victory Road",    badges=8, level=52),
    dict(id=32, name="Elite Four & Champion",    chapter="Pokémon League",  badges=8, level=57),
    dict(id=33, name="Post-game: Sevii Islands 4-7", chapter="Post-game",   badges=8, level=62),
    dict(id=34, name="Post-game: Cerulean Cave", chapter="Post-game",       badges=8, level=68),
]
STAGE_BY_ID = {s["id"]: s for s in STAGES}
MAX_STAGE = max(s["id"] for s in STAGES)

# Badge -> stage at which it is HELD (the gym stage is where you fight, so the
# badge is held from the next stage onward).
BADGE_STAGE = {1: 5, 2: 9, 3: 12, 4: 17, 5: 23, 6: 25, 7: 28, 8: 30}

# HM field-move usability: stage at which you have BOTH the HM and its badge.
# (HM location and badge requirement both verified in the decomp.)
HM_STAGE = {
    "CUT": 11,        # HM01 on the S.S. Anne; needs Cascade Badge (held from 9)
    "FLY": 20,        # HM02 Route 16 house; needs Thunder Badge
    "SURF": 23,       # HM03 Safari Zone Secret House; needs Soul Badge (held from 23)
    "STRENGTH": 21,   # HM04 from the Warden for the Gold Teeth; needs Rainbow Badge
    "FLASH": 13,      # HM05 Route 2 East Building; needs Boulder Badge
    "ROCK_SMASH": 28, # HM06 Kindle Road, One Island — post-Blaine only
    "WATERFALL": 33,  # HM07 Icefall Cave, Four Island — post-game only
}

ROD_STAGE = {"old_rod": 9, "good_rod": 21, "super_rod": 19}

# ------------------------------------------------------------------ map -> stage
MAP_STAGE = {
    "PALLET_TOWN": 0, "ROUTE1": 1, "VIRIDIAN_CITY": 1, "ROUTE22": 2,
    "ROUTE2": 3, "VIRIDIAN_FOREST": 3, "PEWTER_CITY": 4,
    "ROUTE3": 5, "MT_MOON_1F": 5, "MT_MOON_B1F": 5, "MT_MOON_B2F": 5,
    "ROUTE4": 6, "CERULEAN_CITY": 6, "ROUTE24": 7, "ROUTE25": 7,
    "ROUTE5": 9, "ROUTE6": 9, "VERMILION_CITY": 9,
    "SSANNE_EXTERIOR": 10, "SSANNE_1F_ROOM2": 10, "SSANNE_1F_ROOM5": 10,
    "SSANNE_1F_ROOM7": 10, "SSANNE_2F_CORRIDOR": 10, "SSANNE_2F_ROOM2": 10,
    "SSANNE_2F_ROOM4": 10, "SSANNE_B1F_ROOM1": 10, "SSANNE_B1F_ROOM2": 10,
    "SSANNE_B1F_ROOM3": 10, "SSANNE_B1F_ROOM4": 10, "SSANNE_DECK": 10,
    "ROUTE11": 12, "DIGLETTS_CAVE_B1F": 12,
    "ROUTE9": 13, "ROUTE10": 13, "ROCK_TUNNEL_1F": 13, "ROCK_TUNNEL_B1F": 13,
    "LAVENDER_TOWN": 14, "ROUTE8": 14, "ROUTE7": 14,
    "CELADON_CITY": 15,
    "ROCKET_HIDEOUT_B1F": 17, "ROCKET_HIDEOUT_B2F": 17,
    "ROCKET_HIDEOUT_B3F": 17, "ROCKET_HIDEOUT_B4F": 17,
    "POKEMON_TOWER_3F": 18, "POKEMON_TOWER_4F": 18, "POKEMON_TOWER_5F": 18,
    "POKEMON_TOWER_6F": 18, "POKEMON_TOWER_7F": 18,
    "ROUTE12": 19, "ROUTE13": 19, "ROUTE14": 19, "ROUTE15": 19,
    "ROUTE16": 20, "ROUTE17": 20, "ROUTE18": 20,
    "FUCHSIA_CITY": 21, "SAFARI_ZONE_CENTER": 21, "SAFARI_ZONE_EAST": 21,
    "SAFARI_ZONE_NORTH": 21, "SAFARI_ZONE_WEST": 21,
    "SAFFRON_CITY": 23,
    "ROUTE19": 25, "ROUTE20": 25, "ROUTE21_NORTH": 25, "ROUTE21_SOUTH": 25,
    "SEAFOAM_ISLANDS_1F": 25, "SEAFOAM_ISLANDS_B1F": 25, "SEAFOAM_ISLANDS_B2F": 25,
    "SEAFOAM_ISLANDS_B3F": 25, "SEAFOAM_ISLANDS_B4F": 25,
    "POWER_PLANT": 25,
    "CINNABAR_ISLAND": 26, "POKEMON_MANSION_1F": 26, "POKEMON_MANSION_2F": 26,
    "POKEMON_MANSION_3F": 26, "POKEMON_MANSION_B1F": 26,
    # Sevii 1-3 open only after Blaine
    "ONE_ISLAND": 28, "ONE_ISLAND_KINDLE_ROAD": 28, "ONE_ISLAND_TREASURE_BEACH": 28,
    "MT_EMBER_EXTERIOR": 28, "MT_EMBER_SUMMIT_PATH_1F": 28,
    "MT_EMBER_SUMMIT_PATH_2F": 28, "MT_EMBER_SUMMIT_PATH_3F": 28,
    "TWO_ISLAND_CAPE_BRINK": 28, "THREE_ISLAND": 28, "THREE_ISLAND_PORT": 28,
    "THREE_ISLAND_BOND_BRIDGE": 28, "THREE_ISLAND_BERRY_FOREST": 28,
    "ROUTE23": 30,
    "VICTORY_ROAD_1F": 31, "VICTORY_ROAD_2F": 31, "VICTORY_ROAD_3F": 31,
    "INDIGO_PLATEAU_EXTERIOR": 32,
    # Ruby Path is post-game (needs National Dex quest)
    "MT_EMBER_RUBY_PATH_1F": 33, "MT_EMBER_RUBY_PATH_B1F": 33,
    "MT_EMBER_RUBY_PATH_B1F_STAIRS": 33, "MT_EMBER_RUBY_PATH_B2F": 33,
    "MT_EMBER_RUBY_PATH_B2F_STAIRS": 33, "MT_EMBER_RUBY_PATH_B3F": 33,
    "CERULEAN_CAVE_1F": 34, "CERULEAN_CAVE_2F": 34, "CERULEAN_CAVE_B1F": 34,
}

def map_stage(map_const):
    """Earliest stage at which a map is reachable."""
    m = map_const.replace("MAP_", "")
    if m in MAP_STAGE: return MAP_STAGE[m]
    # Sevii 4-7 and everything else post-game
    for pref in ("FOUR_ISLAND", "FIVE_ISLAND", "SIX_ISLAND", "SEVEN_ISLAND"):
        if m.startswith(pref): return 33
    if m.startswith("ONE_ISLAND") or m.startswith("TWO_ISLAND") or m.startswith("THREE_ISLAND") \
       or m.startswith("MT_EMBER"):
        return 28
    return 33   # unknown -> treat as post-game rather than claiming early access

METHOD_GATE = {
    "land_mons": 0,
    "water_mons": HM_STAGE["SURF"],
    "rock_smash_mons": HM_STAGE["ROCK_SMASH"],
}

# ------------------------------------------------------------------ non-wild sources
STATIC_SOURCES = [
    # (species, stage, kind, note)
    ("SPECIES_BULBASAUR",  0, "starter",  "Starter choice, Oak's Lab"),
    ("SPECIES_CHARMANDER", 0, "starter",  "Starter choice, Oak's Lab"),
    ("SPECIES_SQUIRTLE",   0, "starter",  "Starter choice, Oak's Lab"),
    ("SPECIES_MAGIKARP",   6, "purchase", "Route 4 salesman, 500"),
    ("SPECIES_ABRA",      15, "prize",    "Celadon Game Corner prize"),
    ("SPECIES_CLEFAIRY",  15, "prize",    "Celadon Game Corner prize"),
    ("SPECIES_DRATINI",   15, "prize",    "Celadon Game Corner prize"),
    ("SPECIES_PORYGON",   15, "prize",    "Celadon Game Corner prize"),
    ("SPECIES_PINSIR",    15, "prize",    "Celadon Game Corner prize (LeafGreen)"),
    ("SPECIES_EEVEE",     15, "gift",     "Celadon Condominiums roof, L25"),
    ("SPECIES_HYPNO",     18, "static",   "Pokemon Tower, L30"),
    ("SPECIES_MAROWAK",   18, "static",   "Ghost Marowak, Pokemon Tower (uncatchable)"),
    ("SPECIES_SNORLAX",   19, "static",   "Route 12 / Route 16, L30"),
    ("SPECIES_HITMONLEE", 23, "gift",     "Saffron Fighting Dojo (choose one)"),
    ("SPECIES_HITMONCHAN",23, "gift",     "Saffron Fighting Dojo (choose one)"),
    ("SPECIES_LAPRAS",    23, "gift",     "Silph Co. 7F, L25"),
    ("SPECIES_ELECTRODE", 25, "static",   "Power Plant, L34"),
    ("SPECIES_ZAPDOS",    25, "static",   "Power Plant, L50"),
    ("SPECIES_ARTICUNO",  25, "static",   "Seafoam Islands, L50"),
    ("SPECIES_OMANYTE",   26, "fossil",   "Helix Fossil (Mt. Moon) revived at Cinnabar Lab"),
    ("SPECIES_KABUTO",    26, "fossil",   "Dome Fossil (Mt. Moon) revived at Cinnabar Lab"),
    ("SPECIES_AERODACTYL",26, "fossil",   "Old Amber (Pewter Museum) revived at Cinnabar Lab"),
    ("SPECIES_MOLTRES",   28, "static",   "Mt. Ember, L50"),
    ("SPECIES_MEWTWO",    34, "static",   "Cerulean Cave, L70"),
    ("SPECIES_TOGEPI",    33, "gift",     "Water Labyrinth egg, Five Island"),
]

# In-game trades (src/data/ingame_trades.h). LeafGreen variants where they differ.
INGAME_TRADES = [
    ("SPECIES_MR_MIME",   "SPECIES_ABRA",       14, "Route 2 house"),
    ("SPECIES_JYNX",      "SPECIES_POLIWHIRL",  14, "Cerulean City house"),
    ("SPECIES_NIDORAN_M", "SPECIES_NIDORAN_F",  20, "Route 11 gate (LeafGreen)"),
    ("SPECIES_FARFETCHD", "SPECIES_SPEAROW",    19, "Route 18 gate"),
    ("SPECIES_NIDORINO",  "SPECIES_NIDORINA",   23, "Route 5 gate"),
    ("SPECIES_LICKITUNG", "SPECIES_SLOWBRO",    23, "Route 18 gate (LeafGreen)"),
    ("SPECIES_ELECTRODE", "SPECIES_RAICHU",     28, "Cinnabar Island house"),
    ("SPECIES_TANGELA",   "SPECIES_VENONAT",    28, "Cinnabar Island house"),
    ("SPECIES_SEEL",      "SPECIES_PONYTA",     28, "Cinnabar Island house"),
]

STONE_STAGE = {
    "ITEM_MOON_STONE":    5,   # Mt. Moon item ball
    "ITEM_FIRE_STONE":   15,   # Celadon Dept. Store 4F
    "ITEM_THUNDER_STONE":15,
    "ITEM_WATER_STONE":  15,
    "ITEM_LEAF_STONE":   15,
    "ITEM_SUN_STONE":    33,
}

# Trade evolutions that also need a held item. The item is what actually gates
# them, and in LeafGreen every one of the four is an item ball in the POST-GAME
# Sevii Islands -- nothing you can catch in Kanto holds any of them. Scanned
# from the ROM rather than hand-written, so it cannot drift.
@_functools.lru_cache(maxsize=1)
def evo_item_stage():
    """evolution-item -> earliest stage you can hold one."""
    import re as _re
    path = os.path.join(E.REPO, "data/scripts/item_ball_scripts.inc")
    if not os.path.exists(path): return {}
    flat = {k.replace("_", ""): v for k, v in MAP_STAGE.items()}
    out, label = {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        m = _re.match(r"^(\w+?)_EventScript_Item\w+::", line)
        if m: label = m.group(1); continue
        f = _re.search(r"finditem\s+(ITEM_[A-Z0-9_]+)", line)
        if f and label:
            item = f.group(1)
            st = flat.get(label.upper().replace("_", ""))
            if st is None: st = map_stage("MAP_" + _re.sub(r"(?<!^)(?=[A-Z])", "_", label).upper())
            if st is not None and (item not in out or st < out[item]):
                out[item] = st
    return out

# ------------------------------------------------------------------ availability
def wild_availability():
    """species -> earliest stage obtainable in the wild in LeafGreen, with source."""
    best = {}
    for enc in E.WILD["encounters"]:
        if enc["version"] == "FireRed":
            continue
        ms = map_stage(enc["map"])
        pretty = _pretty_map(enc["map"])
        for method, tbl in enc["tables"].items():
            if method == "fishing_mons":
                groups = E.WILD["fishingGroups"]
                for rod, idxs in groups.items():
                    gate = max(ms, ROD_STAGE.get(rod, 0))
                    for slot in tbl["slots"]:
                        if slot["slot"] not in idxs: continue
                        _record(best, slot["species"], gate, f"{rod.replace('_',' ')} at {pretty}",
                                "wild", slot["minLevel"], slot["maxLevel"])
                continue
            gate = max(ms, METHOD_GATE.get(method, 0))
            label = {"land_mons": "Grass/cave", "water_mons": "Surfing",
                     "rock_smash_mons": "Rock Smash"}.get(method, method)
            for slot in tbl["slots"]:
                _record(best, slot["species"], gate, f"{label} at {pretty}",
                        "wild", slot["minLevel"], slot["maxLevel"])
    return best

def _pretty_map(map_const):
    import re as _re
    s = map_const.replace("MAP_", "").replace("_", " ").title()
    s = _re.sub(r"([A-Za-z])(\d)", r"\1 \2", s)
    return s.replace("Ssanne", "S.S. Anne").replace("Mt ", "Mt. ")

def _record(best, species, stage, source, kind, minlv=None, maxlv=None):
    cur = best.get(species)
    if cur is None or stage < cur["stage"]:
        best[species] = {"stage": stage, "source": source, "kind": kind,
                         "minLevel": minlv, "maxLevel": maxlv}

def base_availability():
    avail = wild_availability()
    for sp, stage, kind, note in STATIC_SOURCES:
        _record(avail, sp, stage, note, kind)
    for sp, want, stage, where in INGAME_TRADES:
        _record(avail, sp, stage, f"In-game trade for {E.SPECIES[want]['name']} ({where})", "ingame_trade")
    return avail

def full_availability():
    """Propagate evolutions forward. Returns species -> record with the earliest
    stage the player could actually have that species in hand."""
    avail = base_availability()
    changed = True
    while changed:
        changed = False
        for src, evos in E.EVOS.items():
            if src not in avail: continue
            src_rec = avail[src]
            for ev in evos:
                tgt = ev["to"]
                method, param = ev["method"], ev["param"]
                stage = None
                note = None
                requires_trade = src_rec.get("requiresTrade", False)
                if method in ("LEVEL", "LEVEL_ATK_LT_DEF", "LEVEL_ATK_GT_DEF",
                              "LEVEL_ATK_EQ_DEF", "LEVEL_SILCOON", "LEVEL_CASCOON",
                              "LEVEL_NINJASK", "LEVEL_SHEDINJA"):
                    need = param if isinstance(param, int) else 0
                    stage = _stage_reaching_level(max(need, src_rec["stage"] and 0), need,
                                                  src_rec["stage"])
                    note = f"Evolve {E.SPECIES[src]['name']} at L{need}"
                elif method == "ITEM":
                    st = STONE_STAGE.get(param)
                    if st is None: continue
                    stage = max(src_rec["stage"], st)
                    note = f"Evolve {E.SPECIES[src]['name']} with {E.ITEMS.get(param,{}).get('name',param)}"
                elif method == "FRIENDSHIP":
                    stage = min(MAX_STAGE, src_rec["stage"] + 2)
                    note = f"Evolve {E.SPECIES[src]['name']} by friendship"
                elif method == "TRADE":
                    stage = src_rec["stage"]
                    note = f"Evolve {E.SPECIES[src]['name']} by trading"
                    requires_trade = True
                elif method == "TRADE_ITEM":
                    # The HELD ITEM is the real gate, not the trade. Porygon is a
                    # Game Corner prize from section 15, but the Up-Grade that
                    # turns it into Porygon2 sits in the Five Island Rocket
                    # Warehouse -- post-game. Treating this like a plain trade
                    # put Porygon2 in parties twenty sections early.
                    st = evo_item_stage().get(param)
                    if st is None: continue      # item not obtainable in LeafGreen
                    stage = max(src_rec["stage"], st)
                    item = E.ITEMS.get(param, {}).get("name", param)
                    note = (f"Evolve {E.SPECIES[src]['name']} by trading "
                            f"while holding the {item}")
                    requires_trade = True
                else:
                    continue
                if stage is None: continue
                cur = avail.get(tgt)
                if cur is None or stage < cur["stage"]:
                    avail[tgt] = {"stage": stage, "source": note, "kind": "evolution",
                                  "from": src, "requiresTrade": requires_trade,
                                  "evoMethod": method, "evoParam": param}
                    changed = True
    for rec in avail.values():
        rec.setdefault("requiresTrade", False)
    return avail

def _stage_reaching_level(_unused, need_level, from_stage):
    """Earliest stage at which a Pokemon obtained at `from_stage` could plausibly
    have reached `need_level`, using the stage level curve (no grinding)."""
    for s in STAGES:
        if s["id"] < from_stage: continue
        if s["level"] >= need_level:
            return s["id"]
    return None

# ------------------------------------------------------------------ trainer -> stage
LOCATION_STAGE = {
    "PalletTown_ProfessorOaksLab": 0, "Route22": 2, "ViridianForest": 3,
    "PewterCity_Gym": 4, "Route3": 5, "MtMoon_1F": 5, "MtMoon_B2F": 5,
    "Route4": 6, "CeruleanCity": 6, "Route24": 7, "Route25": 7,
    "CeruleanCity_Gym": 8, "Route6": 9,
    "SSAnne_1F_Room2": 10, "SSAnne_1F_Room5": 10, "SSAnne_1F_Room7": 10,
    "SSAnne_2F_Corridor": 10, "SSAnne_2F_Room2": 10, "SSAnne_2F_Room4": 10,
    "SSAnne_B1F_Room1": 10, "SSAnne_B1F_Room2": 10, "SSAnne_B1F_Room3": 10,
    "SSAnne_B1F_Room4": 10, "SSAnne_Deck": 10,
    "VermilionCity_Gym": 11, "Route11": 12,
    "Route9": 13, "Route10": 13, "RockTunnel_1F": 13, "RockTunnel_B1F": 13,
    "Route8": 14, "CeladonCity_GameCorner": 15,
    "CeladonCity_Gym": 16,
    "RocketHideout_B1F": 17, "RocketHideout_B2F": 17, "RocketHideout_B3F": 17,
    "RocketHideout_B4F": 17,
    "PokemonTower_2F": 18, "PokemonTower_3F": 18, "PokemonTower_4F": 18,
    "PokemonTower_5F": 18, "PokemonTower_6F": 18, "PokemonTower_7F": 18,
    "Route12": 19, "Route13": 19, "Route14": 19, "Route15": 19,
    "Route16": 20, "Route17": 20, "Route18": 20,
    "FuchsiaCity_Gym": 22,
    "SaffronCity_Dojo": 23,
    "SilphCo_2F": 23, "SilphCo_3F": 23, "SilphCo_4F": 23, "SilphCo_5F": 23,
    "SilphCo_6F": 23, "SilphCo_7F": 23, "SilphCo_8F": 23, "SilphCo_9F": 23,
    "SilphCo_10F": 23, "SilphCo_11F": 23,
    "SaffronCity_Gym": 24,
    "Route19": 25, "Route20": 25, "Route21_North": 25, "Route21_South": 25,
    "PokemonMansion_1F": 26, "PokemonMansion_2F": 26, "PokemonMansion_3F": 26,
    "PokemonMansion_B1F": 26,
    "CinnabarIsland_Gym": 27,
    "OneIsland_KindleRoad": 28, "OneIsland_TreasureBeach": 28,
    "MtEmber_Exterior": 28, "ThreeIsland": 28, "ThreeIsland_BondBridge": 28,
    "ViridianCity_Gym": 29,
    "VictoryRoad_1F": 31, "VictoryRoad_2F": 31, "VictoryRoad_3F": 31,
    "PokemonLeague_LoreleisRoom": 32, "PokemonLeague_BrunosRoom": 32,
    "PokemonLeague_AgathasRoom": 32, "PokemonLeague_LancesRoom": 32,
    "PokemonLeague_ChampionsRoom": 32,
}

def location_stage(loc):
    if loc in LOCATION_STAGE: return LOCATION_STAGE[loc]
    for pref in ("FourIsland", "FiveIsland", "SixIsland", "SevenIsland"):
        if loc.startswith(pref): return 33
    if loc.startswith(("OneIsland", "TwoIsland", "ThreeIsland", "MtEmber")): return 28
    return None

# trainers whose location can't be inferred from a map (RS leftovers etc.)
SKIP_CLASSES = {"Rs Aroma Lady", "Rs Ruin Maniac", "Rs Cooltrainer M", "Rs Cooltrainer F"}

# Rival battles are keyed by story beat, not by map: two of them share Route 22
# (the optional early one and the pre-Victory-Road one), so the map alone is
# ambiguous and these take precedence over the location lookup.
RIVAL_ORDER = {
    "RIVAL_OAKS_LAB": 0, "RIVAL_ROUTE22_EARLY": 2, "RIVAL_CERULEAN": 6,
    "RIVAL_SS_ANNE": 10, "RIVAL_POKEMON_TOWER": 18, "RIVAL_SILPH": 23,
    "RIVAL_ROUTE22_LATE": 30, "CHAMPION": 32,
}

def trainer_stage(const, t):
    for key, st in RIVAL_ORDER.items():
        if key in const: return st
    locs = t.get("locations") or []
    for l in locs:
        s = location_stage(l)
        if s is not None: return s
    return None

if __name__ == "__main__":
    av = full_availability()
    print(f"species obtainable somewhere: {len(av)}")
    by_stage = defaultdict(list)
    for sp, rec in av.items():
        by_stage[rec["stage"]].append(sp)
    running = 0
    for s in STAGES:
        running += len(by_stage.get(s["id"], []))
        newly = sorted(E.SPECIES[x]["name"] for x in by_stage.get(s["id"], []))
        print(f"  [{s['id']:2}] L{s['level']:<3} {s['name']:<40} +{len(newly):<3} total {running}")
        if newly and s["id"] <= 12:
            print(f"        {', '.join(newly)}")
    missing = [t for t, v in E.TRAINERS.items() if v["party"] and trainer_stage(t, v) is None
               and v["class"] not in SKIP_CLASSES]
    print(f"\ntrainers without a stage: {len(missing)}")
    for m in missing[:25]: print("   ", m, E.TRAINERS[m]["class"], E.TRAINERS[m]["locations"])
