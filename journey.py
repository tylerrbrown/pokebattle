"""Journey Mode orchestrator for PokeBattle.

Handles wild encounters, catch mechanics, gym battles, Elite Four,
Champion, and Masters Eight progression.
"""

import asyncio
import json
import math
import random
import time

from battle_engine import (
    PokemonInstance, build_journey_team, calculate_damage,
    resolve_turn, STRUGGLE, DEFAULT_LEVEL
)
import pokemon_data
from pokemon_data import get_type_effectiveness
from ai_player import BotPlayer

# ─── Constants ────────────────────────────────────────

RARITY_WEIGHTS = {"common": 60, "uncommon": 25, "rare": 13, "legendary": 1}
PITY_THRESHOLD = 50

BALL_MODIFIERS = {
    "pokeball": 1.0,
    "greatball": 1.5,
    "ultraball": 2.0,
}

SHOP_ITEMS = {
    "pokeball":  {"name": "Poké Ball",  "price": 200,  "ball_modifier": 1.0, "category": "ball"},
    "greatball": {"name": "Great Ball", "price": 600,  "ball_modifier": 1.5, "category": "ball"},
    "ultraball": {"name": "Ultra Ball", "price": 1200, "ball_modifier": 2.0, "category": "ball"},
    "potion":        {"name": "Potion",        "price": 300,  "category": "healing", "heal_hp": 20},
    "super_potion":  {"name": "Super Potion",  "price": 700,  "category": "healing", "heal_hp": 50},
    "hyper_potion":  {"name": "Hyper Potion",  "price": 1200, "category": "healing", "heal_hp": 200},
    "revive":        {"name": "Revive",        "price": 1500, "category": "healing", "revive": True, "heal_pct": 0.5},
    "full_restore":  {"name": "Full Restore",  "price": 3000, "category": "healing", "heal_full": True, "cure_status": True},
    "fire-stone":    {"name": "Fire Stone",    "price": 2100, "category": "evolution"},
    "water-stone":   {"name": "Water Stone",   "price": 2100, "category": "evolution"},
    "thunder-stone": {"name": "Thunder Stone", "price": 2100, "category": "evolution"},
    "leaf-stone":    {"name": "Leaf Stone",    "price": 2100, "category": "evolution"},
    "moon-stone":    {"name": "Moon Stone",    "price": 2100, "category": "evolution"},
    "sun-stone":     {"name": "Sun Stone",     "price": 2100, "category": "evolution"},
    "link-cable":    {"name": "Link Cable",    "price": 3000, "category": "evolution"},
    # Z-Crystals (one per type)
    "z-normal":   {"name": "Normalium Z",   "price": 5000, "category": "z-crystal", "z_type": "normal"},
    "z-fire":     {"name": "Firium Z",      "price": 5000, "category": "z-crystal", "z_type": "fire"},
    "z-water":    {"name": "Waterium Z",    "price": 5000, "category": "z-crystal", "z_type": "water"},
    "z-electric": {"name": "Electrium Z",   "price": 5000, "category": "z-crystal", "z_type": "electric"},
    "z-grass":    {"name": "Grassium Z",    "price": 5000, "category": "z-crystal", "z_type": "grass"},
    "z-ice":      {"name": "Icium Z",       "price": 5000, "category": "z-crystal", "z_type": "ice"},
    "z-fighting": {"name": "Fightinium Z",  "price": 5000, "category": "z-crystal", "z_type": "fighting"},
    "z-poison":   {"name": "Poisonium Z",   "price": 5000, "category": "z-crystal", "z_type": "poison"},
    "z-ground":   {"name": "Groundium Z",   "price": 5000, "category": "z-crystal", "z_type": "ground"},
    "z-flying":   {"name": "Flyinium Z",    "price": 5000, "category": "z-crystal", "z_type": "flying"},
    "z-psychic":  {"name": "Psychium Z",    "price": 5000, "category": "z-crystal", "z_type": "psychic"},
    "z-bug":      {"name": "Buginium Z",    "price": 5000, "category": "z-crystal", "z_type": "bug"},
    "z-rock":     {"name": "Rockium Z",     "price": 5000, "category": "z-crystal", "z_type": "rock"},
    "z-ghost":    {"name": "Ghostium Z",    "price": 5000, "category": "z-crystal", "z_type": "ghost"},
    "z-dragon":   {"name": "Dragonium Z",   "price": 5000, "category": "z-crystal", "z_type": "dragon"},
    "z-dark":     {"name": "Darkinium Z",   "price": 5000, "category": "z-crystal", "z_type": "dark"},
    "z-steel":    {"name": "Steelium Z",    "price": 5000, "category": "z-crystal", "z_type": "steel"},
    "z-fairy":    {"name": "Fairium Z",     "price": 5000, "category": "z-crystal", "z_type": "fairy"},
    # Rare Candy
    "rare_candy":      {"name": "Rare Candy",      "price": 500,   "category": "rare_candy", "levels": 1},
    "rare_candy_xl":   {"name": "Rare Candy XL",   "price": 2000,  "category": "rare_candy", "levels": 5},
    "rare_candy_xxl":  {"name": "Rare Candy XXL",  "price": 5000,  "category": "rare_candy", "levels": 10},

    # Mega Stones
    "venusaurite":     {"name": "Venusaurite",     "price": 10000, "category": "mega-stone", "mega_for": 3},
    "charizardite-x":  {"name": "Charizardite X",  "price": 10000, "category": "mega-stone", "mega_for": 6},
    "charizardite-y":  {"name": "Charizardite Y",  "price": 10000, "category": "mega-stone", "mega_for": 6},
    "blastoisinite":   {"name": "Blastoisinite",   "price": 10000, "category": "mega-stone", "mega_for": 9},
    "alakazite":       {"name": "Alakazite",        "price": 10000, "category": "mega-stone", "mega_for": 65},
    "gengarite":       {"name": "Gengarite",        "price": 10000, "category": "mega-stone", "mega_for": 94},
    "kangaskhanite":   {"name": "Kangaskhanite",    "price": 10000, "category": "mega-stone", "mega_for": 115},
    "gyaradosite":     {"name": "Gyaradosite",      "price": 10000, "category": "mega-stone", "mega_for": 130},
    "aerodactylite":   {"name": "Aerodactylite",    "price": 10000, "category": "mega-stone", "mega_for": 142},
    "mewtwonite-x":    {"name": "Mewtwonite X",    "price": 10000, "category": "mega-stone", "mega_for": 150},
    "mewtwonite-y":    {"name": "Mewtwonite Y",    "price": 10000, "category": "mega-stone", "mega_for": 150},
    "scizorite":       {"name": "Scizorite",        "price": 10000, "category": "mega-stone", "mega_for": 212},
    "tyranitarite":    {"name": "Tyranitarite",     "price": 10000, "category": "mega-stone", "mega_for": 248},
    # Held Items
    "lucky-egg":       {"name": "Lucky Egg",        "price": 1500, "category": "held"},
}

