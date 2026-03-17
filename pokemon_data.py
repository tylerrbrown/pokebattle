"""Load and validate Gen 1 Pokemon data at startup."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Global data loaded at startup
POKEMON = {}      # dex_id (int) -> pokemon dict
MOVES = {}        # move_id (str) -> move dict
TYPE_CHART = {}   # atk_type -> def_type -> multiplier
POKEMON_LIST = [] # ordered list for client


def load_data():
    """Load all JSON data files. Call once at startup."""
    global POKEMON, MOVES, TYPE_CHART, POKEMON_LIST

    with open(os.path.join(DATA_DIR, "pokemon.json")) as f:
        pokemon_list = json.load(f)

    with open(os.path.join(DATA_DIR, "moves.json")) as f:
        MOVES = json.load(f)

    with open(os.path.join(DATA_DIR, "typechart.json")) as f:
        TYPE_CHART = json.load(f)

    # Index by dex ID
    POKEMON = {p["id"]: p for p in pokemon_list}

    # Build client-safe list (no need to hide anything, but ensure structure)
    POKEMON_LIST = []
    for p in pokemon_list:
        POKEMON_LIST.append({
            "id": p["id"],
            "name": p["name"],
            "types": p["types"],
            "base_stats": p["base_stats"],
            "moves": [
                {"id": mid, **MOVES[mid]}
                for mid in p["moves"]
                if mid in MOVES
            ]
        })

    # Validate
    errors = []
    for p in pokemon_list:
        if len(p["moves"]) < 4:
            errors.append(f"#{p['id']} {p['name']} has {len(p['moves'])} moves (need 4)")
        for m in p["moves"]:
            if m not in MOVES:
                errors.append(f"#{p['id']} {p['name']} references unknown move: {m}")
        if not p["types"]:
            errors.append(f"#{p['id']} {p['name']} has no types")

    if errors:
        print(f"Data validation warnings ({len(errors)}):")
        for e in errors:
            print(f"  {e}")
    else:
        print(f"Data loaded: {len(POKEMON)} Pokemon, {len(MOVES)} moves, {len(TYPE_CHART)} types")

    return len(errors) == 0


def get_pokemon(dex_id):
    """Get pokemon data by dex ID."""
    return POKEMON.get(dex_id)


def get_move(move_id):
    """Get move data by ID."""
    return MOVES.get(move_id)


def get_type_effectiveness(atk_type, def_type):
    """Get type effectiveness multiplier."""
    return TYPE_CHART.get(atk_type, {}).get(def_type, 1.0)


def get_pokemon_list_for_client():
    """Get the full Pokemon list formatted for the client."""
    return POKEMON_LIST
