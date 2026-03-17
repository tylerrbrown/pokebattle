#!/usr/bin/env python3
"""Fetch Gen 1 Pokemon data from PokeAPI and generate static JSON files.

Outputs:
  data/pokemon.json  - 151 Pokemon with stats, types, and 4 moves each
  data/moves.json    - All referenced moves with Gen 1 stats
  data/typechart.json - 15x15 type effectiveness matrix
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE_URL = "https://pokeapi.co/api/v2"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# Gen 1 types
GEN1_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic",
    "bug", "rock", "ghost", "dragon"
]

# Physical types in Gen 1 (all others are special)
PHYSICAL_TYPES = {"normal", "fighting", "poison", "ground", "flying", "bug", "rock", "ghost"}


def fetch_json(url, retries=3):
    """Fetch JSON from URL with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PokeBattle-DataGen/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            if attempt < retries - 1:
                print(f"  Retry {attempt+1} for {url}: {e}")
                time.sleep(2 * (attempt + 1))
            else:
                raise


def get_gen1_moves_for_pokemon(pokemon_data):
    """Extract the best 4 moves for a Pokemon from Gen 1 learnset.

    Priority: level-up moves learned at lowest levels (birth moves first).
    """
    level_moves = []
    for move_entry in pokemon_data["moves"]:
        for version_detail in move_entry["version_group_details"]:
            if version_detail["version_group"]["name"] == "red-blue":
                if version_detail["move_learn_method"]["name"] == "level-up":
                    level = version_detail["level_learned_at"]
                    move_name = move_entry["move"]["name"]
                    level_moves.append((level, move_name))
                break

    # Sort by level (0 = egg/start moves first), then alphabetically
    level_moves.sort(key=lambda x: (x[0], x[1]))

    # Take first 4 unique moves
    seen = set()
    result = []
    for level, move_name in level_moves:
        if move_name not in seen:
            seen.add(move_name)
            result.append(move_name)
            if len(result) == 4:
                break

    # If we still don't have 4, pad with TM moves from Gen 1
    if len(result) < 4:
        for move_entry in pokemon_data["moves"]:
            for version_detail in move_entry["version_group_details"]:
                if version_detail["version_group"]["name"] == "red-blue":
                    if version_detail["move_learn_method"]["name"] == "machine":
                        move_name = move_entry["move"]["name"]
                        if move_name not in seen:
                            seen.add(move_name)
                            result.append(move_name)
                            if len(result) == 4:
                                break
                    break
            if len(result) == 4:
                break

    return result


def fetch_pokemon():
    """Fetch all 151 Gen 1 Pokemon."""
    pokemon_list = []
    all_move_names = set()

    for dex_id in range(1, 152):
        print(f"Fetching Pokemon #{dex_id}...", end=" ", flush=True)
        data = fetch_json(f"{BASE_URL}/pokemon/{dex_id}")

        # Get Gen 1 types (from red-blue)
        types = [t["type"]["name"] for t in data["types"]]
        # Filter to Gen 1 types only
        types = [t for t in types if t in GEN1_TYPES]

        # Get Gen 1 base stats
        stats = {}
        for stat in data["stats"]:
            stat_name = stat["stat"]["name"]
            if stat_name == "hp":
                stats["hp"] = stat["base_stat"]
            elif stat_name == "attack":
                stats["attack"] = stat["base_stat"]
            elif stat_name == "defense":
                stats["defense"] = stat["base_stat"]
            elif stat_name in ("special-attack", "special-defense"):
                # Gen 1 uses single "Special" stat - use special-attack as base
                # (PokeAPI stores Gen 1 Special as special-attack for Gen 1 Pokemon)
                if "special" not in stats:
                    stats["special"] = stat["base_stat"]
            elif stat_name == "speed":
                stats["speed"] = stat["base_stat"]

        # Get moves
        moves = get_gen1_moves_for_pokemon(data)
        all_move_names.update(moves)

        pokemon = {
            "id": dex_id,
            "name": data["name"].capitalize(),
            "types": types,
            "base_stats": stats,
            "moves": moves
        }
        pokemon_list.append(pokemon)
        print(f"{pokemon['name']} ({', '.join(types)}) - {moves}")

        # Be nice to the API
        if dex_id % 10 == 0:
            time.sleep(1)

    return pokemon_list, all_move_names


def fetch_moves(move_names):
    """Fetch all referenced move data."""
    moves = {}
    total = len(move_names)

    for i, move_name in enumerate(sorted(move_names), 1):
        print(f"Fetching move {i}/{total}: {move_name}...", end=" ", flush=True)
        try:
            data = fetch_json(f"{BASE_URL}/move/{move_name}")
        except Exception as e:
            print(f"FAILED: {e}")
            continue

        # Determine category based on Gen 1 rules (type determines physical/special)
        move_type = data["type"]["name"]
        if move_type not in GEN1_TYPES:
            print(f"SKIPPED (non-Gen1 type: {move_type})")
            continue

        power = data["power"]
        if power is None:
            power = 0

        accuracy = data["accuracy"]
        if accuracy is None:
            accuracy = 100  # Status moves that always hit

        pp = data["pp"] or 10

        # Determine category
        if power == 0:
            category = "status"
        elif move_type in PHYSICAL_TYPES:
            category = "physical"
        else:
            category = "special"

        # Determine effect
        effect = None
        effect_chance = 0

        # Check for status effects from meta data
        meta = data.get("meta")
        if meta:
            ailment = meta.get("ailment", {})
            ailment_name = ailment.get("name", "none") if ailment else "none"
            ailment_chance = meta.get("ailment_chance", 0)

            effect_map = {
                "burn": "burn",
                "freeze": "freeze",
                "paralysis": "paralyze",
                "poison": "poison",
                "sleep": "sleep",
                "confusion": "confuse",
            }

            if ailment_name in effect_map:
                effect = effect_map[ailment_name]
                effect_chance = ailment_chance if ailment_chance > 0 else 100

        move = {
            "name": data["name"].replace("-", " ").title(),
            "type": move_type,
            "category": category,
            "power": power,
            "accuracy": accuracy,
            "pp": pp,
            "effect": effect,
            "effect_chance": effect_chance
        }
        moves[move_name] = move
        print(f"{move['name']} ({move_type}, {category}, power={power})")

        if i % 10 == 0:
            time.sleep(1)

    return moves


