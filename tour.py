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
    picked_stops = []
    for (gate, name, label), pairs in grouped.items():
        pairs.sort(key=lambda x: x[1])
        rare_share = pairs[0][1]
        species = sorted(f"{E.SPECIES[sp]['name']} ({share*100:.0f}%)"
                         for sp, share in pairs)
        picked_stops.append((gate, name, label, species,
                             int(round(1 / max(rare_share, 1e-6)))))
    for gate, name, label, species, hunt in sorted(picked_stops):
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
                      "hunt": hunt,
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
# Field Dig is a reusable Escape Rope: in any map flagged allow_escaping it
# warps to the mouth the walk came IN by. TM28 goes unused in combat, so a
# rider can carry it for free from stage 9 (the Cerulean grunt hands it over
# at the end of stage 8). A hop that walks a long way back out of a dungeon
# through its own entrance becomes a 12-step menu action instead.
for _got, _give, _st, _map, _note in INGAME_TRADE_STOPS:
    STOP_NOTES[(f"Trade for {_got} (give {_give})", _map)] = _note

DIG_COST = 12
DIG_STAGE = 9
def _escaping(m):
    mj = R.maps().get(m)
    return bool(mj and mj.get("allow_escaping"))

def apply_dig(steps, stage, start_pos):
    """Rewrite retrace-exits through a dungeon's own mouth as Dig. Returns
    the steps saved. Tracks the entrance used per dungeon visit, exactly the
    tile Escape Rope would target."""
    if stage < DIG_STAGE: return 0
    saved = 0
    escape_at = None          # the outdoor (map,x,y) tile we entered the cave by
    prev = (start_pos[0], start_pos[1], start_pos[2])
    for s in steps:
        if s.get("fly"):
            prev = tuple(s["path"][-1]) if s["path"] else prev
            escape_at = None
            continue
        path = [tuple(p) for p in s["path"]]
        # find where this hop's walk leaves cave ground, and how much cave
        # walking it does before that
        walk_in, exit_i = 0, None
        for i, (a, b) in enumerate(zip(path, path[1:])):
            if a[0] == b[0] and _escaping(a[0]):
                walk_in += abs(a[1] - b[1]) + abs(a[2] - b[2])
            if _escaping(a[0]) and not _escaping(b[0]):
                exit_i = i + 1
                break
            if not _escaping(a[0]) and _escaping(b[0]):
                escape_at = a     # stepping in: remember the mouth
        if exit_i is not None and escape_at is not None and _escaping(path[0][0]):
            out = path[exit_i]
            same_mouth = (out[0] == escape_at[0]
                          and abs(out[1] - escape_at[1]) + abs(out[2] - escape_at[2]) <= 2)
            gain = walk_in - DIG_COST
            if same_mouth and gain > 15:
                dest = path[-1]
                p2 = WD.draw_path(escape_at, dest, stage)
                if p2:
                    new_walk = DIG_COST + len(p2) - 1
                    if new_walk < s["walk"]:
                        saved += s["walk"] - new_walk
                        s["walk"] = new_walk
                        s["path"] = ([[path[0][0], path[0][1], path[0][2]]]
                                     + [[m, x, y] for m, x, y in corners(p2)])
                        note = (f"Use Dig here — you pop straight out to "
                                f"{G.pretty_location(escape_at[0])} — then walk on. "
                                f"(Teach TM28 to anyone along for the ride; no fight wants it.)")
                        s["note"] = (s.get("note") + " " + note) if s.get("note") else note
        # keep tracking entries across the rest of this hop
        for a, b in zip(path[exit_i or 0:], path[(exit_i or 0) + 1:]):
            if not _escaping(a[0]) and _escaping(b[0]):
                escape_at = a
        if path: prev = path[-1]
    return saved

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

def _passes_heal(step):
    """Mirror of sections._step_heal, on this route step: a flight, a Center
    interior, or any walked tile within 4 of a Center door."""
    if step.get("fly"): return True
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
        if s.get("fly"): continue          # flying already lands at a Center
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
    a = start_pos if k == 0 else (steps[k-1]["map"],
                                  steps[k-1]["at"][0], steps[k-1]["at"][1])
    a = WD.reach_tile(a, stage)
    b = WD.reach_tile((s["map"], s["at"][0], s["at"][1]), stage)
    p1 = WD.draw_path(a, door, stage)
    p2 = WD.draw_path(door, b, stage)
    if not p1 or not p2: return 0
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

        # distance matrix: start + every node tile. Once Fly is live, any hop
        # can instead fly to the best Pokémon Center and walk from there.
        fly = stage >= P.HM_STAGE["FLY"]
        cdist, cparent = ({}, {})
        if fly:
            centers = [c for c in WD.center_nodes()
                       if WD._open_map(c[0], stage)]
            cdist, cparent = WD.bfs_multi(centers, stage)
        pts = [pos] + tiles
        wmat = [[INF] * len(pts) for _ in pts]
        for i, p in enumerate(pts):
            d = WD.bfs(p, stage, targets=set(pts))
            for j, q in enumerate(pts):
                if q in d: wmat[i][j] = d[q]
        mat = [row[:] for row in wmat]
        if fly:
            for j, q in enumerate(pts):
                if j == 0: continue
                f = FLY_COST + cdist.get(q, INF)
                for i in range(len(pts)):
                    if i != j and f < mat[i][j]: mat[i][j] = f
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
        def build_steps(ordr):
            stps, cur2, prev_idx, cst = [], pos, 0, 0
            for oi in ordr:
                n = picked[oi - 1]
                tgt = tiles[oi - 1]
                flew = mat[prev_idx][oi] < wmat[prev_idx][oi]
                if flew:
                    path = WD.walk_back(cparent, tgt)   # from the landing Center
                else:
                    path = WD.draw_path(cur2, tgt, stage) or [cur2, tgt]
                step = {"kind": n["kind"], "what": n["what"],
                        "map": n["map"], "at": [n["x"], n["y"]],
                        "walk": int(mat[prev_idx][oi]),
                        "path": [[m, x, y] for m, x, y in corners(path)]}
                if flew: step["fly"] = path[0][0] if path else True
                if n.get("enc"): step["enc"] = n["enc"]
                nt = STOP_NOTES.get((n["what"], n["map"]))
                if nt: step["note"] = nt
                if n.get("underfoot"): step["underfoot"] = True
                if n.get("renewable"): step["renewable"] = n["renewable"]
                if n.get("hunt"): step["hunt"] = n["hunt"]
                if n.get("species"): step["species"] = n["species"]
                stps.append(step)
                cst += mat[prev_idx][oi]
                cur2, prev_idx = tgt, oi
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
        cost -= apply_dig(steps, stage, pos)
        for s in steps:
            if _passes_heal(s): fought_since_heal = False
            if s["kind"] in ("trainer", "catch"): fought_since_heal = True
        route.append({"stage": stage, "steps": steps,
                      "stepTotal": int(cost),
                      "startsAt": [pos[0], pos[1], pos[2]],
                      "endsAt": [cur[0], cur[1], cur[2]]})
        total += cost
        pos = cur
        for n in picked:
            if n["kind"] == "trainer" and n.get("enc"):
                ROUTED_FOUGHT.add(n["enc"].split(":")[-1])
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
