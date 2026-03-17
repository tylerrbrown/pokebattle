"""Gen 1 Pokemon battle engine.

Faithful implementation of Generation I battle mechanics:
- Damage formula with STAB, type effectiveness, critical hits
- Physical/Special split by type (not per-move)
- Status effects: Burn, Poison, Paralyze, Sleep, Freeze
- Fixed-damage moves: Dragon Rage, Seismic Toss, Night Shade, Sonic Boom
- OHKO moves: Guillotine
- Self-destruct mechanics
- PP tracking with Struggle fallback
"""

import random
from pokemon_data import get_type_effectiveness, MOVES

# Gen 1: Physical types use Attack/Defense, Special types use Special/Special
PHYSICAL_TYPES = {"normal", "fighting", "poison", "ground", "flying", "bug", "rock", "ghost"}

LEVEL = 50  # All Pokemon at level 50


def calc_hp(base_hp):
    """Calculate HP stat at level 50 (0 DVs, 0 Stat Exp).
    HP = ((Base + DV) * 2 * Level / 100) + Level + 10
    """
    return int((base_hp * 2 * LEVEL / 100) + LEVEL + 10)


def calc_stat(base_stat):
    """Calculate non-HP stat at level 50 (0 DVs, 0 Stat Exp).
    Stat = ((Base + DV) * 2 * Level / 100) + 5
    """
    return int((base_stat * 2 * LEVEL / 100) + 5)


class PokemonInstance:
    """A Pokemon in battle with computed stats, HP, status, and PP tracking."""

    def __init__(self, species_data, moves_data):
        self.species = species_data
        self.dex_id = species_data["id"]
        self.name = species_data["name"]
        self.types = species_data["types"]

        base = species_data["base_stats"]
        self.max_hp = calc_hp(base["hp"])
        self.current_hp = self.max_hp
        self.attack = calc_stat(base["attack"])
        self.defense = calc_stat(base["defense"])
        self.special = calc_stat(base["special"])
        self.speed = calc_stat(base["speed"])
        self.base_speed = self.speed  # For critical hit calc

        # Moves with PP tracking
        self.moves = []
        for move_id in species_data["moves"]:
            if move_id in moves_data:
                move = dict(moves_data[move_id])  # Copy
                move["id"] = move_id
                move["current_pp"] = move["pp"]
                self.moves.append(move)

        # Status
        self.status = None  # "burn", "poison", "paralyze", "sleep", "freeze"
        self.sleep_turns = 0
        self.is_fainted = False

        # Stat modifiers (Gen 1 stages: -6 to +6)
        self.attack_stage = 0
        self.defense_stage = 0
        self.special_stage = 0
        self.speed_stage = 0
        self.accuracy_stage = 0
        self.evasion_stage = 0

    def get_effective_stat(self, stat_name):
        """Get stat with stage modifiers applied."""
        base_val = getattr(self, stat_name)
        stage = getattr(self, f"{stat_name}_stage", 0)

        # Gen 1 stat stage multipliers
        if stage >= 0:
            return int(base_val * (2 + stage) / 2)
        else:
            return int(base_val * 2 / (2 - stage))

    def get_effective_speed(self):
        """Get speed considering paralysis and stage."""
        spd = self.get_effective_stat("speed")
        if self.status == "paralyze":
            spd = spd // 4
        return max(1, spd)

    def has_usable_moves(self):
        """Check if any move has PP remaining."""
        return any(m["current_pp"] > 0 for m in self.moves)

    def serialize_full(self):
        """Full serialization for the owning player."""
        return {
            "dex_id": self.dex_id,
            "name": self.name,
            "types": self.types,
            "max_hp": self.max_hp,
            "current_hp": self.current_hp,
            "attack": self.attack,
            "defense": self.defense,
            "special": self.special,
            "speed": self.speed,
            "status": self.status,
            "is_fainted": self.is_fainted,
            "moves": [
                {
                    "id": m["id"],
                    "name": m["name"],
                    "type": m["type"],
                    "category": m["category"],
                    "power": m["power"],
                    "accuracy": m["accuracy"],
                    "pp": m["pp"],
                    "current_pp": m["current_pp"],
                    "effect": m.get("effect"),
                }
                for m in self.moves
            ],
        }

    def serialize_public(self):
        """Public serialization for the opponent (no PP info)."""
        return {
            "dex_id": self.dex_id,
            "name": self.name,
            "types": self.types,
            "max_hp": self.max_hp,
            "current_hp": self.current_hp,
            "status": self.status,
            "is_fainted": self.is_fainted,
            "moves": [
                {"id": m["id"], "name": m["name"], "type": m["type"]}
                for m in self.moves
            ],
        }