# Currency awards
CURRENCY_WILD_WIN = 50
CURRENCY_WILD_CATCH = 100
CURRENCY_GYM_WIN = 500
CURRENCY_ELITE_FOUR_WIN = 1000
CURRENCY_CHAMPION_WIN = 5000
CURRENCY_PVP_WIN = 500
CURRENCY_PVP_BOT_WIN = 300
CURRENCY_MASTERS_WIN = 2000


# ─── Wild Encounter ──────────────────────────────────

def generate_wild_pokemon(player_team_avg_level, pity_counter=0, region=None):
    """Pick a random wild Pokemon based on rarity weights, scaled to player level.
    If pity_counter >= PITY_THRESHOLD, forces a legendary encounter.
    If region is set, 85% chance to filter to that region's dex range, 15% global.
    """
    pokemon_list = list(pokemon_data.POKEMON.values())

    # Region filtering: 85% regional, 15% global
    if region and region != "all":
        region_ids = pokemon_data.get_region_pokemon_ids(region)
        if region_ids and random.random() < 0.85:
            regional = [p for p in pokemon_list if p["id"] in region_ids]
            if regional:
                pokemon_list = regional

    # Group by rarity
    by_rarity = {}
    for p in pokemon_list:
        r = p.get("rarity", "common")
        by_rarity.setdefault(r, []).append(p)

    # Pity system: guarantee legendary every PITY_THRESHOLD encounters
    if pity_counter >= PITY_THRESHOLD:
        rarity = "legendary"
    else:
        # Weighted rarity selection
        rarity = random.choices(
            list(RARITY_WEIGHTS.keys()),
            weights=list(RARITY_WEIGHTS.values()),
            k=1
        )[0]

    pool = by_rarity.get(rarity, by_rarity.get("common", []))
    if not pool:
        # Fallback to global pool if regional pool has no Pokemon of this rarity
        all_pokemon = list(pokemon_data.POKEMON.values())
        global_by_rarity = {}
        for p in all_pokemon:
            r = p.get("rarity", "common")
            global_by_rarity.setdefault(r, []).append(p)
        pool = global_by_rarity.get(rarity, global_by_rarity.get("common", []))
    species = random.choice(pool)

    # Level scales with player's team average
    base_level = max(2, int(player_team_avg_level) + random.randint(-3, 3))
    level = min(70, base_level)
    if rarity == "legendary":
        level = max(level, 50)  # Legendaries are always at least level 50

    # Get appropriate moves for this level
    moves = pokemon_data.get_moves_at_level(species["id"], level)
    if not moves:
        moves = species["moves"][:2]  # Fallback

    wild = PokemonInstance(species, pokemon_data.MOVES, level=level, custom_moves=moves)
    # 10% chance of shiny encounter
    is_shiny = random.random() < 0.10
    wild.is_shiny = is_shiny
    return wild, rarity


