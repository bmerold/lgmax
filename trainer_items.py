"""How a trainer's held-back items (Full Restores, Hyper Potions, ...) drag a fight out.

Every rule here is the trainer AI's own, from src/battle_ai_switch_items.c:

  * AI_TrySwitchOrUseItem runs only in trainer battles, at action selection, so a
    used item replaces the foe's attack that turn (items act before moves).
  * ShouldUseItem walks the trainer's item slots in order and uses the first one
    whose rule fires:
      - Full Restore (GetAI_ItemType checks ITEM_FULL_RESTORE by id): only when
        the active Pokemon is below 1/4 of its max HP and not fainted. A foe you
        knock out from above 25% never gets to use it.
      - an HP item (ITEM4_HEAL_HP): when below 1/4 max HP, OR when the HP it is
        missing exceeds the item's heal amount (itemEffects[6]).
      - status-only items (Full Heal) cure conditions the 1v1 model does not
        track, so they are skipped here.
  * Slot i > 0 is skipped while `validMons > (itemsNo - i) + 1` -- a trainer with
    two Full Restores keeps the second until it is down to two Pokemon. itemsNo is
    never decremented (a used slot is just zeroed), so the gate uses the original
    count.

The simulators are expected-value models, so this is too. A foe that the player
needs `t` expected hits to KO takes per-hit damage of about 1/t of its HP; the HP
it is left with before the finishing hit is roughly uniform on (0, 1/t] (the
overshoot of a renewal process). That gives the chance the foe ever sits in an
item's trigger window, and how much HP the item hands back, without simulating
individual rolls.
"""
import math, os, re
import engine as E

FULL = "FULL"     # ITEM6_HEAL_HP_FULL: heals to max (AI threshold byte is 255)

def _parse_effects():
    """item name -> {"kind": "full_restore"|"heal_hp"|"other", "amount": int}
    from src/data/pokemon/item_effects.h."""
    src = open(os.path.join(E.REPO, "src/data/pokemon/item_effects.h")).read()
    blocks = {}
    for name, body in re.findall(r"static const u8 (sItemEffect_\w+)\[\d*\]\s*=\s*\{(.*?)\};",
                                 src, re.S):
        heal = "ITEM4_HEAL_HP" in body
        m6 = re.search(r"\[6\]\s*=\s*([A-Z0-9_]+)", body)
        amt = None
        if m6:
            v = m6.group(1)
            amt = FULL if v in ("ITEM6_HEAL_HP_FULL",) else (int(v) if v.isdigit() else None)
        blocks[name] = (heal, amt)
    out = {}
    for item, eff in re.findall(r"\[(ITEM_\w+)\s*-\s*ITEM_POTION\]\s*=\s*(sItemEffect_\w+)", src):
        heal, amt = blocks.get(eff, (False, None))
        name = E.ITEMS.get(item, {}).get("name", item)
        if item == "ITEM_FULL_RESTORE":
            out[name] = {"kind": "full_restore", "amount": FULL}
        elif heal and amt is not None:
            out[name] = {"kind": "heal_hp", "amount": amt}
        else:
            out[name] = {"kind": "other", "amount": None}
    return out

EFFECTS = _parse_effects()

def is_hp_item(name):
    return EFFECTS.get(name, {}).get("kind") in ("full_restore", "heal_hp")

def _trigger(item, t, foe_hp, hp_frac=1.0, t_full=None):
    """(chance the foe ever uses this item on one pass toward a KO, expected
    player hits the heal hands back). t = the player's expected hits to KO it
    from where it stands now, hp_frac = its current share of max HP (below 1 when
    a fainted member left it damaged and the next one picks it up). t_full is
    the hits to KO it from FULL HP: per-hit damage must come from that, since a
    sliver of HP left says nothing about how hard each hit lands."""
    if foe_hp <= 0 or hp_frac <= 0:
        return 0.0, 0.0
    eff = EFFECTS[item]
    d = 1.0 / t_full if t_full else hp_frac / max(t, 1e-9)   # per hit, share of max HP
    if eff["kind"] == "full_restore":
        window = 0.25
    else:
        a = 255 if eff["amount"] == FULL else eff["amount"]
        window = max(0.25, 1.0 - min(1.0, a / foe_hp))
    if hp_frac <= window:
        # already inside the window: it heals on its next action, before any
        # move -- SetActionsAndBattlersTurnOrder (src/battle_main.c) queues every
        # item and switch ahead of every move, whatever the speeds, and the AI's
        # below-1/4 rule has no roll -- so not even a faster one-hit finish beats it
        p = 1.0
        missing = 1.0 - hp_frac
        heal = missing if (eff["kind"] == "full_restore" or eff["amount"] == FULL) \
               else min(eff["amount"] / foe_hp, missing)
        return p, heal / d
    if t <= 1.0:
        return 0.0, 0.0                    # one-shot: no non-fainted turn to heal on
    p_not_ohko = min(1.0, t - 1.0)
    if eff["kind"] == "full_restore":
        window = 0.25                      # residual HP share that fires it
        missing = 1.0 - window / 2         # fires around 1/8 HP left, heals to full
        heal = missing
    else:
        amt = eff["amount"]
        a = 255 if amt == FULL else amt    # the AI compares against the raw byte
        f = min(1.0, a / foe_hp)
        window = max(0.25, 1.0 - f)        # either rule fires inside this window
        # the "missing > amount" rule fires on the first hit that crosses it
        missing = (min(1.0, f + d / 2) if 1.0 - f > 0.25 else 1.0 - 0.125)
        heal = missing if amt == FULL else min(f, missing)
    p = p_not_ohko * min(1.0, window / d)
    return p, heal / d