def build_team(dex_ids, pokemon_db, moves_db):
    """Build a team of PokemonInstance from dex IDs."""
    team = []
    for dex_id in dex_ids:
        species = pokemon_db.get(dex_id)
        if species:
            team.append(PokemonInstance(species, moves_db))
    return team


# Struggle: used when all PP depleted
STRUGGLE = {
    "id": "struggle",
    "name": "Struggle",
    "type": "normal",
    "category": "physical",
    "power": 50,
    "accuracy": 100,
    "pp": 999,
    "current_pp": 999,
    "effect": "recoil_half",
    "effect_chance": 100,
}


def calculate_damage(attacker, defender, move, tap_multiplier=0.5):
    """Calculate damage using Gen 1 formula.

    Returns: (damage, effectiveness, is_critical)
    """
    power = move["power"]
    if power == 0:
        return 0, 1.0, False

    # Fixed damage moves
    effect = move.get("effect")
    if effect == "fixed_40":
        return 40, 1.0, False
    if effect == "fixed_20":
        return 20, 1.0, False
    if effect == "fixed_level":
        return LEVEL, 1.0, False
    if effect == "ohko":
        # OHKO: if it hits, it's an instant KO
        return defender.current_hp, 1.0, False

    # Determine physical or special
    move_type = move["type"]
    if move_type in PHYSICAL_TYPES:
        atk_stat = attacker.get_effective_stat("attack")
        def_stat = defender.get_effective_stat("defense")
        # Burn halves physical attack
        if attacker.status == "burn":
            atk_stat = atk_stat // 2
    else:
        atk_stat = attacker.get_effective_stat("special")
        def_stat = defender.get_effective_stat("special")

    # Critical hit check (Gen 1: base_speed / 512)
    crit_rate = attacker.base_speed / 512.0
    is_critical = random.random() < crit_rate

    effective_level = LEVEL
    if is_critical:
        effective_level = LEVEL * 2
        # Crits ignore stat stages in Gen 1
        if move_type in PHYSICAL_TYPES:
            atk_stat = attacker.attack
            def_stat = defender.defense
            if attacker.status == "burn":
                atk_stat = atk_stat // 2
        else:
            atk_stat = attacker.special
            def_stat = defender.special

    # Prevent division by zero
    def_stat = max(1, def_stat)

    # Base damage formula
    base = ((2 * effective_level / 5 + 2) * power * atk_stat / def_stat) / 50 + 2

    # STAB
    stab = 1.5 if move_type in attacker.types else 1.0

    # Type effectiveness
    effectiveness = 1.0
    for def_type in defender.types:
        effectiveness *= get_type_effectiveness(move_type, def_type)

    # Random factor (Gen 1: 217-255 / 255)
    rand_factor = random.randint(217, 255) / 255.0

    # Tap multiplier: score 0.0-1.0 maps to 0.85x-1.15x
    tap_mult = 0.85 + (tap_multiplier * 0.30)

    damage = int(base * stab * effectiveness * rand_factor * tap_mult)

    if effectiveness == 0:
        damage = 0
    else:
        damage = max(1, damage)

    return damage, effectiveness, is_critical


def check_accuracy(move, attacker, defender):
    """Check if a move hits. Returns True if it hits."""
    accuracy = move["accuracy"]
    if accuracy >= 100 and move.get("effect") != "ohko":
        return True

    # OHKO moves: hit if attacker speed >= defender speed, accuracy = 30%
    if move.get("effect") == "ohko":
        if attacker.get_effective_speed() < defender.get_effective_speed():
            return False  # OHKO fails if slower in Gen 1
        return random.randint(1, 100) <= 30

    # Apply accuracy/evasion stages
    acc_stage = attacker.accuracy_stage - defender.evasion_stage
    acc_stage = max(-6, min(6, acc_stage))

    if acc_stage >= 0:
        stage_mult = (3 + acc_stage) / 3.0
    else:
        stage_mult = 3.0 / (3 - acc_stage)

    effective_accuracy = int(accuracy * stage_mult)
    return random.randint(1, 100) <= effective_accuracy


