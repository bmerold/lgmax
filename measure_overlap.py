"""Summarize data/measure/*.json (written by a LGMAX_MEASURE=1 build; see measure.py).

For each kind of computation: how many were performed across every worker, how
many distinct inputs that was, and so how much a build-wide input-keyed cache
would have saved. Also broken down by what the duplicates are shared across.
"""
import collections, glob, json, os, sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "measure")
files = sorted(glob.glob(os.path.join(OUT, "*.json")))
if not files:
    sys.exit("no data/measure/*.json -- run a build with LGMAX_MEASURE=1 first")

# tag = <version>__<pass>_<starter>_<trade>
runs = {}
for path in files:
    tag = os.path.basename(path)[:-5]
    runs[tag] = json.load(open(path))

kinds = sorted({k for r in runs.values() for k in r})
print(f"{len(runs)} worker runs: {', '.join(sorted(runs))}\n")
print(f"{'kind':10} {'performed':>11} {'distinct':>10} {'duplicate':>10}")
for kind in kinds:
    total = sum(len(r.get(kind, [])) for r in runs.values())
    uniq = len({h for r in runs.values() for h in r.get(kind, [])})
    dup = 1 - uniq / total if total else 0.0
    print(f"{kind:10} {total:11,} {uniq:10,} {dup:9.1%}")

def split(tag):
    v, rest = tag.split("__", 1)
    p, starter, trade = rest.split("_", 2)
    return {"version": v, "pass": p, "starter": starter, "trade": trade}

# What dimension do duplicates cross? For each hash, the set of values each
# dimension takes among the runs that computed it; a hash seen under 2+ values of
# a dimension is redundancy along that dimension.
print("\nshare of performed work whose inputs recur across >1 value of a dimension")
dims = ["version", "pass", "starter", "trade"]
for kind in kinds:
    where = collections.defaultdict(lambda: {d: set() for d in dims})
    total = 0
    for tag, r in runs.items():
        meta = split(tag)
        for h in r.get(kind, []):
            total += 1
            for d in dims:
                where[h][d].add(meta[d])
    parts = []
    for d in dims:
        n = sum(1 for tag, r in runs.items() for h in r.get(kind, [])
                if len(where[h][d]) > 1)
        parts.append(f"{d} {n / total:5.1%}" if total else f"{d} -")
    print(f"  {kind:10} " + "  ".join(parts))
