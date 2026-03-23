"""Load and validate Pokemon data at startup."""

import json
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Global data loaded at startup
POKEMON = {}      # dex_id (int) -> pokemon dict
MOVES = {}        # move_id (str) -> move dict
TYPE_CHART = {}   # atk_type -> def_type -> multiplier
LEARNSETS = {}    # str(dex_id) -> [{level, move}]
EVOLUTIONS = {}   # str(dex_id) -> {evolves_to, level, method}
ZMOVES = {}       # type (str) -> {name, power_mult}
MEGA_EVOLUTIONS = {}  # str(dex_id) -> mega form data (or list for dual megas)
DYNAMAX = {}      # dynamax data: max_move_powers, max_move_names, gigantamax
REGIONS = []      # region data from regions.json
POKEMON_LIST = [] # ordered list for client
_NAME_TO_ID = {}  # lowercase name -> dex_id (built during load)


def load_data():
    """Load all JSON data files. Call once at startup."""
    global POKEMON, MOVES, TYPE_CHART, LEARNSETS, EVOLUTIONS, ZMOVES, MEGA_EVOLUTIONS, DYNAMAX, REGIONS, POKEMON_LIST, _NAME_TO_ID

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

    zmoves_path = os.path.join(DATA_DIR, "zmoves.json")
    if os.path.exists(zmoves_path):
        with open(zmoves_path) as f:
            ZMOVES = json.load(f)

    mega_path = os.path.join(DATA_DIR, "mega_evolutions.json")
    if os.path.exists(mega_path):
        with open(mega_path) as f:
            MEGA_EVOLUTIONS = json.load(f)

    dynamax_path = os.path.join(DATA_DIR, "dynamax.json")
    if os.path.exists(dynamax_path):
        with open(dynamax_path) as f:
            DYNAMAX = json.load(f)

    regions_path = os.path.join(DATA_DIR, "regions.json")
    if os.path.exists(regions_path):
        with open(regions_path) as f:
            REGIONS = json.load(f)

    # Index by dex ID
    POKEMON = {p["id"]: p for p in pokemon_list}

    # Build name -> dex_id lookup
    _NAME_TO_ID = {p["name"].lower(): p["id"] for p in pokemon_list}

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
    Returns the last 4 unique moves learned at or below the given level.
    Only includes moves that exist in MOVES (filters out invalid learnset entries).
    """
    learnset = get_learnset(dex_id)
    seen = set()
    available = []
    for m in learnset:
        if m["level"] <= level and m["move"] in MOVES and m["move"] not in seen:
            seen.add(m["move"])
            available.append(m["move"])
    # Take the last 4 (most recent moves)
    return available[-4:] if len(available) > 4 else available


def get_initial_moves(dex_id, level):
    """Get initial moves, guaranteeing up to 4 when possible.
    Uses learnset first, then supplements from pokemon.json defaults."""
    moves = get_moves_at_level(dex_id, level)
    if len(moves) < 4:
        species = POKEMON.get(dex_id)
        if species:
            for mid in species["moves"]:
                if mid not in moves and mid in MOVES:
                    moves.append(mid)
                    if len(moves) >= 4:
                        break
    return moves


def get_new_moves_for_level(dex_id, old_level, new_level):
    """Get moves learned between old_level (exclusive) and new_level (inclusive)."""
    learnset = get_learnset(dex_id)
    return [m for m in learnset if old_level < m["level"] <= new_level]


def get_max_move_power(base_power):
    """Get Max Move power from base move power."""
    powers = DYNAMAX.get("max_move_powers", {})
    # Find the closest key <= base_power
    best = 90
    for k, v in powers.items():
        if int(k) <= base_power:
            best = v
    return best


def get_max_move_name(move_type):
    """Get Max Move name for a given type."""
    return DYNAMAX.get("max_move_names", {}).get(move_type, "Max Strike")


def get_gmax_data(dex_id):
    """Get Gigantamax data for a Pokemon. Returns dict or None."""
    return DYNAMAX.get("gigantamax", {}).get(str(dex_id))


def get_starter_moves(dex_id):
    """Get starting moves for a new Pokemon (level 1-5 moves, max 2)."""
    learnset = get_learnset(dex_id)
    moves = [m["move"] for m in learnset if m["level"] <= 5]
    return moves[:2] if moves else ["tackle"]


def get_pokemon_list_for_client():
    """Get the full Pokemon list formatted for the client."""
    return POKEMON_LIST


# ─── Region Helpers ────────────────────────────────────

def get_all_regions():
    """Return the full REGIONS list."""
    return REGIONS


def get_region(region_id):
    """Get region dict by ID (e.g., 'kanto'). Returns None if not found."""
    for r in REGIONS:
        if r["id"] == region_id:
            return r
    return None


def get_region_pokemon_ids(region_id):
    """Get set of dex IDs in a region's dex range."""
    region = get_region(region_id)
    if not region:
        return set()
    lo, hi = region["dex_range"]
    return set(range(lo, hi + 1))


def get_region_gyms(region_id):
    """Get gym list from region data."""
    region = get_region(region_id)
    if not region:
        return []
    return region.get("gyms", [])


def get_region_elite_four(region_id):
    """Get Elite Four list from region data."""
    region = get_region(region_id)
    if not region:
        return []
    return region.get("elite_four", [])


def get_region_champion(region_id):
    """Get champion data from region data."""
    region = get_region(region_id)
    if not region:
        return None
    return region.get("champion")


def resolve_species_name(name):
    """Look up a dex_id from a Pokemon species name (case-insensitive).
    Handles form names: 'Lycanroc' matches 'Lycanroc-midday', 'Mr. Mime' matches 'Mr-mime', etc.
    Returns int or None."""
    lower = name.lower()
    # Exact match first
    result = _NAME_TO_ID.get(lower)
    if result:
        return result
    # Try replacing periods and spaces with hyphens (Mr. Mime -> mr-mime)
    alt = lower.replace(". ", "-").replace(".", "-").replace(" ", "-")
    result = _NAME_TO_ID.get(alt)
    if result:
        return result
    # Prefix match: 'Lycanroc' matches 'Lycanroc-midday'
    for db_name, dex_id in _NAME_TO_ID.items():
        if db_name.startswith(lower + "-") or db_name.startswith(alt + "-"):
            return dex_id
    return None
