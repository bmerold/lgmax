#!/usr/bin/env python3
"""Gen 3 (FireRed/LeafGreen) abilities — one function per ability.

Every ability in `include/constants/abilities.h` (78 entries, ABILITY_NONE ..
ABILITY_AIR_LOCK) has a function here, whether or not it matters to this
project's battle model. That model is deliberately narrow: single battles, no
switching, no weather, no held items beyond the handful CalculateBaseDamage
reads, damage/turn statistics rather than a turn-by-turn state machine. An
ability whose only effect lives outside that model (weather, switch-in tricks,
overworld perks, PP pressure) still gets a function — it returns a descriptor of
what the ability really does in the ROM, tagged `unmodeled`, so the registry is
complete and future work has an obvious home.

Each function returns a small **descriptor** dict; the engine reads descriptors
through the consumer helpers at the bottom (`negates_damage`, `blocks_crit`,
`accuracy_mult`, `immune_to_status`, …). Keeping the effect *data* in the
per-ability function and the *interpretation* in one place each avoids scattering
`if ability == …` across the engine, and lets `verify.py` prove the set of
functions matches the decomp.

Sources, all in `$LGMAX_POKEFIRERED`:
  - the ability list                    include/constants/abilities.h
  - type immunity / Wonder Guard        src/battle_script_commands.c (TypeCalc)
  - Volt/Water Absorb, Flash Fire,      src/battle_util.c (AbilityBattleEffects,
    Soundproof                            ABILITYEFFECT_ABSORBING / _MOVES_BLOCK)
  - crit block                          src/battle_script_commands.c (Cmd_critcalc)
  - accuracy (Compound Eyes, Hustle)    src/battle_script_commands.c (Cmd_accuracycheck)
  - status immunities                   src/battle_script_commands.c (Cmd_seteffect…)
  - the attack/defense stat mults       src/pokemon.c (CalculateBaseDamage), which
                                          engine.base_damage transcribes in order
"""
import os, re

REPO = os.environ.get("LGMAX_POKEFIRERED", os.path.expanduser("~/pokefirered"))


def _parse_ability_names():
    """The canonical ability list, straight from the decomp header, so this
    module can never silently disagree with the ROM about what exists."""
    path = os.path.join(REPO, "include", "constants", "abilities.h")
    names, ids = [], {}
    with open(path) as f:
        for line in f:
            m = re.match(r"#define ABILITY_([A-Z0-9_]+)\s+(\d+)", line)
            if m and m.group(1) != "NONE":
                names.append(m.group(1))
                ids[m.group(1)] = int(m.group(2))
    return names, ids


ABILITY_NAMES, ABILITY_IDS = _parse_ability_names()

# Damaging sound moves Soundproof stops (battle_util.c sSoundMovesTable). The
# status members (Growl, Roar, …) never deal damage, so only these matter to a
# damage model; the full list is kept for correctness of the immunity itself.
SOUND_MOVES = {
    "MOVE_GROWL", "MOVE_ROAR", "MOVE_SING", "MOVE_SUPERSONIC", "MOVE_SCREECH",
    "MOVE_SNORE", "MOVE_UPROAR", "MOVE_METAL_SOUND", "MOVE_GRASS_WHISTLE",
    "MOVE_HYPER_VOICE",
}

# Fire/Ice are special-attack types, so Thick Fat's cut lands on Sp. Atk.
_THICK_FAT_TYPES = ("FIRE", "ICE")

# ---------------------------------------------------------------------------
# One function per ability. Grouped by what the ability touches. Each returns a
# descriptor read by the consumer helpers below; keys used there:
#   immune_types           incoming move types that deal 0 damage (any power)
#   absorb_types           incoming move types negated when they have power
#                          (the ROM also heals 1/4 HP — see `heals_on_absorb`)
#   heals_on_absorb        True if absorb_types also restore HP
#   immune_sound           True if damaging sound moves are negated
#   only_super_effective   True if only super-effective damaging moves land
#   blocks_crit            True if incoming critical hits are prevented
#   accuracy_mult          own-move accuracy factor (unconditional)
#   accuracy_mult_physical own-move accuracy factor, physical moves only
#   attack_mult            Attack ×N inside CalculateBaseDamage
#   attack_mult_status     Attack ×N while this mon carries a major status
#   defense_mult_status    Defense ×N while this mon carries a major status
#   halves_foe_special     foe move types whose Sp. Atk is halved vs this mon
#   pinch_power_type       move type whose power ×1.5 at ≤1/3 HP
#   ignores_burn           True if the burn Attack cut doesn't apply
#   pinch_type             (alias kept for clarity in the pinch abilities)
#   status_immune          major statuses / conditions this mon can't get
#   secondary_mult         own moves' added-effect chance ×N (Serene Grace)
#   blocks_incoming_secondary  True if added effects can't land on this mon
#   sleep_wake_mult        this mon wakes from sleep this many × faster
#   unmodeled              human note: real effect that this model doesn't sim
# Anything not present is simply absent from the descriptor.
# ---------------------------------------------------------------------------