def calc_catch_rate(pokemon_catch_rate, current_hp, max_hp, ball_type="pokeball"):
    """Calculate catch success probability (0.0-1.0).

    Kid-friendly boost: legendaries get 10x, rare get 3x, common get 1.5x.
    """
    ball_mod = BALL_MODIFIERS.get(ball_type, 1.0)
    hp_factor = (3 * max_hp - 2 * current_hp) / (3 * max_hp)
    # Kid-friendly catch boost
    if pokemon_catch_rate <= 10:
        effective_rate = pokemon_catch_rate * 10  # Legendaries: ~2% -> ~24% at 1HP/Ultra
    elif pokemon_catch_rate <= 45:
        effective_rate = pokemon_catch_rate * 3   # Rare: much easier when weakened
    else:
        effective_rate = pokemon_catch_rate * 1.5  # Common: slight boost
    chance = (effective_rate * hp_factor * ball_mod) / 255.0
    return min(1.0, max(0.05, chance))  # Floor raised to 5%


def attempt_catch(pokemon_catch_rate, current_hp, max_hp, ball_type="pokeball"):
    """Attempt to catch a Pokemon. Returns (caught, shakes)."""
    chance = calc_catch_rate(pokemon_catch_rate, current_hp, max_hp, ball_type)

    # Simulate 1-3 shakes
    shakes = 0
    for _ in range(3):
        if random.random() < chance ** 0.33:  # Each shake is cube root of total chance
            shakes += 1
        else:
            break

    caught = shakes == 3
    return caught, shakes


