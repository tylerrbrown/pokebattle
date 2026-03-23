#!/usr/bin/env python3
"""Test suite for the PokeBattle battle engine."""

import sys
import os
import random

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pokemon_data
from battle_engine import (
    PokemonInstance, calc_hp, calc_stat, calculate_damage,
    check_accuracy, check_status_prevents_action, resolve_move,
    resolve_turn, build_team, STRUGGLE, DEFAULT_LEVEL
)

# Load data
pokemon_data.load_data()

passed = 0
failed = 0


def test(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  PASS: {name}")
    else:
        failed += 1
        print(f"  FAIL: {name} {detail}")


def make_pokemon(dex_id):
    """Helper to create a PokemonInstance by dex ID."""
    species = pokemon_data.POKEMON[dex_id]
    return PokemonInstance(species, pokemon_data.MOVES)


def test_stat_calculation():
    print("\n=== Stat Calculations ===")

    # Bulbasaur base HP: 45 -> calc_hp(45) = (45*2*50/100) + 50 + 10 = 45 + 50 + 10 = 105
    test("calc_hp(45) = 105", calc_hp(45) == 105, f"got {calc_hp(45)}")

    # calc_stat(49) = (49*2*50/100) + 5 = 49 + 5 = 54
    test("calc_stat(49) = 54", calc_stat(49) == 54, f"got {calc_stat(49)}")

    # Mewtwo base Special: 154 -> calc_stat(154) = (154*2*50/100) + 5 = 154 + 5 = 159
    test("calc_stat(154) = 159", calc_stat(154) == 159, f"got {calc_stat(154)}")


def test_pokemon_instance():
    print("\n=== Pokemon Instance Creation ===")

    bulbasaur = make_pokemon(1)
    test("Bulbasaur name", bulbasaur.name == "Bulbasaur")
    test("Bulbasaur types", bulbasaur.types == ["grass", "poison"])
    test("Bulbasaur has 4 moves", len(bulbasaur.moves) == 4, f"got {len(bulbasaur.moves)}")
    test("Bulbasaur HP = max_hp", bulbasaur.current_hp == bulbasaur.max_hp)
    test("Bulbasaur not fainted", not bulbasaur.is_fainted)
    test("Bulbasaur no status", bulbasaur.status is None)

    charizard = make_pokemon(6)
    test("Charizard types", charizard.types == ["fire", "flying"])

    # Clefairy should be Normal type (Gen 1 fix)
    clefairy = make_pokemon(35)
    test("Clefairy is Normal type (Gen 1)", clefairy.types == ["normal"], f"got {clefairy.types}")

    mewtwo = make_pokemon(150)
    test("Mewtwo types", mewtwo.types == ["psychic"])
    test("Mewtwo has high special", mewtwo.special > 100)


def test_type_effectiveness():
    print("\n=== Type Effectiveness ===")

    from pokemon_data import get_type_effectiveness as eff

    test("Fire > Grass = 2x", eff("fire", "grass") == 2.0)
    test("Water > Fire = 2x", eff("water", "fire") == 2.0)
    test("Electric > Water = 2x", eff("electric", "water") == 2.0)
    test("Grass > Water = 2x", eff("grass", "water") == 2.0)
    test("Fire < Water = 0.5x", eff("fire", "water") == 0.5)
    test("Normal x Ghost = 0x", eff("normal", "ghost") == 0.0)
    test("Ghost x Normal = 0x", eff("ghost", "normal") == 0.0)
    test("Electric x Ground = 0x", eff("electric", "ground") == 0.0)
    test("Psychic x Ghost = 0x (Gen 1 bug)", eff("psychic", "ghost") == 0.0)
    test("Normal x Normal = 1x", eff("normal", "normal") == 1.0)
    test("Dragon x Dragon = 2x", eff("dragon", "dragon") == 2.0)


def test_damage_calculation():
    print("\n=== Damage Calculation ===")

    random.seed(42)  # Deterministic

    charizard = make_pokemon(6)
    bulbasaur = make_pokemon(1)

    # Find Ember on Charizard
    ember = next((m for m in charizard.moves if m["id"] == "ember"), None)
    test("Charizard has Ember", ember is not None)

    if ember:
        # Fire vs Grass/Poison -> 2x * 0.5x = 1x (neutral)
        # Actually: Fire > Grass = 2x, Fire vs Poison = 1x -> 2x total
        dmg, eff, crit = calculate_damage(charizard, bulbasaur, ember, 0.5)
        test("Ember damage > 0", dmg > 0, f"got {dmg}")
        test("Ember vs Grass/Poison effectiveness", eff == 2.0, f"got {eff}")

    # Normal move vs Ghost type -> immune
    rattata = make_pokemon(19)
    gengar = make_pokemon(94)
    tackle = next((m for m in rattata.moves if m["id"] == "tackle"), None)
    if tackle:
        dmg, eff, crit = calculate_damage(rattata, gengar, tackle, 0.5)
        test("Tackle vs Ghost = immune", dmg == 0 and eff == 0.0, f"dmg={dmg}, eff={eff}")

    # Fixed damage: Dragon Rage = 40
    gyarados = make_pokemon(130)
    dragon_rage = next((m for m in gyarados.moves if m["id"] == "dragon-rage"), None)
    if dragon_rage:
        dmg, eff, crit = calculate_damage(gyarados, bulbasaur, dragon_rage, 0.5)
        test("Dragon Rage = fixed 40", dmg == 40, f"got {dmg}")


def test_stab():
    print("\n=== STAB (Same Type Attack Bonus) ===")

    random.seed(42)

    pikachu = make_pokemon(25)
    # Thunder Shock is Electric, Pikachu is Electric -> STAB
    tshock = next((m for m in pikachu.moves if m["id"] == "thunder-shock"), None)
    test("Pikachu has Thunder Shock", tshock is not None)

    if tshock:
        # STAB should give 1.5x bonus
        target = make_pokemon(19)  # Rattata (Normal)

        # Run multiple times to get average
        damages_stab = []
        for _ in range(100):
            dmg, _, _ = calculate_damage(pikachu, target, tshock, 0.5)
            damages_stab.append(dmg)

        # Same move but pretend it's Normal type (no STAB)
        tshock_no_stab = dict(tshock)
        tshock_no_stab["type"] = "normal"
        damages_no_stab = []
        for _ in range(100):
            dmg, _, _ = calculate_damage(pikachu, target, tshock_no_stab, 0.5)
            damages_no_stab.append(dmg)

        avg_stab = sum(damages_stab) / len(damages_stab)
        avg_no_stab = sum(damages_no_stab) / len(damages_no_stab)
        ratio = avg_stab / max(1, avg_no_stab)
        test("STAB ~1.5x bonus", 1.2 < ratio < 1.9, f"ratio={ratio:.2f}")


def test_status_effects():
    print("\n=== Status Effects ===")

    random.seed(42)

    # Sleep
    poke = make_pokemon(1)
    poke.status = "sleep"
    poke.sleep_turns = 3
    can_act, events = check_status_prevents_action(poke)
    test("Sleeping Pokemon can't act (turn 1)", not can_act)
    test("Sleep decrements turns", poke.sleep_turns == 2)

    poke.sleep_turns = 1
    can_act, events = check_status_prevents_action(poke)
    # sleep_turns goes to 0, should wake up
    test("Pokemon wakes up when sleep_turns reaches 0", can_act and poke.status is None)

    # Paralysis (25% chance to not act)
    random.seed(0)  # Find a seed where para triggers
    poke = make_pokemon(25)
    poke.status = "paralyze"
    para_count = 0
    for _ in range(1000):
        can_act, _ = check_status_prevents_action(poke)
        if not can_act:
            para_count += 1
    ratio = para_count / 1000
    test("Paralysis ~25% full para rate", 0.20 < ratio < 0.30, f"got {ratio:.2%}")

    # Paralysis halves speed
    poke = make_pokemon(25)
    base_speed = poke.get_effective_speed()
    poke.status = "paralyze"
    para_speed = poke.get_effective_speed()
    test("Paralysis quarters speed", para_speed == base_speed // 4, f"base={base_speed}, para={para_speed}")


def test_turn_resolution():
    print("\n=== Turn Resolution ===")

    random.seed(42)

    pikachu = make_pokemon(25)
    bulbasaur = make_pokemon(1)

    # Use Thunder Shock (index 1) and Tackle (index 1) - damage-dealing moves
    # Pikachu moves: growl, thunder-shock, thunder-wave, quick-attack
    # Bulbasaur moves: growl, tackle, leech-seed, vine-whip
    p1_action = {"type": "move", "move_index": 1}  # Thunder Shock
    p2_action = {"type": "move", "move_index": 1}  # Tackle

    events, switches = resolve_turn(pikachu, bulbasaur, p1_action, p2_action, 1.0, 1.0)

    test("Turn produces events", len(events) > 0)
    test("Turn ends with turn_end event", events[-1]["event"] == "turn_end")
    test("Move use events present", any(e["event"] == "move_use" for e in events))

    # Check HP changed (both used damaging moves)
    test("At least one Pokemon took damage",
         pikachu.current_hp < pikachu.max_hp or bulbasaur.current_hp < bulbasaur.max_hp)


def test_dodge_multiplier():
    print("\n=== Dodge Multiplier ===")

    random.seed(42)

    attacker = make_pokemon(6)  # Charizard
    defender = make_pokemon(7)  # Squirtle

    ember = next(m for m in attacker.moves if m["id"] == "ember")

    # No dodge (1.0) -> full damage
    damages_full = []
    for _ in range(200):
        dmg, _, _ = calculate_damage(attacker, defender, ember, 1.0)
        damages_full.append(dmg)

    # Successful dodge (0.8) -> 80% damage
    damages_dodged = []
    for _ in range(200):
        dmg, _, _ = calculate_damage(attacker, defender, ember, 0.8)
        damages_dodged.append(dmg)

    avg_full = sum(damages_full) / len(damages_full)
    avg_dodged = sum(damages_dodged) / len(damages_dodged)
    ratio = avg_full / max(1, avg_dodged)
    test("Dodge reduces damage by ~20%", 1.15 < ratio < 1.35, f"ratio={ratio:.2f}")


def test_speed_priority():
    print("\n=== Speed Priority ===")

    # Jolteon (speed 130) should almost always go before Snorlax (speed 30)
    first_counts = {0: 0, 1: 0}
    for _ in range(100):
        jolteon = make_pokemon(135)   # Jolteon
        snorlax = make_pokemon(143)   # Snorlax
        events, _ = resolve_turn(
            jolteon, snorlax,
            {"type": "move", "move_index": 0},
            {"type": "move", "move_index": 0},
            1.0, 1.0
        )
        # Find first move_use event
        for e in events:
            if e["event"] == "move_use":
                if e["pokemon"] == jolteon.name:
                    first_counts[0] += 1
                else:
                    first_counts[1] += 1
                break

    test("Faster Pokemon (Jolteon) goes first most of the time",
         first_counts[0] > 90, f"Jolteon first: {first_counts[0]}/100")


def test_pp_depletion():
    print("\n=== PP Depletion ===")

    poke = make_pokemon(1)
    # Drain all PP
    for move in poke.moves:
        move["current_pp"] = 0

    test("No usable moves when all PP=0", not poke.has_usable_moves())


def test_build_team():
    print("\n=== Team Building ===")

    team = build_team([1, 4, 7, 25, 143, 150], pokemon_data.POKEMON, pokemon_data.MOVES)
    test("Team has 6 Pokemon", len(team) == 6)
    test("First Pokemon is Bulbasaur", team[0].name == "Bulbasaur")
    test("Last Pokemon is Mewtwo", team[5].name == "Mewtwo")
    test("All Pokemon have 4 moves", all(len(p.moves) == 4 for p in team))
    test("All Pokemon at full HP", all(p.current_hp == p.max_hp for p in team))


def test_serialization():
    print("\n=== Serialization ===")

    poke = make_pokemon(25)  # Pikachu
    full = poke.serialize_full()
    public = poke.serialize_public()

    test("Full serialization has PP info", "current_pp" in full["moves"][0])
    test("Public serialization has no PP info", "current_pp" not in public["moves"][0])
    test("Both have basic fields", full["name"] == "Pikachu" and public["name"] == "Pikachu")
    test("Full has power/accuracy", "power" in full["moves"][0])


def test_all_pokemon_valid():
    total_pokemon = len(pokemon_data.POKEMON)
    print(f"\n=== All {total_pokemon} Pokemon Validation ===")

    all_valid = True
    issues = []
    for dex_id in range(1, total_pokemon + 1):
        try:
            poke = make_pokemon(dex_id)
            if len(poke.moves) != 4:
                issues.append(f"#{dex_id} {poke.name}: {len(poke.moves)} moves")
                all_valid = False
            if not poke.types:
                issues.append(f"#{dex_id} {poke.name}: no types")
                all_valid = False
            if poke.max_hp <= 0:
                issues.append(f"#{dex_id} {poke.name}: invalid HP")
                all_valid = False
        except Exception as e:
            issues.append(f"#{dex_id}: {e}")
            all_valid = False

    test(f"All {total_pokemon} Pokemon create successfully with valid data", all_valid,
         f"\n    " + "\n    ".join(issues) if issues else "")


def main():
    print("PokeBattle Battle Engine Tests")
    print("=" * 50)

    test_stat_calculation()
    test_pokemon_instance()
    test_type_effectiveness()
    test_damage_calculation()
    test_stab()
    test_status_effects()
    test_turn_resolution()
    test_dodge_multiplier()
    test_speed_priority()
    test_pp_depletion()
    test_build_team()
    test_serialization()
    test_all_pokemon_valid()

    print(f"\n{'=' * 50}")
    print(f"Results: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