# --- incoming-damage immunities & absorptions (the real correctness win) ----
def levitate():
    """Ground moves miss entirely. TypeCalc sets MISSED|DOESNT_AFFECT_FOE for a
    Ground move vs a Levitate target, before the type chart is even consulted."""
    return {"immune_types": ("GROUND",)}

def volt_absorb():
    """Electric moves with power are negated and heal 1/4 max HP.
    battle_util.c ABILITYEFFECT_ABSORBING, case ABILITY_VOLT_ABSORB."""
    return {"absorb_types": ("ELECTRIC",), "heals_on_absorb": True}

def water_absorb():
    """Water moves with power are negated and heal 1/4 max HP.
    battle_util.c ABILITYEFFECT_ABSORBING, case ABILITY_WATER_ABSORB."""
    return {"absorb_types": ("WATER",), "heals_on_absorb": True}

def flash_fire():
    """Fire moves are negated (and would boost the holder's own Fire moves — a
    stateful buff this model doesn't carry). battle_util.c ABILITYEFFECT_ABSORBING."""
    return {"immune_types": ("FIRE",),
            "unmodeled": "Fire-move power boost after absorbing is not tracked"}

def wonder_guard():
    """Only super-effective damaging moves connect. TypeCalc. Not obtainable in
    LeafGreen (Shedinja's line is absent), implemented for completeness."""
    return {"only_super_effective": True}

def soundproof():
    """Immune to sound-based moves (sSoundMovesTable). battle_util.c
    ABILITYEFFECT_MOVES_BLOCK."""
    return {"immune_sound": True}

def lightning_rod():
    """In Gen 3 this only *redirects* Electric moves to the holder in double
    battles — it grants no immunity and no absorb in a single battle. So it does
    nothing to a 1v1 damage calc. battle_util.c ABILITYEFFECT_COUNT_OTHER_SIDE
    is consulted only for double-battle targeting (battle_script_commands.c)."""
    return {"unmodeled": "double-battle Electric redirection only; no 1v1 effect"}


# --- critical hits ----------------------------------------------------------
def battle_armor():
    """Incoming critical hits are prevented. Cmd_critcalc gates the crit roll on
    the target lacking Battle Armor / Shell Armor."""
    return {"blocks_crit": True}

def shell_armor():
    """Incoming critical hits are prevented. Cmd_critcalc (see battle_armor)."""
    return {"blocks_crit": True}


# --- accuracy ---------------------------------------------------------------
def compound_eyes():
    """Own moves' accuracy ×1.3. Cmd_accuracycheck: calc = calc * 130 / 100."""
    return {"accuracy_mult": 1.3}

def hustle():
    """Attack ×1.5 (CalculateBaseDamage) but own physical accuracy ×0.8
    (Cmd_accuracycheck: calc = calc * 80 / 100)."""
    return {"attack_mult": 1.5, "accuracy_mult_physical": 0.8}


# --- attack / defense stat mults inside CalculateBaseDamage ------------------
def huge_power():
    """Attack is doubled. CalculateBaseDamage / engine.base_damage."""
    return {"attack_mult": 2.0}

def pure_power():
    """Attack is doubled. CalculateBaseDamage / engine.base_damage."""
    return {"attack_mult": 2.0}

def guts():
    """Attack ×1.5 while statused, and the burn Attack cut is ignored.
    CalculateBaseDamage / engine.base_damage."""
    return {"attack_mult_status": 1.5, "ignores_burn": True}

def marvel_scale():
    """Defense ×1.5 while statused. CalculateBaseDamage / engine.base_damage."""
    return {"defense_mult_status": 1.5}

def thick_fat():
    """Incoming Fire and Ice moves have their attacking Sp. Atk halved.
    CalculateBaseDamage / engine.base_damage."""
    return {"halves_foe_special": _THICK_FAT_TYPES}

