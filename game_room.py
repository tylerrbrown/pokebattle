"""Game room management and state machine for PokeBattle.

States: LOBBY -> TEAM_SELECT -> BATTLE -> GAME_OVER
Battle sub-states: ACTION_SELECT -> TAP_PHASE -> TURN_RESOLVE -> FORCE_SWITCH
"""

import asyncio
import json
import random
import string
import time
from battle_engine import PokemonInstance, build_team, resolve_turn, STRUGGLE
import pokemon_data


class Player:
    """Represents a connected player."""

    def __init__(self, ws):
        self.ws = ws
        self.id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        self.name = None
        self.account_id = None  # Linked player account (if logged in)
        self.room_code = None
        self.team = None           # List of PokemonInstance
        self.team_name = None
        self.team_dex_ids = None   # Original dex IDs for history
        self.ready = False         # Team selection locked in
        self.active_pokemon = 0    # Index into team
        self.chosen_action = None  # {"type": "move"/"switch", "move_index"/"pokemon_index": int}
        self.tap_score = 0.5       # Default tap score
        self.reconnect_token = None
        self.is_bot = False

    async def send(self, msg):
        """Send JSON message to player. Silently fails on broken connection."""
        try:
            await self.ws.send(json.dumps(msg))
        except Exception:
            pass

    def get_active_pokemon(self):
        """Get the player's currently active Pokemon."""
        if self.team and 0 <= self.active_pokemon < len(self.team):
            return self.team[self.active_pokemon]
        return None

    def alive_pokemon_indices(self):
        """Get indices of non-fainted Pokemon."""
        if not self.team:
            return []
        return [i for i, p in enumerate(self.team) if not p.is_fainted]

    def all_fainted(self):
        """Check if all Pokemon are fainted."""
        if not self.team:
            return True
        return all(p.is_fainted for p in self.team)

    def team_status(self, full=False):
        """Serialize team status."""
        if not self.team:
            return []
        serializer = "serialize_full" if full else "serialize_public"
        return [getattr(p, serializer)() for p in self.team]


