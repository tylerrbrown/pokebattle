"""Fetch base_experience, catch_rate, learnsets, and evolution data from PokeAPI.

Enriches data/pokemon.json with base_experience, catch_rate, and rarity.
Creates data/learnsets.json and data/evolutions.json.

Usage: python scripts/enrich_data.py
"""

import json
import os
import sys
import time
import urllib.request

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# Rarity assignments for Gen 1
LEGENDARY = {144, 145, 146, 150, 151}  # Articuno, Zapdos, Moltres, Mewtwo, Mew
RARE = {
    131, 143, 113, 115, 122, 123, 127, 128, 130, 137, 138, 139, 140, 141, 142,
    147, 148, 149,  # Lapras, Snorlax, Chansey, Kangaskhan, Mr. Mime, Scyther, Pinsir, Tauros, Gyarados, Porygon, Omanyte, Omastar, Kabuto, Kabutops, Aerodactyl, Dratini, Dragonair, Dragonite
}
UNCOMMON = {
    1, 4, 7,  # Starters
    25, 26,  # Pikachu, Raichu
    35, 36, 37, 38,  # Clefairy, Clefable, Vulpix, Ninetales
    58, 59,  # Growlithe, Arcanine
    63, 64, 65,  # Abra, Kadabra, Alakazam
    66, 67, 68,  # Machop, Machoke, Machamp
    72, 73,  # Tentacool, Tentacruel
    77, 78,  # Ponyta, Rapidash
    79, 80,  # Slowpoke, Slowbro
    81, 82,  # Magnemite, Magneton
    88, 89,  # Grimer, Muk
    92, 93, 94,  # Gastly, Haunter, Gengar
    95,  # Onix
    100, 101,  # Voltorb, Electrode
    102, 103,  # Exeggcute, Exeggutor
    104, 105,  # Cubone, Marowak
    106, 107, 108,  # Hitmonlee, Hitmonchan, Lickitung
    109, 110,  # Koffing, Weezing
    111, 112,  # Rhyhorn, Rhydon
    114,  # Tangela
    116, 117,  # Horsea, Seadra
    120, 121,  # Staryu, Starmie
    124, 125, 126,  # Jynx, Electabuzz, Magmar
    133, 134, 135, 136,  # Eevee + evos
}
# Everything else is Common