class WildEncounter:
    """Manages a single wild Pokemon encounter."""

    TAP_PHASE_DURATION = 3
    TAP_PHASE_TIMEOUT = 5
    ACTION_TIMEOUT = 30

    def __init__(self, player, player_team, wild_pokemon, wild_rarity):
        self.player = player
        self.team = player_team  # List of PokemonInstance
        self.wild = wild_pokemon
        self.wild_rarity = wild_rarity
        self.active_idx = 0
        self.state = "ACTION_SELECT"
        self.turn_count = 0
        self.created_at = time.time()
        self.catch_window = False  # True after wild would have fainted — one chance to throw a ball

        # Find first non-fainted Pokemon
        for i, p in enumerate(self.team):
            if not p.is_fainted:
                self.active_idx = i
                break

    def get_active(self):
        return self.team[self.active_idx]

    def alive_indices(self):
        return [i for i, p in enumerate(self.team) if not p.is_fainted]

    def all_fainted(self):
        return all(p.is_fainted for p in self.team)

    def serialize_state(self):
        """Get current state for the client."""
        active = self.get_active()
        return {
            "wild_pokemon": {
                "dex_id": self.wild.dex_id,
                "name": self.wild.name,
                "types": self.wild.types,
                "level": self.wild.level,
                "max_hp": self.wild.max_hp,
                "current_hp": self.wild.current_hp,
                "status": self.wild.status,
                "rarity": self.wild_rarity,
                "is_mega": getattr(self.wild, 'is_mega', False),
                "is_shiny": getattr(self.wild, 'is_shiny', False),
            },
            "your_pokemon": active.serialize_full(),
            "your_team": [p.serialize_full() for p in self.team],
            "active_index": self.active_idx,
            "zmove_used": getattr(self, '_zmove_used', False),
            "mega_used": getattr(self, '_mega_used', False),
            "dynamax_used": getattr(self, '_dynamax_used', False),
        }


# ─── Gym Leaders ──────────────────────────────────────

GYM_LEADERS = [
    {
        "id": 1, "name": "Brock", "title": "The Rock-Solid Pokémon Trainer!",
        "type": "rock", "badge": "Boulder Badge",
        "team": [{"dex_id": 74, "level": 12}, {"dex_id": 95, "level": 14}],
        "reward_currency": 500,
        "dialog_intro": "I'm Brock! I'm the Pewter City Gym Leader! My rock-hard willpower is evident!",
        "dialog_win": "Your Pokémon's trust in you is incredible! Take this Boulder Badge!",
        "dialog_lose": "My rock-hard defense is unbeatable!",
    },
    {
        "id": 2, "name": "Misty", "title": "The Tomboyish Mermaid!",
        "type": "water", "badge": "Cascade Badge",
        "team": [{"dex_id": 120, "level": 18}, {"dex_id": 121, "level": 21}],
        "reward_currency": 600,
        "dialog_intro": "I'm Misty! My policy is an all-out offensive with water-type Pokémon!",
        "dialog_win": "You really are a tough trainer! Here, take the Cascade Badge!",
        "dialog_lose": "My water Pokémon washed you away!",
    },
    {
        "id": 3, "name": "Lt. Surge", "title": "The Lightning American!",
        "type": "electric", "badge": "Thunder Badge",
        "team": [{"dex_id": 100, "level": 21}, {"dex_id": 25, "level": 18}, {"dex_id": 26, "level": 24}],
        "reward_currency": 700,
        "dialog_intro": "I'm Lt. Surge! I've been to war and survived with my electric Pokémon!",
        "dialog_win": "The Thunder Badge is yours! Your Pokémon fought with real spirit!",
        "dialog_lose": "You're not strong enough to stand up to my lightning!",
    },
    {
        "id": 4, "name": "Erika", "title": "The Nature-Loving Princess!",
        "type": "grass", "badge": "Rainbow Badge",
        "team": [{"dex_id": 71, "level": 29}, {"dex_id": 114, "level": 24}, {"dex_id": 45, "level": 29}],
        "reward_currency": 800,
        "dialog_intro": "I'm Erika. I adore grass-type Pokémon. Shall we battle?",
        "dialog_win": "You're so talented! Here, take the Rainbow Badge!",
        "dialog_lose": "My grass Pokémon are truly elegant in battle!",
    },
    {
        "id": 5, "name": "Koga", "title": "The Poisonous Ninja Master!",
        "type": "poison", "badge": "Soul Badge",
        "team": [{"dex_id": 109, "level": 37}, {"dex_id": 89, "level": 39}, {"dex_id": 110, "level": 37}, {"dex_id": 49, "level": 43}],
        "reward_currency": 900,
        "dialog_intro": "I'm Koga! A master of poison techniques! Fwahahaha!",
        "dialog_win": "You have proven your worth! Take the Soul Badge!",
        "dialog_lose": "My poison brings swift defeat to all challengers!",
    },
    {
        "id": 6, "name": "Sabrina", "title": "The Master of Psychic Pokémon!",
        "type": "psychic", "badge": "Marsh Badge",
        "team": [{"dex_id": 64, "level": 38}, {"dex_id": 122, "level": 37}, {"dex_id": 49, "level": 38}, {"dex_id": 65, "level": 43}],
        "reward_currency": 1000,
        "dialog_intro": "I had a vision of you coming. I'm Sabrina. I see you losing!",
        "dialog_win": "Your power... it exceeds what I foresaw. Take the Marsh Badge.",
        "dialog_lose": "I foresaw my victory. Psychic power always prevails.",
    },
    {
        "id": 7, "name": "Blaine", "title": "The Hotheaded Quiz Master!",
        "type": "fire", "badge": "Volcano Badge",
        "team": [{"dex_id": 58, "level": 42}, {"dex_id": 77, "level": 40}, {"dex_id": 78, "level": 42}, {"dex_id": 59, "level": 47}],
        "reward_currency": 1100,
        "dialog_intro": "I'm Blaine! My fiery Pokémon will incinerate all challengers!",
        "dialog_win": "You have beaten my fire! Take the Volcano Badge as proof!",
        "dialog_lose": "My flames burn brighter than your ambition!",
    },
    {
        "id": 8, "name": "Giovanni", "title": "The Self-Proclaimed Strongest Trainer!",
        "type": "ground", "badge": "Earth Badge",
        "team": [{"dex_id": 111, "level": 45}, {"dex_id": 51, "level": 42}, {"dex_id": 31, "level": 44}, {"dex_id": 34, "level": 45}, {"dex_id": 112, "level": 50}],
        "reward_currency": 1500,
        "dialog_intro": "I'm Giovanni! I'll show you the true power of ground-type Pokémon!",
        "dialog_win": "Ha! You have proven yourself worthy! Take the Earth Badge!",
        "dialog_lose": "The power of the Earth crushes all who stand before me!",
    },
]