def check_status_prevents_action(pokemon):
    """Check if status prevents acting this turn.

    Returns: (can_act, events_list)
    """
    events = []

    if pokemon.status == "freeze":
        # Gen 1: 20% chance to thaw each turn
        if random.random() < 0.20:
            pokemon.status = None
            events.append({
                "event": "status_cure",
                "pokemon": pokemon.name,
                "status": "freeze",
                "text": f"{pokemon.name} thawed out!"
            })
            return True, events
        events.append({
            "event": "status_prevent",
            "pokemon": pokemon.name,
            "status": "freeze",
            "text": f"{pokemon.name} is frozen solid!"
        })
        return False, events

    if pokemon.status == "sleep":
        pokemon.sleep_turns -= 1
        if pokemon.sleep_turns <= 0:
            pokemon.status = None
            events.append({
                "event": "status_cure",
                "pokemon": pokemon.name,
                "status": "sleep",
                "text": f"{pokemon.name} woke up!"
            })
            return True, events
        events.append({
            "event": "status_prevent",
            "pokemon": pokemon.name,
            "status": "sleep",
            "text": f"{pokemon.name} is fast asleep!"
        })
        return False, events

    if pokemon.status == "paralyze":
        if random.random() < 0.25:
            events.append({
                "event": "status_prevent",
                "pokemon": pokemon.name,
                "status": "paralyze",
                "text": f"{pokemon.name} is fully paralyzed!"
            })
            return False, events

    return True, events


def apply_stat_effect(move, target, attacker, events):
    """Apply stat-changing effects from moves like Growl, Leer, etc."""
    move_id = move.get("id", "")

    stat_changes = {
        "growl": ("attack_stage", -1, target, "Attack"),
        "leer": ("defense_stage", -1, target, "Defense"),
        "tail-whip": ("defense_stage", -1, target, "Defense"),
        "screech": ("defense_stage", -2, target, "Defense"),
        "sand-attack": ("accuracy_stage", -1, target, "accuracy"),
        "smokescreen": ("accuracy_stage", -1, target, "accuracy"),
        "string-shot": ("speed_stage", -1, target, "Speed"),
        "double-team": ("evasion_stage", 1, attacker, "evasion"),
        "minimize": ("evasion_stage", 1, attacker, "evasion"),
        "harden": ("defense_stage", 1, attacker, "Defense"),
        "withdraw": ("defense_stage", 1, attacker, "Defense"),
        "defense-curl": ("defense_stage", 1, attacker, "Defense"),
        "barrier": ("defense_stage", 2, attacker, "Defense"),
        "light-screen": ("special_stage", 1, attacker, "Special"),
        "reflect": ("defense_stage", 1, attacker, "Defense"),
        "sharpen": ("attack_stage", 1, attacker, "Attack"),
        "meditate": ("attack_stage", 1, attacker, "Attack"),
        "growth": ("special_stage", 1, attacker, "Special"),
        "agility": ("speed_stage", 2, attacker, "Speed"),
        "amnesia": ("special_stage", 2, attacker, "Special"),
        "focus-energy": ("attack_stage", 1, attacker, "Attack"),  # Simplified
    }

    if move_id in stat_changes:
        stat_attr, delta, pokemon, stat_name = stat_changes[move_id]
        old_val = getattr(pokemon, stat_attr)
        new_val = max(-6, min(6, old_val + delta))

        if new_val == old_val:
            direction = "won't go any higher!" if delta > 0 else "won't go any lower!"
            events.append({
                "event": "stat_change",
                "pokemon": pokemon.name,
                "stat": stat_name,
                "stages": 0,
                "text": f"{pokemon.name}'s {stat_name} {direction}"
            })
        else:
            setattr(pokemon, stat_attr, new_val)
            if delta > 1:
                text = f"{pokemon.name}'s {stat_name} sharply rose!"
            elif delta > 0:
                text = f"{pokemon.name}'s {stat_name} rose!"
            elif delta < -1:
                text = f"{pokemon.name}'s {stat_name} sharply fell!"
            else:
                text = f"{pokemon.name}'s {stat_name} fell!"
            events.append({
                "event": "stat_change",
                "pokemon": pokemon.name,
                "stat": stat_name,
                "stages": delta,
                "text": text
            })
        return True

    return False


