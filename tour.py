#!/usr/bin/env python3
"""One path through the whole game: every item ball, every hidden item, every
trainer, in the order that costs the fewest steps.

The game is a gated travelling-salesman problem. The stages of the
progression model gate it: a node (something to pick up or fight) becomes
collectable once its map is open AND the world graph can physically reach it
-- an item behind a cut tree on a stage-2 route is not reachable until Cut is
live, and the scheduler simply holds it until the first stage where a path
exists. Within each stage the tour starts wherever the previous stage ended,
visits everything newly collectable, and is pinned to end at the stage's
final story battle (the gym leader, the boss), so the route always advances
the game.

Distances are exact: Dijkstra over world.py's tile graph, ledge hops and
spin-tile slides included. The ordering is nearest-neighbour polished by
2-opt and Or-opt -- near-optimal in practice and deterministic.

Output: data/route.json -- per stage, the ordered actions with their real
tile paths (corner points only), plus step totals.
"""
import json, os, re, collections
import world as WD
import route_order as R
import progression as P
import sections as S
import build_graph as G
import engine as E
import walking as WK

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
REPO = R.REPO

ITEM_GFX = "OBJ_EVENT_GFX_ITEM_BALL"
FLY_COST = 30      # menu, animation, landing -- a nominal fare, not a walk
# Teleport returns you to the last Pokémon Center you healed at, so within a
# stage it drops you at that town's Center for a nominal cost -- the same
# Center-return shortcut Fly gives, but usable much earlier (a party Teleport
# user, i.e. the Abra line, is catchable at Route 24). It's carried like an HM:
# the party holds a Teleport user for the legs where it saves the walk back
# (sections.py adds an Abra-line carrier for a stage that uses it).
TELEPORT_COST = 25
TELEPORT_STAGE = 7   # Abra (the LeafGreen Teleport user) is catchable at Route 24

# The overworld's fixed Pokémon, found by their own sprites.
STATIC_MON_GFX = {
    "OBJ_EVENT_GFX_SNORLAX": "Wake Snorlax (Poké Flute)",
    "OBJ_EVENT_GFX_ARTICUNO": "Catch Articuno",
    "OBJ_EVENT_GFX_ZAPDOS": "Catch Zapdos",
    "OBJ_EVENT_GFX_MOLTRES": "Catch Moltres",
    "OBJ_EVENT_GFX_MEWTWO": "Catch Mewtwo",
}

# One-time events with no sprite of their own to find: gift Pokémon, key
# hand-outs, the choices. Anchored inside the door of the map they happen in
# (or at given coords); the stage is when the story hands them out.
EVENTS = [
    ("Choose your starter", "PalletTown_ProfessorOaksLab", 0),
    ("Oak's Parcel from the Mart clerk", "ViridianCity_Mart", 1),
    ("Deliver the Parcel — Pokédex from Oak", "PalletTown_ProfessorOaksLab", 1),
    ("Town Map from Daisy", "PalletTown_RivalsHouse", 1, "Daisy"),
    ("Old Amber from the scientist", "PewterCity_Museum_1F", 4, "OldAmberScientist"),
    ("Helix or Dome Fossil (pick one)", "MtMoon_B2F", 5, "Fossil"),
    # the salesman shares the Route 4 Center the walk heals at BEFORE Mt.
    # Moon -- buy on the way in, not on some later loop back
    ("Buy the Magikarp (500)", "Route4_PokemonCenter_1F", 5),
    ("S.S. Ticket from Bill", "Route25_SeaCottage", 7, "Bill"),
    ("Bike Voucher from the Fan Club", "VermilionCity_PokemonFanClub", 9),
    # the shop only hands the Bicycle over FOR the Vermilion voucher, so the
    # pickup waits for the next natural pass through Cerulean -- stage 13
    # leaves for Routes 9-10 from Cerulean's east side
    ("Bicycle from the Bike Shop", "CeruleanCity_BikeShop", 13),
    ("Powder Jar from the Berry lady", "CeruleanCity_House5", 6, "BerryPowderMan"),
    ("Old Rod", "VermilionCity_House1", 9),
    ("HM01 Cut from the Captain", "SSAnne_CaptainsOffice", 10),
    ("Trash-can switches (open Surge's door)", "VermilionCity_Gym", 11),
    ("Itemfinder from Oak's aide", "Route11_EastEntrance_2F", 12),
    ("HM05 Flash from Oak's aide (10 owned)", "Route2_EastBuilding", 12),
    ("Tea from the old lady (opens Saffron's gates)", "CeladonCity_Condominiums_1F", 15, "TeaWoman"),
    ("Everstone from the collector", "Route10_PokemonCenter_1F", 13, "Gentleman"),
    ("Coin Case from the gambler", "CeladonCity_Restaurant", 15),
    ("Game Corner prize (one pick)", "CeladonCity_GameCorner_PrizeRoom", 15),
    ("Eevee on the Condominiums roof", "CeladonCity_Condominiums_RoofRoom", 15),
    # the Ghost Marowak on Pokémon Tower 6F blocks the climb to Mr. Fuji: a
    # scripted setwildbattle (SPECIES_MAROWAK, 30) you must defeat, and only the
    # Silph Scope (from Giovanni in the Rocket Hideout, stage 17) lets you fight
    # it. It's a coord_event, so it carries no TRAINER_ const and neither the
    # trainer nor the wild-encounter harvest sees it -- pin it here by hand.
    ("Ghost Marowak battle — needs the Silph Scope", "PokemonTower_6F", 19, "MarowakGhost"),
    ("Poké Flute from Mr. Fuji", "LavenderTown_VolunteerPokemonHouse", 19, "MrFuji"),
    ("Super Rod", "Route12_FishingHouse", 19),
    ("TM27 Return from the gate girl", "Route12_NorthEntrance_2F", 19),
    ("Exp. Share from Oak's aide (50 owned)", "Route15_WestEntrance_2F", 19),
    ("HM02 Fly from the trapped girl", "Route16_House", 20),
    ("Amulet Coin from Oak's aide (40 owned)", "Route16_NorthEntrance_2F", 20),
    ("Good Rod", "FuchsiaCity_House2", 21),
    ("HM03 Surf in the Secret House", "SafariZone_SecretHouse", 21),
    ("HM04 Strength for the Gold Teeth", "FuchsiaCity_WardensHouse", 21, "Warden"),
    ("Lapras from the Silph employee", "SilphCo_7F", 23, "LaprasGuy"),
    ("Master Ball from the President", "SilphCo_11F", 23, "President"),
    ("TM29 Psychic from Mr. Psychic", "SaffronCity_MrPsychicsHouse", 23, "MrPsychic"),
    ("Hitmonlee or Hitmonchan (pick one)", "SaffronCity_Dojo", 23),
    ("Revive your fossils", "CinnabarIsland_PokemonLab_ExperimentRoom", 26, "FossilScientist"),
    ("Help Celio at the Network Center", "OneIsland_PokemonCenter_1F", 28, "Celio"),
    ("HM06 Rock Smash in the Ember Spa", "OneIsland_KindleRoad_EmberSpa", 28, "RockSmashMan"),
    ("Rescue Lostelle in the Berry Forest", "ThreeIsland_BerryForest", 28, "Lostelle"),
    ("The prospector's Nugget", "ThreeIsland_DunsparceTunnel", 28, "Prospector"),
    ("Togepi egg from the caretaker", "FiveIsland_WaterLabyrinth", 33, "EggGentleman"),
    ("TM42 Facade for a Lemonade", "FiveIsland_MemorialPillar", 33, "MemorialMan"),
    ("Take the Ruby", "MtEmber_RubyPath_B3F", 33),
    ("The Sapphire from Gideon", "FiveIsland_RocketWarehouse", 33),
    ("Ruby and Sapphire to Celio — trading unlocked", "OneIsland_PokemonCenter_1F", 33, "Celio"),
    # The Lift Key operates the Rocket Hideout elevator. It's a giveitem ball
    # (skipped by the finditem harvest), and it's the gate: B4F's right wing --
    # Giovanni, the Silph Scope -- has no stairs, so it's reachable only by the
    # elevator, which needs this key. The key sits in the stair-reachable left
    # wing, so the route must grab it before riding to Giovanni.
    ("Lift Key from the ball", "RocketHideout_B4F", 17, "LiftKey"),
]

