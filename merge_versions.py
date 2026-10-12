#!/usr/bin/env python3
"""Merge the per-version payloads into one.

build.sh runs the whole pipeline once per game version (LGMAX_VERSION=lg|fr),
each producing data/payload_<v>.json. This folds them into a single
data/payload.json the page inlines: the heavy, version-identical assets (map art,
front sprites, trainer pics, overworld graphics) are shared once; everything
pool-indexed and solve-derived -- the string pool itself, encounters, sections,
teams, the Dex, economy, trades, grind -- is nested under byVersion[<v>] with
that version's own pool, exactly how the app's _vd()/VER switch reads it.
"""
import json, os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# Pool-independent assets, identical (or a clean union) across versions. Everything
# else is pool-indexed and stays per version.
SHARED = ("mapart", "sprites", "tpics", "ow", "owByMap")


def main():
    versions = [v for v in ("lg", "fr") if os.path.exists(f"{OUT}/payload_{v}.json")]
    if not versions:
        raise SystemExit("merge_versions: no data/payload_<v>.json to merge")
    pv = {v: json.load(open(f"{OUT}/payload_{v}.json")) for v in versions}

    out = {}
    # shared assets: union the per-version dicts (each ships only what its own run
    # touches -- FireRed's Scyther sprite, LeafGreen's Pinsir, each version's maps)
    for k in SHARED:
        merged = {}
        for v in versions:
            a = pv[v].get(k)
            if isinstance(a, dict):
                merged.update(a)
        out[k] = merged
    # everything else stays per version, each with its own string pool
    out["byVersion"] = {v: {k: val for k, val in pv[v].items() if k not in SHARED}
                        for v in versions}
    out["versions"] = versions
    out["defaultVersion"] = "lg" if "lg" in versions else versions[0]

    with open(f"{OUT}/payload.json", "w") as f:
        json.dump(out, f, separators=(",", ":"))
    mb = os.path.getsize(f"{OUT}/payload.json") / 1e6
    print(f"merged payload.json: {mb:.2f} MB  (versions: {', '.join(versions)})")


if __name__ == "__main__":
    main()