class GameRoom:
    """A game room managing two players through the full game lifecycle."""

    # Timeouts
    TEAM_SELECT_TIMEOUT = 90   # seconds
    ACTION_SELECT_TIMEOUT = 30
    TAP_PHASE_DURATION = 3
    TAP_PHASE_TIMEOUT = 5      # grace period for tap result
    FORCE_SWITCH_TIMEOUT = 15

    def __init__(self, code):
        self.code = code
        self.state = "LOBBY"
        self.players = [None, None]
        self.created_at = time.time()
        self.turn_count = 0
        self.battle_log = []
        self.on_game_end = None  # callback(room, winner_idx, summary)

        # Async event coordination
        self._action_events = [None, None]
        self._tap_events = [None, None]
        self._switch_events = [None, None]
        self._timeout_task = None

    def get_player_index(self, player):
        """Get 0 or 1 index for player, or -1 if not found."""
        for i in range(2):
            if self.players[i] and self.players[i].id == player.id:
                return i
        return -1

    def get_opponent(self, player):
        """Get the other player."""
        idx = self.get_player_index(player)
        if idx == -1:
            return None
        return self.players[1 - idx]

    async def add_player(self, player):
        """Add a player to the room. Returns slot index or -1 if full."""
        if self.players[0] is None:
            self.players[0] = player
            player.room_code = self.code
            return 0
        elif self.players[1] is None:
            self.players[1] = player
            player.room_code = self.code
            # Room is full, notify both players
            await self.players[0].send({
                "type": "opponent_joined",
                "opponent_name": player.name,
            })
            # Start team select after a brief delay
            asyncio.create_task(self._start_team_select())
            return 1
        return -1

    async def remove_player(self, player):
        """Handle player disconnection."""
        idx = self.get_player_index(player)
        if idx == -1:
            return

        opponent = self.get_opponent(player)
        self.players[idx] = None

        if opponent:
            await opponent.send({
                "type": "opponent_disconnected",
                "text": f"{player.name} disconnected."
            })

            # If in battle, opponent wins by forfeit
            if self.state == "BATTLE":
                await self._end_game(1 - idx)

        # Cancel any pending timeouts
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()

    async def _start_team_select(self):
        """Transition to team selection phase."""
        self.state = "TEAM_SELECT"

        # Check if both already ready (e.g. journey battle with pre-set teams)
        if all(p and p.ready for p in self.players):
            await self._start_battle()
            return

        pokemon_list = pokemon_data.get_pokemon_list_for_client()

        # Only send team select to players who aren't already ready
        for p in self.players:
            if p and not p.ready:
                await p.send({
                    "type": "team_select_start",
                    "pokemon_list": pokemon_list,
                    "time_limit": self.TEAM_SELECT_TIMEOUT,
                })

        # Bot auto-selects team
        for p in self.players:
            if p and p.is_bot and not p.ready:
                p.select_team()
                opp = self.get_opponent(p)
                if opp:
                    await opp.send({"type": "opponent_ready"})

        # Check again after bot selection
        if all(p and p.ready for p in self.players):
            await self._start_battle()
            return

        # Start timeout
        self._timeout_task = asyncio.create_task(
            self._team_select_timeout()
        )

    async def _team_select_timeout(self):
        """Auto-assign random teams if players don't pick in time."""
        await asyncio.sleep(self.TEAM_SELECT_TIMEOUT)
        for p in self.players:
            if p and not p.ready:
                # Random team
                dex_ids = random.sample(range(1, 152), 6)
                p.team_dex_ids = dex_ids
                p.team = build_team(dex_ids, pokemon_data.POKEMON, pokemon_data.MOVES)
                p.team_name = f"{p.name}'s Team"
                p.ready = True
                await p.send({"type": "team_auto_assigned", "text": "Time's up! A random team was assigned."})

        if all(p and p.ready for p in self.players):
            await self._start_battle()

    async def handle_team_select(self, player, data):
        """Handle team selection from a player."""
        if self.state != "TEAM_SELECT":
            await player.send({"type": "error", "message": "Not in team select phase."})
            return

        if player.ready:
            await player.send({"type": "error", "message": "Team already locked in."})
            return

        # Validate
        team_name = str(data.get("team_name", "")).strip()
        pokemon_ids = data.get("pokemon", [])

        if not team_name or len(team_name) < 2 or len(team_name) > 20:
            await player.send({"type": "error", "message": "Team name must be 2-20 characters."})
            return

        if not isinstance(pokemon_ids, list) or len(pokemon_ids) != 6:
            await player.send({"type": "error", "message": "Must select exactly 6 Pokemon."})
            return

        # Validate dex IDs
        try:
            pokemon_ids = [int(x) for x in pokemon_ids]
        except (ValueError, TypeError):
            await player.send({"type": "error", "message": "Invalid Pokemon IDs."})
            return

        if len(set(pokemon_ids)) != 6:
            await player.send({"type": "error", "message": "All 6 Pokemon must be different."})
            return

        for pid in pokemon_ids:
            if pid < 1 or pid > 151:
                await player.send({"type": "error", "message": f"Invalid Pokemon ID: {pid}"})
                return

        # Build team
        player.team_dex_ids = pokemon_ids
        player.team = build_team(pokemon_ids, pokemon_data.POKEMON, pokemon_data.MOVES)
        player.team_name = team_name
        player.ready = True

        await player.send({"type": "team_locked", "text": "Team locked in! Waiting for opponent..."})

        # Notify opponent
        opponent = self.get_opponent(player)
        if opponent:
            await opponent.send({"type": "opponent_ready"})

        # Check if both ready
        if all(p and p.ready for p in self.players):
            if self._timeout_task and not self._timeout_task.done():
                self._timeout_task.cancel()
            await self._start_battle()

    async def _start_battle(self):
        """Transition to battle phase."""
        self.state = "BATTLE"
        self.turn_count = 0

        # Send battle_start to each player with their team (full) and opponent team (public)
        for i, p in enumerate(self.players):
            opp = self.players[1 - i]
            await p.send({
                "type": "battle_start",
                "your_team": p.team_status(full=True),
                "your_team_name": p.team_name,
                "opponent_team": opp.team_status(full=False),
                "opponent_team_name": opp.team_name,
                "your_active": 0,
                "opponent_active": 0,
            })

        # Request first actions
        await self._request_actions()

    async def _request_actions(self):
        """Ask both players for their action."""
        self._action_events = [asyncio.Event(), asyncio.Event()]

        for i, p in enumerate(self.players):
            opp = self.players[1 - i]
            p.chosen_action = None
            active = p.get_active_pokemon()
            opp_active = opp.get_active_pokemon()

            await p.send({
                "type": "turn_request",
                "turn": self.turn_count + 1,
                "your_pokemon": active.serialize_full(),
                "opponent_pokemon": opp_active.serialize_public(),
                "your_team_status": p.team_status(full=True),
                "opponent_team_status": opp.team_status(full=False),
                "your_active": p.active_pokemon,
                "opponent_active": opp.active_pokemon,
                "time_limit": self.ACTION_SELECT_TIMEOUT,
            })

        # Bot auto-picks action
        for i, p in enumerate(self.players):
            if p and p.is_bot:
                opp = self.players[1 - i]
                p.chosen_action = p.decide_action(opp.get_active_pokemon())
                self._action_events[i].set()

        # Wait for both actions with timeout
        self._timeout_task = asyncio.create_task(self._action_timeout())

    async def _action_timeout(self):
        """Auto-select random action if player doesn't choose in time."""
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._action_events[0].wait(),
                    self._action_events[1].wait()
                ),
                timeout=self.ACTION_SELECT_TIMEOUT
            )
        except asyncio.TimeoutError:
            for i, p in enumerate(self.players):
                if p and p.chosen_action is None:
                    # Auto-select first usable move
                    active = p.get_active_pokemon()
                    if active and active.has_usable_moves():
                        for mi, m in enumerate(active.moves):
                            if m["current_pp"] > 0:
                                p.chosen_action = {"type": "move", "move_index": mi}
                                break
                    else:
                        p.chosen_action = {"type": "move", "move_index": 0}  # Will use Struggle
                    self._action_events[i].set()

        # Both actions received, check if we need tap phase
        await self._process_actions()

    async def handle_action(self, player, data):
        """Handle action selection from a player."""
        if self.state != "BATTLE":
            return

        idx = self.get_player_index(player)
        if idx == -1:
            return

        if player.chosen_action is not None:
            await player.send({"type": "error", "message": "Action already submitted."})
            return

        action_type = data.get("type")
        if action_type == "move":
            move_index = data.get("move_index", 0)
            active = player.get_active_pokemon()
            if active and 0 <= move_index < len(active.moves):
                player.chosen_action = {"type": "move", "move_index": move_index}
            else:
                await player.send({"type": "error", "message": "Invalid move index."})
                return
        elif action_type == "switch":
            pokemon_index = data.get("pokemon_index", 0)
            if pokemon_index in player.alive_pokemon_indices() and pokemon_index != player.active_pokemon:
                player.chosen_action = {"type": "switch", "pokemon_index": pokemon_index}
            else:
                await player.send({"type": "error", "message": "Invalid switch target."})
                return
        else:
            await player.send({"type": "error", "message": "Invalid action type."})
            return

        await player.send({"type": "action_confirmed", "text": "Waiting for opponent..."})

        # Notify opponent
        opponent = self.get_opponent(player)
        if opponent:
            await opponent.send({"type": "opponent_action_locked"})

        self._action_events[idx].set()

    async def _process_actions(self):
        """After both actions received, run tap phase or resolve turn."""
        p1 = self.players[0]
        p2 = self.players[1]

        # Determine if either player used a damage-dealing move (needs tap phase)
        needs_tap = False
        for p in [p1, p2]:
            if p.chosen_action and p.chosen_action["type"] == "move":
                active = p.get_active_pokemon()
                if active:
                    mi = p.chosen_action["move_index"]
                    if mi < len(active.moves) and active.moves[mi]["power"] > 0:
                        needs_tap = True
                    elif not active.has_usable_moves():
                        needs_tap = True  # Struggle

        if needs_tap:
            await self._start_tap_phase()
        else:
            # No damage moves, skip tap phase
            p1.tap_score = 0.5
            p2.tap_score = 0.5
            await self._resolve_turn()

    async def _start_tap_phase(self):
        """Start the quick-time tapping phase."""
        self._tap_events = [asyncio.Event(), asyncio.Event()]

        for p in self.players:
            p.tap_score = 0.5  # Default if they don't tap

            # Only send tap phase to players who used a damage move
            if p.chosen_action and p.chosen_action["type"] == "move":
                active = p.get_active_pokemon()
                mi = p.chosen_action["move_index"]
                move_name = "Struggle"
                if active and mi < len(active.moves):
                    move_name = active.moves[mi]["name"]
                elif active and not active.has_usable_moves():
                    move_name = "Struggle"

                await p.send({
                    "type": "tap_phase",
                    "duration_ms": self.TAP_PHASE_DURATION * 1000,
                    "move_name": move_name,
                })
            else:
                # Player switching, no tap needed
                idx = self.get_player_index(p)
                self._tap_events[idx].set()

        # Bot auto-taps
        for i, p in enumerate(self.players):
            if p and p.is_bot and not self._tap_events[i].is_set():
                p.tap_score = p.get_tap_score()
                self._tap_events[i].set()

        # Wait for tap results
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._tap_events[0].wait(),
                    self._tap_events[1].wait()
                ),
                timeout=self.TAP_PHASE_TIMEOUT
            )
        except asyncio.TimeoutError:
            # Use default 0.5 for anyone who didn't respond
            for i, evt in enumerate(self._tap_events):
                if not evt.is_set():
                    self._tap_events[i].set()

        await self._resolve_turn()

    async def handle_tap_result(self, player, data):
        """Handle tap result from a player."""
        idx = self.get_player_index(player)
        if idx == -1:
            return

        score = data.get("score", 0.5)
        score = max(0.0, min(1.0, float(score)))
        player.tap_score = score
        self._tap_events[idx].set()

    async def _resolve_turn(self):
        """Resolve the turn and send results."""
        p1 = self.players[0]
        p2 = self.players[1]
        self.turn_count += 1

        events = []

        # Handle switches first
        for i, p in enumerate([p1, p2]):
            if p.chosen_action and p.chosen_action["type"] == "switch":
                old_idx = p.active_pokemon
                new_idx = p.chosen_action["pokemon_index"]
                old_name = p.team[old_idx].name
                new_name = p.team[new_idx].name
                p.active_pokemon = new_idx
                events.append({
                    "event": "switch",
                    "player_index": i,
                    "pokemon": new_name,
                    "old_pokemon": old_name,
                    "text": f"{p.name} withdrew {old_name} and sent out {new_name}!"
                })

        # Resolve moves
        p1_active = p1.get_active_pokemon()
        p2_active = p2.get_active_pokemon()

        p1_action = p1.chosen_action or {"type": "move", "move_index": 0}
        p2_action = p2.chosen_action or {"type": "move", "move_index": 0}

        if p1_action["type"] == "move" and p2_action["type"] == "move":
            # Both attacking
            move_events, switches_needed = resolve_turn(
                p1_active, p2_active,
                p1_action, p2_action,
                p1.tap_score, p2.tap_score
            )

            # Tag events with player indices
            for evt in move_events:
                if "pokemon" in evt:
                    if evt["pokemon"] == p1_active.name:
                        evt["player_index"] = 0
                    elif evt["pokemon"] == p2_active.name:
                        evt["player_index"] = 1

            events.extend(move_events)

        elif p1_action["type"] == "move":
            # Only p1 attacks (p2 switched)
            move_events, _ = resolve_turn(
                p1_active, p2.get_active_pokemon(),
                p1_action, {"type": "move", "move_index": -1},  # dummy
                p1.tap_score, 0.5
            )
            # Filter: only p1's move events (p2 "used" nothing since they switched)
            for evt in move_events:
                if "pokemon" in evt:
                    if evt["pokemon"] == p1_active.name:
                        evt["player_index"] = 0
                    else:
                        evt["player_index"] = 1
            events.extend(move_events)

        elif p2_action["type"] == "move":
            # Only p2 attacks (p1 switched)
            move_events, _ = resolve_turn(
                p1.get_active_pokemon(), p2_active,
                {"type": "move", "move_index": -1},
                p2_action,
                0.5, p2.tap_score
            )
            for evt in move_events:
                if "pokemon" in evt:
                    if evt["pokemon"] == p2_active.name:
                        evt["player_index"] = 1
                    else:
                        evt["player_index"] = 0
            events.extend(move_events)

        # Send turn result to both players
        for i, p in enumerate([p1, p2]):
            await p.send({
                "type": "turn_result",
                "turn": self.turn_count,
                "events": events,
                "your_team_status": p.team_status(full=True),
                "opponent_team_status": self.players[1 - i].team_status(full=False),
                "your_active": p.active_pokemon,
                "opponent_active": self.players[1 - i].active_pokemon,
            })

        # Check for game over
        if p1.all_fainted():
            await self._end_game(winner_idx=1)
            return
        if p2.all_fainted():
            await self._end_game(winner_idx=0)
            return

        # Check if anyone needs to switch (fainted active Pokemon)
        switches_needed = []
        if p1.get_active_pokemon().is_fainted and not p1.all_fainted():
            switches_needed.append(0)
        if p2.get_active_pokemon().is_fainted and not p2.all_fainted():
            switches_needed.append(1)

        if switches_needed:
            await self._request_switches(switches_needed)
        else:
            # Next turn
            await self._request_actions()

    async def _request_switches(self, player_indices):
        """Request forced switches from players whose active Pokemon fainted."""
        self._switch_events = [asyncio.Event(), asyncio.Event()]

        for i in range(2):
            if i not in player_indices:
                self._switch_events[i].set()  # Not needed

        for i in player_indices:
            p = self.players[i]
            available = p.alive_pokemon_indices()
            if p.is_bot:
                # Bot auto-switches
                opp = self.players[1 - i]
                target = p.decide_switch(available, opp.get_active_pokemon())
                if target is not None:
                    p.active_pokemon = target
                elif available:
                    p.active_pokemon = available[0]
                self._switch_events[i].set()
            else:
                await p.send({
                    "type": "force_switch_request",
                    "available": available,
                    "team_status": p.team_status(full=True),
                    "time_limit": self.FORCE_SWITCH_TIMEOUT,
                })

        # Wait with timeout
        try:
            await asyncio.wait_for(
                asyncio.gather(
                    self._switch_events[0].wait(),
                    self._switch_events[1].wait()
                ),
                timeout=self.FORCE_SWITCH_TIMEOUT
            )
        except asyncio.TimeoutError:
            # Auto-switch to first alive Pokemon
            for i in player_indices:
                if not self._switch_events[i].is_set():
                    p = self.players[i]
                    available = p.alive_pokemon_indices()
                    if available:
                        p.active_pokemon = available[0]
                    self._switch_events[i].set()

        # Notify both players of switches
        for i in player_indices:
            p = self.players[i]
            new_pokemon = p.get_active_pokemon()
            for j, other in enumerate(self.players):
                await other.send({
                    "type": "switch_in",
                    "player_index": i,
                    "pokemon": new_pokemon.serialize_full() if j == i else new_pokemon.serialize_public(),
                    "active_index": p.active_pokemon,
                    "text": f"{p.name} sent out {new_pokemon.name}!"
                })

        # Continue battle
        await self._request_actions()

    async def handle_force_switch(self, player, data):
        """Handle forced switch after a faint."""
        idx = self.get_player_index(player)
        if idx == -1:
            return

        pokemon_index = data.get("pokemon_index", -1)
        available = player.alive_pokemon_indices()

        if pokemon_index not in available:
            await player.send({"type": "error", "message": "Invalid switch target."})
            return

        player.active_pokemon = pokemon_index
        self._switch_events[idx].set()

    async def _end_game(self, winner_idx):
        """End the game and announce winner."""
        self.state = "GAME_OVER"

        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()

        winner = self.players[winner_idx]
        loser = self.players[1 - winner_idx]

        duration = int(time.time() - self.created_at)

        # Count remaining HP for winner
        winner_remaining = sum(
            p.current_hp for p in winner.team if not p.is_fainted
        ) if winner.team else 0
        winner_alive = len(winner.alive_pokemon_indices()) if winner.team else 0

        summary = {
            "winner_name": winner.name if winner else "Unknown",
            "winner_team_name": winner.team_name if winner else "",
            "loser_name": loser.name if loser else "Unknown",
            "loser_team_name": loser.team_name if loser else "",
            "turns": self.turn_count,
            "duration": duration,
            "winner_remaining_pokemon": winner_alive,
            "winner_remaining_hp": winner_remaining,
        }

        for i, p in enumerate(self.players):
            if p:
                await p.send({
                    "type": "game_over",
                    "winner": winner_idx == i,
                    "summary": summary,
                    "your_team_status": p.team_status(full=True),
                    "opponent_team_status": self.players[1 - i].team_status(full=True),
                })

        # Persist game to database
        if self.on_game_end:
            try:
                self.on_game_end(self, winner_idx, summary)
            except Exception as e:
                print(f"Error in on_game_end callback: {e}")

        return summary

    async def handle_rematch(self, player):
        """Handle rematch request."""
        if self.state != "GAME_OVER":
            return

        player.ready = False
        player.team = None
        player.team_name = None
        player.team_dex_ids = None
        player.active_pokemon = 0
        player.chosen_action = None
        player.tap_score = 0.5

        opponent = self.get_opponent(player)

        # Bot auto-accepts rematch
        if opponent and opponent.is_bot:
            opponent.ready = False
            opponent.team = None
            opponent.team_name = None
            opponent.team_dex_ids = None
            opponent.active_pokemon = 0
            opponent.chosen_action = None
            opponent.tap_score = 0.5
            for p in self.players:
                p.ready = False
                await p.send({"type": "rematch_start"})
            await self._start_team_select()
            return

        if opponent:
            await opponent.send({
                "type": "rematch_request",
                "text": f"{player.name} wants a rematch!"
            })

        # Check if both want rematch (both have ready=False means they sent rematch)
        if opponent and not opponent.ready and opponent.team is None:
            # Both want rematch
            for p in self.players:
                p.ready = False
                await p.send({"type": "rematch_start"})
            await self._start_team_select()