ELITE_FOUR = [
    {
        "id": "e4_1", "name": "Lorelei", "title": "Ice Master",
        "type": "ice", "reward_currency": 1000,
        "team": [{"dex_id": 87, "level": 54}, {"dex_id": 91, "level": 53},
                 {"dex_id": 80, "level": 54}, {"dex_id": 124, "level": 56}, {"dex_id": 131, "level": 56}],
        "dialog_intro": "Welcome to the Pokémon League! I am Lorelei. No one can best my icy Pokemon!",
        "dialog_win": "You're better than I thought! Go on ahead — the next challenge awaits!",
        "dialog_lose": "My icy Pokemon froze you solid!",
    },
    {
        "id": "e4_2", "name": "Bruno", "title": "Fighting Fury",
        "type": "fighting", "reward_currency": 1000,
        "team": [{"dex_id": 95, "level": 53}, {"dex_id": 107, "level": 55},
                 {"dex_id": 106, "level": 55}, {"dex_id": 95, "level": 56}, {"dex_id": 68, "level": 58}],
        "dialog_intro": "I am Bruno of the Elite Four! Through rigorous training, my fighting Pokemon have become the strongest!",
        "dialog_win": "Your strength exceeds mine! Move forward, challenger!",
        "dialog_lose": "My fists of fury are unmatched!",
    },
    {
        "id": "e4_3", "name": "Agatha", "title": "Ghost Specialist",
        "type": "ghost", "reward_currency": 1000,
        "team": [{"dex_id": 94, "level": 56}, {"dex_id": 42, "level": 56},
                 {"dex_id": 93, "level": 55}, {"dex_id": 24, "level": 58}, {"dex_id": 94, "level": 60}],
        "dialog_intro": "I am Agatha. I've heard of your exploits! Let me show you what ghost Pokemon can do!",
        "dialog_win": "You win! I see now why Oak's taken interest in you. Continue on!",
        "dialog_lose": "My ghosts will haunt your dreams, child!",
    },
    {
        "id": "e4_4", "name": "Lance", "title": "Dragon Master",
        "type": "dragon", "reward_currency": 1000,
        "team": [{"dex_id": 130, "level": 58}, {"dex_id": 148, "level": 56},
                 {"dex_id": 148, "level": 56}, {"dex_id": 142, "level": 60}, {"dex_id": 149, "level": 62}],
        "dialog_intro": "I am Lance, the last of the Elite Four! My dragon Pokemon will crush you!",
        "dialog_win": "Incredible! You've conquered the Elite Four! Now face the Champion!",
        "dialog_lose": "Dragons rule supreme! You cannot defeat the Dragon Master!",
    },
]

