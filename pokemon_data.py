"""Load and validate Gen 1 Pokemon data at startup."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Global data loaded at startup
POKEMON = {}      # dex_id (int) -> pokemon dict
MOVES = {}        # move_id (str) -> move dict
TYPE_CHART = {}   # atk_type -> def_type -> multiplier
LEARNSETS = {}    # str(dex_id) -> [{level, move}]
EVOLUTIONS = {}   # str(dex_id) -> {evolves_to, level, method}
POKEMON_LIST = [] # ordered list for client


def load_data():
    """Load all JSON data files. Call once at startup."""
    global POKEMON, MOVES, TYPE_CHART, LEARNSETS, EVOLUTIONS, POKEMON_LIST

    with open(os.path.join(DATA_DIR, "pokemon.json")) as f:
        pokemon_list = json.load(f)

    with open(os.path.join(DATA_DIR, "moves.json")) as f:
        MOVES = json.load(f)

    with open(os.path.join(DATA_DIR, "typechart.json")) as f:
        TYPE_CHART = json.load(f)

    learnsets_path = os.path.join(DATA_DIR, "learnsets.json")
    if os.path.exists(learnsets_path):
        with open(learnsets_path) as f:
            LEARNSETS = json.load(f)

    evolutions_path = os.path.join(DATA_DIR, "evolutions.json")
    if os.path.exists(evolutions_path):
        with open(evolutions_path) as f:
            EVOLUTIONS = json.load(f)

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


def get_learnset(dex_id):
    """Get level-up learnset for a Pokemon."""
    return LEARNSETS.get(str(dex_id), [])


def get_evolution(dex_id):
    """Get level-based evolution data for a Pokemon. Returns None if no evolution.
    For multi-path Pokemon (arrays), returns the first level-based option."""
    evo = EVOLUTIONS.get(str(dex_id))
    if evo is None:
        return None
    if isinstance(evo, list):
        # Multi-path: find a level-based evolution
        for e in evo:
            if e.get("method") == "level":
                return e
        return None  # No level-based evolution
    return evo


def get_all_evolutions(dex_id):
    """Get all evolution paths for a Pokemon (level, stone, item). Returns list."""
    evo = EVOLUTIONS.get(str(dex_id))
    if evo is None:
        return []
    if isinstance(evo, list):
        return evo
    return [evo]


def get_item_evolution(dex_id, item_id):
    """Find an evolution for a Pokemon using a specific item. Returns dict or None."""
    evos = get_all_evolutions(dex_id)
    for e in evos:
        if e.get("method") in ("stone", "item") and e.get("item") == item_id:
            return e
    return None


def get_moves_at_level(dex_id, level):
    """Get the moves a Pokemon should know at a given level.
    Returns the last 4 moves learned at or below the given level.
    """
    learnset = get_learnset(dex_id)
    available = [m["move"] for m in learnset if m["level"] <= level]
    # Take the last 4 (most recent moves)
    return available[-4:] if len(available) > 4 else available


def get_new_moves_for_level(dex_id, old_level, new_level):
    """Get moves learned between old_level (exclusive) and new_level (inclusive)."""
    learnset = get_learnset(dex_id)
    return [m for m in learnset if old_level < m["level"] <= new_level]


def get_starter_moves(dex_id):
    """Get starting moves for a new Pokemon (level 1-5 moves, max 2)."""
    learnset = get_learnset(dex_id)
    moves = [m["move"] for m in learnset if m["level"] <= 5]
    return moves[:2] if moves else ["tackle"]


def get_pokemon_list_for_client():
    """Get the full Pokemon list formatted for the client."""
    return POKEMON_LIST