def apply_status_effect(move, target, events):
    """Try to apply a status condition from a move."""
    effect = move.get("effect")
    chance = move.get("effect_chance", 0)

    if not effect or chance == 0:
        return

    # Skip non-status effects
    if effect in ("fixed_40", "fixed_20", "fixed_level", "ohko", "self_destruct", "recoil_half"):
        return

    # Can't apply status if target already has one (Gen 1)
    if target.status is not None:
        return

    # Can't apply if target is fainted
    if target.is_fainted:
        return

    # Type immunities for status
    if effect == "paralyze" and "electric" in target.types:
        return
    if effect == "poison" and ("poison" in target.types or "ground" in target.types):
        return  # Gen 1: Poison types immune to poison (not ground, but keeping simple)
    if effect == "burn" and "fire" in target.types:
        return
    if effect == "freeze" and "ice" in target.types:
        return

    if random.randint(1, 100) <= chance:
        target.status = effect
        if effect == "sleep":
            target.sleep_turns = random.randint(1, 7)

        status_text = {
            "burn": f"{target.name} was burned!",
            "poison": f"{target.name} was poisoned!",
            "paralyze": f"{target.name} is paralyzed! It may be unable to move!",
            "sleep": f"{target.name} fell asleep!",
            "freeze": f"{target.name} was frozen solid!",
            "confuse": f"{target.name} became confused!",
        }
        events.append({
            "event": "status_apply",
            "pokemon": target.name,
            "status": effect,
            "text": status_text.get(effect, f"{target.name} was afflicted with {effect}!")
        })