CHAMPION = {
    "id": "champion", "name": "Blue", "title": "Pokémon Champion",
    "type": "normal", "reward_currency": 5000,
    "team": [{"dex_id": 18, "level": 61}, {"dex_id": 65, "level": 59},
             {"dex_id": 112, "level": 61}, {"dex_id": 130, "level": 63},
             {"dex_id": 59, "level": 63}, {"dex_id": 103, "level": 65}],
    "dialog_intro": "So, you finally made it! I'm the Pokémon League Champion! You think you can beat ME?",
    "dialog_win": "NO! That's impossible! You beat me? ...Fine. You are the new Champion! Congratulations!",
    "dialog_lose": "Ha! I'm the Champion for a reason! Come back when you're stronger!",
}

MASTERS_EIGHT = [
    {
        "id": "m8_1", "name": "Leon", "title": "Unbeatable Champion",
        "type": "fire", "reward_currency": 2000,
        "team": [{"dex_id": 6, "level": 75}, {"dex_id": 149, "level": 73},
                 {"dex_id": 143, "level": 72}, {"dex_id": 94, "level": 74},
                 {"dex_id": 65, "level": 73}, {"dex_id": 131, "level": 76}],
        "dialog_intro": "I'm Leon! Let me show you what an unbeatable Champion looks like!",
        "dialog_win": "Brilliant! What a battle! You've got real championship spirit!",
        "dialog_lose": "I'm the unbeatable Champion for a reason! Better luck next time!",
    },
    {
        "id": "m8_2", "name": "Cynthia", "title": "Sinnoh's Finest",
        "type": "dragon", "reward_currency": 2000,
        "team": [{"dex_id": 130, "level": 73}, {"dex_id": 59, "level": 72},
                 {"dex_id": 112, "level": 74}, {"dex_id": 131, "level": 73},
                 {"dex_id": 68, "level": 74}, {"dex_id": 149, "level": 76}],
        "dialog_intro": "I'm Cynthia, Champion of Sinnoh. Are you ready for my challenge?",
        "dialog_win": "What an impressive display! You truly are a remarkable trainer!",
        "dialog_lose": "It seems you need more training. Come back when you're ready.",
    },
    {
        "id": "m8_3", "name": "Steven", "title": "Steel Collector",
        "type": "steel", "reward_currency": 2000,
        "team": [{"dex_id": 82, "level": 72}, {"dex_id": 76, "level": 73},
                 {"dex_id": 142, "level": 74}, {"dex_id": 91, "level": 73},
                 {"dex_id": 95, "level": 72}, {"dex_id": 141, "level": 75}],
        "dialog_intro": "I'm Steven Stone. My steel Pokemon and I have trained in the deepest caves!",
        "dialog_win": "Splendid! Your bond with your Pokemon is truly something special!",
        "dialog_lose": "Steel is unyielding. You'll need a stronger approach.",
    },
    {
        "id": "m8_4", "name": "Diantha", "title": "Kalos Queen",
        "type": "psychic", "reward_currency": 2000,
        "team": [{"dex_id": 36, "level": 72}, {"dex_id": 121, "level": 73},
                 {"dex_id": 103, "level": 74}, {"dex_id": 124, "level": 73},
                 {"dex_id": 126, "level": 74}, {"dex_id": 6, "level": 76}],
        "dialog_intro": "I'm Diantha. Shall we have a beautiful battle?",
        "dialog_win": "Magnifique! You battled with such grace and power!",
        "dialog_lose": "Beauty and strength go hand in hand. Perhaps next time!",
    },
    {
        "id": "m8_5", "name": "Lance", "title": "Dragon Master Supreme",
        "type": "dragon", "reward_currency": 2000,
        "team": [{"dex_id": 149, "level": 75}, {"dex_id": 130, "level": 74},
                 {"dex_id": 142, "level": 74}, {"dex_id": 6, "level": 73},
                 {"dex_id": 148, "level": 72}, {"dex_id": 149, "level": 78}],
        "dialog_intro": "We meet again! My dragons have grown even stronger since last time!",
        "dialog_win": "You've surpassed even the Dragon Master! Incredible!",
        "dialog_lose": "My dragons are supreme! None can defeat the Dragon Master!",
    },
    {
        "id": "m8_6", "name": "Iris", "title": "Dragon Prodigy",
        "type": "dragon", "reward_currency": 2000,
        "team": [{"dex_id": 149, "level": 74}, {"dex_id": 131, "level": 73},
                 {"dex_id": 59, "level": 74}, {"dex_id": 65, "level": 73},
                 {"dex_id": 112, "level": 75}, {"dex_id": 149, "level": 77}],
        "dialog_intro": "Hi! I'm Iris! Let's have the best battle ever!",
        "dialog_win": "Wow! That was so exciting! You're really, really strong!",
        "dialog_lose": "Yay! My Pokemon and I are the best team!",
    },
    {
        "id": "m8_7", "name": "Alder", "title": "Wandering Champion",
        "type": "bug", "reward_currency": 2000,
        "team": [{"dex_id": 127, "level": 73}, {"dex_id": 68, "level": 74},
                 {"dex_id": 143, "level": 75}, {"dex_id": 59, "level": 74},
                 {"dex_id": 9, "level": 74}, {"dex_id": 6, "level": 77}],
        "dialog_intro": "I'm Alder! I wander the land seeking powerful trainers. Show me your strength!",
        "dialog_win": "Ha ha! What a thrill! You've reignited my fighting spirit!",
        "dialog_lose": "Experience and wisdom triumph! Keep growing, young trainer!",
    },
    {
        "id": "m8_8", "name": "Red", "title": "The Living Legend",
        "type": "normal", "reward_currency": 3000,
        "team": [{"dex_id": 25, "level": 80}, {"dex_id": 3, "level": 77},
                 {"dex_id": 6, "level": 77}, {"dex_id": 9, "level": 77},
                 {"dex_id": 143, "level": 78}, {"dex_id": 131, "level": 78}],
        "dialog_intro": "...",
        "dialog_win": "... ... ... (Red nods respectfully.)",
        "dialog_lose": "...",
    },
]