def overgrow():
    """Grass-move power ×1.5 at ≤1/3 HP. CalculateBaseDamage pinch check."""
    return {"pinch_power_type": "GRASS"}

def blaze():
    """Fire-move power ×1.5 at ≤1/3 HP. CalculateBaseDamage pinch check."""
    return {"pinch_power_type": "FIRE"}

def torrent():
    """Water-move power ×1.5 at ≤1/3 HP. CalculateBaseDamage pinch check."""
    return {"pinch_power_type": "WATER"}

def swarm():
    """Bug-move power ×1.5 at ≤1/3 HP. CalculateBaseDamage pinch check."""
    return {"pinch_power_type": "BUG"}


# --- status immunities (Cmd_seteffect… / status-setting scripts) ------------
def immunity():
    """Cannot be poisoned. seteffect refuses TOXIC/POISON on an Immunity mon."""
    return {"status_immune": ("poison",)}

def limber():
    """Cannot be paralyzed. seteffect refuses paralysis on a Limber mon."""
    return {"status_immune": ("paralysis",)}

def insomnia():
    """Cannot fall asleep. seteffect refuses sleep on an Insomnia mon."""
    return {"status_immune": ("sleep",)}

def vital_spirit():
    """Cannot fall asleep. seteffect refuses sleep on a Vital Spirit mon."""
    return {"status_immune": ("sleep",)}

def water_veil():
    """Cannot be burned. seteffect refuses burn on a Water Veil mon."""
    return {"status_immune": ("burn",)}

def magma_armor():
    """Cannot be frozen. seteffect refuses freeze on a Magma Armor mon."""
    return {"status_immune": ("freeze",)}

def own_tempo():
    """Cannot be confused. seteffect refuses confusion on an Own Tempo mon."""
    return {"status_immune": ("confusion",)}

def oblivious():
    """Cannot be infatuated. seteffect refuses Attract on an Oblivious mon.
    (Attract isn't part of the damage/tempo model, so this is informational.)"""
    return {"status_immune": ("attract",)}

def inner_focus():
    """Cannot flinch. Flinch is checked against Inner Focus before it applies."""
    return {"status_immune": ("flinch",)}

def early_bird():
    """Wakes from sleep twice as fast (the sleep counter drops by 2).
    Shortens, rather than prevents, sleep."""
    return {"sleep_wake_mult": 2.0}


# --- added-effect (secondary) odds ------------------------------------------
def serene_grace():
    """Own moves' added-effect chance is doubled. battle_script_commands.c
    doubles the secondary chance when the attacker has Serene Grace."""
    return {"secondary_mult": 2.0}

def shield_dust():
    """Added effects of damaging moves can't land on this mon. Checked before an
    added effect is applied (battle_script_commands.c)."""
    return {"blocks_incoming_secondary": True}