def resolve_move(attacker_pokemon, defender_pokemon, move, tap_score, events):
    """Resolve a single move execution."""
    # Use Struggle if the move is struggle
    is_struggle = move.get("id") == "struggle"

    events.append({
        "event": "move_use",
        "pokemon": attacker_pokemon.name,
        "move": move["name"],
        "move_type": move["type"],
        "tap_multiplier": tap_score,
    })

    # Deduct PP (not for Struggle)
    if not is_struggle and "current_pp" in move:
        move["current_pp"] = max(0, move["current_pp"] - 1)

    # Special non-damage moves
    move_id = move.get("id", "")

    # Moves that don't do damage but have effects
    if move["power"] == 0 and move.get("effect") not in ("fixed_40", "fixed_20", "fixed_level", "ohko"):
        # Try stat changes
        stat_applied = apply_stat_effect(move, defender_pokemon, attacker_pokemon, events)

        # Try status effects (sleep powder, thunder wave, etc.)
        if move_id in ("hypnosis", "sing", "sleep-powder", "spore", "lovely-kiss"):
            # Accuracy check first
            if not check_accuracy(move, attacker_pokemon, defender_pokemon):
                events.append({"event": "miss", "pokemon": attacker_pokemon.name, "text": f"{attacker_pokemon.name}'s attack missed!"})
                return
            apply_status_effect({"effect": "sleep", "effect_chance": 100}, defender_pokemon, events)
            return

        if move_id in ("stun-spore", "thunder-wave"):
            if not check_accuracy(move, attacker_pokemon, defender_pokemon):
                events.append({"event": "miss", "pokemon": attacker_pokemon.name, "text": f"{attacker_pokemon.name}'s attack missed!"})
                return
            apply_status_effect({"effect": "paralyze", "effect_chance": 100}, defender_pokemon, events)
            return

        if move_id in ("poison-powder", "poison-gas"):
            if not check_accuracy(move, attacker_pokemon, defender_pokemon):
                events.append({"event": "miss", "pokemon": attacker_pokemon.name, "text": f"{attacker_pokemon.name}'s attack missed!"})
                return
            apply_status_effect({"effect": "poison", "effect_chance": 100}, defender_pokemon, events)
            return

        if move_id == "confuse-ray":
            if not check_accuracy(move, attacker_pokemon, defender_pokemon):
                events.append({"event": "miss", "pokemon": attacker_pokemon.name, "text": f"{attacker_pokemon.name}'s attack missed!"})
                return
            events.append({"event": "status_apply", "pokemon": defender_pokemon.name, "status": "confuse", "text": f"{defender_pokemon.name} became confused!"})
            return

        if move_id == "supersonic":
            if not check_accuracy(move, attacker_pokemon, defender_pokemon):
                events.append({"event": "miss", "pokemon": attacker_pokemon.name, "text": f"{attacker_pokemon.name}'s attack missed!"})
                return
            events.append({"event": "status_apply", "pokemon": defender_pokemon.name, "status": "confuse", "text": f"{defender_pokemon.name} became confused!"})
            return

        if move_id == "rest":
            attacker_pokemon.current_hp = attacker_pokemon.max_hp
            attacker_pokemon.status = "sleep"
            attacker_pokemon.sleep_turns = 2
            events.append({"event": "heal", "pokemon": attacker_pokemon.name, "new_hp": attacker_pokemon.current_hp, "max_hp": attacker_pokemon.max_hp, "text": f"{attacker_pokemon.name} went to sleep and became healthy!"})
            return

        if move_id == "recover":
            heal = attacker_pokemon.max_hp // 2
            attacker_pokemon.current_hp = min(attacker_pokemon.max_hp, attacker_pokemon.current_hp + heal)
            events.append({"event": "heal", "pokemon": attacker_pokemon.name, "new_hp": attacker_pokemon.current_hp, "max_hp": attacker_pokemon.max_hp, "text": f"{attacker_pokemon.name} recovered health!"})
            return

        # Disable, transform, metronome, etc. - simplified: just show the move was used
        if move_id in ("disable", "transform", "metronome", "conversion", "mist",
                       "whirlwind", "roar", "teleport", "splash", "leech-seed"):
            if move_id == "splash":
                events.append({"event": "no_effect", "text": "But nothing happened!"})
            elif move_id == "leech-seed":
                if not check_accuracy(move, attacker_pokemon, defender_pokemon):
                    events.append({"event": "miss", "pokemon": attacker_pokemon.name, "text": f"{attacker_pokemon.name}'s attack missed!"})
                    return
                if "grass" in defender_pokemon.types:
                    events.append({"event": "no_effect", "text": "It doesn't affect {defender_pokemon.name}..."})
                else:
                    events.append({"event": "status_apply", "pokemon": defender_pokemon.name, "status": "leech_seed", "text": f"{defender_pokemon.name} was seeded!"})
            elif stat_applied:
                pass  # Already handled
            else:
                events.append({"event": "no_effect", "text": f"But it failed!"})
            return

        if not stat_applied:
            events.append({"event": "no_effect", "text": "But nothing happened!"})
        return

    # === Damage-dealing moves ===

    # Accuracy check
    if not check_accuracy(move, attacker_pokemon, defender_pokemon):
        events.append({
            "event": "miss",
            "pokemon": attacker_pokemon.name,
            "text": f"{attacker_pokemon.name}'s attack missed!"
        })
        return

    # Calculate damage
    damage, effectiveness, is_critical = calculate_damage(
        attacker_pokemon, defender_pokemon, move, tap_score
    )

    # Type effectiveness message
    if effectiveness == 0:
        events.append({
            "event": "effectiveness",
            "multiplier": 0,
            "text": f"It doesn't affect {defender_pokemon.name}..."
        })
        return
    elif effectiveness >= 2:
        events.append({
            "event": "effectiveness",
            "multiplier": effectiveness,
            "text": "It's super effective!"
        })
    elif effectiveness <= 0.5:
        events.append({
            "event": "effectiveness",
            "multiplier": effectiveness,
            "text": "It's not very effective..."
        })

    if is_critical:
        events.append({
            "event": "critical_hit",
            "text": "A critical hit!"
        })

    # Apply damage
    defender_pokemon.current_hp = max(0, defender_pokemon.current_hp - damage)
    events.append({
        "event": "damage",
        "pokemon": defender_pokemon.name,
        "damage": damage,
        "new_hp": defender_pokemon.current_hp,
        "max_hp": defender_pokemon.max_hp,
    })

    # Self-destruct: attacker faints
    if move.get("effect") == "self_destruct":
        attacker_pokemon.current_hp = 0
        attacker_pokemon.is_fainted = True
        events.append({
            "event": "faint",
            "pokemon": attacker_pokemon.name,
            "text": f"{attacker_pokemon.name} fainted!"
        })

    # Struggle recoil
    if is_struggle:
        recoil = max(1, damage // 2)
        attacker_pokemon.current_hp = max(0, attacker_pokemon.current_hp - recoil)
        events.append({
            "event": "recoil",
            "pokemon": attacker_pokemon.name,
            "damage": recoil,
            "new_hp": attacker_pokemon.current_hp,
            "max_hp": attacker_pokemon.max_hp,
            "text": f"{attacker_pokemon.name} is hit with recoil!"
        })

    # Check faint from damage
    if defender_pokemon.current_hp <= 0:
        defender_pokemon.is_fainted = True
        events.append({
            "event": "faint",
            "pokemon": defender_pokemon.name,
            "text": f"{defender_pokemon.name} fainted!"
        })

    # Check attacker faint from recoil
    if attacker_pokemon.current_hp <= 0 and not attacker_pokemon.is_fainted:
        attacker_pokemon.is_fainted = True
        events.append({
            "event": "faint",
            "pokemon": attacker_pokemon.name,
            "text": f"{attacker_pokemon.name} fainted!"
        })

    # Apply secondary status effect (burn chance from Flamethrower, etc.)
    if not defender_pokemon.is_fainted:
        apply_status_effect(move, defender_pokemon, events)


def resolve_turn(p1_pokemon, p2_pokemon, p1_action, p2_action, p1_tap, p2_tap):
    """Resolve a full battle turn.

    Args:
        p1_pokemon, p2_pokemon: Active PokemonInstance for each player
        p1_action: {"type": "move", "move_index": 0} or {"type": "switch", ...}
        p2_action: Same format
        p1_tap, p2_tap: Tap scores (0.0-1.0)

    Returns: list of events, list of fainted player indices needing switch
    """
    events = []
    switches_needed = []

    # Build list of actions to execute
    # Switches always go first
    movers = []

    for pid, (pokemon, action, tap) in enumerate([(p1_pokemon, p1_action, p1_tap), (p2_pokemon, p2_action, p2_tap)]):
        if action["type"] == "move":
            move_idx = action.get("move_index", 0)
            if pokemon.has_usable_moves() and move_idx < len(pokemon.moves) and pokemon.moves[move_idx]["current_pp"] > 0:
                move = pokemon.moves[move_idx]
            elif pokemon.has_usable_moves():
                # Invalid move index, pick first usable
                move = next(m for m in pokemon.moves if m["current_pp"] > 0)
            else:
                move = STRUGGLE
            movers.append((pid, pokemon, move, tap))

    # Sort movers by speed (faster goes first)
    if len(movers) == 2:
        spd0 = movers[0][1].get_effective_speed()
        spd1 = movers[1][1].get_effective_speed()
        if spd1 > spd0 or (spd1 == spd0 and random.random() < 0.5):
            movers.reverse()

    # Execute moves
    for pid, atk_pokemon, move, tap in movers:
        def_pokemon = p2_pokemon if pid == 0 else p1_pokemon

        if atk_pokemon.is_fainted:
            continue

        # Check status prevents action
        can_act, status_events = check_status_prevents_action(atk_pokemon)
        events.extend(status_events)
        if not can_act:
            continue

        # Skip if defender already fainted (no hitting a dead Pokemon)
        if def_pokemon.is_fainted:
            continue

        resolve_move(atk_pokemon, def_pokemon, move, tap, events)

    # End-of-turn: burn and poison damage
    for pid, pokemon in enumerate([p1_pokemon, p2_pokemon]):
        if pokemon.is_fainted:
            continue

        if pokemon.status == "burn":
            dot = max(1, pokemon.max_hp // 16)
            pokemon.current_hp = max(0, pokemon.current_hp - dot)
            events.append({
                "event": "dot_damage",
                "pokemon": pokemon.name,
                "status": "burn",
                "damage": dot,
                "new_hp": pokemon.current_hp,
                "max_hp": pokemon.max_hp,
                "text": f"{pokemon.name} is hurt by its burn!"
            })
            if pokemon.current_hp <= 0:
                pokemon.is_fainted = True
                events.append({
                    "event": "faint",
                    "pokemon": pokemon.name,
                    "text": f"{pokemon.name} fainted!"
                })

        elif pokemon.status == "poison":
            dot = max(1, pokemon.max_hp // 16)
            pokemon.current_hp = max(0, pokemon.current_hp - dot)
            events.append({
                "event": "dot_damage",
                "pokemon": pokemon.name,
                "status": "poison",
                "damage": dot,
                "new_hp": pokemon.current_hp,
                "max_hp": pokemon.max_hp,
                "text": f"{pokemon.name} is hurt by poison!"
            })
            if pokemon.current_hp <= 0:
                pokemon.is_fainted = True
                events.append({
                    "event": "faint",
                    "pokemon": pokemon.name,
                    "text": f"{pokemon.name} fainted!"
                })

    # Determine who needs to switch
    if p1_pokemon.is_fainted:
        switches_needed.append(0)
    if p2_pokemon.is_fainted:
        switches_needed.append(1)

    events.append({"event": "turn_end"})
    return events, switches_needed