def fetch_json(url, retries=3):
    """Fetch JSON from URL with retries."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PokeBattle/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)
            else:
                print(f"  FAILED: {url} - {e}")
                return None


def get_rarity(dex_id, species_data=None, base_stats=None):
    """Assign rarity. Uses hardcoded Gen 1+2 sets, then falls back to auto-assign."""
    if dex_id in LEGENDARY:
        return "legendary"
    if dex_id in RARE:
        return "rare"
    if dex_id in UNCOMMON:
        return "uncommon"
    # For Pokemon beyond Gen 1+2 hardcoded sets, auto-assign
    if dex_id > 251:
        is_legend = False
        if species_data:
            is_legend = species_data.get("is_legendary", False) or species_data.get("is_mythical", False)
        if is_legend:
            return "legendary"
        if base_stats:
            bst = sum(base_stats.values())
            if bst >= 580:
                return "rare"
            elif bst >= 450:
                return "uncommon"
        return "common"
    return "common"


def main():
    # Load existing pokemon.json
    pokemon_path = os.path.join(DATA_DIR, "pokemon.json")
    with open(pokemon_path, "r") as f:
        pokemon_list = json.load(f)

    # Load existing moves.json to filter learnsets
    moves_path = os.path.join(DATA_DIR, "moves.json")
    with open(moves_path, "r") as f:
        existing_moves = set(json.load(f).keys())

    learnsets = {}
    evolutions = {}
    seen_chains = set()

    print(f"Enriching {len(pokemon_list)} Pokemon...")

    for i, poke in enumerate(pokemon_list):
        dex_id = poke["id"]
        print(f"  [{i+1}/{len(pokemon_list)}] #{dex_id} {poke['name']}...", end=" ", flush=True)

        # Fetch Pokemon data
        data = fetch_json(f"https://pokeapi.co/api/v2/pokemon/{dex_id}")
        if not data:
            poke["base_experience"] = 64
            poke["catch_rate"] = 45
            poke["rarity"] = get_rarity(dex_id)
            print("SKIPPED (API error)")
            continue

        poke["base_experience"] = data.get("base_experience") or 64

        # Fetch species for catch_rate and evolution chain
        species_data = fetch_json(f"https://pokeapi.co/api/v2/pokemon-species/{dex_id}")
        if species_data:
            poke["catch_rate"] = species_data.get("capture_rate", 45)
            poke["rarity"] = get_rarity(dex_id, species_data, poke.get("base_stats"))

            # Evolution chain
            chain_url = species_data.get("evolution_chain", {}).get("url")
            if chain_url and chain_url not in seen_chains:
                seen_chains.add(chain_url)
                chain_data = fetch_json(chain_url)
                if chain_data:
                    _parse_chain(chain_data["chain"], evolutions)
        else:
            poke["catch_rate"] = 45
            poke["rarity"] = get_rarity(dex_id, None, poke.get("base_stats"))

        # Learnset: level-up moves from best available version group
        VG_PRIORITY = [
            "red-blue", "gold-silver", "crystal",
            "ruby-sapphire", "emerald", "firered-leafgreen",
            "diamond-pearl", "platinum", "heartgold-soulsilver",
            "black-white", "black-2-white-2",
            "x-y", "omega-ruby-alpha-sapphire",
            "sun-moon", "ultra-sun-ultra-moon",
            "sword-shield", "scarlet-violet",
        ]
        level_moves = []
        for vg_name in VG_PRIORITY:
            for mv in data.get("moves", []):
                for vg in mv.get("version_group_details", []):
                    if (vg.get("version_group", {}).get("name") == vg_name and
                            vg.get("move_learn_method", {}).get("name") == "level-up"):
                        move_name = mv["move"]["name"]
                        level_moves.append({
                            "level": vg["level_learned_at"],
                            "move": move_name
                        })
            if level_moves:
                break

        # Sort by level, then alphabetically
        level_moves.sort(key=lambda x: (x["level"], x["move"]))
        learnsets[str(dex_id)] = level_moves
        print(f"OK ({len(level_moves)} moves)")

        # Be polite to PokeAPI
        time.sleep(0.15)

    # Save enriched pokemon.json
    with open(pokemon_path, "w") as f:
        json.dump(pokemon_list, f, indent=2)
    print(f"\nSaved enriched pokemon.json")

    # Save learnsets.json
    learnsets_path = os.path.join(DATA_DIR, "learnsets.json")
    with open(learnsets_path, "w") as f:
        json.dump(learnsets, f, indent=2)
    print(f"Saved learnsets.json ({len(learnsets)} Pokemon)")

    # Save evolutions.json
    evolutions_path = os.path.join(DATA_DIR, "evolutions.json")
    with open(evolutions_path, "w") as f:
        json.dump(evolutions, f, indent=2)
    print(f"Saved evolutions.json ({len(evolutions)} entries)")


def _parse_chain(chain, evolutions):
    """Recursively parse evolution chain."""
    species_url = chain.get("species", {}).get("url", "")
    # Extract dex ID from URL
    from_id = _id_from_url(species_url)

    for evo in chain.get("evolves_to", []):
        to_url = evo.get("species", {}).get("url", "")
        to_id = _id_from_url(to_url)

        if from_id and to_id:
            # Get evolution details
            details = evo.get("evolution_details", [{}])
            detail = details[0] if details else {}

            trigger = detail.get("trigger", {}).get("name", "unknown")
            min_level = detail.get("min_level")

            if trigger == "level-up" and min_level:
                evolutions[str(from_id)] = {
                    "evolves_to": to_id,
                    "level": min_level,
                    "method": "level"
                }
            elif trigger == "use-item":
                item = detail.get("item", {}).get("name", "unknown")
                evolutions[str(from_id)] = {
                    "evolves_to": to_id,
                    "method": "stone",
                    "item": item
                }
            elif trigger == "trade":
                evolutions[str(from_id)] = {
                    "evolves_to": to_id,
                    "method": "trade"
                }
            else:
                evolutions[str(from_id)] = {
                    "evolves_to": to_id,
                    "method": trigger,
                    "level": min_level
                }

        # Recurse
        _parse_chain(evo, evolutions)


def _id_from_url(url):
    """Extract Pokemon ID from PokeAPI URL."""
    if not url:
        return None
    parts = url.rstrip("/").split("/")
    try:
        return int(parts[-1])
    except (ValueError, IndexError):
        return None


if __name__ == "__main__":
    main()