class RoomManager:
    """Manages all active game rooms."""

    def __init__(self, on_game_end=None):
        self.rooms = {}  # code -> GameRoom
        self.player_rooms = {}  # player.id -> room_code
        self.on_game_end = on_game_end  # callback for all rooms

    def generate_code(self):
        """Generate a unique 4-letter room code."""
        for _ in range(100):
            code = ''.join(random.choices(string.ascii_uppercase, k=4))
            if code not in self.rooms:
                return code
        raise RuntimeError("Could not generate unique room code")

    async def create_room(self, player):
        """Create a new room and add the player."""
        code = self.generate_code()
        room = GameRoom(code)
        room.on_game_end = self.on_game_end
        self.rooms[code] = room
        await room.add_player(player)
        self.player_rooms[player.id] = code
        return code

    async def join_room(self, player, code):
        """Join an existing room."""
        code = code.upper().strip()
        room = self.rooms.get(code)
        if not room:
            await player.send({"type": "error", "message": f"Room {code} not found."})
            return None

        if room.state != "LOBBY":
            await player.send({"type": "error", "message": "Game already in progress."})
            return None

        slot = await room.add_player(player)
        if slot == -1:
            await player.send({"type": "error", "message": "Room is full."})
            return None

        self.player_rooms[player.id] = code
        return code

    def get_room(self, player):
        """Get the room a player is in."""
        code = self.player_rooms.get(player.id)
        if code:
            return self.rooms.get(code)
        return None

    async def remove_player(self, player):
        """Remove a player from their room."""
        room = self.get_room(player)
        if room:
            await room.remove_player(player)
            del self.player_rooms[player.id]

            # Clean up empty rooms or bot-only rooms
            has_human = any(
                p is not None and not p.is_bot for p in room.players
            )
            if not has_human:
                for p in room.players:
                    if p and p.id in self.player_rooms:
                        del self.player_rooms[p.id]
                del self.rooms[room.code]

    def get_active_rooms(self):
        """Get list of active rooms for admin."""
        result = []
        for code, room in self.rooms.items():
            players = []
            for p in room.players:
                if p:
                    players.append({"name": p.name, "id": p.id})
            result.append({
                "code": code,
                "state": room.state,
                "players": players,
                "turn": room.turn_count,
                "created_at": room.created_at,
                "age_seconds": int(time.time() - room.created_at),
            })
        return result

    async def close_room(self, code):
        """Force-close a room (admin action)."""
        room = self.rooms.get(code)
        if not room:
            return False

        for p in room.players:
            if p:
                await p.send({"type": "room_closed", "text": "Room was closed by admin."})
                if p.id in self.player_rooms:
                    del self.player_rooms[p.id]

        del self.rooms[code]
        return True

    async def cleanup_old_rooms(self):
        """Remove rooms older than 2 hours. Called periodically."""
        cutoff = time.time() - 7200  # 2 hours
        to_remove = [
            code for code, room in self.rooms.items()
            if room.created_at < cutoff
        ]
        for code in to_remove:
            await self.close_room(code)
        return len(to_remove)