# In-game trades (src/data/ingame_trades.h), each at the building it lives in.
# (received, given, stage, map, give-instruction). The stage is the first at
# which the building is open AND you can hold the requested Pokémon.
# each trade's NPC script label, so the stop pins on the trader, not the door
INGAME_TRADE_NPC = {
    "Route2_House": "Reyley", "CeruleanCity_House3": "Dontae",
    "VermilionCity_House2": "Elyssa", "Route11_EastEntrance_2F": "Turner",
    "Route18_EastEntrance_2F": "Haden", "UndergroundPath_NorthEntrance": "Saige",
    "CinnabarIsland_PokemonLab_Lounge": "Clifton",
    "CinnabarIsland_PokemonLab_ExperimentRoom": "Garett",
}
INGAME_TRADE_STOPS = [
    ("Mr. Mime", "Abra",       13, "Route2_House",
     "Trade an Abra for Mr. Mime — the ONLY Mr. Mime in LeafGreen. Catch a "
     "spare Abra on Route 24 to hand over (you keep your own)."),
    ("Farfetch'd", "Spearow",  10, "VermilionCity_House2",
     "Trade a Spearow for Farfetch'd — the only one in the game. Catch a "
     "spare Spearow back on Route 22."),
    ("Nidoran♀", "Nidoran♂", 9, "UndergroundPath_NorthEntrance",
     "Trade a Nidoran♂ for a Nidoran♀ (fills the other gender's dex "
     "slot). Both are on Route 3 — catch a spare to give; no leveling needed."),
    ("Nidorina", "Nidorino",   12, "Route11_EastEntrance_2F",
     "Trade a Nidorino for a Nidorina. LEVEL a Nidoran♂ to 16 to evolve "
     "it first — catch a spare on Route 3 to raise, don't give your team's."),
    ("Jynx", "Poliwhirl",      19, "CeruleanCity_House3",
     "Trade a Poliwhirl for Jynx. Super-Rod a Poliwag on Route 6 and LEVEL it "
     "to 25 to evolve — or Super-Rod a Poliwhirl straight out of the water."),
    ("Lickitung", "Slowbro",   21, "Route18_EastEntrance_2F",
     "Trade a Slowbro for Lickitung — the only Lickitung in the game. "
     "LeafGreen has no Psyduck, so you must LEVEL a Slowpoke to 37 (Slowpoke "
     "is on Route 10 / Seafoam)."),
    ("Electrode", "Raichu",    26, "CinnabarIsland_PokemonLab_Lounge",
     "Trade a Raichu for Electrode. No wild Raichu exists — catch a Pikachu "
     "(Power Plant or Viridian Forest) and use a Thunder Stone on it."),
    ("Seel", "Ponyta",         28, "CinnabarIsland_PokemonLab_ExperimentRoom",
     "Trade a Ponyta for Seel. Catch a spare Ponyta on Mt. Ember (Sevii "
     "Islands) to give — Seel is also catchable in the Seafoam Islands."),
]
for _got, _give, _st, _map, _note in INGAME_TRADE_STOPS:
    EVENTS.append((f"Trade for {_got} (give {_give})", _map, _st,
                   INGAME_TRADE_NPC.get(_map)))

# Story precedence inside a stage: the fetch has to happen before the stop
# that spends it, even when the TSP would rather swing by the other way.
EVENT_BEFORE = [
    ("Bike Voucher from the Fan Club", "Bicycle from the Bike Shop"),
    ("Trash-can switches (open Surge's door)", "Leader Lt. Surge"),
    ("Super Nerd Miguel", "Helix or Dome Fossil (pick one)"),
    ("Oak's Parcel from the Mart clerk", "Deliver the Parcel — Pokédex from Oak"),
    ("Deliver the Parcel — Pokédex from Oak", "Town Map from Daisy"),
    ("Poké Flute from Mr. Fuji", "Wake Snorlax (Poké Flute)"),
    ("Take the Ruby", "Ruby and Sapphire to Celio — trading unlocked"),
    ("The Sapphire from Gideon", "Ruby and Sapphire to Celio — trading unlocked"),
]

# Stages with no boss battle can still have a story finish line: stage 1 is
# over when the Parcel is delivered and the Pokédex is in hand.
EVENT_ANCHORS = {"Deliver the Parcel — Pokédex from Oak"}

# ------------------------------------------------------------------ nodes
def _item_ball_scripts():
    """Script label -> item constant, from the one flat file that defines
    every real item ball. Prop balls (starters, Eevee...) aren't in it."""
    txt = open(f"{REPO}/data/scripts/item_ball_scripts.inc").read()
    out = {}
    for m in re.finditer(r"(\w+)::\s*\n\s*finditem (ITEM_\w+)", txt):
        out[m.group(1)] = m.group(2)
    return out

def renewable_items():
    """Flag -> rarity tier for the game's renewable hidden items (mushrooms,
    beach gems...). These START ABSENT: at any moment only one rolled tier
    per map exists, re-rolled every ~1500 steps you walk on that map
    (src/renewable_hidden_items.c). The guide must not promise them."""
    out, tier = {}, None
    for line in open(f"{REPO}/src/renewable_hidden_items.c"):
        m = re.search(r"\.(rare|uncommon|common)\s*=", line)
        if m: tier = m.group(1)
        f = re.search(r"HIDDEN_ID\((FLAG_HIDDEN_ITEM_\w+)\)", line)
        if f and tier: out[f.group(1)] = tier
    return out

def ambient_renewables():
    """map -> [[x, y, item, tier]] for the renewable hidden items. These are
    ABSENT by default: a new game sets every renewable flag, and only a map
    entry with 1500+ steps on the counter re-rolls ONE rarity tier into
    existence (60/30/10). They are shown as ambient spawn markers, never as
    route stops -- a single pass usually finds nothing."""
    renew = renewable_items()
    out = collections.defaultdict(list)
    for name, mj in R.maps().items():
        if WD._map_stage(name) is None: continue
        for b in mj.get("bg_events", []):
            if b.get("type") != "hidden_item": continue
            tier = renew.get(b.get("flag", ""))
            if tier:
                out[name].append([b.get("x", 0), b.get("y", 0),
                                  pretty_item(b.get("item", "?")), tier])
    return dict(out)

def pretty_item(const):
    return const.replace("ITEM_", "").replace("_", " ").title().replace("Tm", "TM").replace("Hm", "HM")