def build_trainer_team(team_spec):
    """Build a team from a gym/E4/champion team spec.
    Supports both {dex_id, level} and {species, level} formats."""
    team = []
    for entry in team_spec:
        dex_id = entry.get("dex_id")
        if not dex_id and entry.get("species"):
            dex_id = pokemon_data.resolve_species_name(entry["species"])
        if not dex_id:
            continue
        species = pokemon_data.POKEMON.get(dex_id)
        if species:
            moves = pokemon_data.get_moves_at_level(dex_id, entry["level"])
            if not moves:
                moves = species["moves"][:4]
            team.append(PokemonInstance(species, pokemon_data.MOVES,
                                       level=entry["level"], custom_moves=moves))
    return team


def _normalize_region_gym(region_gym):
    """Convert a regions.json gym entry into the internal gym dict format."""
    g = region_gym
    return {
        "id": g["id"],
        "name": g["name"],
        "title": g.get("title", f"The {g['type'].title()} Gym Leader!"),
        "type": g["type"],
        "badge": g["badge"],
        "team": g["team"],
        "reward_currency": g.get("reward", 500),
        "dialog_intro": g.get("dialog", {}).get("intro", f"I'm {g['name']}! Let's battle!"),
        "dialog_win": g.get("dialog", {}).get("defeat", f"You've earned the {g['badge']}!"),
        "dialog_lose": g.get("dialog", {}).get("lose", "Better luck next time!"),
    }


