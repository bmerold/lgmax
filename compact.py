#!/usr/bin/env python3
"""Compact the analysis into an array-based payload small enough to inline in a
single self-contained page. Same information, far fewer bytes: repeated object
keys become positional arrays, and shared strings become indices into pools.
"""
import base64, json, os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

def main():
    graph = json.load(open(f"{OUT}/encounters.json"))
    recs = json.load(open(f"{OUT}/recommendations.json"))
    secs = json.load(open(f"{OUT}/sections.json"))
    mapart = json.load(open(f"{OUT}/mapart/index.json"))
    enc_by_id = {e["id"]: e for e in graph}

    # ---- string pools (species, move and type names repeat thousands of times)
    pool, pool_idx = [], {}
    def S(x):
        if x is None: return -1
        if isinstance(x, str):
            for _a, _b in (("Pokemon", "Pokémon"), ("Mr Psychics", "Mr. Psychic's"),
                           ("Mr Fujis", "Mr. Fuji's"), ("Berry Forest", "Berry Forest")):
                if _a in x: x = x.replace(_a, _b)
        if x not in pool_idx:
            pool_idx[x] = len(pool); pool.append(x)
        return pool_idx[x]

    def r1(v): return round(float(v), 1)
    def r2(v): return round(float(v), 2)

    def detail_row(d):
        # [move, moveType, eff, dmgMin, dmgMax, critMax, turns, ohko,
        #  faster, threat, threatType, threatPct, threatMax, pctPerHit]
        return [S(d["move"]), S(d["moveType"]), d["eff"], d["dmgMin"], d["dmgMax"],
                d["critMax"], r2(d["turns"]), r2(d["ohko"]), 1 if d["faster"] else 0,
                S(d["threat"]), S(d.get("threatType")), r1(d["threatPct"]),
                d["threatMax"], r1(d["pctPerHit"]), S(d.get("tm"))]

    def team_row(t):
        # [name, types, turns, takenPct, kos, survives, hp, stats,
        #  obtainedAt, obtainedVia, requiresTrade, detail[]]
        st = t["stats"]
        return [S(t["name"]), [S(x) for x in t["types"]], r2(t["turns"]),
                r1(t["takenPct"]), t["kos"], 1 if t["survives"] else 0, t["hp"],
                [st["hp"], st["attack"], st["defense"], st["spAttack"],
                 st["spDefense"], st["speed"]],
                t["obtainedAt"], S(t["obtainedVia"]), 1 if t["requiresTrade"] else 0,
                [detail_row(d) for d in t["detail"]],
                S(t.get("starterLine")), [S(a) for a in t.get("alts", [])]]

    def counter_row(b):
        # [name, types, move, moveType, eff, min, max, critMax, turns, ohko,
        #  faster, threat, threatPct, pctPerHit]
        return [S(b["name"]), [S(x) for x in b["types"]], S(b["move"]),
                S(b["moveType"]), b["eff"], b["dmgMin"], b["dmgMax"], b["critMax"],
                r2(b["turns"]), r2(b["ohko"]), 1 if b["faster"] else 0,
                S(b["threat"]), r1(b["threatPct"]), r1(b["pctPerHit"]),
                S(b.get("starterLine")), [S(a) for a in b.get("alts", [])],
                S(b.get("tm")), 1 if b.get("requiresTrade") else 0]

    out_encs = []
    for e in graph:
        rec = recs.get(e["id"])
        node = {
            "id": e["id"], "k": e["kind"], "st": e["stage"],
            "n": e["name"], "l": e["location"], "mx": e["maxLevel"],
        }
        if e["kind"] == "wild":
            node["m"] = e["method"]
            node["er"] = e.get("encounterRate", 0)
            node["w"] = [[S(w["name"]), [S(x) for x in dict.fromkeys(w["types"])],
                          w["minLevel"], w["maxLevel"], w["chance"]]
                         for w in e["wildMons"]]
        else:
            node["p"] = [[S(m["name"]), [S(x) for x in dict.fromkeys(m["types"])],
                          m["level"], m["stats"]["hp"],
                          [S(mv["name"]) for mv in m["moves"]],
                          S(m["nature"]), S(m["ability"]),
                          [m["stats"]["hp"], m["stats"]["attack"], m["stats"]["defense"],
                           m["stats"]["spAttack"], m["stats"]["spDefense"], m["stats"]["speed"]]]
                         for m in e["party"]]
            if e.get("starterVariant"): node["sv"] = e["starterVariant"]
            if e.get("items"): node["it"] = e["items"]
            if e.get("doubleBattle"): node["db"] = 1
        if rec:
            node["pl"] = rec["playerLevel"]
            node["bg"] = rec["badges"]
            node["team"] = [team_row(t) for t in rec["team"]]
            node["cnt"] = [[S(c["opponent"]), c["level"],
                            [S(x) for x in c["types"]], c["hp"],
                            c["chance"] if c["chance"] is not None else -1,
                            [counter_row(b) for b in c["best"]]]
                           for c in rec["counters"]]
        out_encs.append(node)

    stages = [{"id": s["id"], "name": s["name"], "chapter": s["chapter"],
               "badges": s["badges"], "level": s["level"], "gym": s.get("gym")}
              for s in __import__("progression").STAGES]

    # ---- per-section party recommendations, one set per starter
    def move_row(m):
        # [name, type, power, poolPP, used, left, basePP, refills, tmItem]
        return [S(m["name"]), S(m["type"]), m["power"], m["pp"], m["used"], m["left"],
                m.get("basePP", m["pp"]), m.get("refills", 0), S(m.get("tm"))]

    def member_row(t):
        # [name, types, level, hp, lowestHpPct, fainted, obtainedAt, via, newHere, moves]
        return [S(t["name"]), [S(x) for x in t["types"]], t["level"], t["hp"],
                t["hpLeft"], t.get("fainted", 0), t["obtainedAt"], S(t["obtainedVia"]),
                1 if t["newHere"] else 0, [move_row(m) for m in t["moves"]]]

    def step_row(x):
        # [opp, lvl, by, move, moveType, eff, turns, switched, hpLeft, min, max, selfKO]
        return [S(x["opp"]), x["lvl"], S(x.get("by")), S(x.get("move")),
                S(x.get("moveType")), x.get("eff", 1), x.get("turns", 0),
                1 if x.get("switched") else 0, x.get("hpLeft", 0),
                x.get("dmgMin", 0), x.get("dmgMax", 0), 1 if x.get("selfKO") else 0,
                x.get("n", 0), 1 if x.get("handover") else 0]

    def log_row(l):
        # [encName, id, kind, location, steps, healedAfter, wildCount]
        return [S(l["enc"]), l["id"], S(l["kind"]), S(l["location"]),
                [step_row(x) for x in l["steps"]],
                S(l["healedAfter"]) if isinstance(l.get("healedAfter"), str)
                    else (1 if l.get("healedAfter") else 0),
                l.get("count", 0)]

    def merge_walks(log):
        """Interleaving scatters a map's wild battles between the trainers, which
        renders as forty one-line rows. Merge them back into one row per map,
        held at the position the walking starts."""
        import build_graph as _G
        out, idx = [], {}
        for l in log:
            if l["kind"] != "wild":
                out.append(l); continue
            g = l.get("group") or l.get("location") or "the area"
            if g in idx:
                tgt = out[idx[g]]
                tgt["count"] = tgt.get("count", 1) + l.get("count", 1)
                tgt["turns"] = round(tgt.get("turns", 0) + l.get("turns", 0), 1)
                if l.get("healedAfter") and not tgt.get("healedAfter"):
                    tgt["healedAfter"] = l["healedAfter"]
                for st in l["steps"]:
                    same = next((x for x in tgt["steps"]
                                 if x["opp"] == st["opp"] and x.get("by") == st.get("by")), None)
                    if same: same["n"] = same.get("n", 1) + st.get("n", 1)
                    else: tgt["steps"].append(dict(st))
            else:
                e = dict(l); e["steps"] = [dict(x) for x in l["steps"]]
                e["enc"] = "Walking " + _G.pretty_location(g)
                idx[g] = len(out); out.append(e)
        for e in out:
            if e["kind"] == "wild":
                e["steps"].sort(key=lambda x: -x.get("n", 1))
        return out

    def bench_row(b):
        return [S(b["name"]), [S(x) for x in b["types"]], b["soloTurns"],
                b["soloFailed"], S(b["via"])]

    used_maps = set()

    def leg_maps(merged):
        """The maps a leg crosses, in walk order, with every trainer battle
        pinned to the tile its object event stands on. Numbers count the
        trainer rows in display order, so they match the battle list. A
        scripted battle without an overworld tile (rival ambushes) simply
        gets no pin."""
        seq, marks, n = [], {}, 0
        def add(mp):
            if mp in mapart["maps"]:
                if mp not in seq: seq.append(mp)
                return True
            return False
        for l in merged:
            if l["kind"] == "wild":
                add(l.get("group") or "")
                continue
            n += 1
            e = enc_by_id.get(l["id"]) or {}
            add(e.get("locationRaw") or "")
            tile = mapart["trainers"].get(e.get("trainerConst") or "")
            if tile and add(tile[0]):
                marks.setdefault(tile[0], []).append(
                    [tile[1], tile[2], n, S(l["enc"]), S(l["kind"])])
        used_maps.update(seq)
        # raw folder names, NOT pooled: S() prettifies "Pokemon" -> "Pokémon",
        # which would break the join against the mapart keys
        return [[mp, marks.get(mp, [])] for mp in seq]

    def leg_rows(sec):
        """A section split at its full heals. Each leg carries its own roster
        ledger and log slice, so no PP bar shown ever spans a heal."""
        legs = sec.get("legs") or [{
            # a section with no battles at all still renders as one (empty) leg
            "title": None, "endsAt": None, "rows": len(sec["log"]),
            "battles": sec["battles"], "opposingMons": sec["opposingMons"],
            "wildBattles": sec.get("wildBattles", 0), "turns": sec["turns"],
            "wildTurns": sec.get("wildTurns", 0), "faints": sec["faints"],
            "unanswered": sec["unanswered"], "team": sec["team"]}]
        out, i0 = [], 0
        for leg in legs:
            chunk = sec["log"][i0:i0 + leg["rows"]]; i0 += leg["rows"]
            merged = merge_walks(chunk)
            out.append({
                "ti": S(leg["title"]), "hz": S(leg.get("endsAt")),
                "b": leg["battles"], "om": leg["opposingMons"],
                "wb": leg["wildBattles"], "t": leg["turns"],
                "wt": leg["wildTurns"], "f": leg["faints"],
                "u": leg["unanswered"],
                "team": [member_row(t) for t in leg["team"]],
                "log": [log_row(l) for l in merged],
                "mp": leg_maps(merged),
            })
        return out

    out_sections = {}
    for _mode, _by_starter in secs["sections"].items():
      out_sections[_mode] = {}
      for starter, per_stage in _by_starter.items():
        rows = {}
        for stage, sec in per_stage.items():
            if not sec: continue
            rows[str(stage)] = {
                "lv": sec["level"], "bg": sec["badges"], "b": sec["battles"],
                "wb": sec.get("wildBattles", 0), "wt": sec.get("wildTurns", 0),
                "om": sec["opposingMons"], "t": sec["turns"], "f": sec["faints"],
                "u": sec["unanswered"], "nb": 1 if sec.get("noBattles") else 0,
                "hm": [[S(h["name"]), S(h["hm"]), S(h["by"]), S(h["how"]),
                        S(h["gave"]), S(h["badge"]), S(h.get("why", "")),
                        S(h.get("gaveBack")), 1 if h.get("permanent") else 0]
                       for h in sec.get("hms", [])],
                "kit": [[S(k["name"]), S(k["hm"]), k["from"]]
                        for k in sec.get("hmKit", [])],
                "keep": [[S(k["name"]), k["next"], S(k["nextName"]), k["nextLevel"],
                          k["gap"], k["times"], S(k["why"]), S(k.get("becomes"))]
                         for k in sec.get("keep", [])],
                "bench": [bench_row(b) for b in sec["bench"]],
                "legs": leg_rows(sec),
            }
        out_sections[_mode][starter] = rows

    choices = {}
    for _mode, _cl in secs["choices"].items():
      choices[_mode] = []
      for c in _cl:
        choices[_mode].append({
            "id": c["id"], "label": c["label"], "where": c["where"],
            "note": c.get("note", ""), "best": c["best"],
            "rows": [{
                "name": r["name"], "types": r["types"], "line": r["line"],
                "fromStage": r["fromStage"], "teamPicks": r["teamPicks"],
                "sectionsScored": r["sectionsScored"],
                "soloClears": r.get("soloClears"), "soloRatio": r.get("soloRatio"),
                "totalTurns": r.get("totalTurns"), "gymsCarried": r.get("gymsCarried"),
                "via": r["obtainedVia"],
            } for r in c["rows"]]
        })

    # ---- the map images the legs reference, embedded so the page stays
    # self-contained. Indexed PNGs straight from render_maps.py, keyed by map.
    import build_graph as _G
    art = {}
    for mp in sorted(used_maps):
        meta = mapart["maps"][mp]
        b64 = base64.b64encode(
            open(f"{OUT}/mapart/{mp}.png", "rb").read()).decode()
        art[mp] = [meta["w"], meta["h"], b64, S(_G.pretty_location(mp))]

    payload = {"pool": pool, "stages": stages, "encounters": out_encs,
               "mapart": art,
               "sections": out_sections, "choices": choices,
               "commitments": secs.get("tradeCommitments", {}),
               "tmPlans": secs.get("tmPlans", {}),
               "tmSupply": secs.get("tmSupply", {}),
               "wildLoad": secs.get("wildLoad", {})}
    path = f"{OUT}/payload.json"
    with open(path, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"pool strings: {len(pool)}")
    print(f"encounters: {len(out_encs)}")
    print(f"payload.json: {os.path.getsize(path)/1e6:.2f} MB")

if __name__ == "__main__":
    main()
