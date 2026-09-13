# Model, assumptions, and where things live

## Source of truth

Everything is extracted from the **pret/pokefirered decompilation**, not from wikis: base stats,
movesets, learnsets, TM/HM compatibility, evolutions, the type chart, trainer rosters, wild
encounter tables, item data, in-game healing spots, in-game trades (the `#if defined(LEAFGREEN)`
branches), Game Corner prizes, the VS Seeker rematch table, map tile attributes and object events,
the obedience roll, and the damage formula itself.

## Chosen ruleset

- **Objective:** fewest expected turns to KO, tie-broken by damage taken.
- **Levels:** natural playthrough, no grinding. One player level per section.
- **No in-battle items** on the player's side, ever. Trainer Full Restores ARE counted.
- Player specimen: 15 IVs, 0 EVs, neutral nature.
- LeafGreen only. Trade evolutions sit behind the trading toggle.

## Legality rules (`constraints.py`)

1. **One Pokémon per evolution line.** Charmander and Charmeleon are the same Pokémon at two
   points in time.
2. **Branching lines collapse to their shared root** — which is what makes the three Eeveelutions
   mutually exclusive, and Hitmonlee/Hitmonchan (root Tyrogue) likewise.
3. **Explicit one-of groups** for lines gated behind a single item: the starter, the Mt. Moon
   fossil (Helix → Omanyte *or* Dome → Kabuto).
4. **Run-wide commitments.** Rules 1–3 only constrain a single list. Because each section was
   solved independently, a run could spend its one Eevee on a Vaporeon in Saffron City and a
   Jolteon in the gym next door. So `sections.py` runs **two passes**: pass 1 solves freely and
   feeds the Choices evaluation; each one-time group's winner is locked into
   `data/commitments.json`; pass 2 re-solves with the losers removed. `optimize.py` reads the same
   file so the encounter rankings agree. Eevee itself is deliberately not a commitment — holding
   an unevolved Eevee commits to nothing yet.
5. **Outgrown forms are dropped.** A species whose *level* evolution is already reachable can't be
   recommended. Stone and trade evolutions are exempt: declining a Thunder Stone to keep Pikachu's
   learnset is a real decision; staying a Bulbasaur past 16 is not.
6. **Game Corner prizes are not exclusive** — coins are grindable.

## Section simulation

- HP and PP carry across every battle in a section and reset only at a Pokémon Center or an
  in-game healing spot (see `04-healing-model.md`).
- **Wild battles are in the PP load** (see `03-wild-battle-load.md`) — 357 across the run,
  interleaved between the trainers rather than front-loaded.
- Party members are added greedily; it stops once the party clears the section and another body
  wouldn't meaningfully speed it up, which is why most sections need fewer than six.
- Move slots are chosen per section, so a Pokémon may list fewer than four moves.
- A section with no trainer battles is still a section you walk: the Safari Zone and Cerulean Cave
  get a real party solved on their wild load.

## Switching model

- **Before a trainer battle** the lead is free — you can reorder your party outside battle.
- **Before a wild battle** you cannot see what is coming, so each map gets **one designated lead**
  chosen blind against the map's whole weighted encounter table. The ideal route Pokémon is the
  well-rounded one that one-shots everything, not a specialist. The lead rotates only when it
  faints, runs dry, or gets too low for another average encounter.
- **Switching mid-battle** costs a turn and a hit, both charged. After a faint the next send-out
  is free, as in the real game.
- **Mid-opponent handovers** are searched: one Pokémon softens, a second finishes. The prefix is
  priced off the same expected-turn DP as the whole fight (`E[H] − E[h]`). Because expected turns
  are very nearly **linear** in remaining HP, a handover is essentially never *faster* than the
  best solo — the switch turn is pure tax. What it buys is **survival** and **PP reach**.

## Obedience (`IsMonDisobedient`)

Fires **only** for a Pokémon with someone else's OT, which in a normal playthrough means the four
in-game trades: Jynx, Mr. Mime, Farfetch'd, Lickitung. The cap is 10, rising to 30/50/70 at the
second, fourth and sixth badges and vanishing at the eighth. Above it the ROM rolls
`calc = (level + cap) * (Random() & 255) >> 8` and obeys only when `calc < cap`.
`engine.obedience_odds` enumerates all 256 values rather than approximating.

The expected-turn curve is stretched by `1 / P(obey)`. PP is not charged for a disobedient turn.
**It bites in exactly one section**: Erika's gym, level 31 on three badges against a cap of 30,
where a traded Jynx obeys 49% of the time. Before this was modelled Jynx was the recommended
answer to that gym; it is now Kadabra.

## Engine correctness

Verified against an independent transcription of Smogon's `gen3.ts` over ~10,000 randomized
matchups: **0 mismatches** after fixes. Bugs found and fixed along the way:

1. Explosion/Self-Destruct did not halve the target's Defense (~2× under-reported).
2. The minimum-1 damage clamp was a no-op.
3. The charmap regex dropped the escaped apostrophe, corrupting the deterministic trainer-nature
   hash for every party containing Farfetch'd.