def _normalize_region_e4(region_e4, index):
    """Convert a regions.json E4 entry into the internal E4 dict format."""
    e = region_e4
    return {
        "id": f"e4_{index + 1}",
        "name": e["name"],
        "title": e.get("title", f"{e['type'].title()} Expert"),
        "type": e["type"],
        "team": e["team"],
        "reward_currency": e.get("reward", 1000),
        "dialog_intro": e.get("dialog", {}).get("intro", f"I'm {e['name']} of the Elite Four!"),
        "dialog_win": e.get("dialog", {}).get("defeat", "You've beaten me! Move on!"),
        "dialog_lose": e.get("dialog", {}).get("lose", "The Elite Four stands supreme!"),
    }


def _normalize_region_champion(region_champ):
    """Convert a regions.json champion entry into the internal champion dict format."""
    c = region_champ
    return {
        "id": "champion",
        "name": c["name"],
        "title": c.get("title", "Pokemon Champion"),
        "type": c.get("type", "normal"),
        "team": c["team"],
        "reward_currency": c.get("reward", 5000),
        "dialog_intro": c.get("dialog", {}).get("intro", f"I'm {c['name']}, the Champion!"),
        "dialog_win": c.get("dialog", {}).get("defeat", "You are the new Champion!"),
        "dialog_lose": c.get("dialog", {}).get("lose", "The Champion stands supreme!"),
    }


def get_gym_leaders(region="kanto"):
    """Get all gym leaders for a region. Falls back to hardcoded Kanto for backward compat."""
    if region == "kanto" or not region:
        # Check regions.json first, fall back to hardcoded
        region_gyms = pokemon_data.get_region_gyms("kanto")
        if region_gyms:
            return [_normalize_region_gym(g) for g in region_gyms]
        return GYM_LEADERS
    region_gyms = pokemon_data.get_region_gyms(region)
    if region_gyms:
        return [_normalize_region_gym(g) for g in region_gyms]
    return GYM_LEADERS


def get_gym(gym_id, region="kanto"):
    """Get gym leader data by ID (1-8) for a region."""
    leaders = get_gym_leaders(region)
    for g in leaders:
        if g["id"] == gym_id:
            return g
    return None


def get_next_gym(badges, region="kanto"):
    """Get the next gym to challenge based on earned badges for a region."""
    leaders = get_gym_leaders(region)
    earned = set(badges)
    for g in leaders:
        if g["id"] not in earned:
            return g
    return None  # All badges earned


def get_elite_four(region="kanto"):
    """Get Elite Four list for a region."""
    if region == "kanto" or not region:
        region_e4 = pokemon_data.get_region_elite_four("kanto")
        if region_e4:
            return [_normalize_region_e4(e, i) for i, e in enumerate(region_e4)]
        return ELITE_FOUR
    region_e4 = pokemon_data.get_region_elite_four(region)
    if region_e4:
        return [_normalize_region_e4(e, i) for i, e in enumerate(region_e4)]
    return ELITE_FOUR


def get_elite_four_member(index, region="kanto"):
    """Get Elite Four member by index (0-3) for a region."""
    e4 = get_elite_four(region)
    if 0 <= index < len(e4):
        return e4[index]
    return None


def get_champion(region="kanto"):
    """Get champion data for a region."""
    if region == "kanto" or not region:
        region_champ = pokemon_data.get_region_champion("kanto")
        if region_champ:
            return _normalize_region_champion(region_champ)
        return CHAMPION
    region_champ = pokemon_data.get_region_champion(region)
    if region_champ:
        return _normalize_region_champion(region_champ)
    return CHAMPION


def get_masters_opponent(opponent_id):
    """Get Masters Eight opponent by ID."""
    for m in MASTERS_EIGHT:
        if m["id"] == opponent_id:
            return m
    return None