# --- effects outside this battle model (documented, not simulated) ----------
# Weather setters: no weather is modeled, so these change nothing here.
def drizzle():   return {"unmodeled": "summons rain on switch-in (no weather modeled)"}
def drought():   return {"unmodeled": "summons harsh sun on switch-in (no weather modeled)"}
def sand_stream():return {"unmodeled": "summons a sandstorm on switch-in (no weather modeled)"}
def air_lock():  return {"unmodeled": "negates all weather effects while active"}
def cloud_nine():return {"unmodeled": "negates all weather effects while active"}
# Weather-dependent perks:
def swift_swim():  return {"unmodeled": "Speed ×2 in rain (no weather, no speed-tie sim)"}
def chlorophyll(): return {"unmodeled": "Speed ×2 in sun (no weather, no speed-tie sim)"}
def rain_dish():   return {"unmodeled": "heals 1/16 HP per turn in rain (no weather)"}
def sand_veil():   return {"unmodeled": "evasion ×1.25 in a sandstorm (no weather)"}
def forecast():    return {"unmodeled": "Castform type follows the weather (no weather)"}
# Switch-in / turn-order / multi-mon tricks (no switching or allies modeled):
def intimidate():  return {"unmodeled": "lowers the foe's Attack one stage on switch-in"}
def trace():       return {"unmodeled": "copies a foe's ability on switch-in"}
def speed_boost():   return {"unmodeled": "+1 Speed stage at each turn's end (no stage sim)"}
def truant():        return {"unmodeled": "acts only every other turn"}
def shadow_tag():    return {"unmodeled": "the foe can't flee or switch"}
def arena_trap():    return {"unmodeled": "grounded foes can't flee or switch"}
def magnet_pull():   return {"unmodeled": "Steel-type foes can't flee or switch"}
def suction_cups():  return {"unmodeled": "can't be forced out by Roar/Whirlwind"}
def plus():          return {"unmodeled": "Sp. Atk ×1.5 with a Minus ally (doubles)"}
def minus():         return {"unmodeled": "Sp. Atk ×1.5 with a Plus ally (doubles)"}
# Stat-drop / meddling immunities (no stat-stage or item model to protect):
def clear_body():   return {"unmodeled": "foes can't lower this mon's stats"}
def white_smoke():  return {"unmodeled": "foes can't lower this mon's stats"}
def hyper_cutter():  return {"unmodeled": "Attack can't be lowered by foes"}
def keen_eye():      return {"unmodeled": "accuracy can't be lowered by foes"}
def sticky_hold():   return {"unmodeled": "held item can't be stolen or knocked off"}
# Contact / retaliation effects (attacker-side state after being hit):
def static():       return {"unmodeled": "30% to paralyze an attacker on contact"}
def poison_point():  return {"unmodeled": "30% to poison an attacker on contact"}
def flame_body():    return {"unmodeled": "30% to burn an attacker on contact"}
def cute_charm():    return {"unmodeled": "30% to infatuate an attacker on contact"}
def effect_spore():  return {"unmodeled": "30% poison/paralysis/sleep on contact"}
def rough_skin():    return {"unmodeled": "attacker loses 1/16 HP on contact"}
def color_change():  return {"unmodeled": "type changes to the last move that hit it"}
def liquid_ooze():   return {"unmodeled": "draining HP from this mon damages the drainer"}
def synchronize():   return {"unmodeled": "passes its burn/poison/paralysis back to the setter"}
# On-hit / end-of-turn self effects:
def shed_skin():     return {"unmodeled": "33%/turn to cure its own major status"}
def natural_cure():  return {"unmodeled": "cures its own status on switch-out"}
def sturdy():        return {"unmodeled": "immune to OHKO moves (already unusable here)"}
def rock_head():     return {"no_recoil": True,
                             "unmodeled": "recoil isn't priced into move choice"}
def pressure():      return {"unmodeled": "moves targeting it cost 2 PP (PP not charged here)"}
def damp():          return {"unmodeled": "no one can use Self-Destruct/Explosion"}
def stench():        return {"unmodeled": "no in-battle effect in Gen 3"}
def illuminate():    return {"unmodeled": "raises wild encounter rate (overworld)"}
def run_away():      return {"unmodeled": "guarantees fleeing from wild battles"}
def pickup():        return {"unmodeled": "may hold an item after battle (overworld)"}
def cacophony():     return {"unmodeled": "unused dummy ability (Soundproof clone)"}


# ---------------------------------------------------------------------------
# Registry: every ability name -> its function. Built explicitly and then
# checked against the decomp so a missing or stray entry is caught at import.
# ---------------------------------------------------------------------------
ABILITIES = {
    "STENCH": stench, "DRIZZLE": drizzle, "SPEED_BOOST": speed_boost,
    "BATTLE_ARMOR": battle_armor, "STURDY": sturdy, "DAMP": damp, "LIMBER": limber,
    "SAND_VEIL": sand_veil, "STATIC": static, "VOLT_ABSORB": volt_absorb,
    "WATER_ABSORB": water_absorb, "OBLIVIOUS": oblivious, "CLOUD_NINE": cloud_nine,
    "COMPOUND_EYES": compound_eyes, "INSOMNIA": insomnia, "COLOR_CHANGE": color_change,
    "IMMUNITY": immunity, "FLASH_FIRE": flash_fire, "SHIELD_DUST": shield_dust,
    "OWN_TEMPO": own_tempo, "SUCTION_CUPS": suction_cups, "INTIMIDATE": intimidate,
    "SHADOW_TAG": shadow_tag, "ROUGH_SKIN": rough_skin, "WONDER_GUARD": wonder_guard,
    "LEVITATE": levitate, "EFFECT_SPORE": effect_spore, "SYNCHRONIZE": synchronize,
    "CLEAR_BODY": clear_body, "NATURAL_CURE": natural_cure, "LIGHTNING_ROD": lightning_rod,
    "SERENE_GRACE": serene_grace, "SWIFT_SWIM": swift_swim, "CHLOROPHYLL": chlorophyll,
    "ILLUMINATE": illuminate, "TRACE": trace, "HUGE_POWER": huge_power,
    "POISON_POINT": poison_point, "INNER_FOCUS": inner_focus, "MAGMA_ARMOR": magma_armor,
    "WATER_VEIL": water_veil, "MAGNET_PULL": magnet_pull, "SOUNDPROOF": soundproof,
    "RAIN_DISH": rain_dish, "SAND_STREAM": sand_stream, "PRESSURE": pressure,
    "THICK_FAT": thick_fat, "EARLY_BIRD": early_bird, "FLAME_BODY": flame_body,
    "RUN_AWAY": run_away, "KEEN_EYE": keen_eye, "HYPER_CUTTER": hyper_cutter,
    "PICKUP": pickup, "TRUANT": truant, "HUSTLE": hustle, "CUTE_CHARM": cute_charm,
    "PLUS": plus, "MINUS": minus, "FORECAST": forecast, "STICKY_HOLD": sticky_hold,
    "SHED_SKIN": shed_skin, "GUTS": guts, "MARVEL_SCALE": marvel_scale,
    "LIQUID_OOZE": liquid_ooze, "OVERGROW": overgrow, "BLAZE": blaze, "TORRENT": torrent,
    "SWARM": swarm, "ROCK_HEAD": rock_head, "DROUGHT": drought, "ARENA_TRAP": arena_trap,
    "VITAL_SPIRIT": vital_spirit, "WHITE_SMOKE": white_smoke, "PURE_POWER": pure_power,
    "SHELL_ARMOR": shell_armor, "CACOPHONY": cacophony, "AIR_LOCK": air_lock,
}