def after_heal(slots, hp, max_hp, valid_mons):
    """Expected HP of a foe left at `hp` once its next action comes round: the
    first slot whose ShouldUseItem rule fires is used before any move (items act
    first), so a foe parked in a heal window is not really that low. Ranks the
    plans that lose a member by what they truly leave standing."""
    if hp <= 0:
        return 0.0
    n, want, gain = len(slots), 1.0, 0.0
    for i, (item, left) in enumerate(slots):
        if left <= 0 or not is_hp_item(item):
            continue
        if i and valid_mons > (n - i) + 1:
            continue                       # ShouldUseItem's held-back-slot gate
        eff = EFFECTS[item]
        if eff["kind"] == "full_restore":
            fires, healed = hp < int(max_hp) // 4, max_hp
        else:
            a = 255 if eff["amount"] == FULL else eff["amount"]
            fires = hp < int(max_hp) // 4 or max_hp - hp > a
            healed = max_hp if eff["amount"] == FULL else min(max_hp, hp + eff["amount"])
        if not fires:
            continue
        q = want * left                    # this slot still holds its item
        gain += q * (healed - hp)
        want *= 1.0 - left                 # emptied: the next matching slot is tried
        if want <= 0:
            break
    return hp + gain

def choose_item(slots, left, hp, max_hp, valid_mons):
    """The slot ShouldUseItem uses this turn on a foe at `hp`, or None: the
    first slot, in order, that still holds an HP item, passes the held-back-slot
    gate, and whose rule fires (Full Restore below 1/4; an HP item below 1/4 or
    missing more than it heals). `left[i]` says slot i is still full. The
    random-roll replays (rolls.py) use it; the expected model is `drag`."""
    if hp <= 0:
        return None
    n = len(slots)
    for i, (item, _) in enumerate(slots):
        if not left[i] or not is_hp_item(item):
            continue
        if i and valid_mons > (n - i) + 1:
            continue
        eff = EFFECTS[item]
        if eff["kind"] == "full_restore":
            fires = hp < max_hp // 4
        else:
            a = 255 if eff["amount"] == FULL else eff["amount"]
            fires = hp < max_hp // 4 or max_hp - hp > a
        if fires:
            return i
    return None

def heal_to(item, hp, max_hp):
    """The foe's HP after using `item` at `hp`."""
    eff = EFFECTS[item]
    if eff["kind"] == "full_restore" or eff["amount"] == FULL:
        return float(max_hp)
    return float(min(max_hp, hp + eff["amount"]))

def new_slots(items):
    """Per-battle item state: [name, chance still unused] in trainer slot order."""
    return [[it, 1.0] for it in items]

def drag(slots, t, foe_hp, valid_mons, hp_frac=1.0, t_full=None):
    """Expected cost of the trainer's items against one foe.

    Returns (extra player turns, extra foe attacks, uses), where uses[i] is the
    expected consumption of slot i (pass it to `spend`). Each heal hands back
    `heal` hits of work, and the foe spends that turn healing instead of
    attacking, so its extra attacks are one fewer than the extra turns."""
    n = len(slots)
    uses = [0.0] * n
    extra_t = extra_a = 0.0
    want = 1.0                 # chance the foe reaches a heal point with this slot next
    for i, (item, left) in enumerate(slots):
        if left <= 0 or not is_hp_item(item):
            continue
        if i and valid_mons > (n - i) + 1:
            continue           # ShouldUseItem's held-back-slot gate
        p, heal = _trigger(item, t, foe_hp, hp_frac, t_full)
        q = want * left * p
        if q <= 0:
            continue
        uses[i] = q
        extra_t += q * heal
        extra_a += q * (heal - 1.0)
        # an empty slot leaves the same trigger pending for the next slot; a used
        # one sends the foe back up, from where it may need healing again
        want = want * (1.0 - left) + q
    return extra_t, extra_a, uses

def spend(slots, uses):
    for s, u in zip(slots, uses):
        s[1] = max(0.0, s[1] - u)
