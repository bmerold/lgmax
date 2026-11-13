"""Opt-in overlap measurement (LGMAX_MEASURE=1): how much of the build's solving is
the same computation repeated across choice combinations (version x starter x
trade mode x pass)?

Each forked per-starter worker records a short hash of the INPUTS of every real
computation it performs -- a section solve, and every cache MISS in the pure
per-fight leaves (sections.profile / sections.threat), whose keys fully determine
their outputs. Within a worker those leaves are already memoized, so every record
is work actually done; a hash that shows up in several workers is work a
build-wide cache would do once. measure_overlap.py totals it up.

Off by default: with LGMAX_MEASURE unset every hook is a no-op.
"""
import collections, hashlib, json, os

ON = bool(os.environ.get("LGMAX_MEASURE"))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "measure")
_rec = collections.defaultdict(list)

def canon(x):
    """A deterministic, hashable rendering (sets and dicts sorted)."""
    if isinstance(x, dict):
        return tuple(sorted((canon(k), canon(v)) for k, v in x.items()))
    if isinstance(x, (set, frozenset)):
        return tuple(sorted(canon(v) for v in x))
    if isinstance(x, (list, tuple)):
        return tuple(canon(v) for v in x)
    if isinstance(x, float):
        return round(x, 6)
    return x

def note(kind, key):
    if ON:
        _rec[kind].append(hashlib.blake2b(repr(canon(key)).encode(),
                                          digest_size=8).hexdigest())

def reset():
    _rec.clear()

def dump(tag):
    """Write this worker's records as data/measure/<version>__<tag>.json."""
    if not ON:
        return
    os.makedirs(OUT, exist_ok=True)
    v = os.environ.get("LGMAX_VERSION", "lg")
    with open(os.path.join(OUT, f"{v}__{tag}.json"), "w") as f:
        json.dump(dict(_rec), f)