def descriptor(ability):
    """The ability's effect descriptor (empty for NONE / anything unknown)."""
    fn = ABILITIES.get(ability)
    return fn() if fn else {}


# ---------------------------------------------------------------------------
# Consumer helpers — the interface the engine actually calls.
# ---------------------------------------------------------------------------
def negates_damage(def_ability, move_type, move_power, move_const, type_eff):
    """Does the defender's ability reduce this move's damage to zero?

    type_eff is the type-chart multiplier already computed for the move. Mirrors
    TypeCalc (Levitate, Wonder Guard) and ABILITYEFFECT_ABSORBING / _MOVES_BLOCK
    (Volt/Water Absorb, Flash Fire, Soundproof)."""
    d = descriptor(def_ability)
    if move_type in d.get("immune_types", ()):            # Levitate, Flash Fire
        return True
    if move_power and move_type in d.get("absorb_types", ()):   # Volt/Water Absorb
        return True
    if move_power and d.get("only_super_effective") and type_eff <= 1:  # Wonder Guard
        return True
    if d.get("immune_sound") and move_const in SOUND_MOVES:     # Soundproof
        return True
    return False


def blocks_crit(def_ability):
    """Battle Armor / Shell Armor prevent incoming critical hits."""
    return bool(descriptor(def_ability).get("blocks_crit"))


def accuracy_mult(atk_ability, physical):
    """Own-move accuracy multiplier from the attacker's ability (Compound Eyes
    always; Hustle on physical moves)."""
    d = descriptor(atk_ability)
    mult = d.get("accuracy_mult", 1.0)
    if physical:
        mult *= d.get("accuracy_mult_physical", 1.0)
    return mult


def halves_foe_special(def_ability, move_type):
    """Thick Fat: the defender halves the attacker's Sp. Atk vs Fire/Ice."""
    return move_type in descriptor(def_ability).get("halves_foe_special", ())


def immune_to_status(def_ability, status):
    """Ability-based immunity to a major status / flinch / confusion."""
    return status in descriptor(def_ability).get("status_immune", ())


def secondary_scale(atk_ability, def_ability):
    """Multiplier on a damaging move's added-effect chance: Serene Grace doubles
    it, Shield Dust (on the target) zeroes it. Returns 0.0 when blocked."""
    if descriptor(def_ability).get("blocks_incoming_secondary"):
        return 0.0
    return descriptor(atk_ability).get("secondary_mult", 1.0)


def sleep_wake_mult(def_ability):
    """How much faster this mon wakes from sleep (Early Bird = 2×)."""
    return descriptor(def_ability).get("sleep_wake_mult", 1.0)


# The remaining CalculateBaseDamage stat mults (Huge/Pure Power, Hustle, Guts,
# Marvel Scale, the Overgrow line) are order-sensitive integer ops, so
# engine.base_damage keeps their cited transcription inline rather than routing
# them through a helper here; the per-ability descriptors above document them.
