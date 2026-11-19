"""One plan segment played out turn by turn with real random rolls.

The planner (sections.plan_vs) prices fights by their chances; this module is
the other half: it plays a chosen segment the way the game would, so the League
tab can replay a six many times and report how often it clears
(league_sets.clear_rate). Every rule here is the battle engine's own:

  * Each use rolls accuracy, then one of the move's damage outcomes (the 16
    rolls x crit, as engine.move_profile builds them, binned like the planner's
    curves). A miss deals nothing.
  * SetActionsAndBattlersTurnOrder (src/battle_main.c) runs item and switch
    actions before any move, then moves by Speed, so a trainer's Full Restore
    always goes first and a switch-in eats the foe's hit that turn.
  * The trainer AI picks its item with ShouldUseItem (src/battle_ai_switch_items.c)
    -- trainer_items.choose_item -- on the foe's actual HP, one slot per turn.
  * Self-Destruct / Explosion faint the user whether they hit or miss
    (setatkhptozero, data/battle_scripts_1.s); charge moves spend a turn winding
    up and Hyper Beam a turn recharging unless the target fainted.
  * A traded Pokemon over its obedience cap loafs on part of its turns, and a
    secondary-effect status (paralysis, flinch, burn, sleep) is applied at the
    per-turn rate engine.status_tempo gives it: the game's state machine
    collapsed to its average, the one approximation left in here.

The foe always uses its best move against the member (sections.threat), as the
planner assumes.
"""
import bisect
import engine as E
import trainer_items as TI

TURN_CAP = 60      # a segment still going after this many turns is a stall


def _sampler(src):
    """(values, cumulative chances) for a profile/threat's binned per-hit
    damage, cached on the dict like the planner's KO curves."""
    s = src.get("_cum")
    if s is None:
        vals, cum, run = [], [], 0.0
        for d, p in sorted(src["dist"].items()):
            run += p; vals.append(d); cum.append(run)
        s = src["_cum"] = (vals, cum)
    return s


def hit(rng, src, acc=None):
    """Damage of one use: 0 on a miss, else one sampled roll."""
    a = src["acc"] if acc is None else acc
    if a < 100 and rng.random() * 100.0 >= a:
        return 0
    vals, cum = _sampler(src)
    if not vals:
        return 0
    i = bisect.bisect_left(cum, rng.random() * cum[-1])
    return vals[min(i, len(vals) - 1)]


def play(rng, m, p, thr, opp, foe_hp, target, switched, faster, slots, valid_mons,
         selfko, tempo=None, thr_setup=None, setup_turns=0, heal=None, heals=0,
         kit_amount=0.0):
    """Play one segment: member `m` uses profile `p`'s move on the foe (at
    foe_hp) until the foe is at or below `target` HP, the member falls, or a
    stall. `tempo` is (our act share, foe act share, foe burn, foe chip, our
    chip, our burn): the status_tempo averages both ways. A League line first spends
    `setup_turns` (its X items / setup moves; the foe hits it at thr_setup),
    and may heal up to `heals` times for `kit_amount` HP each when the foe's
    top roll could drop it. Returns a dict with the outcome; the member and
    the trainer's slots are not touched (run_section books them)."""
    maxfoe = opp["stats"]["hp"]
    hp, foe = float(m.hp), float(foe_hp)
    turns, uses, heals_used, setup = 0, 0, 0, 0
    items = [0.0] * len(slots)
    left = [s[1] >= 0.5 for s in slots]       # a slot still holds its item
    our_act, foe_act, burn, foe_chip, our_chip, our_burn = tempo or (1.0, 1.0, 0.0, 0.0, 0.0, 0.0)
    eff = E.MOVES[p["move"]]["effect"]
    charging = recharging = False
    out_of_pp = False
    phys = thr.get("cat") == "PHYSICAL"
    our_phys = E.MOVES[p["move"]]["category"] == "PHYSICAL"

    def foe_turn(cur_thr):
        """The foe's action this turn: an item if ShouldUseItem fires, else (if
        status lets it act) its best move. Items act first, so the caller runs
        this before our move when it is an item."""
        i = TI.choose_item(slots, left, foe, maxfoe, valid_mons)
        if i is not None:
            return ("item", i)
        return ("move", cur_thr)

    def take_hit(cur_thr):
        nonlocal hp
        if rng.random() >= foe_act or not cur_thr.get("const"):
            return
        d = hit(rng, cur_thr)
        if phys and burn > 0 and rng.random() < burn:
            d //= 2
        hp -= d

    def use_item(i):
        nonlocal foe
        foe = TI.heal_to(slots[i][0], foe, maxfoe)
        left[i] = False
        items[i] += 1.0

    # the switch-in turn: we spend it coming in, the foe acts
    if switched:
        turns += 1
        kind, what = foe_turn(thr_setup or thr)
        if kind == "item": use_item(what)
        else: take_hit(what)
    # League setup: each X item / setup move is a turn the foe hits back
    for _ in range(setup_turns):
        if hp <= 0: break
        turns += 1; setup += 1            # the X item goes before the foe's move
        kind, what = foe_turn(thr_setup or thr)
        if kind == "item": use_item(what)
        else: take_hit(what)
    while hp > 0 and foe > target and turns < TURN_CAP:
        turns += 1
        # our potion is an item action too: it goes before any move, so heal
        # when the foe's top roll could finish us and the bag allows
        healing = heals_used < heals and hp <= thr.get("max", 0)
        if healing:
            hp = min(float(m.maxhp), hp + kit_amount)
            heals_used += 1
        kind, what = foe_turn(thr)
        if kind == "item":
            use_item(what)                    # items go before every move
        foe_moves = kind == "move"
        if foe_moves and not faster:
            take_hit(thr)
            if hp <= 0: break
        if healing:
            pass                              # the turn went on the potion
        elif recharging:
            recharging = False
        elif rng.random() * max(1.0, m.obey) >= 1.0 or rng.random() >= our_act:
            pass                              # loafing, or held by its status
        elif eff in E.CHARGE_EFFECTS and not charging:
            charging = True
        else:
            charging = False
            if m.pp.get(p["move"], 0) - uses < 1:
                out_of_pp = True
                break                         # out of PP: the planner picks again
            uses += 1
            d = hit(rng, p)
            if our_phys and our_burn > 0 and rng.random() < our_burn:
                d //= 2                       # a burned attacker's physical hits halve
            foe -= d
            if selfko:
                hp = 0.0
            if eff in E.RECHARGE_EFFECTS and foe > 0:
                recharging = True
        if foe <= target or hp <= 0:
            break
        if foe_moves and faster:
            take_hit(thr)
        # residual: our burn/poison on it, its on us
        foe -= foe_chip * maxfoe
        hp -= our_chip * m.maxhp
    return {"done": foe <= target, "foe": max(0.0, foe), "hp": max(0.0, hp),
            "turns": float(turns), "uses": uses, "items": items,
            "heals": heals_used, "setup": setup,
            "stalled": hp > 0 and foe > target and not out_of_pp}
