"""AI opponent for single-player PokeBattle.

Bot player that mimics the Player interface but makes decisions
server-side without a WebSocket connection.
"""

import random
import string

from battle_engine import build_team, PokemonInstance
from pokemon_data import get_type_effectiveness, get_moves_at_level
import pokemon_data

BOT_NAMES = [
    "Bug Catcher", "Youngster", "Lass", "Hiker",
    "Beauty", "Swimmer", "Rocket Grunt", "Ace Trainer",
    "Psychic", "Blackbelt", "Fisherman", "Biker",
    "Juggler", "Tamer", "Bird Keeper", "Scientist",
    "Cooltrainer", "Channeler", "Super Nerd", "Gentleman",
]


class BotPlayer:
    """AI player with the same interface as Player."""

    def __init__(self, name=None):
        self.ws = None
        self.id = 'bot_' + ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.name = name or random.choice(BOT_NAMES)
        self.account_id = None
        self.room_code = None
        self.team = None
        self.team_name = None
        self.team_dex_ids = None
        self.ready = False
        self.active_pokemon = 0
        self.chosen_action = None
        self.tap_score = 0.5
        self.reconnect_token = None
        self.is_bot = True

    async def send(self, msg):
        """No-op — bot has no WebSocket."""
        pass

    def get_active_pokemon(self):
        if self.team and 0 <= self.active_pokemon < len(self.team):
            return self.team[self.active_pokemon]
        return None

    def alive_pokemon_indices(self):
        if not self.team:
            return []
        return [i for i, p in enumerate(self.team) if not p.is_fainted]

    def all_fainted(self):
        if not self.team:
            return True
        return all(p.is_fainted for p in self.team)

    def team_status(self, full=False):
        if not self.team:
            return []
        serializer = "serialize_full" if full else "serialize_public"
        return [getattr(p, serializer)() for p in self.team]

    # ─── AI Decisions ─────────────────────────────────

    def select_team(self):
        """Pick 6 random Pokemon at default level 50."""
        dex_ids = random.sample(range(1, len(pokemon_data.POKEMON) + 1), 6)
        self.team_dex_ids = dex_ids
        self.team = build_team(dex_ids, pokemon_data.POKEMON, pokemon_data.MOVES)
        self.team_name = f"{self.name}'s Team"
        self.ready = True

    def select_team_at_level(self, avg_level):
        """Pick 6 random Pokemon scaled to a target level."""
        dex_ids = random.sample(range(1, len(pokemon_data.POKEMON) + 1), 6)
        self.team_dex_ids = dex_ids
        team = []
        for dex_id in dex_ids:
            species = pokemon_data.POKEMON.get(dex_id)
            if species:
                level = max(2, avg_level + random.randint(-2, 2))
                moves = get_moves_at_level(dex_id, level)
                if not moves:
                    moves = species["moves"][:2]
                team.append(PokemonInstance(species, pokemon_data.MOVES, level=level, custom_moves=moves))
        self.team = team
        self.team_name = f"{self.name}'s Team"
        self.ready = True

    def decide_action(self, opp_pokemon):
        """Choose the best move or switch."""
        my_pokemon = self.get_active_pokemon()
        if not my_pokemon or my_pokemon.is_fainted:
            return {"type": "move", "move_index": 0}

        # Consider switching if at type disadvantage
        matchup = self._calc_type_matchup(my_pokemon, opp_pokemon)
        switch_chance = 0.05 if matchup >= 1.0 else (0.30 if matchup <= 0.5 else 0.10)

        if random.random() < switch_chance:
            target = self._find_best_switch(opp_pokemon)
            if target is not None and target != self.active_pokemon:
                return {"type": "switch", "pokemon_index": target}

        return self._pick_best_move(my_pokemon, opp_pokemon)

    def decide_switch(self, available, opp_pokemon):
        """Choose which Pokemon to switch to after a faint."""
        if not available:
            return 0
        return self._find_best_switch_from(available, opp_pokemon)

    def get_tap_score(self):
        """Random tap score — slightly worse than a decent human."""
        return random.uniform(0.3, 0.8)

    # ─── Internal ─────────────────────────────────────

    def _pick_best_move(self, my_pokemon, opp_pokemon):
        """Score all moves and pick the best."""
        best_idx = 0
        best_score = -1

        for i, move in enumerate(my_pokemon.moves):
            if move["current_pp"] <= 0:
                continue
            score = self._score_move(move, my_pokemon, opp_pokemon)
            if score > best_score:
                best_score = score
                best_idx = i

        return {"type": "move", "move_index": best_idx}

    def _score_move(self, move, attacker, defender):
        """Score a move: power * accuracy * type_eff * STAB + randomness."""
        power = move.get("power", 0)
        accuracy = move.get("accuracy", 100)
        move_id = move.get("id", "")

        # Status moves
        if power == 0:
            if move_id in ("hypnosis", "sing", "sleep-powder", "spore", "lovely-kiss"):
                return 70 if defender.status is None else 5
            if move_id in ("thunder-wave", "stun-spore"):
                return 60 if defender.status is None else 5
            if move_id in ("poison-powder", "poison-gas"):
                return 40 if defender.status is None else 5
            return 15 + random.uniform(0, 10)

        # Damage move scoring
        effectiveness = 1.0
        for def_type in defender.types:
            effectiveness *= get_type_effectiveness(move["type"], def_type)

        stab = 1.5 if move["type"] in attacker.types else 1.0
        score = power * (accuracy / 100.0) * effectiveness * stab

        # Randomness for variety
        score *= random.uniform(0.85, 1.15)
        return score

    def _calc_type_matchup(self, my_pokemon, opp_pokemon):
        """How favorable our matchup is. >1 good, <1 bad."""
        best_eff = 0
        for move in my_pokemon.moves:
            if move.get("power", 0) > 0 and move["current_pp"] > 0:
                eff = 1.0
                for def_type in opp_pokemon.types:
                    eff *= get_type_effectiveness(move["type"], def_type)
                best_eff = max(best_eff, eff)
        return best_eff if best_eff > 0 else 0.5

    def _find_best_switch(self, opp_pokemon):
        """Find best Pokemon to switch to (excluding current)."""
        available = [i for i in self.alive_pokemon_indices() if i != self.active_pokemon]
        return self._find_best_switch_from(available, opp_pokemon)

    def _find_best_switch_from(self, available, opp_pokemon):
        """From candidate indices, pick best type matchup."""
        if not available:
            return available[0] if available else None

        best_idx = available[0]
        best_score = -1

        for idx in available:
            pokemon = self.team[idx]
            score = 0
            for move in pokemon.moves:
                if move.get("power", 0) > 0:
                    eff = 1.0
                    for def_type in opp_pokemon.types:
                        eff *= get_type_effectiveness(move["type"], def_type)
                    stab = 1.5 if move["type"] in pokemon.types else 1.0
                    move_score = move["power"] * eff * stab
                    score = max(score, move_score)

            if score > best_score:
                best_score = score
                best_idx = idx

        return best_idx