4. Razor Wind was given a high crit ratio it does not have in FRLG.
5. Modifier ordering in `CalculateBaseDamage` did not match the C under truncation.
6. Variable-power moves (Return, Hidden Power, Low Kick, Magnitude, Super Fang, Flail, Eruption,
   Rollout, Fury Cutter, Present, Twineedle, Triple Kick) were scored at placeholder power 1.
7. Charge moves and Hyper Beam's recharge now cost their real turns.
8. The evolution parser dropped multi-branch entries (Eevee, Gloom, Poliwhirl) — 172 lines, was 164.
9. VS Seeker rematches were classified by numeric suffix, catching ordinary numbered trainers.
10. 17 named trainers that exist in the table but are referenced by no script are excluded.
11. Only the other starters' *base forms* were excluded from a run's pool, so Ivysaur and
    Charmeleon could be recommended to someone who picked a different starter.
12. Ranked lists could offer two members of one line. Collapsed by line root.
13. Sections were solved independently, so a run used all three Eeveelutions. Two-pass commitments.
14. A healing spot fired once per trainer standing on it, refilling Pokémon Tower four times.
15. Gym TMs were counted twice; retry branches double-counted TM42.
16. `giveitem_msg` takes its text argument first, so a naive regex found no TM pickups at all.
17. Three Pokémon tied at exactly 2.00 turns and the pick came down to dict-iteration order. Ties
    now resolve by HP lost, then toward Pokémon the rest of the run already uses, then by name.
18. The bench listed a Pokémon that Self-Destructs and leaves the fight unfinished.
19. The starter was force-added to every section as its **base form**, putting a level-5 Charmander
    on the Champion's doorstep.
20. The continuity tiebreak preferred the Bulbasaur a run was carrying over the Ivysaur it became.
21. **Obedience was not modelled at all.**
22. **HMs were not considered at all** — 17 of 22 sections recommended a party that could not cross
    the section it was recommended for.
23. Sections with no trainer battles returned an empty recommendation, dropping the Safari Zone and
    Cerulean Cave.
24. **`TRADE_ITEM` evolutions ignored the item**, putting Porygon2 in parties from section 15.

## Progression facts worth remembering

- **Obedience caps come only from badges 2/4/6/8** (10 → 30 → 50 → 70 → unlimited), and only for
  traded Pokémon. Tables showing 20/40/80 are wrong.
- **FRLG gives the PLAYER a 10% stat boost from badges**: Boulder → Attack, Soul → Defense,
  Volcano → Sp. Atk and Sp. Def.
- **The physical/special split is by TYPE, not by move.** Jynx's Ice Punch runs off Special Attack.
- **TMs are single-use**; HMs are not. 43 TMs exist in exactly one copy.
- **Only Mr. Mime and Starmie can learn TM24 Thunderbolt** among the late-game Psychics — not
  Kadabra, Hypno, Exeggutor or Jynx. Thunderbolt is super-effective on 20 distinct Elite Four
  opponents, Lance's Gyarados at 4×.
- **The Sevii Islands 1–3 trip is triggered by beating Blaine.** HM06 Rock Smash is post-Blaine;
  HM07 Waterfall is post-game.
- **No evolution stones are Game Corner prizes in FRLG.** Moon Stone is the only non-purchasable one.
- **Magmar, Ponyta and Rapidash are Sevii-locked in LeafGreen** — so at Erika the only Fire types
  in the game are Vulpix/Ninetales, Flareon, and your own Charmeleon.
- **Charizard does not exist at Erika.** Charmeleon evolves at 36; Erika is level 31.
- Rival trainer constants are named for the **rival's** starter, so `TRAINER_CHAMPION_FIRST_CHARMANDER`
  is the team you face if **you** picked Bulbasaur.

## Findings

- The three starters finish within ~1% of each other on total turns.
- **Game Corner prize: Abra** is the standout — Kadabra earns a party slot in 10 of 25 sections.
- Haunter solos all 17 battles of Pokémon Tower, burning 28 of its 30 Thunderbolt PP.
- Most sections need 1–3 Pokémon. The exceptions are Silph Co. and the Elite Four.
- Route sections are where the party is widest, and it is **PP**, not damage, that forces it.
- **Erika's gym is not a Fire-type puzzle.** Grass/Poison makes Fire and Ice both exactly 2×.
- **Mr. Mime beats Kadabra at the Elite Four** on movepool and bulk — Kadabra cannot learn TM24 at
  all, and at level 57 Mr. Mime has 150 Special Defense to Kadabra's 93.

## Known, deliberate limitations

- No stat-stage modelling on either side (measured player-side cost: ~1 turn; see `06-`).
- No status effects, no held-item type boosts.
- Trainer AI is "their single best damaging move", close to but not identical to
  `AI_SCRIPT_CHECK_BAD_MOVE`.
- Charge/recharge moves are charged their real turns but deduct PP per turn rather than per use.
- The post-game section lumps Sevii 4–7 and the Elite Four rematch into one continuous run with no
  healing, so it wipes. A sectioning artifact, not a verdict.
- The party builder is greedy, so individual section numbers are not monotonic in the size of the
  candidate pool.
- Whether a given cut tree is on the critical path or is an optional shortcut is not determined.
