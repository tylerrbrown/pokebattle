#!/usr/bin/env python3
"""Fetch Pokemon data from PokeAPI and generate static JSON files.

Usage:
  python generate_data.py                  # Default: Gen 1+2 (1-251)
  python generate_data.py --start 252 --end 386  # Gen 3 only (append)
  python generate_data.py --start 1 --end 649    # Gen 1-5 (full regenerate)

Outputs:
  data/pokemon.json  - Pokemon with stats, types, and 4 moves each
  data/moves.json    - All referenced moves
  data/typechart.json - Type effectiveness matrix (18 types incl. Fairy)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

BASE_URL = "https://pokeapi.co/api/v2"
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

# All 18 Pokemon types
ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice",
    "fighting", "poison", "ground", "flying", "psychic",
    "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy"
]

# Physical types (Gen 1-3 physical/special split by type)
PHYSICAL_TYPES = {"normal", "fighting", "poison", "ground", "flying", "bug", "rock", "ghost", "steel"}


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


VERSION_GROUPS_PRIORITY = [
    "red-blue", "gold-silver", "crystal",
    "ruby-sapphire", "emerald", "firered-leafgreen",
    "diamond-pearl", "platinum", "heartgold-soulsilver",
    "black-white", "black-2-white-2",
    "x-y", "omega-ruby-alpha-sapphire",
    "sun-moon", "ultra-sun-ultra-moon",
    "sword-shield", "scarlet-violet",
]

def get_moves_for_pokemon(pokemon_data):
    """Extract the best 4 moves for a Pokemon from its learnset.

    Tries version groups in priority order, falling back to later games.
    Priority: level-up moves learned at lowest levels (birth moves first).
    """
    level_moves = []
    tm_moves = []

    for vg_name in VERSION_GROUPS_PRIORITY:
        for move_entry in pokemon_data["moves"]:
            for vd in move_entry["version_group_details"]:
                if vd["version_group"]["name"] == vg_name:
                    method = vd["move_learn_method"]["name"]
                    move_name = move_entry["move"]["name"]
                    if method == "level-up":
                        level_moves.append((vd["level_learned_at"], move_name))
                    elif method == "machine" and len(level_moves) < 4:
                        tm_moves.append(move_name)
        if level_moves:
            break  # Found moves in this version group

    level_moves.sort(key=lambda x: (x[0], x[1]))

    seen = set()
    result = []
    for level, move_name in level_moves:
        if move_name not in seen:
            seen.add(move_name)
            result.append(move_name)
            if len(result) == 4:
                break

    if len(result) < 4:
        for move_name in tm_moves:
            if move_name not in seen:
                seen.add(move_name)
                result.append(move_name)
                if len(result) == 4:
                    break

    return result


def fetch_pokemon(start=1, end=251):
    """Fetch Pokemon in the given dex range."""
    pokemon_list = []
    all_move_names = set()
    total = end - start + 1

    for dex_id in range(start, end + 1):
        print(f"Fetching Pokemon #{dex_id} [{dex_id-start+1}/{total}]...", end=" ", flush=True)
        data = fetch_json(f"{BASE_URL}/pokemon/{dex_id}")

        # Get types (all 18 types supported)
        types = [t["type"]["name"] for t in data["types"]]
        types = [t for t in types if t in ALL_TYPES]

        # Get base stats (use special-attack as "special" for our single-special system)
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
                if "special" not in stats:
                    stats["special"] = stat["base_stat"]
            elif stat_name == "speed":
                stats["speed"] = stat["base_stat"]

        # Get moves
        moves = get_moves_for_pokemon(data)
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

        # Determine category based on type (physical/special split)
        move_type = data["type"]["name"]
        if move_type not in ALL_TYPES:
            print(f"SKIPPED (unknown type: {move_type})")
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
    """Generate full 18-type effectiveness chart (incl. Dark, Steel, Fairy)."""
    chart = {}
    for atk in ALL_TYPES:
        chart[atk] = {}
        for dfn in ALL_TYPES:
            chart[atk][dfn] = 1.0

    super_effective = {
        "fire": ["grass", "ice", "bug", "steel"],
        "water": ["fire", "ground", "rock"],
        "electric": ["water", "flying"],
        "grass": ["water", "ground", "rock"],
        "ice": ["grass", "ground", "flying", "dragon"],
        "fighting": ["normal", "ice", "rock", "dark", "steel"],
        "poison": ["grass", "fairy"],
        "ground": ["fire", "electric", "poison", "rock", "steel"],
        "flying": ["grass", "fighting", "bug"],
        "psychic": ["fighting", "poison"],
        "bug": ["grass", "psychic", "dark"],
        "rock": ["fire", "ice", "flying", "bug"],
        "ghost": ["ghost", "psychic"],
        "dragon": ["dragon"],
        "dark": ["ghost", "psychic"],
        "steel": ["ice", "rock", "fairy"],
        "fairy": ["fighting", "dragon", "dark"],
    }

    not_very_effective = {
        "normal": ["rock", "steel"],
        "fire": ["fire", "water", "rock", "dragon"],
        "water": ["water", "grass", "dragon"],
        "electric": ["electric", "grass", "dragon"],
        "grass": ["fire", "grass", "poison", "flying", "bug", "dragon", "steel"],
        "ice": ["fire", "water", "ice", "steel"],
        "fighting": ["poison", "flying", "psychic", "bug", "fairy"],
        "poison": ["poison", "ground", "rock", "ghost"],
        "ground": ["grass", "bug"],
        "flying": ["electric", "rock", "steel"],
        "psychic": ["psychic", "steel"],
        "bug": ["fire", "fighting", "flying", "ghost", "poison", "steel", "fairy"],
        "rock": ["fighting", "ground", "steel"],
        "ghost": ["dark"],
        "dragon": ["steel"],
        "dark": ["fighting", "dark", "fairy"],
        "steel": ["fire", "water", "electric", "steel"],
        "fairy": ["fire", "poison", "steel"],
    }

    immunities = {
        "normal": ["ghost"],
        "electric": ["ground"],
        "fighting": ["ghost"],
        "ground": ["flying"],
        "ghost": ["normal"],
        "psychic": ["dark"],
        "dragon": ["fairy"],
        "poison": ["steel"],
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
    parser = argparse.ArgumentParser(description="Generate PokeBattle data from PokeAPI")
    parser.add_argument("--start", type=int, default=1, help="Starting dex ID (default: 1)")
    parser.add_argument("--end", type=int, default=251, help="Ending dex ID (default: 251)")
    parser.add_argument("--append", action="store_true", help="Append to existing data files instead of overwriting")
    args = parser.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)

    # Generate type chart (always full 18 types)
    print("=== Generating Type Chart ===")
    typechart = generate_typechart()
    with open(os.path.join(DATA_DIR, "typechart.json"), "w") as f:
        json.dump(typechart, f, indent=2)
    print(f"Saved typechart.json ({len(ALL_TYPES)} types)")

    # Load existing data if appending
    existing_pokemon = []
    existing_moves = {}
    if args.append:
        poke_path = os.path.join(DATA_DIR, "pokemon.json")
        move_path = os.path.join(DATA_DIR, "moves.json")
        if os.path.exists(poke_path):
            with open(poke_path) as f:
                existing_pokemon = json.load(f)
            print(f"Loaded {len(existing_pokemon)} existing Pokemon")
        if os.path.exists(move_path):
            with open(move_path) as f:
                existing_moves = json.load(f)
            print(f"Loaded {len(existing_moves)} existing moves")

    # Fetch Pokemon
    print(f"\n=== Fetching Pokemon #{args.start}-{args.end} ===")
    pokemon_list, all_move_names = fetch_pokemon(args.start, args.end)

    # Remove existing moves from fetch list
    new_move_names = all_move_names - set(existing_moves.keys())
    print(f"\n=== Fetching {len(new_move_names)} New Moves (skipping {len(all_move_names) - len(new_move_names)} already known) ===")
    new_moves = fetch_moves(new_move_names)
    moves = {**existing_moves, **new_moves}

    # Validate
    print("\n=== Validating ===")
    missing_moves = set()
    for poke in pokemon_list:
        valid_moves = [m for m in poke["moves"] if m in moves]
        missing_moves.update(set(poke["moves"]) - set(valid_moves))
        poke["moves"] = valid_moves

    if missing_moves:
        print(f"Warning: {len(missing_moves)} moves not found: {missing_moves}")

    short_pokemon = [p for p in pokemon_list if len(p["moves"]) < 4]
    if short_pokemon:
        print(f"Warning: {len(short_pokemon)} Pokemon have <4 moves:")
        for p in short_pokemon:
            print(f"  #{p['id']} {p['name']}: {p['moves']}")

    # Merge with existing if appending
    if args.append:
        existing_ids = {p["id"] for p in existing_pokemon}
        for p in pokemon_list:
            if p["id"] not in existing_ids:
                existing_pokemon.append(p)
        existing_pokemon.sort(key=lambda p: p["id"])
        pokemon_list = existing_pokemon

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