def generate_typechart():
    """Generate Gen 1 type effectiveness chart."""
    # Initialize all to 1.0
    chart = {}
    for atk in GEN1_TYPES:
        chart[atk] = {}
        for dfn in GEN1_TYPES:
            chart[atk][dfn] = 1.0

    # Super effective (2x)
    super_effective = {
        "fire": ["grass", "ice", "bug"],
        "water": ["fire", "ground", "rock"],
        "electric": ["water", "flying"],
        "grass": ["water", "ground", "rock"],
        "ice": ["grass", "ground", "flying", "dragon"],
        "fighting": ["normal", "ice", "rock"],
        "poison": ["grass", "bug"],
        "ground": ["fire", "electric", "poison", "rock"],
        "flying": ["grass", "fighting", "bug"],
        "psychic": ["fighting", "poison"],
        "bug": ["grass", "poison", "psychic"],
        "rock": ["fire", "ice", "flying", "bug"],
        "ghost": ["ghost"],
        "dragon": ["dragon"],
    }

    # Not very effective (0.5x)
    not_very_effective = {
        "normal": ["rock"],
        "fire": ["fire", "water", "rock", "dragon"],
        "water": ["water", "grass", "dragon"],
        "electric": ["electric", "grass", "dragon"],
        "grass": ["fire", "grass", "poison", "flying", "bug", "dragon"],
        "ice": ["fire", "water", "ice"],
        "fighting": ["poison", "flying", "psychic", "bug"],
        "poison": ["poison", "ground", "rock", "ghost"],
        "ground": ["grass", "bug"],
        "flying": ["electric", "rock"],
        "psychic": ["psychic"],
        "bug": ["fire", "fighting", "flying", "ghost"],
        "rock": ["fighting", "ground"],
        "ghost": [],
        "dragon": [],
    }

    # Immunities (0x)
    immunities = {
        "normal": ["ghost"],
        "electric": ["ground"],
        "fighting": ["ghost"],
        "poison": [],
        "ground": ["flying"],
        "psychic": ["ghost"],  # Gen 1 bug: Ghost doesn't affect Psychic
        "ghost": ["normal"],
    }

    for atk, targets in super_effective.items():
        for dfn in targets:
            chart[atk][dfn] = 2.0

    for atk, targets in not_very_effective.items():
        for dfn in targets:
            chart[atk][dfn] = 0.5

    for atk, targets in immunities.items():
        for dfn in targets:
            chart[atk][dfn] = 0.0

    return chart


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    # Generate type chart first (no API needed)
    print("=== Generating Type Chart ===")
    typechart = generate_typechart()
    with open(os.path.join(DATA_DIR, "typechart.json"), "w") as f:
        json.dump(typechart, f, indent=2)
    print(f"Saved typechart.json ({len(GEN1_TYPES)} types)")

    # Fetch Pokemon
    print("\n=== Fetching Pokemon ===")
    pokemon_list, all_move_names = fetch_pokemon()

    # Fetch moves
    print(f"\n=== Fetching {len(all_move_names)} Moves ===")
    moves = fetch_moves(all_move_names)

    # Validate: ensure all Pokemon moves exist in moves dict
    print("\n=== Validating ===")
    missing_moves = set()
    for poke in pokemon_list:
        valid_moves = []
        for m in poke["moves"]:
            if m in moves:
                valid_moves.append(m)
            else:
                missing_moves.add(m)
        poke["moves"] = valid_moves

    if missing_moves:
        print(f"Warning: {len(missing_moves)} moves not found: {missing_moves}")

    # Check Pokemon with <4 moves
    short_pokemon = [p for p in pokemon_list if len(p["moves"]) < 4]
    if short_pokemon:
        print(f"Warning: {len(short_pokemon)} Pokemon have <4 moves:")
        for p in short_pokemon:
            print(f"  #{p['id']} {p['name']}: {p['moves']}")

    # Save
    with open(os.path.join(DATA_DIR, "pokemon.json"), "w") as f:
        json.dump(pokemon_list, f, indent=2)
    print(f"\nSaved pokemon.json ({len(pokemon_list)} Pokemon)")

    with open(os.path.join(DATA_DIR, "moves.json"), "w") as f:
        json.dump(moves, f, indent=2)
    print(f"Saved moves.json ({len(moves)} moves)")

    print("\nDone!")


if __name__ == "__main__":
    main()
