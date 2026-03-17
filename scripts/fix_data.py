#!/usr/bin/env python3
"""Fix Gen 1 data issues after initial PokeAPI fetch.

Fixes:
1. Bite was Normal-type in Gen 1 (reclassified Dark in Gen 2)
2. Clefairy/Clefable were Normal-type in Gen 1 (reclassified Fairy in Gen 6)
3. Gust was Normal-type in Gen 1 (reclassified Flying in Gen 2) -- actually it was already Flying in RBY
4. Pokemon with <4 moves get padded with appropriate moves
5. Leech Life was 20 power in Gen 1 (buffed to 80 in Gen 7)
6. Some move stats need Gen 1 corrections
"""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def main():
    # Load data
    with open(os.path.join(DATA_DIR, "pokemon.json")) as f:
        pokemon = json.load(f)
    with open(os.path.join(DATA_DIR, "moves.json")) as f:
        moves = json.load(f)

    # === Fix 1: Add "bite" as Normal-type (Gen 1) ===
    moves["bite"] = {
        "name": "Bite",
        "type": "normal",
        "category": "physical",
        "power": 60,
        "accuracy": 100,
        "pp": 25,
        "effect": None,
        "effect_chance": 0
    }
    print("Added bite as Normal-type move (Gen 1)")

    # === Fix 2: Leech Life was 20 power in Gen 1 ===
    if "leech-life" in moves:
        moves["leech-life"]["power"] = 20
        print("Fixed Leech Life power: 80 -> 20 (Gen 1)")

    # === Fix 3: Low Kick had fixed 50 power in Gen 1 ===
    if "low-kick" in moves:
        moves["low-kick"]["power"] = 50
        moves["low-kick"]["category"] = "physical"
        print("Fixed Low Kick: power=50, physical (Gen 1)")

    # === Fix 4: Dragon Rage always does 40 damage (fixed damage move) ===
    # We'll mark it specially
    if "dragon-rage" in moves:
        moves["dragon-rage"]["power"] = 1  # Special handling in engine
        moves["dragon-rage"]["category"] = "special"
        moves["dragon-rage"]["effect"] = "fixed_40"
        moves["dragon-rage"]["effect_chance"] = 100
        print("Fixed Dragon Rage: fixed 40 damage (Gen 1)")

    # === Fix 5: Seismic Toss does damage equal to user's level ===
    if "seismic-toss" in moves:
        moves["seismic-toss"]["power"] = 1
        moves["seismic-toss"]["category"] = "physical"
        moves["seismic-toss"]["effect"] = "fixed_level"
        moves["seismic-toss"]["effect_chance"] = 100
        print("Fixed Seismic Toss: fixed damage = level (Gen 1)")

    # === Fix 6: Night Shade does damage equal to user's level ===
    if "night-shade" in moves:
        moves["night-shade"]["power"] = 1
        moves["night-shade"]["category"] = "special"
        moves["night-shade"]["effect"] = "fixed_level"
        moves["night-shade"]["effect_chance"] = 100
        print("Fixed Night Shade: fixed damage = level (Gen 1)")

    # === Fix 7: Sonic Boom always does 20 damage ===
    if "sonic-boom" in moves:
        moves["sonic-boom"]["power"] = 1
        moves["sonic-boom"]["category"] = "physical"
        moves["sonic-boom"]["effect"] = "fixed_20"
        moves["sonic-boom"]["effect_chance"] = 100
        print("Fixed Sonic Boom: fixed 20 damage (Gen 1)")

    # === Fix 8: Guillotine is OHKO move ===
    if "guillotine" in moves:
        moves["guillotine"]["power"] = 1
        moves["guillotine"]["category"] = "physical"
        moves["guillotine"]["accuracy"] = 30
        moves["guillotine"]["effect"] = "ohko"
        moves["guillotine"]["effect_chance"] = 100
        print("Fixed Guillotine: OHKO move (Gen 1)")

    # === Fix 9: Self-Destruct - user faints ===
    if "self-destruct" in moves:
        moves["self-destruct"]["effect"] = "self_destruct"
        moves["self-destruct"]["effect_chance"] = 100
        print("Fixed Self-Destruct: user faints after use")

    # === Fix Pokemon types ===
    type_fixes = {
        35: ["normal"],   # Clefairy was Normal in Gen 1
        36: ["normal"],   # Clefable was Normal in Gen 1
        81: ["electric"], # Magnemite was pure Electric in Gen 1 (no Steel)
        82: ["electric"], # Magneton was pure Electric in Gen 1
    }

    for poke in pokemon:
        if poke["id"] in type_fixes:
            old_types = poke["types"]
            poke["types"] = type_fixes[poke["id"]]
            print(f"Fixed #{poke['id']} {poke['name']} types: {old_types} -> {poke['types']}")

    # === Fix Pokemon with bite in their moveset (re-add it) ===
    bite_pokemon = {
        23: ["leer", "wrap", "poison-sting", "bite"],        # Ekans
        24: ["leer", "poison-sting", "wrap", "bite"],        # Arbok
        41: ["leech-life", "supersonic", "bite", "confuse-ray"],  # Zubat
        42: ["bite", "leech-life", "screech", "supersonic"],  # Golbat
        52: ["growl", "scratch", "bite", "pay-day"],          # Meowth
        53: ["bite", "growl", "scratch", "screech"],          # Persian
        58: ["bite", "roar", "ember", "leer"],                # Growlithe
        115: ["comet-punch", "rage", "bite", "tail-whip"],   # Kangaskhan
        130: ["bite", "dragon-rage", "hydro-pump", "leer"],  # Gyarados
        142: ["agility", "wing-attack", "supersonic", "bite"],  # Aerodactyl
    }

    # Pad remaining Pokemon with <4 moves
    move_padding = {
        10: ["string-shot", "tackle", "bug-bite", "harden"],     # Caterpie - give it basic moves
        11: ["harden", "tackle", "string-shot", "defense-curl"], # Metapod
        13: ["poison-sting", "string-shot", "harden", "tackle"], # Weedle
        14: ["harden", "poison-sting", "string-shot", "tackle"], # Kakuna
        129: ["splash", "tackle", "flail", "bounce"],            # Magikarp (lol)
        132: ["transform", "pound", "tackle", "scratch"],        # Ditto
    }

    # Add bug-bite and flail to moves if not present (simple moves for padding)
    if "bug-bite" not in moves:
        moves["bug-bite"] = {
            "name": "Bug Bite",
            "type": "bug",
            "category": "physical",
            "power": 30,
            "accuracy": 100,
            "pp": 20,
            "effect": None,
            "effect_chance": 0
        }
    # Magikarp flail - we'll just give it tackle and struggle-adjacent moves
    # Actually let's just give the short Pokemon tackle/pound/scratch as generic fillers
    # that already exist in the moves dict

    for poke in pokemon:
        pid = poke["id"]
        if pid in bite_pokemon:
            poke["moves"] = bite_pokemon[pid]
            print(f"Fixed #{pid} {poke['name']} moves: {poke['moves']}")
        elif pid in move_padding:
            poke["moves"] = [m for m in move_padding[pid] if m in moves][:4]
            print(f"Padded #{pid} {poke['name']} moves: {poke['moves']}")

    # Final validation
    short = [p for p in pokemon if len(p["moves"]) < 4]
    if short:
        # For any remaining short Pokemon, pad with tackle/pound/scratch/growl
        fillers = ["tackle", "pound", "scratch", "growl", "leer"]
        for p in short:
            existing = set(p["moves"])
            for filler in fillers:
                if filler not in existing and filler in moves:
                    p["moves"].append(filler)
                    existing.add(filler)
                    if len(p["moves"]) >= 4:
                        break
            print(f"Auto-padded #{p['id']} {p['name']} moves: {p['moves']}")

    # Verify all Pokemon have valid moves
    all_ok = True
    for p in pokemon:
        for m in p["moves"]:
            if m not in moves:
                print(f"ERROR: #{p['id']} {p['name']} has unknown move: {m}")
                all_ok = False
        if len(p["moves"]) < 4:
            print(f"ERROR: #{p['id']} {p['name']} still has <4 moves: {p['moves']}")
            all_ok = False

    if all_ok:
        print("\nAll 151 Pokemon have 4 valid moves!")

    # Save
    with open(os.path.join(DATA_DIR, "pokemon.json"), "w") as f:
        json.dump(pokemon, f, indent=2)
    with open(os.path.join(DATA_DIR, "moves.json"), "w") as f:
        json.dump(moves, f, indent=2)

    print(f"\nSaved fixed data: {len(pokemon)} Pokemon, {len(moves)} moves")


if __name__ == "__main__":
    main()