def harvest():
    """Every collectable with a tile: items, hidden items, trainers."""
    balls = _item_ball_scripts()
    renew = renewable_items()
    nodes = []
    for name, mj in R.maps().items():
        if WD._map_stage(name) is None: continue
        for o in mj.get("object_events", []):
            if o.get("graphics_id") != ITEM_GFX: continue
            item = balls.get(o.get("script"))
            if not item: continue          # prop ball, not an item
            nodes.append({"kind": "item", "what": pretty_item(item),
                          "map": name,
                          "x": o.get("x", 0), "y": o.get("y", 0)})
        for b in mj.get("bg_events", []):
            if b.get("type") != "hidden_item": continue
            if b.get("flag", "") in renew:
                continue     # renewable: absent by default, never a stop
            nodes.append({"kind": "hidden",
                          "what": pretty_item(b.get("item", "?")),
                          "map": name, "x": b.get("x", 0), "y": b.get("y", 0),
                          "underfoot": bool(b.get("underfoot"))})

    # the overworld's fixed Pokémon, standing on their own tiles
    for name, mj in R.maps().items():
        if WD._map_stage(name) is None: continue
        for o in mj.get("object_events", []):
            label = STATIC_MON_GFX.get(o.get("graphics_id"))
            if label:
                nodes.append({"kind": "event", "what": label, "map": name,
                              "x": o.get("x", 0), "y": o.get("y", 0)})

    # gift/choice/hand-out events: anchored on the NPC who gives them when
    # one is named (the Old Amber scientist stands in the museum's cut-gated
    # east wing -- the front door would be both the wrong tile and wrongly
    # reachable before Cut), otherwise inside the door
    for ev in EVENTS:
        label, name, stage = ev[0], ev[1], ev[2]
        hint = ev[3] if len(ev) > 3 else None
        if name not in R.maps(): continue
        xy = None
        if hint:
            for o in R.maps()[name].get("object_events", []):
                if hint in (o.get("script") or ""):
                    xy = (o.get("x", 0), o.get("y", 0)); break
            # some one-offs fire from a step-on trigger, not an NPC -- the Ghost
            # Marowak on Pokémon Tower 6F is a coord_event, not an object
            if xy is None:
                for c in R.maps()[name].get("coord_events", []):
                    if hint in (c.get("script") or ""):
                        xy = (c.get("x", 0), c.get("y", 0)); break
        if xy is None:
            g = WD.grid(name)
            xy = ((g.warps[0][0], g.warps[0][1]) if g.warps
                  else WD.encounter_anchor(name, "land") or (g.w // 2, g.h // 2))
        nodes.append({"kind": "event", "what": label, "map": name,
                      "x": xy[0], "y": xy[1], "stage": stage})

    # catch stops: a species is caught the first stage it exists. Among the
    # grounds that offer it then, the site is the one where the HUNT is
    # cheapest: expected steps ~ steps-per-encounter / slot share, plus a
    # penalty for ground the walk doesn't already cross (maps whose trainers
    # this stage fights are free). A 1% Clefairy on Mt. Moon 1F is ~100
    # encounters; the 6% slot on B2F is ~17 -- the stop goes to B2F.
    graph = json.load(open(f"{OUT}/encounters.json"))
    trainer_ground = {(e["stage"], e.get("locationRaw")) for e in graph
                      if e["kind"] not in ("wild", "rematch")}
    OFF_ROUTE_PENALTY = 250          # steps-worth of "not on the way"
    NON_LAND_SPE = 20                # surf/rod: nominal steps per encounter

    first_gate = {}
    cands = collections.defaultdict(dict)   # sp -> {(map,label): (share, spe)}
    def offer(sp, gate, name, label, share, spe):
        cur = first_gate.get(sp)
        if cur is None or gate < cur:
            first_gate[sp] = gate; cands[sp] = {(name, label): (share, spe)}
        elif gate == cur:
            old = cands[sp].get((name, label))
            if old is None or share > old[0]:
                cands[sp][(name, label)] = (share, spe)
    for enc in E.WILD["encounters"]:
        if enc["version"] == "FireRed": continue
        name = WD._const_to_folder().get(enc["map"])
        if not name or WD._map_stage(name) is None: continue
        ms = WD._map_stage(name)
        for method, tbl in (enc["tables"] or {}).items():
            if not tbl or not tbl.get("slots"): continue
            if method == "fishing_mons":
                for rod, idxs in E.WILD["fishingGroups"].items():
                    gate = max(ms, P.ROD_STAGE.get(rod, 0))
                    slots = [x for x in tbl["slots"] if x["slot"] in idxs]
                    tot = sum(x["rate"] for x in slots) or 1
                    for slot in slots:
                        offer(slot["species"], gate, name,
                              rod.replace("_", " "),
                              slot["rate"] / tot, NON_LAND_SPE)
            else:
                gate = max(ms, P.METHOD_GATE.get(method, 0))
                label = {"land_mons": "grass/cave", "water_mons": "surfing",
                         "rock_smash_mons": "Rock Smash"}.get(method, method)
                tot = sum(x["rate"] for x in tbl["slots"]) or 1
                spe = (WK.steps_per_encounter(tbl.get("encounterRate", 0))
                       if method == "land_mons" else None) or NON_LAND_SPE
                agg = collections.defaultdict(float)
                for slot in tbl["slots"]:
                    agg[slot["species"]] += slot["rate"] / tot
                for sp, share in agg.items():
                    offer(sp, gate, name, label, share, spe)

    # a species the run already owns by other means needs no wild stop:
    # the Route 4 salesman's Magikarp precedes the Old Rod one by three stages
    av = P.full_availability()
    for sp in list(first_gate):
        rec = av.get(sp)
        if rec and rec["stage"] < first_gate[sp]:
            del first_gate[sp]; cands.pop(sp, None)

    # each species goes where its hunt is cheapest; then stops group by site
    grouped = collections.defaultdict(list)  # (gate,map,label) -> [(sp, share)]
    for sp, gate in first_gate.items():
        def cost(item):
            (name, label), (share, spe) = item
            hunt = spe / max(share, 1e-6)
            free = (gate, name) in trainer_ground
            return (hunt + (0 if free else OFF_ROUTE_PENALTY), name, label)
        (name, label), (share, _) = min(cands[sp].items(), key=cost)
        grouped[(gate, name, label)].append((sp, share))
    import math as _math
    picked_stops = []
    for (gate, name, label), pairs in grouped.items():
        pairs.sort(key=lambda x: x[1])
        rare_share = max(pairs[0][1], 1e-6)
        species = sorted(f"{E.SPECIES[sp]['name']} ({share*100:.0f}%)"
                         for sp, share in pairs)
        # Finding a specific slot is a geometric process: each fight is an
        # independent chance `rare_share`. The mean 1/p undersells the tail,
        # so we also carry the 80th-percentile count -- the number of fights
        # you need to have found it 4 runs in 5.
        mean = int(round(1 / rare_share))
        p80 = int(_math.ceil(_math.log(0.2) / _math.log(1 - rare_share))) \
              if rare_share < 1 else 1
        picked_stops.append((gate, name, label, species, mean, max(p80, mean)))
    for gate, name, label, species, hunt, huntHi in sorted(picked_stops):
        mode = ("water" if label == "surfing"
                else "shore" if "rod" in label else "land")
        xy = WD.encounter_anchor(name, mode) or WD.encounter_anchor(name, "land")
        if xy is None:
            g = WD.grid(name)
            if not g.warps: continue
            xy = (g.warps[0][0], g.warps[0][1])
        names_only = ", ".join(x.split(" (")[0] for x in species)
        nodes.append({"kind": "catch", "map": name, "x": xy[0], "y": xy[1],
                      "stage": gate, "species": species, "method": label,
                      "hunt": hunt, "huntHi": huntHi,
                      "what": f"Catch {names_only} ({label})"})

    # trainers: the same set the sections fight, on the tiles they stand on
    tiles = R.trainer_tiles()
    seen = set()
    for e in graph:
        if e["kind"] in ("wild", "rematch"): continue
        if e.get("starterVariant") not in (None, "Bulbasaur"): continue
        const = e["trainerConst"]
        if const in seen: continue
        seen.add(const)
        t = SCRIPTED_TILE.get(const) or tiles.get(const)
        if t:
            mp, x, y = t
        else:
            # scripted battle with no overworld tile: anchor to its map's
            # door, or failing that any open ground (the Nugget Bridge
            # Rocket stands on a warpless outdoor route)
            mp = e.get("locationRaw") or ""
            if mp not in R.maps(): continue
            g = WD.grid(mp)
            if g.warps:
                x, y = g.warps[0][0], g.warps[0][1]
            else:
                xy = (WD.encounter_anchor(mp, "land")
                      or WD.first_open_tile(mp))
                if xy is None: continue
                x, y = xy
        nodes.append({"kind": "trainer", "what": e["name"], "map": mp,
                      "x": x, "y": y, "enc": e["id"], "stage": e["stage"]})
    return nodes

def node_stage(n):
    """The stage a node belongs to (reachability may still defer it)."""
    if "stage" in n: return n["stage"]
    st = WD._map_stage(n["map"])
    return 33 if st is None else st

# ------------------------------------------------------------------ anchors
def stage_anchor(stage, nodes):
    """The battle a stage ends on: the last fight of the section walk."""
    graph = json.load(open(f"{OUT}/encounters.json"))
    encs = [e for e in graph if e["stage"] == stage
            and e["kind"] not in ("wild", "rematch")
            and e.get("starterVariant") in (None, "Bulbasaur")]
    if not encs: return None
    ordered, _ = R.section_order(stage, encs, S.FLOOR_ORDER)
    by_enc = {n.get("enc"): n for n in nodes if n["kind"] == "trainer"}
    for e in reversed(ordered):
        n = by_enc.get(e["id"])
        if n: return n
    return None

# ------------------------------------------------------------------ solver
def scc_groups(mat, n, start_row=0):
    """One-way terrain (Cycling Road's downhill, the Seafoam hole drops)
    makes some node pairs reachable in only one direction. Group the nodes
    that CAN all reach each other, then order the groups so every one-way
    arc points forward — inside a group the tour is free, between groups the
    order is forced."""
    reach = [[mat[i][j] < INF for j in range(n + 1)] for i in range(n + 1)]
    groups, assigned = [], set()
    for i in range(1, n + 1):
        if i in assigned: continue
        grp = [j for j in range(1, n + 1) if j not in assigned
               and reach[i][j] and reach[j][i]]
        assigned.update(grp)
        groups.append(grp)
    # Kahn's algorithm over the group DAG, nearest-to-start first among ties
    n_g = len(groups)
    before = [[False] * n_g for _ in range(n_g)]
    for a in range(n_g):
        for b in range(n_g):
            if a == b: continue
            if any(reach[i][j] for i in groups[a] for j in groups[b]) and \
               not any(reach[j][i] for i in groups[a] for j in groups[b]):
                before[a][b] = True
    indeg = [sum(before[a][b] for a in range(n_g)) for b in range(n_g)]
    out, done = [], set()
    while len(out) < n_g:
        ready = [g for g in range(n_g) if g not in done and indeg[g] == 0]
        if not ready:
            ready = [g for g in range(n_g) if g not in done]  # cycle: give up
        g = min(ready, key=lambda g: min(mat[start_row][i] for i in groups[g]))
        out.append(groups[g]); done.add(g)
        for b in range(n_g):
            if before[g][b]: indeg[b] -= 1
    return out

def order_tour(mat, start_i, end_i, idxs):
    """Open tour from start over `idxs`, optionally pinned to end at end_i.
    Nearest-neighbour seed, then Or-opt (segments of 1..6, both orientations,
    O(1) deltas -- a four-stop pocket left for a late backtrack relocates as
    one piece) and 2-opt, all scored with the real directed matrix."""
    todo = [i for i in idxs if i != end_i]
    tour, cur = [], start_i
    while todo:
        nxt = min(todo, key=lambda j: mat[cur][j])
        tour.append(nxt); todo.remove(nxt); cur = nxt
    if end_i is not None: tour.append(end_i)

    def cost(t):
        c, prev = 0, start_i
        for j in t:
            c += mat[prev][j]; prev = j
        return c

    best = tour
    fixed_tail = 1 if end_i is not None else 0
    improved, rounds = True, 0
    while improved and rounds < 24:
        improved = False; rounds += 1
        n = len(best)
        # ---- Or-opt: relocate a run of 1..6 stops, forwards or reversed
        for L in range(1, 7):
            if improved: break
            for i in range(0, n - fixed_tail - L + 1):
                seg = best[i:i + L]
                prev = best[i - 1] if i > 0 else start_i
                nxt = best[i + L] if i + L < n else None
                gain = mat[prev][seg[0]]
                if nxt is not None:
                    gain += mat[seg[-1]][nxt] - mat[prev][nxt]
                if gain <= 1e-9: continue
                fwd = sum(mat[seg[k]][seg[k + 1]] for k in range(L - 1))
                rev = sum(mat[seg[k + 1]][seg[k]] for k in range(L - 1))
                rest = best[:i] + best[i + L:]
                m = len(rest)
                hit = None
                for j in range(0, m - fixed_tail + 1):
                    if fixed_tail and j == m: break
                    P = rest[j - 1] if j > 0 else start_i
                    Q = rest[j] if j < m else None
                    base = mat[P][Q] if Q is not None else 0
                    for flip in (0, 1):
                        s0 = seg[-1] if flip else seg[0]
                        s1 = seg[0] if flip else seg[-1]
                        add = mat[P][s0] - base + ((rev if flip else fwd) - fwd)
                        if Q is not None: add += mat[s1][Q]
                        if add < gain - 1e-9:
                            hit = (j, flip); break
                    if hit: break
                if hit:
                    j, flip = hit
                    piece = seg[::-1] if flip else seg
                    best = rest[:j] + piece + rest[j:]
                    improved = True
                    break
        if improved: continue
        # ---- 2-opt: reverse a middle segment
        base_c = cost(best)
        for i in range(0, n - fixed_tail - 1):
            for j in range(i + 2, n - fixed_tail + 1):
                cand = best[:i] + best[i:j][::-1] + best[j:]
                if cost(cand) < base_c - 1e-9:
                    best = cand; improved = True
                    break
            if improved: break
    return best, cost(best)

def corners(path):
    """A tile path compressed to its turning points."""
    if not path: return []
    out = [path[0]]
    for i in range(1, len(path) - 1):
        (m0, x0, y0), (m1, x1, y1), (m2, x2, y2) = path[i - 1], path[i], path[i + 1]
        if m0 != m1 or m1 != m2 or (x1 - x0, y1 - y0) != (x2 - x1, y2 - y1):
            out.append(path[i])
    if len(path) > 1: out.append(path[-1])
    return out

# Scripted battles that fire from a coord trigger, pinned where the player
# actually stands when they fire. The Cerulean rival ambushes you on the
# three tiles at the foot of Nugget Bridge (VAR_MAP_SCENE_CERULEAN_CITY_RIVAL
# at (22-24, 6)), not at any door.
# Every scripted rival battle fires from coord triggers (or the champion's
# approach), never from a trainer tile -- pinned where the player stands
# when each one actually starts, straight from the maps' coord_events.
_SCRIPTED_RIVAL = {
    "RIVAL_OAKS_LAB":      ("PalletTown_ProfessorOaksLab", 6, 8),
    "RIVAL_ROUTE22_EARLY": ("Route22", 33, 5),
    "RIVAL_CERULEAN":      ("CeruleanCity", 23, 6),
    "RIVAL_SS_ANNE":       ("SSAnne_2F_Corridor", 31, 6),
    "RIVAL_POKEMON_TOWER": ("PokemonTower_2F", 16, 6),
    "RIVAL_SILPH":         ("SilphCo_7F", 2, 5),
    "RIVAL_ROUTE22_LATE":  ("Route22", 33, 5),
    "CHAMPION_FIRST":      ("PokemonLeague_ChampionsRoom", 6, 9),
    "CHAMPION_REMATCH":    ("PokemonLeague_ChampionsRoom", 6, 9),
}
SCRIPTED_TILE = {f"TRAINER_{k}_{st}": v for k, v in _SCRIPTED_RIVAL.items()
                 for st in ("BULBASAUR", "CHARMANDER", "SQUIRTLE")}
# Flint must SPOT you from range so he walks off his post -- fought
# point-blank he stays on (28,4) and walls off TM43's pocket until Cut.
# The node stands at the foot of his three-tile sight line.
SCRIPTED_TILE["TRAINER_CAMPER_FLINT"] = ("Route25", 28, 7)

# Play notes for stops with a mechanical catch the map can't show.
STOP_NOTES = {
    ("Camper Flint", "Route25"):
        "Stand HERE, two tiles below him, so he spots you and walks down to "
        "fight — battled point-blank he never moves, and his body walls off "
        "TM43's pocket until you have Cut.",
    ("TM43", "Route25"):
        "Through the gap Camper Flint vacated — only if he walked down to "
        "meet you. If he's still on his post, this pocket waits for Cut "
        "(the tree on the right).",
    ("Trash-can switches (open Surge's door)", "VermilionCity_Gym"):
        "The 15 cans are a 5-wide grid. The FIRST switch hides in a random "
        "can; the SECOND is always in a can directly beside the first (up, "
        "down, left or right) — a wrong second guess re-scrambles both. So "
        "sweep the cans one by one, and when the first clicks, work its "
        "neighbors. The electric fence to Surge drops when both are set.",
}

# ------------------------------------------------------------------ sight aggro
# A trainer with sight forces the battle the moment your walk enters their
# line -- and their BODY is a wall, so a path drawn through them really means
# stepping around them, usually into that line. Both count as aggro.
_AGGRO = None
_AGGRO_FACE = {"MOVEMENT_TYPE_FACE_RIGHT": [(1, 0)], "MOVEMENT_TYPE_FACE_LEFT": [(-1, 0)],
               "MOVEMENT_TYPE_FACE_UP": [(0, -1)], "MOVEMENT_TYPE_FACE_DOWN": [(0, 1)]}
def aggro_cones():
    """[(map, {tiles}, (x, y))] per sighted trainer: the sight ray in every
    facing the movement type allows, plus the body tile itself."""
    global _AGGRO
    if _AGGRO is not None: return _AGGRO
    _AGGRO = []
    for name, mj in R.maps().items():
        if WD._map_stage(name) is None: continue
        try: g = WD.grid(name)
        except Exception: continue
        for o in mj.get("object_events", []):
            if o.get("trainer_type", "TRAINER_TYPE_NONE") == "TRAINER_TYPE_NONE": continue
            sight = int(o.get("trainer_sight_or_berry_tree_id", "0") or 0)
            if sight <= 0: continue
            tiles = {(o["x"], o["y"])}
            for dx, dy in _AGGRO_FACE.get(o.get("movement_type"),
                                          [(1, 0), (-1, 0), (0, 1), (0, -1)]):
                x, y = o["x"], o["y"]
                for _ in range(sight):
                    x += dx; y += dy
                    if not g.inb(x, y) or g.c(x, y): break
                    tiles.add((x, y))
            _AGGRO.append((name, tiles, (o["x"], o["y"])))
    return _AGGRO

def walked_tiles(path):
    """map -> set of tiles a corner-point path actually covers."""
    out, prev = {}, None
    for m, x, y in path:
        if prev and prev[0] == m:
            n = max(abs(x - prev[1]), abs(y - prev[2]), 1)
            for t in range(n + 1):
                out.setdefault(m, set()).add(
                    (round(prev[1] + (x - prev[1]) * t / n),
                     round(prev[2] + (y - prev[2]) * t / n)))
        else:
            out.setdefault(m, set()).add((x, y))
        prev = (m, x, y)
    return out

# ------------------------------------------------------------------ dig out
# Field Dig is a reusable Escape Rope: from inside any single-entrance cave
# (world.dungeon_mouth) it warps to the one mouth you came in by. It's priced
# straight into the stage distance matrix as a hop from a cave tile to that
# mouth, so there's no post-pass here. TM28 goes unused in combat, so a rider
# carries it for free from stage 9 (the Cerulean grunt hands it over at the
# end of stage 8).
for _got, _give, _st, _map, _note in INGAME_TRADE_STOPS:
    STOP_NOTES[(f"Trade for {_got} (give {_give})", _map)] = _note

DIG_COST = 12
DIG_STAGE = 9
def _escaping(m):
    mj = R.maps().get(m)
    return bool(mj and mj.get("allow_escaping"))

# ------------------------------------------------------------------ deliberate heals
# The walk only records a heal when it happens to pass a Pokémon Center; it
# never *seeks* one. A player does: after a fight arc, heal in town before the
# next one. So each stage that opens with fights still owed gets one cheap
# detour — the walk to its first trainer is re-routed past the nearest open
# Center door when that costs at most HEAL_DETOUR_MAX extra steps (the full
# round trip through the Center runs 40-ish steps even for a door en route).
HEAL_DETOUR_MAX = 60

_CENTER_DOORS = None
def center_doors():
    """Outdoor door tile of every Pokémon Center, per map."""
    global _CENTER_DOORS
    if _CENTER_DOORS is None:
        _CENTER_DOORS = {}
        for nm, mj in R.maps().items():
            for w in mj.get("warp_events", []):
                dm = w.get("dest_map") or ""
                if "_POKEMON_CENTER" in dm and dm.endswith("_1F"):
                    _CENTER_DOORS.setdefault(nm, []).append((w.get("x", 0), w.get("y", 0)))
    return _CENTER_DOORS

# B4F's Lift Key ball: reaching it opens the elevator (world.LIFT_GATE).
LIFT_KEY_TILE = ("RocketHideout_B4F", 3, 2)

def gate_token(node):
    """The gate a stop opens when the walk reaches it: a trainer opens its own
    barrier (its const), the Lift Key ball opens the Rocket Hideout elevator."""
    if node["kind"] == "trainer":
        return (node.get("enc") or "").split(":")[-1] or None
    if (node["map"], node["x"], node["y"]) == LIFT_KEY_TILE:
        return WD.LIFT_GATE
    return None

def stop_short(path, mp, x, y):
    """Trim a trailing trainer/NPC body tile so the walk stops on the tile beside
    it -- you talk from there or trip its sight line, never standing on the body.
    The dropped tile's predecessor is orthogonally adjacent and already on the
    drawn path, so it's a real reachable tile even on the spin-floor puzzles."""
    if len(path) >= 2 and tuple(path[-1]) == (mp, x, y) and (x, y) in WD.grid(mp).bodies:
        return path[:-1]
    return path

def corridor_stand(cur, node, stage):
    """The tile beside a trainer/NPC to stand on that keeps the walk in line with
    the approach: nearest the tile you came from, ties broken toward the tile on
    the same row or column (the through-corridor). A body's own far side is a
    detour; this is the near, aligned side."""
    m, x, y = node
    g = WD.grid(m)
    if (x, y) not in g.bodies: return node
    surf = WD._surf_ok(g, stage)
    nbrs = [(m, x + dx, y + dy) for dx, dy in WD.DIRS
            if g.inb(x + dx, y + dy) and WD._tile_open(g, x + dx, y + dy, stage, surf)
            and (x + dx, y + dy) not in g.bodies]
    if not nbrs: return node
    if not (cur and cur[0] == m): return nbrs[0]
    return min(nbrs, key=lambda t: (abs(t[1] - cur[1]) + abs(t[2] - cur[2]),
                                    0 if t[1] == cur[1] or t[2] == cur[2] else 1))

def straighten(steps, stage, start_pos, fought0):
    """Post-pass over a stage's finished, aggro-clean steps: stand each trainer on
    its near, corridor-aligned side when re-routing there is strictly shorter and
    the re-drawn legs still clear every unfought sight line. The stop ORDER is left
    untouched, so the fought-state at each step is fixed -- this can't unbalance the
    aggro reorder the way choosing stands inside it did (the Nugget Bridge snake).
    Returns the steps saved. Each change is local: it rewrites only the leg into a
    trainer and the leg back out to the next stop, whose own tile is unchanged, so
    continuity past it holds."""
    t2c = {v: k for k, v in R.trainer_tiles().items()}
    cones = aggro_cones()
    def cst(step):
        return (step.get("enc") or "").split(":")[-1] if step.get("kind") == "trainer" else None
    def clears(path, fought, target):
        """No walked tile enters an unfought trainer's sight (or body), bar the
        target being fought on this leg."""
        w = walked_tiles(path)
        for name, atiles, pos0 in cones:
            if name not in w or not (atiles & w[name]): continue
            c = t2c.get((name, pos0[0], pos0[1]))
            if c and c not in fought and c != target:
                return False
        return True
    def plen(p): return len(p) - 1 if p else 0

    fought = set(fought0)
    prev = tuple(start_pos)
    saved = 0
    for i, s in enumerate(steps):
        c = cst(s)
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        cur_end = tuple(s["path"][-1]) if s["path"] else prev
        body = (s["at"][0], s["at"][1])
        # only a trainer body, with a normal following leg we can redraw and a
        # normal current leg; skip fly/teleport/heal legs and the stage's last stop
        # a Dig leg pops out of a dungeon mouth -- redrawing it would throw the
        # shortcut away, so leave any leg apply_dig rewrote (and its neighbour) be
        dig = lambda st: st and "Dig" in (st.get("note") or "")
        redrawable = (s["kind"] == "trainer" and not (s.get("fly") or s.get("teleport"))
                      and not dig(s) and nxt and not (nxt.get("fly") or nxt.get("teleport"))
                      and not dig(nxt) and body in _grid_bodies(s["map"]))
        if redrawable:
            new_stand = corridor_stand(prev, (s["map"], body[0], body[1]), stage)
            nxt_end = tuple(nxt["path"][-1]) if nxt["path"] else None
            if new_stand != cur_end and nxt_end:
                old_in = WD.draw_path(prev, cur_end, stage)
                old_out = WD.draw_path(cur_end, nxt_end, stage)
                new_in = WD.draw_path(prev, new_stand, stage)
                new_out = WD.draw_path(new_stand, nxt_end, stage)
                if new_in and new_out and old_in and old_out and \
                   plen(new_in) + plen(new_out) < plen(old_in) + plen(old_out) and \
                   clears(new_in, fought, c) and clears(new_out, fought | {c}, cst(nxt)):
                    s["path"] = [[a, b, d] for a, b, d in corners(new_in)]
                    nxt["path"] = [[a, b, d] for a, b, d in corners(new_out)]
                    saved += (plen(old_in) + plen(old_out)) - (plen(new_in) + plen(new_out))
                    s["walk"] = plen(new_in)
                    nxt["walk"] = plen(new_out)
                    cur_end = new_stand
        prev = cur_end
        if c: fought.add(c)
    return saved

def _grid_bodies(mp):
    try: return WD.grid(mp).bodies
    except Exception: return set()

def _passes_heal(step):
    """Mirror of sections._step_heal, on this route step: a flight or teleport
    (both land you at a Center), a Center interior, or any walked tile within 4
    of a Center door."""
    if step.get("fly") or step.get("teleport"): return True
    doors = center_doors()
    prev = None
    for m, x, y in step["path"]:
        if "PokemonCenter" in m: return True
        if prev and prev[0] == m:
            n = max(abs(x - prev[1]), abs(y - prev[2]), 1)
            pts = [(round(prev[1] + (x - prev[1]) * t / n),
                    round(prev[2] + (y - prev[2]) * t / n)) for t in range(n + 1)]
        else:
            pts = [(x, y)]
        for px, py in pts:
            for wx, wy in doors.get(m, ()):
                if max(abs(px - wx), abs(py - wy)) <= 4: return True
        prev = (m, x, y)
    return False

def insert_heal(steps, stage, start_pos, fought_in):
    """Re-route one hop of this stage's walk past a Center door, if the stage
    has a trainer coming, the party has fought since its last heal, and the
    detour is cheap. Returns the added step count (0 if nothing changed)."""
    ft = next((k for k, s in enumerate(steps) if s["kind"] == "trainer"), None)
    if ft is None: return 0
    fought = fought_in
    for s in steps[:ft]:
        if _passes_heal(s): fought = False
        if s["kind"] in ("trainer", "catch"): fought = True
    if not fought: return 0

    door_tiles = [(m, x, y) for m, xys in center_doors().items()
                  if WD._open_map(m, stage) for x, y in xys]
    if not door_tiles: return 0
    best = None       # (detour, k, door)
    for k in range(ft + 1):
        s = steps[k]
        # Fly/Teleport already land you at (or return you to) a Center, and a Dig
        # leg is a jump out of a cave -- none is a normal walk from the previous
        # stop that a heal detour can be spliced into (doing so would leave the
        # hop's landing field pointing at a path it no longer starts on).
        if s.get("fly") or s.get("teleport") or s.get("dig"): continue
        a = start_pos if k == 0 else (steps[k-1]["map"],
                                      steps[k-1]["at"][0], steps[k-1]["at"][1])
        a = WD.reach_tile(a, stage)
        b = WD.reach_tile((s["map"], s["at"][0], s["at"][1]), stage)
        da = WD.bfs(a, stage, targets=set(door_tiles))
        near = sorted((d, t) for t, d in ((t, da[t]) for t in door_tiles if t in da))[:3]
        for d_at, door in near:
            db = WD.bfs(door, stage, targets={b})
            if b not in db: continue
            detour = d_at + db[b] - s["walk"]
            if detour >= 0 and (best is None or detour < best[0]):
                best = (detour, k, door)
    if best is None or best[0] > HEAL_DETOUR_MAX: return 0

    detour, k, door = best
    s = steps[k]
    # start the detour where the walk actually stands after the previous step --
    # its trimmed endpoint, beside any body -- not on the previous stop's tile
    a = start_pos if k == 0 else tuple(steps[k-1]["path"][-1])
    p1 = WD.draw_path(a, door, stage)
    # route to the stop the same body-aware way the main walk does, then trim any
    # trailing body tile so the post-heal leg stops beside a trainer, not on it
    p2 = WD.draw_path(door, (s["map"], s["at"][0], s["at"][1]), stage)
    if not p1 or not p2: return 0
    p2 = stop_short(p2, s["map"], s["at"][0], s["at"][1])
    # the heal is its own stop, so the guide can say "heal here" out loud
    steps.insert(k, {
        "kind": "heal",
        "what": f"Heal up — {G.pretty_location(door[0])} Pokémon Center",
        "map": door[0], "at": [door[1], door[2]],
        "walk": len(p1) - 1,
        "path": [[m, x, y] for m, x, y in corners(p1)]})
    s["path"] = [[m, x, y] for m, x, y in corners(p2)]
    s["walk"] = len(p2) - 1
    return detour

# ------------------------------------------------------------------ the run
INF = 1 << 30

def solve(max_stage=34, verbose=True):
    nodes = harvest()
    for n in nodes:
        n["_st"] = node_stage(n)
    pending = list(nodes)
    pos = WD.spawn()
    route, total = [], 0
    fought_since_heal = False
    ROUTED_FOUGHT = set()   # trainer consts already fought by the walk so far

    for stage in range(0, max_stage + 1):
        due = [n for n in pending if n["_st"] <= stage]
        if not due: continue
        # what can this stage's world actually reach?
        reach = {n_id: WD.reach_tile((n["map"], n["x"], n["y"]), stage)
                 for n_id, n in enumerate(due)}
        dist0 = WD.bfs(pos, stage)
        todo = [i for i in range(len(due)) if reach[i] in dist0]
        if not todo:
            continue
        picked = [due[i] for i in todo]
        tiles = [reach[todo_i] for todo_i in todo]
        anchor = stage_anchor(stage, picked)
        if anchor is None:
            anchor = next((n for n in picked
                           if n.get("what") in EVENT_ANCHORS), None)
        end_i = picked.index(anchor) if anchor in picked else None

        # distance matrix: start + every node tile. A hop can instead return to a
        # Pokémon Center and walk from there -- via Teleport once a party Teleport
        # user is around (stage 7+), or Fly once HM02 is (stage 20+). Fly is the
        # more capable move, so from its stage the hop is a Fly; before then it's a
        # Teleport (a bit cheaper, no flight animation).
        # Fly (HM02, stage 20+) drops you at ANY visited town, so it lands at the
        # Center nearest your DESTINATION. Teleport (a party move, stage 7+) only
        # returns you to the Center you last healed at -- in a no-grind run that's
        # the town you're working out of, i.e. the one nearest where you're
        # STANDING, never one near the target. Both depart from the open air only.
        fly = stage >= P.HM_STAGE["FLY"]
        teleport = (stage >= TELEPORT_STAGE) and not fly
        cdist, cparent, centers = {}, {}, []
        if fly or teleport:
            centers = [c for c in WD.center_nodes() if WD._open_map(c[0], stage)]
            cdist, cparent = WD.bfs_multi(centers, stage)
        pts = [pos] + tiles
        wmat = [[INF] * len(pts) for _ in pts]
        for i, p in enumerate(pts):
            d = WD.bfs(p, stage, targets=set(pts))
            for j, q in enumerate(pts):
                if q in d: wmat[i][j] = d[q]
        mat = [row[:] for row in wmat]
        def _nearest_center(t):        # the town you'd Teleport back to from tile t
            while cparent.get(t) is not None: t = cparent[t]
            return t if t in centers else None
        # which shortcut, if any, wins each leg -- so build_steps can redraw and
        # label it (a body-blind cost BFS would otherwise cut through trainers)
        hop_kind = [[None] * len(pts) for _ in pts]
        tele_land, dig_land = {}, {}    # departure index -> Center / cave mouth it lands at
        tset = set(pts)
        if fly:
            for j, q in enumerate(pts):
                if j == 0: continue
                f = FLY_COST + cdist.get(q, INF)
                for i in range(len(pts)):
                    if i != j and WD.is_outdoors(pts[i][0]) and f < mat[i][j]:
                        mat[i][j] = f; hop_kind[i][j] = "fly"
        elif teleport:
            land_from = {}                 # nearest Center -> departures that use it
            for i, p in enumerate(pts):
                if not WD.is_outdoors(p[0]): continue
                c = _nearest_center(p)
                if c is None: continue
                tele_land[i] = c
                land_from.setdefault(c, []).append(i)
            for c, idxs in land_from.items():          # one BFS each, not per-Center
                df = WD.bfs(c, stage, targets=tset)
                for i in idxs:
                    for j in range(1, len(pts)):
                        if i != j:
                            f = TELEPORT_COST + df.get(pts[j], INF)
                            if f < mat[i][j]: mat[i][j] = f; hop_kind[i][j] = "tele"
        # Dig / Escape Rope pops you from inside a single-entrance cave to its one
        # mouth (world.dungeon_mouth). It departs from IN the cave, so it never
        # competes with Fly/Teleport (open-air only) for the same leg. Available
        # from stage 9, when the Cerulean grunt hands over TM28.
        if stage >= DIG_STAGE:
            mouth_from = {}                # shared mouth -> departures inside that cave
            for i, p in enumerate(pts):
                M = WD.dungeon_mouth(p)
                if M is None: continue
                dig_land[i] = M
                mouth_from.setdefault(M, []).append(i)
            for M, idxs in mouth_from.items():
                df = WD.bfs(M, stage, targets=tset)
                for i in idxs:
                    for j in range(1, len(pts)):
                        if i != j:
                            f = DIG_COST + df.get(pts[j], INF)
                            if f < mat[i][j]: mat[i][j] = f; hop_kind[i][j] = "dig"
        groups = scc_groups(mat, len(pts) - 1)
        order, cur_idx = [], 0
        for gi, grp in enumerate(groups):
            pin = ((end_i + 1) if end_i is not None and (end_i + 1) in grp
                   and gi == len(groups) - 1 else None)
            sub, _ = order_tour(mat, cur_idx, pin, grp)
            order += sub
            cur_idx = sub[-1]
        # story precedence: a stop that depends on a fetch is pushed back to
        # just after it (never the fetch pulled forward, which would tear the
        # stop off the stage's pinned ending), repeated to a fixpoint
        by_what = {picked[i - 1].get("what"): i for i in order}
        pairs = list(EVENT_BEFORE)
        for n in picked:
            if n["kind"] == "catch" and "rod" in (n.get("method") or ""):
                pairs.append((n["method"].title(), n["what"]))
        for _ in range(8):
            changed = False
            for a_name, b_name in pairs:
                ia, ib = by_what.get(a_name), by_what.get(b_name)
                if ia is None or ib is None: continue
                if order.index(ia) > order.index(ib):
                    order.remove(ib)
                    order.insert(order.index(ia) + 1, ib)
                    changed = True
            if not changed: break

        # trainer-gated doors: a stop shut behind a barrier can't be reached until
        # the barrier's fight is won, so it is ordered after every gate trainer.
        # Each barrier's "behind" set is the tiles reachable only once it opens
        # (bfs with just that door shut). Repaired to a fixpoint like the story
        # precedence above -- the far stop is pushed to just after the last gate.
        stage_barriers = []            # (gate tokens, set of tiles behind the gate)
        gate_consts = set()
        for m in {picked[oi - 1]["map"] for oi in order}:
            for _tiles, _gates in WD.gated_barriers().get(m, ()):
                if _gates <= ROUTED_FOUGHT: continue   # opened in an earlier stage
                gate_consts |= set(_gates)
        # the Lift Key ball, when this stage collects it, gates the elevator too
        lift_here = any(gate_token(picked[oi - 1]) == WD.LIFT_GATE for oi in order)
        if lift_here:
            gate_consts.add(WD.LIFT_GATE)
        if gate_consts:
            reach_open = set(dist0)
            for m in {picked[oi - 1]["map"] for oi in order}:
                for _tiles, _gates in WD.gated_barriers().get(m, ()):
                    if _gates <= ROUTED_FOUGHT: continue
                    WD._OPEN_GATES = frozenset(gate_consts - set(_gates))  # this door shut
                    behind = reach_open - set(WD.bfs(pos, stage))
                    if behind:
                        stage_barriers.append((set(_gates), behind))
            if lift_here:
                # every hideout gate shut, not just the lift: the barrier grunts
                # themselves sit in the stair-less wings and are reached only by
                # riding the lift, so opening the doors here (which the grunts do)
                # would wrongly make them look stair-reachable. All gated tiles are
                # lift-dependent, so this correctly orders them after the key.
                WD._OPEN_GATES = frozenset()
                behind = reach_open - set(WD.bfs(pos, stage))
                if behind:
                    stage_barriers.append(({WD.LIFT_GATE}, behind))
            WD._OPEN_GATES = None
        for _ in range(len(order) + 4):
            changed = False
            for gates, behind in stage_barriers:
                gpos = [k for k, oi in enumerate(order) if gate_token(picked[oi - 1]) in gates]
                if not gpos: continue
                last_gate = max(gpos)
                for k, oi in enumerate(order):
                    if k < last_gate and tiles[oi - 1] in behind:
                        order.pop(k)
                        gpos2 = [j for j, o in enumerate(order)
                                 if gate_token(picked[o - 1]) in gates]
                        order.insert(max(gpos2) + 1, oi)
                        changed = True
                        break
                if changed: break
            if not changed: break

        def build_steps(ordr):
            stps, cur2, prev_idx, cst = [], pos, 0, 0
            # trainer-gated doors open only for fights already won, so each walked
            # segment is drawn against the doors' state at that moment -- a
            # pre-fight leg routes around a locked barrier, a post-fight one
            # through it (world.gated_barriers / _OPEN_GATES). Reset to "all open"
            # on the way out so the matrix and later stages are unaffected.
            fought = set(ROUTED_FOUGHT)
            try:
                for oi in ordr:
                    n = picked[oi - 1]
                    tgt = tiles[oi - 1]
                    WD._OPEN_GATES = frozenset(fought)
                    kind = hop_kind[prev_idx][oi]
                    if kind:
                        # This leg is a shortcut, not a walk: land where the hop
                        # drops you, then draw the walk on from there body-aware
                        # (the cost BFS is body-blind and would cut through
                        # trainers). Teleport -> the Center nearest your DEPARTURE
                        # (last healed); Dig -> the cave's one mouth; Fly -> the
                        # Center nearest the target.
                        landing = (dig_land.get(prev_idx) if kind == "dig"
                                   else tele_land.get(prev_idx) if kind == "tele"
                                   else None)
                        path = WD.draw_path(landing, tgt, stage) if landing else None
                        if not path:
                            raw = WD.walk_back(cparent, tgt)
                            landing = raw[0] if raw else cur2
                            path = WD.draw_path(landing, tgt, stage) or WD.expand_spins(raw, stage)
                    else:
                        path = WD.draw_path(cur2, tgt, stage) or [cur2, tgt]
                    path = stop_short(path, n["map"], n["x"], n["y"])
                    end = tuple(path[-1]) if path else tgt
                    step = {"kind": n["kind"], "what": n["what"],
                            "map": n["map"], "at": [n["x"], n["y"]],
                            "walk": int(mat[prev_idx][oi]),
                            "path": [[m, x, y] for m, x, y in corners(path)]}
                    if kind:
                        where = path[0][0] if path else True
                        step[{"fly": "fly", "tele": "teleport", "dig": "dig"}[kind]] = where
                    if n.get("enc"): step["enc"] = n["enc"]
                    nt = STOP_NOTES.get((n["what"], n["map"]))
                    if kind == "dig":
                        # keep the word "Dig" in the note: straighten() reads it to
                        # leave the popped-out leg (and its neighbour) untouched
                        dn = (f"Use Dig (or an Escape Rope) — you pop straight out to "
                              f"{G.pretty_location(where)}, then walk on. Teach TM28 to "
                              f"anyone along for the ride; no fight wants it.")
                        nt = (nt + " " + dn) if nt else dn
                    if nt: step["note"] = nt
                    if n.get("underfoot"): step["underfoot"] = True
                    if n.get("renewable"): step["renewable"] = n["renewable"]
                    if n.get("hunt"): step["hunt"] = n["hunt"]
                    if n.get("huntHi"): step["huntHi"] = n["huntHi"]
                    if n.get("species"): step["species"] = n["species"]
                    stps.append(step)
                    cst += mat[prev_idx][oi]
                    cur2, prev_idx = end, oi
                    # reaching a stop opens its gate for the next leg: a trainer's
                    # barrier door on defeat, the Rocket Hideout elevator on the key
                    tok = gate_token(n)
                    if tok: fought.add(tok)
            finally:
                WD._OPEN_GATES = None
            return stps, cst, cur2

        # a hop that enters an unfought trainer's aggro forces that battle on
        # the spot: pull the trainer to just before the hop, to a fixpoint
        t2c = {v: k for k, v in R.trainer_tiles().items()}
        moved_pairs = set()
        for _ in range(12):
            steps, cost, cur = build_steps(order)
            fought_now = set(ROUTED_FOUGHT)
            move = None
            for si, (oi, step) in enumerate(zip(order, steps)):
                w = walked_tiles(step["path"])
                for name, atiles, pos0 in aggro_cones():
                    if name not in w or not (atiles & w[name]): continue
                    c = t2c.get((name, pos0[0], pos0[1]))
                    if not c or c in fought_now: continue
                    tj = next((k2 for k2 in order
                               if (picked[k2 - 1].get("enc") or "").endswith(":" + c)), None)
                    if tj is None or order.index(tj) <= si: continue
                    if (tj, si) in moved_pairs: continue
                    move = (tj, si); break
                if move: break
                n = picked[oi - 1]
                if n["kind"] == "trainer" and n.get("enc"):
                    fought_now.add(n["enc"].split(":")[-1])
            if not move: break
            moved_pairs.add(move)
            tj, si = move
            order.remove(tj); order.insert(si, tj)
        steps, cost, cur = build_steps(order)
        cost += insert_heal(steps, stage, pos, fought_since_heal)
        # Dig / Escape Rope is priced into the distance matrix above (a hop from
        # inside a single-entrance cave to its mouth), so it needs no post-pass.
        # order is now final and aggro-clean; straighten stand tiles where it's
        # shorter and still clears every unfought sight line
        cost -= straighten(steps, stage, pos, ROUTED_FOUGHT)
        for s in steps:
            if _passes_heal(s): fought_since_heal = False
            if s["kind"] in ("trainer", "catch"): fought_since_heal = True
        route.append({"stage": stage, "steps": steps,
                      "stepTotal": int(cost),
                      # this stage's walk is cut shorter by a Teleport hop, so the
                      # recommended party must carry a Teleport user (handled like
                      # an HM in sections.py). Fly hops don't need a carrier -- HM02
                      # is a field move any mon can hold from its own item.
                      "teleport": any(s.get("teleport") for s in steps),
                      "startsAt": [pos[0], pos[1], pos[2]],
                      "endsAt": [cur[0], cur[1], cur[2]]})
        total += cost
        pos = cur
        for n in picked:
            tok = gate_token(n)      # keep gates open in later stages (the lift too)
            if tok: ROUTED_FOUGHT.add(tok)
        done = {id(n) for n in picked}
        pending = [n for n in pending if id(n) not in done]
        if verbose:
            st = P.STAGE_BY_ID.get(stage, {})
            kinds = collections.Counter(n["kind"] for n in picked)
            print(f"  [{stage:2}] {st.get('name','?')[:34]:36} "
                  f"{int(cost):6} steps  "
                  f"{kinds.get('trainer',0):3} fights "
                  f"{kinds.get('item',0):3} items "
                  f"{kinds.get('hidden',0):3} hidden "
                  f"{kinds.get('catch',0):2} catch "
                  f"{kinds.get('event',0):2} events", flush=True)

    leftovers = [(n["kind"], n["what"], n["map"]) for n in pending]
    return {"stages": route, "stepTotal": int(total), "left": leftovers}

def main():
    print("routing the whole game...", flush=True)
    out = solve()
    out["renewables"] = ambient_renewables()
    with open(f"{OUT}/route.json", "w") as f:
        json.dump(out, f)
    print(f"\n  whole game: {out['stepTotal']} steps")
    if out["left"]:
        print(f"  unreachable ({len(out['left'])}):")
        for k, w, m in out["left"][:15]:
            print(f"    {k:8} {w:28} {m}")

if __name__ == "__main__":
    main()
