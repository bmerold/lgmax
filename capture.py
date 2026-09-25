#!/usr/bin/env python3
"""Wild-capture odds, transcribed from the ROM's Cmd_handleballthrow.

Pure functions, no I/O: the caller passes a species' catch rate (data/
species.json's `catchRate`, straight from gSpeciesInfo), the ball, the target's
HP fraction and any status, and gets back the per-throw catch probability the
game actually rolls. Every constant here comes from the decompilation:

  - the capture math: src/battle_script_commands.c, Cmd_handleballthrow
    (`odds = (catchRate * ballMult / 10) * (3*maxHP - 2*hp) / (3*maxHP)`, then
    the sleep/freeze x2 and poison/burn/paralysis/toxic x1.5 status terms);
  - four successful shakes catch: include/battle_controllers.h enumerates
    BALL_3_SHAKES_SUCCESS = 4, and the loop catches only when shakes reaches it,
    so P(catch) = (b / 65536) ** 4 with b = 1048560 / Sqrt(Sqrt(16711680/odds));
  - ball multipliers: sBallCatchBonuses[] (src/battle_script_commands.c:808).

FireRed/LeafGreen has no critical capture and no generation catch bonus, so this
is the whole model. It is deliberately independent of the rest of the pipeline
(one job per module) and fed values by its caller, like walking.py.
"""
import math

# sBallCatchBonuses[], in tenths (1.0x Poke, 1.5x Great, 2.0x Ultra, 1.5x
# Safari). A Kanto run realistically throws these plus the single Master Ball
# (guaranteed) from Silph Co.; the rest of gen-3's balls are Sevii/post-game.
BALL_MULT = {"Poke": 10, "Great": 15, "Ultra": 20, "Safari": 15}
MASTER = "Master"
# The ball order a run should prefer, best first, among the always-buyable trio.
BALLS_BY_STAGE = ["Ultra", "Great", "Poke"]

# Status multipliers, applied after the ball/HP term. Sleep and freeze double the
# odds (the strongest lever); the residual statuses give 1.5x. Only sleep and
# paralysis are practical to inflict and hold on a wild target in this model.
STATUS_MULT = {"none": 1.0, "sleep": 2.0, "freeze": 2.0,
               "poison": 1.5, "burn": 1.5, "paralysis": 1.5}

# A fixed HP scale so the ROM's integer flooring in the HP term is reproduced;
# the term depends only on the HP fraction, so the exact max HP does not matter.
_HP_SCALE = 1000


def _isqrt(n):
    """The ROM's Sqrt() -- integer square root."""
    return math.isqrt(int(max(0, n)))


def capture_value(catch_rate, ball, hp_frac=1.0, status="none"):
    """The ROM's `odds` (a) before the shake conversion, as an integer. A value
    over 254 is an automatic catch; the shake check runs otherwise."""
    mult = BALL_MULT.get(ball, 10)
    hp = max(1, round(min(1.0, max(0.0, hp_frac)) * _HP_SCALE))
    a = (catch_rate * mult // 10) * (3 * _HP_SCALE - 2 * hp) // (3 * _HP_SCALE)
    sm = STATUS_MULT.get(status, 1.0)
    if sm == 2.0:
        a *= 2
    elif sm == 1.5:
        a = a * 15 // 10
    return a


def catch_chance(catch_rate, ball, hp_frac=1.0, status="none"):
    """Probability in [0, 1] of catching in a single throw. The Master Ball is
    always 1.0; hp_frac is current HP / max HP (1.0 at full, ~0.0 at 1 HP)."""
    if ball == MASTER:
        return 1.0
    a = capture_value(catch_rate, ball, hp_frac, status)
    if a > 254:
        return 1.0
    if a <= 0:
        return 0.0
    b = 1048560 // _isqrt(_isqrt(16711680 // a))
    return (b / 65536) ** 4


def expected_balls(catch_rate, ball, hp_frac=1.0, status="none"):
    """Mean throws to catch (1 / per-throw chance); None if it can't be caught."""
    p = catch_chance(catch_rate, ball, hp_frac, status)
    return (1.0 / p) if p > 0 else None


def best_ball(catch_rate, balls, hp_frac=1.0, status="none"):
    """The best-odds ball from `balls` (ties broken by BALLS_BY_STAGE order)."""
    order = {b: i for i, b in enumerate(BALLS_BY_STAGE)}
    return max(balls, key=lambda b: (catch_chance(catch_rate, b, hp_frac, status),
                                     -order.get(b, 99)))
