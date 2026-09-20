# Do stat-boosting moves beat hit-and-switch?

Measured, not guessed. `setup_study.py`.

## Method

For every (party member × trainer battle) pair in a full run — **1,723 of them** — compare two
lines of play with the *same* Pokémon: best damaging move every turn, versus k turns of stat boost
followed by a sweep with the boost applied and no switching (boosts vanish the moment you switch
out, so a sweep has to be a sweep).

Gen 3 stat stages from `gStatStageRatios`: +1 is ×1.5, +2 ×2, +3 ×2.5, +4 ×3, +5 ×3.5, +6 ×4,
applied as an integer multiply-then-divide on the raw stat.

**Break-even:** if a trainer takes T turns unboosted, k turns of setup pay when
`T > k / (1 − 1/m(k))`. One Swords Dance (×2) needs T > 2; one Sharpen (×1.5) needs T > 3.

## Result

| | |
|---|---|
| pairs examined | 1,723 |
| setup is faster on turns, **ignoring** survival | **2** (0.1%) |
| …and the Pokémon survives to finish | **1** |
| total turns saved across a playthrough | **0.9** |

## Why it fails, in order of weight

1. **75% of the time the recommended Pokémon already one-shots every member of that trainer's
   party** (1,288 of 1,723). You cannot beat one turn per KO. This is a consequence of the app's
   own objective — it picks Pokémon *because* they OHKO.
2. **1,596 of 1,723 pairs have no stat-boosting move in their pool at all.**
3. **Of the 127 that do, 85 have it on the wrong side of the split.** Gen 3 splits
   physical/special by **type**, so Swords Dance only helps Normal/Fighting/Flying/Ground/Rock/
   Bug/Ghost/Poison/Steel coverage. Charizard's best move against the Champion's Pidgeot is
   **Overheat, a special Fire move** — +2 Attack changes its damage by zero.
4. **KOs are discrete.** A ×1.5 that doesn't cross a hit-count threshold saves nothing.

## Where it nearly works

366 of 456 trainer fights clear in three turns or fewer — at or below break-even. The fights that
clear it comfortably are the Champion (11.7t), the Champion rematch (12.3t), Lance rematch (10.3t)
and the rivals (7–9t). Those fail for a different reason: **one Pokémon cannot take five or six
Elite Four Pokémon without fainting**, and the boosts die with it.

## Defensive setup

The more plausible case, and also thin. Of **61 late-game sweeps the party currently loses**, a
defensive boost rescues **2**: Omastar survives Giovanni with 2× Withdraw (5.0 → 7.0 turns) and
Mr. Mime survives Agatha with 3× Barrier (5.7 → 8.7). Both trade ~3 turns for the body — losing
plays under "fewest turns", winning plays under "don't lose a Pokémon".

## The caveat

All measured against parties chosen *because* they one-shot. Setup is the classic answer for a
**solo run or a fixed six** you did not get to tailor per fight. The app's premise is what makes
setup redundant.

## Re-checked against the current engine and the *actual* plan

The numbers above compare setting up to the same Pokémon **not** setting up — a single-mon baseline.
When the ability/stat-stage engine landed, a full setup planner was wired into `sections.py`
(offensive and defensive, every team member tested as a solo sweeper against each trainer) and
compared against what the optimized plan **actually spends** on that fight, not a single-mon
baseline. Across all three starters × both trade modes it found **one** applicable case, saving
**0.01 turns**, and **zero** defensive rescues.

The reason is sharper than "setup ties the plan": it *loses* to it. Gen 3 gives a free switch when a
trainer's Pokémon faints (SHIFT), so the plan answers each of a trainer's Pokémon with a different
one-shotter — one turn per KO. A single Pokémon setting up then sweeping cannot beat one-turn-per-KO.
Concretely, Black Belt Daisuke (Victory Road): the study's best line is Gyarados + Dragon Dance
soloing in **4.0** turns; the plan clears it in **3.1** (Moltres and Charizard OHKOing, switching
free between his party). Defensive setup fails too — the plans rarely lose a Pokémon, and a fight
hard enough to faint one can't be soloed even with a defensive boost.

So the planner does not model setup: it is not that setup is unmodeled, it is that setup is
dominated by the app's own objective. The engine's stat-stage support remains (Intimidate uses it);
the setup integration was removed as inert. `setup_study.py` is the standing analysis.
