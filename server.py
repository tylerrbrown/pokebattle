#!/usr/bin/env python3
"""PokeBattle — Gen 1 Pokemon Battle Server.

WebSocket game server with HTTP static file serving.
Uses the `websockets` library for real-time multiplayer.
"""

import asyncio
import json
import mimetypes
import os
import pathlib
import random
import re
import sqlite3
import string
import time

import websockets
from websockets.http11 import Response as HttpResponse, Headers as HttpHeaders

import pokemon_data
from ai_player import BotPlayer
from game_room import Player, RoomManager
from player_accounts import AccountManager, calc_xp_yield, xp_for_level
from journey import (
    generate_wild_pokemon, attempt_catch, WildEncounter,
    build_trainer_team, GYM_LEADERS, ELITE_FOUR, CHAMPION, MASTERS_EIGHT,
    SHOP_ITEMS, CURRENCY_WILD_WIN, CURRENCY_WILD_CATCH, CURRENCY_GYM_WIN,
    CURRENCY_ELITE_FOUR_WIN, CURRENCY_CHAMPION_WIN, CURRENCY_MASTERS_WIN,
    CURRENCY_PVP_WIN, CURRENCY_PVP_BOT_WIN,
    get_gym, get_next_gym, get_elite_four_member, get_masters_opponent,
    get_gym_leaders, get_elite_four, get_champion,
    generate_tournament_bracket, CURRENCY_TOURNAMENT, RARE_CANDY_TOURNAMENT,
    TOURNAMENT_ROUND_NAMES,
)
from battle_engine import PokemonInstance, build_journey_team, resolve_turn, calculate_damage, STRUGGLE

APP_DIR = pathlib.Path(__file__).parent


def _get_current_moves(poke_row):
    """Get a Pokemon's current move list, falling back to learnset + species defaults if DB is NULL.
    Filters out invalid moves (not in moves.json) to prevent move lock."""
    if poke_row.get("moves"):
        raw = json.loads(poke_row["moves"])
        # Filter out moves that don't exist in moves.json
        valid = [m for m in raw if m in pokemon_data.MOVES]
        if valid:
            return valid
        # All moves were invalid, fall through to regenerate
    return pokemon_data.get_initial_moves(poke_row["dex_id"], poke_row.get("level", 5))

DB_PATH = APP_DIR / "pokebattle.db"
PORT = int(os.environ.get("POKEBATTLE_PORT", 5060))
ADMIN_SECRET = os.environ.get("POKEBATTLE_ADMIN_SECRET", "pb-x9f2k7m4-admin-2024")

# ─── SQLite ─────────────────────────────────────────────

def init_db():
    """Initialize the SQLite database."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            player1_name TEXT NOT NULL,
            player2_name TEXT NOT NULL,
            player1_team_name TEXT,
            player2_team_name TEXT,
            player1_team TEXT NOT NULL,
            player2_team TEXT NOT NULL,
            winner INTEGER NOT NULL,
            turns INTEGER NOT NULL,
            duration_sec INTEGER NOT NULL,
            finished_at INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bug_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER,
            username TEXT NOT NULL,
            description TEXT NOT NULL,
            game_state TEXT,
            submitted_at INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()


def record_game(room, winner_idx, summary):
    """Record a completed game to the database and award PvP currency."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        p1 = room.players[0]
        p2 = room.players[1]
        conn.execute("""
            INSERT INTO games (room_code, player1_name, player2_name,
                player1_team_name, player2_team_name,
                player1_team, player2_team,
                winner, turns, duration_sec, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            room.code,
            p1.name if p1 else "?",
            p2.name if p2 else "?",
            p1.team_name if p1 else "",
            p2.team_name if p2 else "",
            json.dumps(p1.team_dex_ids if p1 else []),
            json.dumps(p2.team_dex_ids if p2 else []),
            winner_idx + 1,
            summary.get("turns", 0),
            summary.get("duration", 0),
            int(time.time()),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error recording game: {e}")

    # Award currency to the PvP winner
    currency = summary.get("currency_gained", 0)
    winner_account = summary.get("winner_account_id")
    if currency > 0 and winner_account and account_mgr:
        try:
            account_mgr.add_currency(winner_account, currency)
            print(f"[pvp] Awarded ${currency} to account {winner_account}")
        except Exception as e:
            print(f"Error awarding PvP currency: {e}")


def reload_journey_teams(room):
    """Re-load journey teams for logged-in players on rematch."""
    for p in room.players:
        if p and not p.is_bot and not p.ready and getattr(p, 'account_id', None):
            team_data = account_mgr.get_team(p.account_id)
            if team_data:
                journey_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
                p.team = journey_team
                p.team_dex_ids = [pkmn.dex_id for pkmn in journey_team]
                p.team_name = f"{p.name}'s Team"
                p.ready = True


# Global state
room_manager = RoomManager(on_game_end=record_game, on_rematch=reload_journey_teams)
account_mgr = None  # Initialized in main()
active_encounters = {}  # player.id -> WildEncounter
trade_rooms = {}  # code -> TradeRoom
player_trade_rooms = {}  # player.id -> code
active_tournaments = {}  # player.account_id -> TournamentState


class TournamentState:
    """In-memory state for an active tournament run."""

    DIFFICULTY_PER_ROUND = [0.4, 0.6, 0.8, 1.0]

    def __init__(self, player_account_id, bracket):
        self.player_account_id = player_account_id
        self.bracket = bracket  # list of 4 opponent dicts from generate_tournament_bracket
        self.current_round = 0  # 0-3
        self.results = []  # list of "win" / "loss" per completed round
        self.created_at = time.time()

    @property
    def is_complete(self):
        return len(self.results) >= 4 and all(r == "win" for r in self.results)

    @property
    def is_eliminated(self):
        return any(r == "loss" for r in self.results)

    def current_opponent(self):
        if self.current_round < len(self.bracket):
            return self.bracket[self.current_round]
        return None

    def difficulty(self):
        return self.DIFFICULTY_PER_ROUND[min(self.current_round, 3)]

    def serialize(self):
        """Serialize for sending to client."""
        rounds = []
        for i, opp in enumerate(self.bracket):
            status = "locked"
            if i < len(self.results):
                status = self.results[i]
            elif i == self.current_round:
                status = "current"
            rounds.append({
                "round_num": i,
                "round_name": TOURNAMENT_ROUND_NAMES[i],
                "opponent_name": opp["name"],
                "opponent_title": opp["title"],
                "opponent_type": opp["type"],
                "team_size": len(opp["team"]),
                "max_level": max(t["level"] for t in opp["team"]),
                "reward_currency": opp["reward_currency"],
                "status": status,
            })
        return {
            "current_round": self.current_round,
            "rounds": rounds,
            "is_complete": self.is_complete,
            "is_eliminated": self.is_eliminated,
            "total_winnings": sum(
                CURRENCY_TOURNAMENT[i] for i in range(len(self.results))
                if i < len(self.results) and self.results[i] == "win"
            ),
        }


# ─── Trade Room ────────────────────────────────────────

class TradeRoom:
    """A lightweight trade room for two players to swap Pokemon."""

    def __init__(self, code, player):
        self.code = code
        self.players = [player, None]
        self.offers = [None, None]       # pokemon_row_id each player offers
        self.confirmed = [False, False]  # both must confirm
        self.created_at = time.time()

    def get_player_index(self, player):
        for i in range(2):
            if self.players[i] and self.players[i].id == player.id:
                return i
        return -1

    def get_opponent(self, player):
        idx = self.get_player_index(player)
        if idx == -1:
            return None
        return self.players[1 - idx]

    def is_full(self):
        return self.players[0] is not None and self.players[1] is not None


def generate_trade_code():
    """Generate a unique 4-letter trade room code."""
    for _ in range(100):
        code = ''.join(random.choices(string.ascii_uppercase, k=4))
        if code not in trade_rooms and code not in room_manager.rooms:
            return code
    raise RuntimeError("Could not generate unique trade code")


async def _handle_trade_message(player, msg_type, data):
    """Handle all trade-related WebSocket messages. Returns True if handled."""

    if msg_type == "create_trade":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return True
        code = generate_trade_code()
        room = TradeRoom(code, player)
        trade_rooms[code] = room
        player_trade_rooms[player.id] = code
        await player.send({"type": "trade_room_created", "code": code})
        return True

    if msg_type == "join_trade":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return True
        code = str(data.get("code", "")).strip().upper()
        if not re.match(r'^[A-Z]{4}$', code):
            await player.send({"type": "error", "message": "Trade code must be 4 letters."})
            return True
        room = trade_rooms.get(code)
        if not room:
            await player.send({"type": "error", "message": f"Trade room {code} not found."})
            return True
        if room.is_full():
            await player.send({"type": "error", "message": "Trade room is full."})
            return True
        if room.get_player_index(player) != -1:
            await player.send({"type": "error", "message": "Already in this trade room."})
            return True
        room.players[1] = player
        player_trade_rooms[player.id] = code
        # Notify both players
        p0 = room.players[0]
        await p0.send({
            "type": "trade_partner_joined",
            "partner_name": player.name,
        })
        await player.send({
            "type": "trade_room_joined",
            "code": code,
            "partner_name": p0.name,
        })
        # Send both players their Pokemon list for selection
        for p in room.players:
            if p and getattr(p, 'account_id', None):
                team = account_mgr.get_team(p.account_id)
                storage = account_mgr.get_storage(p.account_id)
                await p.send({
                    "type": "trade_pokemon_list",
                    "team": team,
                    "storage": storage,
                })
        return True

    if msg_type == "trade_offer":
        code = player_trade_rooms.get(player.id)
        if not code:
            await player.send({"type": "error", "message": "Not in a trade room."})
            return True
        room = trade_rooms.get(code)
        if not room:
            await player.send({"type": "error", "message": "Trade room not found."})
            return True
        idx = room.get_player_index(player)
        if idx == -1:
            return True
        pokemon_id = data.get("pokemon_id")
        if not pokemon_id:
            await player.send({"type": "error", "message": "No Pokemon selected."})
            return True
        # Verify ownership
        poke = account_mgr.get_pokemon_by_id(pokemon_id, player.account_id)
        if not poke:
            await player.send({"type": "error", "message": "Pokemon not found."})
            return True
        room.offers[idx] = pokemon_id
        room.confirmed[idx] = False  # Reset confirmation when offer changes
        room.confirmed[1 - idx] = False
        # Look up Pokemon data for display
        poke_data = pokemon_data.get_pokemon(poke["dex_id"])
        offer_info = {
            "pokemon_id": poke["id"],
            "dex_id": poke["dex_id"],
            "name": poke.get("nickname") or (poke_data["name"] if poke_data else "???"),
            "level": poke["level"],
            "types": poke_data.get("types", []) if poke_data else [],
        }
        # Tell the player their offer is set
        await player.send({"type": "trade_offer_set", "offer": offer_info})
        # Tell the opponent what's being offered
        opp = room.get_opponent(player)
        if opp:
            # Pokedex: opponent sees this Pokemon
            if getattr(opp, 'account_id', None):
                account_mgr.mark_seen(opp.account_id, poke["dex_id"])
            await opp.send({"type": "trade_partner_offer", "offer": offer_info})
        return True

    if msg_type == "trade_confirm":
        code = player_trade_rooms.get(player.id)
        if not code:
            return True
        room = trade_rooms.get(code)
        if not room:
            return True
        idx = room.get_player_index(player)
        if idx == -1:
            return True
        # Both must have offered
        if room.offers[0] is None or room.offers[1] is None:
            await player.send({"type": "error", "message": "Both players must offer a Pokemon first."})
            return True
        room.confirmed[idx] = True
        opp = room.get_opponent(player)
        if opp:
            await opp.send({"type": "trade_partner_confirmed"})

        # Check if both confirmed
        if room.confirmed[0] and room.confirmed[1]:
            # Execute trade
            p0 = room.players[0]
            p1 = room.players[1]
            success = account_mgr.trade_pokemon(
                p0.account_id, room.offers[0],
                p1.account_id, room.offers[1],
            )
            if success:
                # Pokedex: mark received Pokemon as caught for each player
                # After swap: p0 now owns offers[1], p1 now owns offers[0]
                for receiver, received_id in [(p0, room.offers[1]), (p1, room.offers[0])]:
                    if receiver and getattr(receiver, 'account_id', None):
                        poke = account_mgr.get_pokemon_by_id(received_id, receiver.account_id)
                        if poke:
                            account_mgr.mark_caught(receiver.account_id, poke["dex_id"])
                for p in room.players:
                    if p:
                        await p.send({"type": "trade_complete"})
                # Clean up trade room
                _cleanup_trade_room(code)
            else:
                for p in room.players:
                    if p:
                        await p.send({"type": "error", "message": "Trade failed. Please try again."})
                room.confirmed = [False, False]
        return True

    if msg_type == "trade_cancel":
        code = player_trade_rooms.get(player.id)
        if not code:
            return True
        room = trade_rooms.get(code)
        if room:
            opp = room.get_opponent(player)
            if opp:
                await opp.send({"type": "trade_cancelled", "message": f"{player.name} left the trade."})
                if opp.id in player_trade_rooms:
                    del player_trade_rooms[opp.id]
        _cleanup_trade_room(code)
        if player.id in player_trade_rooms:
            del player_trade_rooms[player.id]
        await player.send({"type": "trade_left"})
        return True

    return False  # Not a trade message


def _cleanup_trade_room(code):
    """Remove a trade room and clean up player references."""
    room = trade_rooms.get(code)
    if room:
        for p in room.players:
            if p and p.id in player_trade_rooms:
                del player_trade_rooms[p.id]
        del trade_rooms[code]


# ─── HTTP Static File Server ───────────────────────────

async def process_request(connection, request):
    """Serve static files for non-WebSocket HTTP requests."""
    path = request.path

    # WebSocket upgrade — let it through
    if path == "/ws":
        return None

    # Admin API endpoints
    if path.startswith("/api/admin/"):
        return await handle_admin_api(request)

    # Static file serving
    if path == "/":
        path = "/index.html"

    # Strip query string for static file serving
    if '?' in path:
        path = path.split('?', 1)[0]

    # Security: prevent path traversal
    try:
        file_path = (APP_DIR / path.lstrip("/")).resolve()
        if APP_DIR.resolve() not in file_path.parents and file_path != APP_DIR.resolve():
            return HttpResponse(404, "Not Found", HttpHeaders(), b"Not Found")
    except Exception:
        return HttpResponse(404, "Not Found", HttpHeaders(), b"Not Found")

    if file_path.is_file():
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        body = file_path.read_bytes()

        # Cache sprites for 1 day
        cache = "max-age=86400" if "/sprites/" in path else "no-cache"

        headers = HttpHeaders({
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "Cache-Control": cache,
        })
        return HttpResponse(200, "OK", headers, body)

    return HttpResponse(404, "Not Found", HttpHeaders(), b"Not Found")


async def handle_admin_api(request):
    """Handle admin REST API requests."""
    path = request.path
    headers = request.headers

    # Auth check
    auth = headers.get("X-Admin-Key", "")
    # Also check query string
    qs = ""
    if "?" in path:
        path, qs = path.split("?", 1)
    params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p) if qs else {}
    key = auth or params.get("k", "")

    if key != ADMIN_SECRET:
        return HttpResponse(
            403, "Forbidden",
            HttpHeaders({"Content-Type": "application/json"}),
            json.dumps({"error": "Invalid admin key"}).encode()
        )

    resp_headers = HttpHeaders({"Content-Type": "application/json"})

    if path == "/api/admin/rooms":
        body = json.dumps(room_manager.get_active_rooms()).encode()
        return HttpResponse(200, "OK", resp_headers, body)

    if path == "/api/admin/history":
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM games ORDER BY finished_at DESC LIMIT 50"
            ).fetchall()
            conn.close()
            games = [dict(r) for r in rows]
            body = json.dumps(games).encode()
            return HttpResponse(200, "OK", resp_headers, body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            return HttpResponse(500, "Error", resp_headers, body)

    if path == "/api/admin/stats":
        try:
            conn = sqlite3.connect(str(DB_PATH))
            total = conn.execute("SELECT COUNT(*) FROM games").fetchone()[0]
            avg_turns = conn.execute(
                "SELECT COALESCE(AVG(turns), 0) FROM games"
            ).fetchone()[0]
            avg_duration = conn.execute(
                "SELECT COALESCE(AVG(duration_sec), 0) FROM games"
            ).fetchone()[0]

            # Most picked Pokemon
            rows = conn.execute(
                "SELECT player1_team, player2_team FROM games"
            ).fetchall()
            conn.close()

            pokemon_counts = {}
            for row in rows:
                for team_json in row:
                    try:
                        ids = json.loads(team_json)
                        for pid in ids:
                            pokemon_counts[pid] = pokemon_counts.get(pid, 0) + 1
                    except (json.JSONDecodeError, TypeError):
                        pass

            # Top 10 Pokemon
            top_pokemon = sorted(pokemon_counts.items(), key=lambda x: -x[1])[:10]
            top_pokemon_named = []
            for pid, count in top_pokemon:
                poke = pokemon_data.get_pokemon(pid)
                name = poke["name"] if poke else f"#{pid}"
                top_pokemon_named.append({"id": pid, "name": name, "count": count})

            body = json.dumps({
                "total_games": total,
                "avg_turns": round(avg_turns, 1),
                "avg_duration_sec": round(avg_duration, 1),
                "active_rooms": len(room_manager.rooms),
                "top_pokemon": top_pokemon_named,
            }).encode()
            return HttpResponse(200, "OK", resp_headers, body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            return HttpResponse(500, "Error", resp_headers, body)

    if path == "/api/admin/bugs":
        try:
            bugs_dir = APP_DIR / "bugs"
            reports = []
            if bugs_dir.exists():
                for f in sorted(bugs_dir.glob("*.md"), reverse=True):
                    content = f.read_text(encoding="utf-8")
                    reports.append({"filename": f.name, "content": content})
            body = json.dumps(reports[:100]).encode()
            return HttpResponse(200, "OK", resp_headers, body)
        except Exception as e:
            body = json.dumps({"error": str(e)}).encode()
            return HttpResponse(500, "Error", resp_headers, body)

    # DELETE room
    match = re.match(r"/api/admin/rooms/([A-Z]{4})", path)
    if match:
        code = match.group(1)
        # Note: websockets library only calls process_request for GET
        # So DELETE needs to be handled differently — we'll use GET with ?action=close
        if params.get("action") == "close":
            closed = await room_manager.close_room(code)
            body = json.dumps({"closed": closed}).encode()
            return HttpResponse(200, "OK", resp_headers, body)

    return HttpResponse(404, "Not Found", resp_headers, b'{"error": "Not found"}')


# ─── WebSocket Message Router ──────────────────────────

async def handle_message(player, msg, room_mgr):
    """Route an incoming WebSocket message."""
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        await player.send({"type": "error", "message": "Invalid JSON."})
        return

    msg_type = data.get("type", "")

    # ─── Account Messages ─────────────────────────────
    if msg_type == "register":
        username = str(data.get("username", "")).strip()
        pin = data.get("pin")
        if not pin or not re.match(r'^\d{4}$', str(pin)):
            await player.send({"type": "register_error", "message": "PIN must be 4 digits."})
            return
        result, error = account_mgr.register(username, pin=str(pin))
        if error:
            await player.send({"type": "register_error", "message": error})
        else:
            player.name = result["username"]
            player.account_id = result["id"]
            await player.send({"type": "register_ok", "profile": result})
            await player.send({
                "type": "pokemon_list",
                "pokemon_list": pokemon_data.get_pokemon_list_for_client(),
                "evolutions": pokemon_data.EVOLUTIONS,
                "mega_evolutions": pokemon_data.MEGA_EVOLUTIONS,
                "dynamax": pokemon_data.DYNAMAX,
            })
        return

    if msg_type == "login":
        token = data.get("token")
        username = data.get("username")
        profile = None
        if token:
            profile = account_mgr.login_by_token(token)
            if profile:
                # Token auto-login bypasses PIN
                player.name = profile["username"]
                player.account_id = profile["id"]
                full = account_mgr.get_profile(profile["id"])
                await player.send({"type": "login_ok", "profile": full})
                await player.send({
                    "type": "pokemon_list",
                    "pokemon_list": pokemon_data.get_pokemon_list_for_client(),
                    "evolutions": pokemon_data.EVOLUTIONS,
                    "mega_evolutions": pokemon_data.MEGA_EVOLUTIONS,
                    "dynamax": pokemon_data.DYNAMAX,
                })
            else:
                await player.send({"type": "login_error", "message": "Account not found."})
        elif username:
            profile = account_mgr.login_by_username(username.strip())
            if profile:
                # Store pending account for PIN verification
                player._pending_pin_account = profile
                if profile.get("has_pin"):
                    await player.send({"type": "needs_pin", "username": profile["username"]})
                else:
                    await player.send({"type": "needs_pin_setup", "username": profile["username"]})
            else:
                await player.send({"type": "login_error", "message": "Account not found."})
        else:
            await player.send({"type": "login_error", "message": "Account not found."})
        return

    if msg_type == "verify_pin":
        pending = getattr(player, '_pending_pin_account', None)
        if not pending:
            await player.send({"type": "pin_error", "message": "No pending login."})
            return
        pin = str(data.get("pin", ""))
        if pin == pending["pin"]:
            player.name = pending["username"]
            player.account_id = pending["id"]
            player._pending_pin_account = None
            full = account_mgr.get_profile(pending["id"])
            await player.send({"type": "login_ok", "profile": full})
            await player.send({
                "type": "pokemon_list",
                "pokemon_list": pokemon_data.get_pokemon_list_for_client(),
                "evolutions": pokemon_data.EVOLUTIONS,
                "mega_evolutions": pokemon_data.MEGA_EVOLUTIONS,
                "dynamax": pokemon_data.DYNAMAX,
            })
        else:
            await player.send({"type": "pin_error", "message": "Wrong PIN. Try again."})
        return

    if msg_type == "set_pin":
        pending = getattr(player, '_pending_pin_account', None)
        if not pending:
            await player.send({"type": "pin_error", "message": "No pending login."})
            return
        pin = str(data.get("pin", ""))
        if not re.match(r'^\d{4}$', pin):
            await player.send({"type": "pin_error", "message": "PIN must be 4 digits."})
            return
        account_mgr.set_pin(pending["id"], pin)
        player.name = pending["username"]
        player.account_id = pending["id"]
        player._pending_pin_account = None
        full = account_mgr.get_profile(pending["id"])
        await player.send({"type": "login_ok", "profile": full})
        await player.send({
            "type": "pokemon_list",
            "pokemon_list": pokemon_data.get_pokemon_list_for_client(),
            "evolutions": pokemon_data.EVOLUTIONS,
            "mega_evolutions": pokemon_data.MEGA_EVOLUTIONS,
            "dynamax": pokemon_data.DYNAMAX,
        })
        return

    if msg_type == "choose_starter":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        dex_id = data.get("dex_id")
        initial_moves = pokemon_data.get_initial_moves(dex_id, 5)
        if account_mgr.choose_starter(player.account_id, dex_id, default_moves=initial_moves):
            account_mgr.mark_caught(player.account_id, dex_id)
            full = account_mgr.get_profile(player.account_id)
            await player.send({"type": "starter_chosen", "profile": full})
        else:
            await player.send({"type": "error", "message": "Invalid starter choice."})
        return

    if msg_type == "delete_account":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        ok = account_mgr.delete_incomplete_account(player.account_id)
        if ok:
            player.account_id = None
            await player.send({"type": "account_deleted"})
        else:
            await player.send({"type": "error", "message": "Cannot delete account after choosing starter."})
        return

    if msg_type == "get_profile":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        full = account_mgr.get_profile(player.account_id)
        await player.send({"type": "profile", "profile": full})
        return

    if msg_type == "get_team":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        team = account_mgr.get_team(player.account_id)
        all_pokemon = account_mgr.get_all_pokemon(player.account_id)
        await player.send({"type": "team_data", "team": team, "all_pokemon": all_pokemon})
        return

    # ─── Backpack / Storage ───────────────────────────
    if msg_type == "get_storage":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        team = account_mgr.get_team(player.account_id)
        storage = account_mgr.get_storage(player.account_id)
        await player.send({"type": "storage_data", "team": team, "storage": storage})
        return

    if msg_type == "swap_to_team":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        team_id = data.get("team_pokemon_id")
        storage_id = data.get("storage_pokemon_id")
        ok = account_mgr.swap_team_member(player.account_id, team_id, storage_id)
        if ok:
            team = account_mgr.get_team(player.account_id)
            storage = account_mgr.get_storage(player.account_id)
            await player.send({"type": "storage_data", "team": team, "storage": storage})
        else:
            await player.send({"type": "error", "message": "Swap failed."})
        return

    if msg_type == "move_to_team":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        pokemon_id = data.get("pokemon_id")
        ok = account_mgr.move_to_team(player.account_id, pokemon_id)
        if ok:
            team = account_mgr.get_team(player.account_id)
            storage = account_mgr.get_storage(player.account_id)
            await player.send({"type": "storage_data", "team": team, "storage": storage})
        else:
            await player.send({"type": "error", "message": "Team is full (6 max)."})
        return

    if msg_type == "move_to_storage":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        pokemon_id = data.get("pokemon_id")
        ok = account_mgr.move_to_storage(player.account_id, pokemon_id)
        if ok:
            team = account_mgr.get_team(player.account_id)
            storage = account_mgr.get_storage(player.account_id)
            await player.send({"type": "storage_data", "team": team, "storage": storage})
        else:
            await player.send({"type": "error", "message": "Must keep at least 1 Pokemon on team."})
        return

    # ─── Game Messages ─────────────────────────────────
    if msg_type == "create_ai_battle":
        if not player.name:
            await player.send({"type": "error", "message": "Not logged in."})
            return

        bot = BotPlayer()
        code = await room_mgr.create_room(player)
        await player.send({
            "type": "room_created",
            "code": code,
            "ai_battle": True,
            "opponent_name": bot.name,
        })
        # Add bot to room — triggers team select
        room_mgr.player_rooms[bot.id] = code
        room = room_mgr.rooms.get(code)
        if room:
            await room.add_player(bot)
        return

    if msg_type == "create_journey_battle":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return

        # Pre-set player's Journey team
        journey_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        player.team = journey_team
        player.team_dex_ids = [p.dex_id for p in journey_team]
        player.team_name = f"{player.name}'s Team"
        player.ready = True

        # Create bot scaled to player's average level
        avg_level = sum(p.level for p in journey_team) / len(journey_team)
        bot = BotPlayer()
        bot.select_team_at_level(int(avg_level))

        code = await room_mgr.create_room(player)
        await player.send({
            "type": "room_created",
            "code": code,
            "ai_battle": True,
            "journey_battle": True,
            "opponent_name": bot.name,
        })
        # Add bot to room — both already ready, skips team select
        room_mgr.player_rooms[bot.id] = code
        room = room_mgr.rooms.get(code)
        if room:
            await room.add_player(bot)
        return

    if msg_type == "create_room":
        name = str(data.get("name", "")).strip()
        if not name or len(name) < 2 or len(name) > 16:
            await player.send({"type": "error", "message": "Name must be 2-16 characters."})
            return
        if not re.match(r'^[a-zA-Z0-9 ]+$', name):
            await player.send({"type": "error", "message": "Name can only contain letters, numbers, and spaces."})
            return

        player.name = name

        # Pre-set journey team for PvP if logged in
        if getattr(player, 'account_id', None):
            team_data = account_mgr.get_team(player.account_id)
            if team_data:
                journey_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
                player.team = journey_team
                player.team_dex_ids = [p.dex_id for p in journey_team]
                player.team_name = f"{player.name}'s Team"
                player.ready = True

        code = await room_mgr.create_room(player)
        await player.send({"type": "room_created", "code": code})

    elif msg_type == "join_room":
        name = str(data.get("name", "")).strip()
        code = str(data.get("code", "")).strip().upper()

        if not name or len(name) < 2 or len(name) > 16:
            await player.send({"type": "error", "message": "Name must be 2-16 characters."})
            return
        if not re.match(r'^[a-zA-Z0-9 ]+$', name):
            await player.send({"type": "error", "message": "Name can only contain letters, numbers, and spaces."})
            return
        if not re.match(r'^[A-Z]{4}$', code):
            await player.send({"type": "error", "message": "Room code must be 4 letters."})
            return

        player.name = name

        # Pre-set journey team for PvP if logged in
        if getattr(player, 'account_id', None):
            team_data = account_mgr.get_team(player.account_id)
            if team_data:
                journey_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
                player.team = journey_team
                player.team_dex_ids = [p.dex_id for p in journey_team]
                player.team_name = f"{player.name}'s Team"
                player.ready = True

        result = await room_mgr.join_room(player, code)
        if result:
            opponent = room_mgr.get_room(player).get_opponent(player)
            await player.send({
                "type": "room_joined",
                "code": code,
                "opponent_name": opponent.name if opponent else None,
            })

    elif msg_type == "select_team":
        room = room_mgr.get_room(player)
        if room:
            await room.handle_team_select(player, data)

    elif msg_type == "choose_action":
        room = room_mgr.get_room(player)
        if room:
            # Client sends action_type to avoid JSON key collision with message type
            data["type"] = data.pop("action_type", data.get("type"))
            await room.handle_action(player, data)

    elif msg_type == "dodge_result":
        room = room_mgr.get_room(player)
        if room:
            await room.handle_dodge_result(player, data)

    elif msg_type == "force_switch":
        room = room_mgr.get_room(player)
        if room:
            await room.handle_force_switch(player, data)

    elif msg_type == "rematch":
        room = room_mgr.get_room(player)
        if room:
            await room.handle_rematch(player)

    elif msg_type == "leave":
        await room_mgr.remove_player(player)
        await player.send({"type": "left_room"})

    elif msg_type == "ping":
        await player.send({"type": "pong"})

    # ─── Journey Mode Messages ────────────────────────

    elif msg_type == "wild_encounter":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return
        avg_level = sum(p["level"] for p in team_data) / len(team_data)
        pity_counter = account_mgr.get_encounter_counter(player.account_id)
        current_region = account_mgr.get_current_region(player.account_id)
        wild, rarity = generate_wild_pokemon(avg_level, pity_counter=pity_counter, region=current_region)
        # Update pity counter: reset on legendary, increment otherwise
        if rarity == "legendary":
            account_mgr.reset_encounter_counter(player.account_id)
            new_counter = 0
        else:
            new_counter = account_mgr.increment_encounter_counter(player.account_id)
        team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        encounter = WildEncounter(player, team, wild, rarity)
        active_encounters[player.id] = encounter
        account_mgr.mark_seen(player.account_id, wild.dex_id)
        region_data = pokemon_data.get_region(current_region)
        region_name = region_data["name"] if region_data else "Kanto"
        # Pity hint: subtle atmospheric message when close to guaranteed legendary
        pity_hint = None
        if new_counter >= 45:
            pity_hint = "The air crackles with strange energy..."
        elif new_counter >= 40:
            pity_hint = "You sense something powerful nearby..."
        await player.send({"type": "wild_encounter_start", "region_name": region_name, "pity_hint": pity_hint, **encounter.serialize_state()})

    elif msg_type == "start_training":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return
        dex_id = data.get("dex_id")
        level = data.get("level", 50)
        # Validate dex_id
        species = pokemon_data.POKEMON.get(dex_id)
        if not species:
            await player.send({"type": "error", "message": "Invalid Pokémon."})
            return
        # Clamp level 1-100
        level = max(1, min(100, int(level)))
        # Build the training opponent
        moves = pokemon_data.get_moves_at_level(dex_id, level)
        if not moves:
            moves = species["moves"][:4]
        training_pokemon = PokemonInstance(species, pokemon_data.MOVES, level=level, custom_moves=moves)
        team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        encounter = WildEncounter(player, team, training_pokemon, "training")
        encounter.is_training = True
        active_encounters[player.id] = encounter
        state = encounter.serialize_state()
        state["is_training"] = True
        await player.send({"type": "training_battle_start", **state})

    elif msg_type == "wild_action":
        encounter = active_encounters.get(player.id)
        if not encounter:
            await player.send({"type": "error", "message": "No active encounter."})
            return
        await _handle_wild_action(player, encounter, data)

    elif msg_type == "get_gyms":
        if not getattr(player, 'account_id', None):
            return
        current_region = account_mgr.get_current_region(player.account_id)
        badges = account_mgr.get_badges(player.account_id, region=current_region)
        leaders = get_gym_leaders(current_region)
        next_gym = get_next_gym(badges, current_region)
        gyms = []
        for g in leaders:
            gyms.append({
                "id": g["id"], "name": g["name"], "type": g["type"],
                "badge": g["badge"], "completed": g["id"] in badges,
                "team_size": len(g["team"]),
                "max_level": max(t.get("level", 50) for t in g["team"]),
            })
        region_data = pokemon_data.get_region(current_region)
        await player.send({
            "type": "gym_list",
            "gyms": gyms,
            "next_gym_id": next_gym["id"] if next_gym else None,
            "badges": badges,
            "region": current_region,
            "region_name": region_data["name"] if region_data else "Kanto",
        })

    elif msg_type == "start_gym":
        if not getattr(player, 'account_id', None):
            return
        gym_id = data.get("gym_id")
        current_region = account_mgr.get_current_region(player.account_id)
        gym = get_gym(gym_id, current_region)
        if not gym:
            await player.send({"type": "error", "message": "Invalid gym."})
            return
        badges = account_mgr.get_badges(player.account_id, region=current_region)
        # Must beat gyms in order
        if gym_id > 1 and (gym_id - 1) not in badges:
            await player.send({"type": "error", "message": "Beat the previous gym first!"})
            return
        await player.send({
            "type": "gym_intro",
            "gym": {
                "id": gym["id"], "name": gym["name"], "title": gym.get("title", "Gym Leader"),
                "type": gym["type"], "badge": gym["badge"],
                "dialog_intro": gym["dialog_intro"],
                "team_size": len(gym["team"]),
                "max_level": max(t.get("level", 50) for t in gym["team"]),
            }
        })

    elif msg_type == "gym_battle_start":
        if not getattr(player, 'account_id', None):
            return
        gym_id = data.get("gym_id")
        current_region = account_mgr.get_current_region(player.account_id)
        gym = get_gym(gym_id, current_region)
        if not gym:
            return
        # Build player team from their caught Pokemon
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return
        player_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        gym_team = build_trainer_team(gym["team"])

        # Use wild encounter system for gym battle (1v1 AI battle)
        encounter = WildEncounter(player, player_team, None, None)
        encounter.gym = gym
        encounter.gym_team = gym_team
        encounter.gym_active = 0
        encounter.wild = gym_team[0]
        encounter.is_gym = True
        encounter.battle_region = current_region
        active_encounters[player.id] = encounter
        # Pokedex: mark all gym Pokemon as seen
        account_mgr.mark_seen_batch(player.account_id, [p.dex_id for p in gym_team])

        await player.send({
            "type": "gym_battle_start",
            **encounter.serialize_state(),
            "gym_name": gym["name"],
            "gym_team_size": len(gym_team),
        })

    # ─── Region Travel ─────────────────────────────
    elif msg_type == "get_regions":
        if not getattr(player, 'account_id', None):
            return
        current_region = account_mgr.get_current_region(player.account_id)
        badges_by_region = account_mgr.get_badges_by_region(player.account_id)
        regions = []
        for r in pokemon_data.get_all_regions():
            region_badges = badges_by_region.get(r["id"], [])
            regions.append({
                "id": r["id"],
                "name": r["name"],
                "generation": r["generation"],
                "dex_range": r["dex_range"],
                "description": r["description"],
                "color": r["color"],
                "icon_dex_id": r["icon_dex_id"],
                "pokemon_count": r["dex_range"][1] - r["dex_range"][0] + 1,
                "badge_count": len(region_badges),
                "badges": region_badges,
            })
        await player.send({
            "type": "regions_list",
            "regions": regions,
            "current_region": current_region,
        })

    elif msg_type == "travel_to_region":
        if not getattr(player, 'account_id', None):
            return
        region_id = data.get("region_id", "").strip().lower()
        region = pokemon_data.get_region(region_id)
        if not region:
            await player.send({"type": "error", "message": "Unknown region."})
            return
        account_mgr.set_current_region(player.account_id, region_id)
        await player.send({
            "type": "region_changed",
            "region_id": region_id,
            "region_name": region["name"],
            "region_color": region["color"],
        })

    # ─── Elite Four / Champion / Masters Eight ──────
    elif msg_type == "get_elite_four":
        if not getattr(player, 'account_id', None):
            return
        current_region = account_mgr.get_current_region(player.account_id)
        badges = account_mgr.get_badges(player.account_id, region=current_region)
        milestones = account_mgr.get_milestones(player.account_id)
        if len(badges) < 8:
            await player.send({"type": "error", "message": "Beat all 8 gym leaders first!"})
            return
        e4_members = get_elite_four(current_region)
        # Region-prefixed milestone check
        prefix = f"{current_region}:" if current_region != "kanto" else ""
        e4_list = []
        for i, e in enumerate(e4_members):
            e4_list.append({
                "id": e["id"], "name": e["name"], "title": e["title"],
                "type": e["type"], "completed": f"{prefix}{e['id']}_defeated" in milestones,
                "team_size": len(e["team"]),
                "max_level": max(t.get("level", 50) for t in e["team"]),
            })
        region_data = pokemon_data.get_region(current_region)
        await player.send({
            "type": "e4_list",
            "members": e4_list,
            "milestones": milestones,
            "region": current_region,
            "region_name": region_data["name"] if region_data else "Kanto",
        })

    elif msg_type == "start_e4":
        if not getattr(player, 'account_id', None):
            return
        e4_id = data.get("e4_id")
        current_region = account_mgr.get_current_region(player.account_id)
        e4_members = get_elite_four(current_region)
        member = None
        e4_index = -1
        for i, e in enumerate(e4_members):
            if e["id"] == e4_id:
                member = e
                e4_index = i
                break
        if not member:
            await player.send({"type": "error", "message": "Invalid E4 member."})
            return
        milestones = account_mgr.get_milestones(player.account_id)
        prefix = f"{current_region}:" if current_region != "kanto" else ""
        # Must beat E4 in order
        if e4_index > 0:
            prev_id = e4_members[e4_index - 1]["id"]
            if f"{prefix}{prev_id}_defeated" not in milestones:
                await player.send({"type": "error", "message": f"Beat {e4_members[e4_index-1]['name']} first!"})
                return
        await player.send({
            "type": "trainer_intro",
            "trainer": {
                "id": member["id"], "name": member["name"], "title": member["title"],
                "type": member["type"], "dialog_intro": member["dialog_intro"],
                "team_size": len(member["team"]),
                "max_level": max(t.get("level", 50) for t in member["team"]),
                "category": "e4",
            }
        })

    elif msg_type == "e4_battle_start":
        if not getattr(player, 'account_id', None):
            return
        e4_id = data.get("e4_id")
        current_region = account_mgr.get_current_region(player.account_id)
        e4_members = get_elite_four(current_region)
        member = None
        for e in e4_members:
            if e["id"] == e4_id:
                member = e
                break
        if not member:
            return
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return
        player_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        trainer_team = build_trainer_team(member["team"])
        encounter = WildEncounter(player, player_team, None, None)
        encounter.gym = member  # reuse gym pattern for all trainer battles
        encounter.gym_team = trainer_team
        encounter.gym_active = 0
        encounter.wild = trainer_team[0]
        encounter.is_gym = True
        encounter.trainer_category = "e4"
        encounter.battle_region = current_region
        active_encounters[player.id] = encounter
        account_mgr.mark_seen_batch(player.account_id, [p.dex_id for p in trainer_team])
        await player.send({
            "type": "gym_battle_start",
            **encounter.serialize_state(),
            "gym_name": member["name"],
            "gym_team_size": len(trainer_team),
        })

    elif msg_type == "get_champion":
        if not getattr(player, 'account_id', None):
            return
        current_region = account_mgr.get_current_region(player.account_id)
        milestones = account_mgr.get_milestones(player.account_id)
        e4_members = get_elite_four(current_region)
        prefix = f"{current_region}:" if current_region != "kanto" else ""
        # Must beat all E4 for this region
        for e in e4_members:
            if f"{prefix}{e['id']}_defeated" not in milestones:
                await player.send({"type": "error", "message": "Beat the entire Elite Four first!"})
                return
        champ = get_champion(current_region)
        await player.send({
            "type": "trainer_intro",
            "trainer": {
                "id": champ["id"], "name": champ["name"], "title": champ["title"],
                "type": champ.get("type", "normal"),
                "dialog_intro": champ["dialog_intro"],
                "team_size": len(champ["team"]),
                "max_level": max(t.get("level", 50) for t in champ["team"]),
                "category": "champion",
            }
        })

    elif msg_type == "champion_battle_start":
        if not getattr(player, 'account_id', None):
            return
        current_region = account_mgr.get_current_region(player.account_id)
        champ = get_champion(current_region)
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return
        player_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        trainer_team = build_trainer_team(champ["team"])
        encounter = WildEncounter(player, player_team, None, None)
        encounter.gym = champ
        encounter.gym_team = trainer_team
        encounter.gym_active = 0
        encounter.wild = trainer_team[0]
        encounter.is_gym = True
        encounter.trainer_category = "champion"
        encounter.battle_region = current_region
        active_encounters[player.id] = encounter
        account_mgr.mark_seen_batch(player.account_id, [p.dex_id for p in trainer_team])
        await player.send({
            "type": "gym_battle_start",
            **encounter.serialize_state(),
            "gym_name": champ["name"],
            "gym_team_size": len(trainer_team),
        })

    elif msg_type == "get_masters":
        if not getattr(player, 'account_id', None):
            return
        milestones = account_mgr.get_milestones(player.account_id)
        # Masters unlocked by any region's champion defeat
        has_champion = "champion_defeated" in milestones or any(
            f"{r['id']}:champion_defeated" in milestones for r in pokemon_data.get_all_regions()
        )
        if not has_champion:
            await player.send({"type": "error", "message": "Beat a Champion first!"})
            return
        m8_list = []
        for m in MASTERS_EIGHT:
            m8_list.append({
                "id": m["id"], "name": m["name"], "title": m["title"],
                "type": m["type"], "completed": f"{m['id']}_defeated" in milestones,
                "team_size": len(m["team"]),
                "max_level": max(t["level"] for t in m["team"]),
            })
        await player.send({
            "type": "masters_list",
            "members": m8_list,
            "milestones": milestones,
        })

    elif msg_type == "start_masters":
        if not getattr(player, 'account_id', None):
            return
        m8_id = data.get("m8_id")
        member = get_masters_opponent(m8_id)
        if not member:
            await player.send({"type": "error", "message": "Invalid Masters opponent."})
            return
        await player.send({
            "type": "trainer_intro",
            "trainer": {
                "id": member["id"], "name": member["name"], "title": member["title"],
                "type": member["type"], "dialog_intro": member["dialog_intro"],
                "team_size": len(member["team"]),
                "max_level": max(t["level"] for t in member["team"]),
                "category": "masters",
            }
        })

    elif msg_type == "masters_battle_start":
        if not getattr(player, 'account_id', None):
            return
        m8_id = data.get("m8_id")
        member = get_masters_opponent(m8_id)
        if not member:
            return
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokémon in team."})
            return
        player_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        trainer_team = build_trainer_team(member["team"])
        encounter = WildEncounter(player, player_team, None, None)
        encounter.gym = member
        encounter.gym_team = trainer_team
        encounter.gym_active = 0
        encounter.wild = trainer_team[0]
        encounter.is_gym = True
        encounter.trainer_category = "masters"
        active_encounters[player.id] = encounter
        account_mgr.mark_seen_batch(player.account_id, [p.dex_id for p in trainer_team])
        await player.send({
            "type": "gym_battle_start",
            **encounter.serialize_state(),
            "gym_name": member["name"],
            "gym_team_size": len(trainer_team),
        })

    elif msg_type == "get_shop":
        if not getattr(player, 'account_id', None):
            return
        currency = account_mgr.get_currency(player.account_id)
        profile = account_mgr.get_profile(player.account_id)
        inventory = account_mgr.get_inventory(player.account_id)
        items = []
        for key, item in SHOP_ITEMS.items():
            category = item.get("category", "ball")
            if category == "ball":
                owned = profile.get("pokeballs", 0) if key == "pokeball" else 0
            else:
                owned = inventory.get(key, 0)
            items.append({
                "type": key,
                "name": item["name"],
                "price": item["price"],
                "category": category,
                "owned": owned,
                "description": _item_description(key, item),
            })
        await player.send({"type": "shop_data", "items": items, "currency": currency,
                           "pokeballs": profile.get("pokeballs", 0),
                           "inventory": inventory})

    elif msg_type == "buy_item":
        if not getattr(player, 'account_id', None):
            return
        item_type = data.get("item_type", "")
        quantity = data.get("quantity", 1)
        if item_type not in SHOP_ITEMS or quantity < 1:
            await player.send({"type": "error", "message": "Invalid item."})
            return
        total_cost = SHOP_ITEMS[item_type]["price"] * quantity
        if account_mgr.spend_currency(player.account_id, total_cost):
            item = SHOP_ITEMS[item_type]
            category = item.get("category", "ball")
            if category == "ball":
                account_mgr.add_pokeballs(player.account_id, quantity)
            else:
                account_mgr.add_item(player.account_id, item_type, quantity)
            currency = account_mgr.get_currency(player.account_id)
            profile = account_mgr.get_profile(player.account_id)
            inventory = account_mgr.get_inventory(player.account_id)
            await player.send({
                "type": "buy_result", "success": True,
                "item_type": item_type, "quantity": quantity,
                "new_currency": currency,
                "pokeballs": profile.get("pokeballs", 0),
                "inventory": inventory,
            })
        else:
            await player.send({"type": "buy_result", "success": False,
                               "message": "Not enough PokéDollars!"})

    elif msg_type == "get_learnable_moves":
        if not getattr(player, 'account_id', None):
            return
        pokemon_row_id = data.get("pokemon_id")
        if not pokemon_row_id:
            await player.send({"type": "error", "message": "Missing pokemon_id."})
            return
        # Find this Pokemon in the player's collection
        all_pokemon = account_mgr.get_all_pokemon(player.account_id)
        poke_row = next((p for p in all_pokemon if p["id"] == pokemon_row_id), None)
        if not poke_row:
            await player.send({"type": "error", "message": "Pokemon not found."})
            return
        dex_id = poke_row["dex_id"]
        level = poke_row["level"]
        current_moves = _get_current_moves(poke_row)
        # Get all moves learnable at or below current level from learnset
        learnset = pokemon_data.get_learnset(dex_id)
        learnable = []
        seen = set()
        for entry in learnset:
            if entry["level"] <= level and entry["move"] not in seen:
                seen.add(entry["move"])
                move_data = pokemon_data.MOVES.get(entry["move"])
                if move_data:
                    learnable.append({
                        "move_id": entry["move"],
                        "level_learned": entry["level"],
                        "name": move_data["name"],
                        "type": move_data["type"],
                        "category": move_data["category"],
                        "power": move_data["power"],
                        "accuracy": move_data["accuracy"],
                        "pp": move_data["pp"],
                    })
        # Include species default moves from pokemon.json not already in learnable
        species = pokemon_data.POKEMON.get(dex_id)
        if species:
            for mid in species["moves"]:
                if mid not in seen and mid in pokemon_data.MOVES:
                    seen.add(mid)
                    move_data = pokemon_data.MOVES[mid]
                    learnable.append({
                        "move_id": mid,
                        "level_learned": 1,
                        "name": move_data["name"],
                        "type": move_data["type"],
                        "category": move_data["category"],
                        "power": move_data["power"],
                        "accuracy": move_data["accuracy"],
                        "pp": move_data["pp"],
                    })
        # Build current move details
        current_move_details = []
        for mid in current_moves:
            md = pokemon_data.MOVES.get(mid)
            if md:
                current_move_details.append({
                    "move_id": mid,
                    "name": md["name"],
                    "type": md["type"],
                    "category": md["category"],
                    "power": md["power"],
                    "accuracy": md["accuracy"],
                    "pp": md["pp"],
                })
        poke_data = pokemon_data.get_pokemon(dex_id)
        await player.send({
            "type": "learnable_moves",
            "pokemon_id": pokemon_row_id,
            "dex_id": dex_id,
            "pokemon_name": poke_data["name"] if poke_data else f"#{dex_id}",
            "level": level,
            "current_moves": current_move_details,
            "learnable_moves": learnable,
        })
        return

    elif msg_type == "swap_move":
        if not getattr(player, 'account_id', None):
            return
        pokemon_row_id = data.get("pokemon_id")
        old_move = data.get("old_move")
        new_move = data.get("new_move")
        if not pokemon_row_id or not new_move:
            await player.send({"type": "error", "message": "Missing parameters."})
            return
        # Find this Pokemon
        all_pokemon = account_mgr.get_all_pokemon(player.account_id)
        poke_row = next((p for p in all_pokemon if p["id"] == pokemon_row_id), None)
        if not poke_row:
            await player.send({"type": "error", "message": "Pokemon not found."})
            return
        dex_id = poke_row["dex_id"]
        level = poke_row["level"]
        current_moves = _get_current_moves(poke_row)
        # Validate that the new move is in the learnset or species defaults
        learnset = pokemon_data.get_learnset(dex_id)
        valid_moves = {entry["move"] for entry in learnset if entry["level"] <= level}
        species = pokemon_data.POKEMON.get(dex_id)
        if species:
            for mid in species["moves"]:
                if mid in pokemon_data.MOVES:
                    valid_moves.add(mid)
        if new_move not in valid_moves:
            await player.send({"type": "error", "message": "Cannot learn that move yet."})
            return
        if new_move in current_moves:
            await player.send({"type": "error", "message": "Already knows that move."})
            return
        # Perform the swap
        if old_move and old_move in current_moves:
            idx = current_moves.index(old_move)
            current_moves[idx] = new_move
        elif len(current_moves) < 4:
            current_moves.append(new_move)
        else:
            await player.send({"type": "error", "message": "Must replace an existing move."})
            return
        account_mgr.update_pokemon_moves(pokemon_row_id, current_moves)
        # Return updated move details
        new_move_data = pokemon_data.MOVES.get(new_move, {})
        await player.send({
            "type": "swap_move_ok",
            "pokemon_id": pokemon_row_id,
            "new_moves": current_moves,
            "swapped_move": {
                "move_id": new_move,
                "name": new_move_data.get("name", new_move),
            },
        })
        return

    elif msg_type == "learn_move_choice":
        # Handle level-up move learning choice from client
        if not getattr(player, 'account_id', None):
            return
        pokemon_row_id = data.get("pokemon_id")
        new_move = data.get("new_move")
        replace_move = data.get("replace_move")  # None/null means skip learning
        if not pokemon_row_id or not new_move:
            return
        if replace_move is None:
            # Player chose to skip learning this move
            await player.send({"type": "learn_move_skipped", "pokemon_id": pokemon_row_id, "move": new_move})
            return
        # Find the Pokemon
        all_pokemon = account_mgr.get_all_pokemon(player.account_id)
        poke_row = next((p for p in all_pokemon if p["id"] == pokemon_row_id), None)
        if not poke_row:
            return
        current_moves = _get_current_moves(poke_row)
        if replace_move == "__add__":
            # Add to empty slot (fewer than 4 moves)
            if len(current_moves) < 4:
                current_moves.append(new_move)
        elif replace_move in current_moves:
            idx = current_moves.index(replace_move)
            current_moves[idx] = new_move
        else:
            return
        account_mgr.update_pokemon_moves(pokemon_row_id, current_moves)
        new_move_data = pokemon_data.MOVES.get(new_move, {})
        await player.send({
            "type": "learn_move_ok",
            "pokemon_id": pokemon_row_id,
            "new_move": new_move,
            "new_move_name": new_move_data.get("name", new_move),
            "current_moves": current_moves,
        })
        return

    elif msg_type == "use_item":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        await _handle_use_item(player, data)

    elif msg_type == "use_rare_candy":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        await _handle_use_rare_candy(player, data)

    elif msg_type == "use_evolution_item":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        item_id = data.get("item_id")
        pokemon_row_id = data.get("pokemon_id")
        if not item_id or not pokemon_row_id:
            await player.send({"type": "error", "message": "Missing parameters."})
            return
        # Validate inventory
        inv = account_mgr.get_inventory(player.account_id)
        if inv.get(item_id, 0) <= 0:
            await player.send({"type": "error", "message": "You don't have that item."})
            return
        # Find the pokemon
        all_pokemon = account_mgr.get_all_pokemon(player.account_id)
        poke_row = next((p for p in all_pokemon if p["id"] == pokemon_row_id), None)
        if not poke_row:
            await player.send({"type": "error", "message": "Pokemon not found."})
            return
        # Check if this pokemon can evolve with this item
        evo = pokemon_data.get_item_evolution(poke_row["dex_id"], item_id)
        if not evo:
            await player.send({"type": "error", "message": "This item has no effect on that Pokemon."})
            return
        # Perform evolution
        new_dex_id = evo["evolves_to"]
        new_poke = pokemon_data.get_pokemon(new_dex_id)
        account_mgr.update_pokemon_species(pokemon_row_id, new_dex_id)
        account_mgr.use_item(player.account_id, item_id)
        # Pokedex: mark evolved form as caught
        account_mgr.mark_caught(player.account_id, new_dex_id)
        await player.send({
            "type": "evolution_item_result",
            "success": True,
            "pokemon_id": pokemon_row_id,
            "from_dex_id": poke_row["dex_id"],
            "from_name": pokemon_data.POKEMON.get(poke_row["dex_id"], {}).get("name", "???"),
            "to_dex_id": new_dex_id,
            "to_name": new_poke["name"] if new_poke else "???",
            "item_used": item_id,
        })
        return

    elif msg_type == "use_rare_candy":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        await _handle_use_rare_candy(player, data)

    elif msg_type == "get_progression":
        if not getattr(player, 'account_id', None):
            return
        current_region = account_mgr.get_current_region(player.account_id)
        badges = account_mgr.get_badges(player.account_id, region=current_region)
        badges_by_region = account_mgr.get_badges_by_region(player.account_id)
        milestones = account_mgr.get_milestones(player.account_id)
        profile = account_mgr.get_profile(player.account_id)
        await player.send({
            "type": "progression_data",
            "badges": badges,
            "badges_by_region": badges_by_region,
            "current_region": current_region,
            "milestones": milestones,
            "currency": profile.get("currency", 500),
            "pokeballs": profile.get("pokeballs", 10),
            "total_pokemon": profile.get("total_pokemon", 0),
        })

    # ─── Pokedex Messages ────────────────────────────
    elif msg_type == "get_pokedex":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        # Backfill on first access: mark all owned Pokemon as caught
        pokedex = account_mgr.get_pokedex(player.account_id)
        if not pokedex:
            count = account_mgr.backfill_pokedex(player.account_id)
            if count > 0:
                pokedex = account_mgr.get_pokedex(player.account_id)
        counts = account_mgr.get_pokedex_counts(player.account_id)
        shiny_ids = account_mgr.get_shiny_dex_ids(player.account_id)
        total = len(pokemon_data.POKEMON)
        entries = []
        for dex_id in sorted(pokemon_data.POKEMON.keys()):
            poke = pokemon_data.POKEMON[dex_id]
            entry = pokedex.get(dex_id, {})
            entries.append({
                "dex_id": dex_id,
                "name": poke["name"],
                "types": poke["types"],
                "base_stats": poke.get("base_stats", {}),
                "seen": entry.get("seen", False),
                "caught": entry.get("caught", False),
                "has_shiny": dex_id in shiny_ids,
            })
        await player.send({
            "type": "pokedex_data",
            "entries": entries,
            "counts": {"seen": counts["seen"], "caught": counts["caught"], "total": total},
        })


    # ─── Tournament Messages ─────────────────────────
    elif msg_type == "get_tournament":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        milestones = account_mgr.get_milestones(player.account_id)
        if "champion_defeated" not in milestones:
            await player.send({"type": "error", "message": "Beat the Champion first to unlock tournaments!"})
            return
        ts = active_tournaments.get(player.account_id)
        if ts and not ts.is_eliminated and not ts.is_complete:
            await player.send({"type": "tournament_data", **ts.serialize()})
        else:
            # No active tournament — show entry screen
            currency = account_mgr.get_currency(player.account_id)
            await player.send({
                "type": "tournament_data",
                "active": False,
                "currency": currency,
                "entry_fee": 500,
            })

    elif msg_type == "start_tournament":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        milestones = account_mgr.get_milestones(player.account_id)
        if "champion_defeated" not in milestones:
            await player.send({"type": "error", "message": "Beat the Champion first!"})
            return
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokemon in team."})
            return
        # Deduct entry fee
        if not account_mgr.spend_currency(player.account_id, 500):
            await player.send({"type": "error", "message": "Not enough PokeDollars! Entry fee is $500."})
            return
        # Generate bracket
        avg_level = sum(p["level"] for p in team_data) / len(team_data)
        bracket = generate_tournament_bracket(avg_level)
        ts = TournamentState(player.account_id, bracket)
        active_tournaments[player.account_id] = ts
        await player.send({"type": "tournament_started", **ts.serialize()})

    elif msg_type == "start_tournament_match":
        # Show trainer intro for current tournament opponent
        if not getattr(player, 'account_id', None):
            return
        ts = active_tournaments.get(player.account_id)
        if not ts or ts.is_eliminated or ts.is_complete:
            await player.send({"type": "error", "message": "No active tournament."})
            return
        opponent = ts.current_opponent()
        if not opponent:
            await player.send({"type": "error", "message": "No more opponents."})
            return
        await player.send({
            "type": "trainer_intro",
            "trainer": {
                "id": opponent["id"],
                "name": opponent["name"],
                "title": opponent["title"],
                "type": opponent["type"],
                "dialog_intro": opponent["dialog_intro"],
                "team_size": len(opponent["team"]),
                "max_level": max(t["level"] for t in opponent["team"]),
                "category": "tournament",
                "tournament_round_name": opponent["round_name"],
            }
        })

    elif msg_type == "tournament_battle_start":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        ts = active_tournaments.get(player.account_id)
        if not ts or ts.is_eliminated or ts.is_complete:
            await player.send({"type": "error", "message": "No active tournament."})
            return
        opponent = ts.current_opponent()
        if not opponent:
            await player.send({"type": "error", "message": "No more opponents."})
            return
        # Build player team fresh (HP/PP reset between tournament matches)
        team_data = account_mgr.get_team(player.account_id)
        if not team_data:
            await player.send({"type": "error", "message": "No Pokemon in team."})
            return
        player_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        trainer_team = build_trainer_team(opponent["team"])
        encounter = WildEncounter(player, player_team, None, None)
        encounter.gym = opponent
        encounter.gym_team = trainer_team
        encounter.gym_active = 0
        encounter.wild = trainer_team[0]
        encounter.is_gym = True
        encounter.trainer_category = "tournament"
        active_encounters[player.id] = encounter
        await player.send({
            "type": "gym_battle_start",
            **encounter.serialize_state(),
            "gym_name": opponent["name"],
            "gym_team_size": len(trainer_team),
        })

    elif msg_type == "tournament_continue":
        # Advance to next round after viewing results
        if not getattr(player, 'account_id', None):
            return
        ts = active_tournaments.get(player.account_id)
        if not ts:
            await player.send({"type": "error", "message": "No active tournament."})
            return
        if ts.is_complete:
            await player.send({"type": "tournament_data", **ts.serialize()})
        elif ts.is_eliminated:
            del active_tournaments[player.account_id]
            await player.send({"type": "tournament_eliminated", "message": "Tournament over."})
        else:
            await player.send({"type": "tournament_data", **ts.serialize()})

    elif msg_type == "tournament_forfeit":
        if not getattr(player, 'account_id', None):
            return
        ts = active_tournaments.get(player.account_id)
        if ts:
            del active_tournaments[player.account_id]
        await player.send({"type": "tournament_forfeited", "message": "You forfeited the tournament."})

    # ─── Trade Messages ─────────────────────────────
    elif msg_type in ("create_trade", "join_trade", "trade_offer", "trade_confirm", "trade_cancel"):
        await _handle_trade_message(player, msg_type, data)

    # ─── Bug Reports ─────────────────────────────
    elif msg_type == "submit_bug_report":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        description = str(data.get("description", "")).strip()
        if not description:
            await player.send({"type": "error", "message": "Please describe the bug."})
            return
        if len(description) > 2000:
            description = description[:2000]
        context = account_mgr.get_bug_report_context(player.account_id)
        if context:
            context["current_screen"] = data.get("current_screen", "unknown")
        try:
            bugs_dir = APP_DIR / "bugs"
            bugs_dir.mkdir(exist_ok=True)
            ts = int(time.time())
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d_%H%M%S")
            safe_name = "".join(c if c.isalnum() else "_" for c in (player.name or "unknown"))
            filename = f"{date_str}_{safe_name}.md"
            team_str = ""
            if context and context.get("team"):
                team_str = ", ".join(f"Lv{p['level']}" for p in context["team"])
            lines = [
                f"# Bug Report — {player.name or 'unknown'}",
                f"",
                f"- **Date**: {dt.strftime('%m/%d/%Y %I:%M:%S %p')} UTC",
                f"- **Player**: {player.name or 'unknown'} (ID: {player.account_id})",
                f"- **Screen**: {context.get('current_screen', 'unknown') if context else 'unknown'}",
                f"- **Team**: {team_str or 'N/A'}",
                f"- **Badges**: {len(context.get('badges', [])) if context else 0}",
                f"- **Currency**: ${context.get('currency', 0) if context else 0}",
                f"- **Total Pokemon**: {context.get('total_pokemon', 0) if context else 0}",
                f"",
                f"## Description",
                f"",
                description,
                f"",
            ]
            (bugs_dir / filename).write_text("\n".join(lines), encoding="utf-8")
            await player.send({"type": "bug_report_submitted", "message": "Bug report submitted! Thanks for helping improve the game."})
            print(f"[bug] Report from {player.name}: {description[:80]} -> bugs/{filename}")
        except Exception as e:
            print(f"[bug] Error saving report: {e}")
            await player.send({"type": "error", "message": "Failed to save bug report."})

    else:
        await player.send({"type": "error", "message": f"Unknown message type: {msg_type}"})


# ─── Item Helpers ─────────────────────────────────────

def _item_description(key, item):
    """Generate a short description for a shop item."""
    if item.get("category") == "ball":
        mod = item.get("ball_modifier", 1.0)
        if mod == 1.0:
            return "Standard Poké Ball"
        return f"{mod}x catch rate"
    if key == "potion":
        return "Restores 20 HP"
    if key == "super_potion":
        return "Restores 50 HP"
    if key == "hyper_potion":
        return "Restores 200 HP"
    if key == "revive":
        return "Revives fainted to 50% HP"
    if key == "full_restore":
        return "Full HP + cure status"
    if key == "lucky-egg":
        return "2x XP from all battles!"
    if item.get("category") == "rare_candy":
        levels = item.get("levels", 1)
        return f"+{levels} level{'s' if levels > 1 else ''}"
    return ""


async def _handle_use_item(player, data):
    """Handle using a healing item on a Pokemon."""
    item_type = data.get("item_type", "")
    pokemon_index = data.get("pokemon_index", -1)
    context = data.get("context", "team")  # "team", "wild", or "gym"

    if item_type not in SHOP_ITEMS:
        await player.send({"type": "error", "message": "Invalid item."})
        return

    item = SHOP_ITEMS[item_type]
    if item.get("category") != "healing":
        await player.send({"type": "error", "message": "Can't use that item here."})
        return

    # Check the player actually has this item
    if not account_mgr.use_item(player.account_id, item_type):
        await player.send({"type": "error", "message": f"No {item['name']} left!"})
        return

    # ─── Using item from My Team screen (heal between battles) ─────
    if context == "team":
        team_data = account_mgr.get_team(player.account_id)
        if pokemon_index < 0 or pokemon_index >= len(team_data):
            # Refund — invalid target
            account_mgr.add_item(player.account_id, item_type, 1)
            await player.send({"type": "error", "message": "Invalid Pokémon."})
            return

        # Team screen items work on DB-stored team, so just send confirmation
        # The actual HP restoration happens when the team is next loaded into battle
        # We'll track it in a lightweight way: send success + updated inventory
        inventory = account_mgr.get_inventory(player.account_id)
        await player.send({
            "type": "item_used",
            "item_type": item_type,
            "item_name": item["name"],
            "pokemon_index": pokemon_index,
            "context": "team",
            "inventory": inventory,
            "message": f"Used {item['name']}!",
        })
        return

    # ─── Using item during wild/gym encounter ─────
    encounter = active_encounters.get(player.id)
    if not encounter:
        # Refund — no encounter
        account_mgr.add_item(player.account_id, item_type, 1)
        await player.send({"type": "error", "message": "No active battle."})
        return

    is_gym = getattr(encounter, 'is_gym', False)

    # Determine target Pokemon
    if item.get("revive"):
        # Revive targets a fainted team member
        if pokemon_index < 0 or pokemon_index >= len(encounter.team):
            account_mgr.add_item(player.account_id, item_type, 1)
            await player.send({"type": "error", "message": "Invalid Pokémon."})
            return
        target = encounter.team[pokemon_index]
        if not target.is_fainted:
            account_mgr.add_item(player.account_id, item_type, 1)
            await player.send({"type": "error", "message": f"{target.name} isn't fainted!"})
            return
        # Revive to 50% HP
        target.is_fainted = False
        target.current_hp = max(1, int(target.max_hp * item["heal_pct"]))
    else:
        # Healing item targets active Pokemon (or a specified team member)
        if pokemon_index >= 0 and pokemon_index < len(encounter.team):
            target = encounter.team[pokemon_index]
        else:
            target = encounter.get_active()

        if target.is_fainted:
            account_mgr.add_item(player.account_id, item_type, 1)
            await player.send({"type": "error", "message": f"{target.name} has fainted! Use a Revive."})
            return

        hp_full = target.current_hp >= target.max_hp
        has_status = target.status is not None
        can_cure = item.get("cure_status") and has_status
        if hp_full and not can_cure:
            account_mgr.add_item(player.account_id, item_type, 1)
            await player.send({"type": "error", "message": f"{target.name} is already at full HP!"})
            return

        old_hp = target.current_hp
        if item.get("heal_full"):
            target.current_hp = target.max_hp
        elif item.get("heal_hp"):
            target.current_hp = min(target.max_hp, target.current_hp + item["heal_hp"])

        if item.get("cure_status") and target.status:
            target.status = None

    # Build result events
    events = [{
        "type": "item_use",
        "item_name": item["name"],
        "pokemon": target.name,
        "hp": target.current_hp,
        "max_hp": target.max_hp,
    }]

    # Wild Pokemon attacks after item use (costs a turn)
    wild_events = _wild_attacks(encounter)
    events += wild_events

    inventory = account_mgr.get_inventory(player.account_id)

    if encounter.all_fainted():
        del active_encounters[player.id]
        if is_gym:
            gym = encounter.gym
            await player.send({
                "type": "gym_defeat",
                "events": events,
                "gym_name": gym["name"],
                "dialog_lose": gym["dialog_lose"],
            })
        else:
            await player.send({"type": "wild_blackout", "events": events})
        return

    # Check if active fainted from wild attack after item use
    active = encounter.get_active()
    if active.is_fainted:
        alive = encounter.alive_indices()
        if not alive:
            del active_encounters[player.id]
            if is_gym:
                gym = encounter.gym
                await player.send({
                    "type": "gym_defeat",
                    "events": events,
                    "gym_name": gym["name"],
                    "dialog_lose": gym["dialog_lose"],
                })
            else:
                await player.send({"type": "wild_blackout", "events": events})
            return
        await player.send({
            "type": "wild_force_switch",
            "events": events,
            "available": alive,
            **encounter.serialize_state(),
            "inventory": inventory,
        })
        return

    await player.send({
        "type": "wild_turn_result",
        "events": events,
        **encounter.serialize_state(),
        "inventory": inventory,
    })


# ─── Rare Candy Handler ───────────────────────────────

async def _handle_use_rare_candy(player, data):
    """Handle using a Rare Candy item on a Pokemon from My Team screen.
    Supports both tiered shop candy (rare_candy, rare_candy_xl, etc.) and
    battle-drop rare_candy (plain +1 level)."""
    item_type = data.get("item_type", "rare_candy")
    pokemon_row_id = data.get("pokemon_id")

    # Validate item type — must be in SHOP_ITEMS (tiered) or plain rare_candy (battle drop)
    if item_type in SHOP_ITEMS:
        item = SHOP_ITEMS[item_type]
        if item.get("category") != "rare_candy":
            await player.send({"type": "error", "message": "Not a Rare Candy."})
            return
        levels_to_gain = item.get("levels", 1)
        item_name = item["name"]
    elif item_type == "rare_candy":
        # Battle-drop rare candy (+1 level, not in SHOP_ITEMS)
        levels_to_gain = 1
        item_name = "Rare Candy"
    else:
        await player.send({"type": "error", "message": "Invalid item."})
        return

    if not pokemon_row_id:
        await player.send({"type": "error", "message": "Missing pokemon_id."})
        return

    # Check inventory
    if not account_mgr.use_item(player.account_id, item_type):
        await player.send({"type": "error", "message": f"No {item_name} left!"})
        return

    # Get the Pokemon from DB
    all_pokemon = account_mgr.get_all_pokemon(player.account_id)
    poke_row = next((p for p in all_pokemon if p["id"] == pokemon_row_id), None)
    if not poke_row:
        # Refund
        account_mgr.add_item(player.account_id, item_type, 1)
        await player.send({"type": "error", "message": "Pokemon not found."})
        return

    old_level = poke_row["level"]
    current_xp = poke_row["xp"]
    dex_id = poke_row["dex_id"]

    if old_level >= 100:
        # Refund
        account_mgr.add_item(player.account_id, item_type, 1)
        await player.send({"type": "error", "message": "Already at max level!"})
        return

    # Calculate target level (capped at 100)
    target_level = min(100, old_level + levels_to_gain)

    # Calculate XP needed to reach target level
    target_xp = xp_for_level(target_level)
    xp_to_add = max(0, target_xp - current_xp)

    # Award the XP (this updates DB)
    result = account_mgr.award_xp(pokemon_row_id, xp_to_add)
    if not result:
        account_mgr.add_item(player.account_id, item_type, 1)
        await player.send({"type": "error", "message": "Failed to award XP."})
        return

    # Process level-by-level: moves and evolution at each level
    if result["leveled_up"]:
        new_moves_all = pokemon_data.get_new_moves_for_level(
            dex_id, old_level, result["new_level"]
        )
        result["new_moves"] = [
            {"level": m["level"], "move_id": m["move"],
             "move_data": pokemon_data.MOVES.get(m["move"], {})}
            for m in new_moves_all if m["move"] in pokemon_data.MOVES
        ]

        # Auto-learn moves if fewer than 4, otherwise queue for player
        if result["new_moves"]:
            # Re-fetch to get latest moves
            all_pokemon = account_mgr.get_all_pokemon(player.account_id)
            poke_row = next((p for p in all_pokemon if p["id"] == pokemon_row_id), None)
            current_moves = _get_current_moves(poke_row) if poke_row else []

            auto_learned = []
            pending_learn = []
            for nm in result["new_moves"]:
                mid = nm["move_id"]
                if mid in current_moves:
                    continue
                if len(current_moves) < 4:
                    current_moves.append(mid)
                    auto_learned.append(nm)
                else:
                    pending_learn.append(nm)

            if auto_learned:
                account_mgr.update_pokemon_moves(pokemon_row_id, current_moves)

            result["auto_learned"] = auto_learned
            result["pending_learn"] = pending_learn

        # Check evolution at each level gained (handle chain evolutions)
        current_dex = dex_id
        evolutions = []
        for lvl in range(old_level + 1, result["new_level"] + 1):
            evo = pokemon_data.get_evolution(current_dex)
            if evo and evo.get("method") == "level" and lvl >= evo.get("level", 999):
                new_dex = evo["evolves_to"]
                evolutions.append({
                    "from_dex_id": current_dex,
                    "to_dex_id": new_dex,
                    "to_name": pokemon_data.POKEMON.get(new_dex, {}).get("name", "???"),
                    "at_level": lvl,
                })
                current_dex = new_dex

        # Apply all evolutions (final species)
        if evolutions:
            final_dex = evolutions[-1]["to_dex_id"]
            account_mgr.update_pokemon_species(pokemon_row_id, final_dex)
            result["evolution"] = evolutions[-1]  # Primary evolution for overlay
            result["all_evolutions"] = evolutions  # Full chain
            # Pokedex: mark each evolved form as caught
            for evo_step in evolutions:
                account_mgr.mark_caught(player.account_id, evo_step["to_dex_id"])

    inventory = account_mgr.get_inventory(player.account_id)
    await player.send({
        "type": "rare_candy_result",
        "success": True,
        "item_type": item_type,
        "item_name": item_name,
        "levels_gained": result["new_level"] - old_level,
        "xp_result": result,
        "inventory": inventory,
    })


# ─── Wild Encounter Action Handler ────────────────────

async def _handle_wild_action(player, encounter, data):
    """Process a wild encounter action (move, catch, switch, run)."""
    action = data.get("action_type", "")
    is_gym = getattr(encounter, 'is_gym', False)
    is_training = getattr(encounter, 'is_training', False)

    if action == "run":
        if is_gym:
            await player.send({"type": "error", "message": "Can't run from a gym battle!"})
            return
        del active_encounters[player.id]
        await player.send({"type": "wild_fled"})
        return

    if action == "ball":
        if is_gym:
            await player.send({"type": "error", "message": "Can't catch a trainer's Pokémon!"})
            return
        if is_training:
            await player.send({"type": "error", "message": "Can't catch Pokémon in training mode!"})
            return
        ball_type = data.get("ball_type", "pokeball")
        if not account_mgr.use_pokeball(player.account_id):
            await player.send({"type": "error", "message": "No Poké Balls left!"})
            return
        wild = encounter.wild
        catch_rate = wild.species.get("catch_rate", 45)
        caught, shakes = attempt_catch(catch_rate, wild.current_hp, wild.max_hp, ball_type)
        await player.send({
            "type": "wild_catch_attempt",
            "shakes": shakes, "caught": caught, "ball_type": ball_type,
        })
        if caught:
            # Add to collection
            moves_for_db = pokemon_data.get_initial_moves(wild.dex_id, wild.level)
            added = account_mgr.catch_pokemon(player.account_id, wild.dex_id, wild.level, default_moves=moves_for_db, is_shiny=getattr(wild, 'is_shiny', False))
            # Pokedex: mark as caught
            account_mgr.mark_caught(player.account_id, wild.dex_id)
            # Award currency
            account_mgr.add_currency(player.account_id, CURRENCY_WILD_CATCH)
            # Award XP to active Pokemon
            xp_results = _award_encounter_xp(encounter, wild)
            # Rare Candy drop
            rare_candy = _award_rare_candy(player, "wild")
            del active_encounters[player.id]
            await player.send({
                "type": "wild_caught",
                "pokemon": {"dex_id": wild.dex_id, "name": wild.name, "level": wild.level, "is_shiny": getattr(wild, 'is_shiny', False)},
                "added_to_team": added,
                "currency_gained": CURRENCY_WILD_CATCH,
                "rare_candy_gained": rare_candy,
                "xp_results": xp_results,
            })
        else:
            # Wild Pokemon attacks back after failed catch
            events = _wild_attacks(encounter)
            if encounter.all_fainted():
                del active_encounters[player.id]
                await player.send({"type": "wild_blackout", "events": events})
            else:
                await player.send({
                    "type": "wild_turn_result",
                    "events": events,
                    **encounter.serialize_state(),
                })
        return

    if action == "switch":
        idx = data.get("pokemon_index", -1)
        alive = encounter.alive_indices()
        if idx not in alive or idx == encounter.active_idx:
            await player.send({"type": "error", "message": "Invalid switch."})
            return
        encounter.active_idx = idx
        # Wild Pokemon attacks
        events = _wild_attacks(encounter)
        if encounter.all_fainted():
            del active_encounters[player.id]
            await player.send({"type": "wild_blackout", "events": events})
        else:
            await player.send({
                "type": "wild_turn_result",
                "events": [{"type": "switch", "pokemon": encounter.get_active().name}] + events,
                **encounter.serialize_state(),
            })
        return

    if action == "mega_evolve":
        my_poke = encounter.get_active()
        if getattr(encounter, '_mega_used', False):
            await player.send({"type": "error", "message": "Already used Mega Evolution this battle!"})
            return
        if getattr(encounter, '_dynamax_used', False):
            await player.send({"type": "error", "message": "Can't Mega Evolve after Dynamaxing!"})
            return
        mega_stone_id = data.get("mega_stone")
        inventory = account_mgr.get_inventory(player.account_id) if hasattr(account_mgr, 'get_inventory') else {}
        if not inventory.get(mega_stone_id, 0):
            await player.send({"type": "error", "message": "You don't have that Mega Stone!"})
            return
        mega_data_all = pokemon_data.MEGA_EVOLUTIONS.get(str(my_poke.dex_id))
        if not mega_data_all:
            await player.send({"type": "error", "message": "This Pokemon can't Mega Evolve!"})
            return
        # Find the right mega form for this stone
        if isinstance(mega_data_all, list):
            mega_data = next((m for m in mega_data_all if m["mega_stone"] == mega_stone_id), None)
        else:
            mega_data = mega_data_all if mega_data_all["mega_stone"] == mega_stone_id else None
        if not mega_data:
            await player.send({"type": "error", "message": "Wrong Mega Stone for this Pokemon!"})
            return
        my_poke.mega_evolve(mega_data)
        encounter._mega_used = True
        await player.send({
            "type": "mega_evolved",
            "pokemon_name": my_poke.name,
            "new_types": my_poke.types,
            **encounter.serialize_state(),
        })
        return

    if action == "dynamax":
        my_poke = encounter.get_active()
        if getattr(encounter, '_dynamax_used', False):
            await player.send({"type": "error", "message": "Already used Dynamax this battle!"})
            return
        if getattr(encounter, '_mega_used', False) or my_poke.is_mega:
            await player.send({"type": "error", "message": "Can't Dynamax after Mega Evolution!"})
            return
        if getattr(encounter, '_zmove_used', False):
            await player.send({"type": "error", "message": "Can't Dynamax after using a Z-Move!"})
            return
        if my_poke.is_fainted:
            await player.send({"type": "error", "message": "Can't Dynamax a fainted Pokemon!"})
            return
        my_poke.dynamax()
        encounter._dynamax_used = True
        gmax = pokemon_data.get_gmax_data(my_poke.dex_id)
        await player.send({
            "type": "dynamaxed",
            "pokemon_name": (gmax["name"] if gmax else my_poke.name) + " Dynamaxed!",
            "is_gigantamax": gmax is not None,
            "gmax_data": gmax,
            **encounter.serialize_state(),
        })
        return

    if action == "move":
        move_index = data.get("move_index", 0)
        use_zmove = data.get("z_move", False)
        my_poke = encounter.get_active()
        wild = encounter.wild

        # Get player's move
        if my_poke.has_usable_moves():
            if 0 <= move_index < len(my_poke.moves) and my_poke.moves[move_index]["current_pp"] > 0:
                player_move = my_poke.moves[move_index]
            else:
                player_move = next((m for m in my_poke.moves if m["current_pp"] > 0), STRUGGLE)
        else:
            player_move = STRUGGLE

        # Dynamax: convert to Max Moves
        if my_poke.is_dynamaxed and player_move.get("id") != "struggle":
            player_move = dict(player_move)  # Copy to avoid mutating
            if player_move["power"] > 0:
                gmax = pokemon_data.get_gmax_data(my_poke.dex_id)
                if gmax and player_move["type"] == gmax["gmax_type"]:
                    player_move["name"] = gmax["gmax_move"]
                else:
                    player_move["name"] = pokemon_data.get_max_move_name(player_move["type"])
                player_move["power"] = pokemon_data.get_max_move_power(player_move["power"])
                player_move["accuracy"] = 100  # Max Moves never miss
            else:
                player_move["name"] = "Max Guard"

        # Z-Move: boost power for this turn, mark used
        z_move_name = None
        if use_zmove and not getattr(encounter, '_zmove_used', False):
            z_crystal_type = player_move.get("type")
            z_data = pokemon_data.ZMOVES.get(z_crystal_type)
            if z_data and player_move["power"] > 0:
                # Check player has the right Z-Crystal
                z_key = f"z-{z_crystal_type}"
                inventory = account_mgr.get_inventory(player.account_id) if hasattr(account_mgr, 'get_inventory') else {}
                if inventory.get(z_key, 0) > 0:
                    player_move = dict(player_move)  # Copy to avoid mutating
                    player_move["power"] = int(player_move["power"] * z_data["power_mult"])
                    player_move["accuracy"] = 100  # Z-Moves never miss
                    z_move_name = z_data["name"]
                    encounter._zmove_used = True

        # Wild Pokemon picks a random move
        if wild.has_usable_moves():
            wild_usable = [m for m in wild.moves if m["current_pp"] > 0]
            wild_move = random.choice(wild_usable)
        else:
            wild_move = STRUGGLE

        # Dodge: player dodges wild's attack, wild doesn't dodge player's attack
        player_dodged = data.get("dodged", False)
        player_dodge_mult = 0.8 if player_dodged else 1.0
        wild_dodge_mult = 1.0  # Wild Pokemon don't dodge

        events = []

        # Speed determines who goes first
        player_speed = my_poke.get_effective_speed()
        wild_speed = wild.get_effective_speed()
        player_first = player_speed > wild_speed or (player_speed == wild_speed and random.random() < 0.5)

        if player_first:
            events += _resolve_single_move(my_poke, wild, player_move, wild_dodge_mult, "player")
            if not wild.is_fainted:
                events += _resolve_single_move(wild, my_poke, wild_move, player_dodge_mult, "wild")
        else:
            events += _resolve_single_move(wild, my_poke, wild_move, player_dodge_mult, "wild")
            if not my_poke.is_fainted:
                events += _resolve_single_move(my_poke, wild, player_move, wild_dodge_mult, "player")

        encounter.turn_count += 1

        # Tick Dynamax for player's active Pokemon
        if my_poke.is_dynamaxed and not my_poke.is_fainted:
            if my_poke.tick_dynamax():
                events.append({"type": "dynamax_end", "pokemon": my_poke.name, "side": "player"})

        # Check outcomes
        if wild.is_fainted:
            # Catch window: first time wild would faint in a wild encounter,
            # hold it at 1 HP and prompt the player to throw a ball
            if not is_gym and not is_training and not encounter.catch_window:
                encounter.catch_window = True
                wild.current_hp = 1
                wild.is_fainted = False
                events.append({
                    "type": "catch_window",
                    "pokemon": wild.name,
                })
                await player.send({
                    "type": "wild_catch_window",
                    "events": events,
                    **encounter.serialize_state(),
                })
                return

            xp_results = _award_encounter_xp(encounter, wild)
            if not is_training:
                account_mgr.add_currency(player.account_id, CURRENCY_WILD_WIN)

            if is_gym:
                # Check if more trainer Pokemon remain
                gym_team = encounter.gym_team
                encounter.gym_active += 1
                if encounter.gym_active < len(gym_team):
                    # Next trainer Pokemon
                    encounter.wild = gym_team[encounter.gym_active]
                    events.append({"type": "gym_next_pokemon", "pokemon": encounter.wild.name,
                                   "remaining": len(gym_team) - encounter.gym_active})
                    await player.send({
                        "type": "wild_turn_result",
                        "events": events,
                        **encounter.serialize_state(),
                        "xp_results": xp_results,
                    })
                    return
                else:
                    # Trainer victory! Handle by category
                    trainer = encounter.gym
                    category = getattr(encounter, 'trainer_category', 'gym')
                    reward = trainer.get("reward_currency", CURRENCY_GYM_WIN)
                    account_mgr.add_currency(player.account_id, reward - CURRENCY_WILD_WIN)

                    battle_region = getattr(encounter, 'battle_region', 'kanto')
                    milestone_prefix = f"{battle_region}:" if battle_region != "kanto" else ""

                    if category == "e4":
                        account_mgr.record_milestone(player.account_id, f"{milestone_prefix}{trainer['id']}_defeated")
                        rare_candy = _award_rare_candy(player, "e4")
                        await player.send({
                            "type": "trainer_victory",
                            "events": events,
                            "trainer_name": trainer["name"],
                            "dialog_win": trainer["dialog_win"],
                            "currency_gained": reward,
                            "rare_candy_gained": rare_candy,
                            "category": "e4",
                            "xp_results": xp_results,
                        })
                    elif category == "champion":
                        account_mgr.record_milestone(player.account_id, f"{milestone_prefix}champion_defeated")
                        rare_candy = _award_rare_candy(player, "champion")
                        await player.send({
                            "type": "trainer_victory",
                            "events": events,
                            "trainer_name": trainer["name"],
                            "dialog_win": trainer["dialog_win"],
                            "currency_gained": reward,
                            "rare_candy_gained": rare_candy,
                            "category": "champion",
                            "xp_results": xp_results,
                        })
                    elif category == "masters":
                        account_mgr.record_milestone(player.account_id, f"{trainer['id']}_defeated")
                        # Check if all Masters beaten
                        milestones = account_mgr.get_milestones(player.account_id)
                        all_beaten = all(f"{m['id']}_defeated" in milestones for m in MASTERS_EIGHT)
                        rare_candy = _award_rare_candy(player, "masters")
                        await player.send({
                            "type": "trainer_victory",
                            "events": events,
                            "trainer_name": trainer["name"],
                            "dialog_win": trainer["dialog_win"],
                            "currency_gained": reward,
                            "rare_candy_gained": rare_candy,
                            "category": "masters",
                            "all_masters_beaten": all_beaten,
                            "xp_results": xp_results,
                        })
                    elif category == "tournament":
                        # Tournament round victory
                        ts = active_tournaments.get(player.account_id)
                        round_num = ts.current_round if ts else 0
                        rare_candy = _award_rare_candy(player, "tournament")
                        is_champion = (ts and round_num == 3)
                        if ts:
                            ts.results.append("win")
                            ts.current_round += 1
                            if ts.is_complete:
                                # Tournament champion!
                                account_mgr.record_milestone(player.account_id, "tournament_champion")
                                # First-time bonus: 5 Rare Candy XL
                                milestones = account_mgr.get_milestones(player.account_id)
                        await player.send({
                            "type": "trainer_victory",
                            "events": events,
                            "trainer_name": trainer["name"],
                            "dialog_win": trainer["dialog_win"],
                            "currency_gained": reward,
                            "rare_candy_gained": rare_candy,
                            "category": "tournament",
                            "tournament_round": round_num,
                            "tournament_round_name": TOURNAMENT_ROUND_NAMES[min(round_num, 3)],
                            "tournament_complete": is_champion,
                            "tournament_state": ts.serialize() if ts else None,
                            "xp_results": xp_results,
                        })
                    else:
                        # Regular gym — use region-specific badge tracking
                        badge_ok = account_mgr.earn_badge(player.account_id, trainer["id"], region=battle_region)
                        if not badge_ok:
                            print(f"[WARN] Badge not awarded for player {player.account_id}, gym {trainer['id']}, region {battle_region}")
                        rare_candy = _award_rare_candy(player, "gym")
                        await player.send({
                            "type": "gym_victory",
                            "events": events,
                            "gym_name": trainer["name"],
                            "badge": trainer.get("badge", ""),
                            "dialog_win": trainer.get("dialog_win", ""),
                            "currency_gained": reward,
                            "rare_candy_gained": rare_candy,
                            "xp_results": xp_results,
                        })
                    del active_encounters[player.id]
                    return

            # Training battles: no currency, no rare candy
            if is_training:
                del active_encounters[player.id]
                await player.send({
                    "type": "wild_fainted",
                    "events": events,
                    "currency_gained": 0,
                    "rare_candy_gained": 0,
                    "is_training": True,
                    "xp_results": xp_results,
                })
                return

            # Rare Candy drop for wild encounter win
            rare_candy = _award_rare_candy(player, "wild")
            del active_encounters[player.id]
            await player.send({
                "type": "wild_fainted",
                "events": events,
                "currency_gained": CURRENCY_WILD_WIN,
                "rare_candy_gained": rare_candy,
                "xp_results": xp_results,
            })
            return

        if my_poke.is_fainted:
            alive = encounter.alive_indices()
            if not alive:
                del active_encounters[player.id]
                if is_gym:
                    trainer = encounter.gym
                    category = getattr(encounter, 'trainer_category', 'gym')
                    if category == "tournament":
                        # Tournament loss — eliminate player
                        ts = active_tournaments.get(player.account_id)
                        round_num = ts.current_round if ts else 0
                        if ts:
                            ts.results.append("loss")
                        await player.send({
                            "type": "trainer_defeat",
                            "events": events,
                            "trainer_name": trainer["name"],
                            "dialog_lose": trainer.get("dialog_lose", ""),
                            "category": "tournament",
                            "tournament_round": round_num,
                            "tournament_round_name": TOURNAMENT_ROUND_NAMES[min(round_num, 3)],
                        })
                        # Clean up tournament state
                        if ts and player.account_id in active_tournaments:
                            del active_tournaments[player.account_id]
                    elif category in ("e4", "champion", "masters"):
                        await player.send({
                            "type": "trainer_defeat",
                            "events": events,
                            "trainer_name": trainer["name"],
                            "dialog_lose": trainer.get("dialog_lose", ""),
                            "category": category,
                        })
                    else:
                        await player.send({
                            "type": "gym_defeat",
                            "events": events,
                            "gym_name": trainer["name"],
                            "dialog_lose": trainer.get("dialog_lose", ""),
                        })
                else:
                    await player.send({"type": "wild_blackout", "events": events})
                return
            # Need to switch
            await player.send({
                "type": "wild_force_switch",
                "events": events,
                "available": alive,
                **encounter.serialize_state(),
            })
            return

        await player.send({
            "type": "wild_turn_result",
            "events": events,
            **encounter.serialize_state(),
        })
        return

    await player.send({"type": "error", "message": f"Unknown wild action: {action}"})


def _resolve_single_move(attacker, defender, move, dodge_mult, side):
    """Resolve one side's move in a wild battle. Returns events list.

    dodge_mult: 1.0 = no dodge (full damage), 0.8 = dodged (20% reduction).
    side: "player" or "wild" — indicates who the attacker is.
    Events on the defender get the opposite side (defender_side).
    """
    defender_side = "wild" if side == "player" else "player"
    events = []
    events.append({"type": "move_use", "side": side, "pokemon": attacker.name, "move": move["name"],
                   "move_type": move.get("type", "normal"), "is_damage_move": move.get("power", 0) > 0,
                   "dodged": dodge_mult < 1.0})

    power = move.get("power", 0)
    if power == 0:
        # Status move — simplified handling
        effect = move.get("effect")
        if effect and "status" in str(effect) and defender.status is None:
            status_map = {
                "sleep": ["hypnosis", "sing", "sleep-powder", "spore", "lovely-kiss"],
                "paralyze": ["thunder-wave", "stun-spore", "glare"],
                "poison": ["poison-powder", "poison-gas", "toxic"],
            }
            for status, move_ids in status_map.items():
                if move["id"] in move_ids:
                    accuracy = move.get("accuracy", 100)
                    if random.randint(1, 100) <= accuracy:
                        defender.status = status
                        events.append({"type": "status_apply", "side": defender_side, "pokemon": defender.name, "status": status})
                    else:
                        events.append({"type": "miss", "side": side, "pokemon": attacker.name})
                    break
        return events

    # Damage move
    accuracy = move.get("accuracy", 100)
    if random.randint(1, 100) > accuracy:
        events.append({"type": "miss", "side": side, "pokemon": attacker.name})
        return events

    # Dodge multiplier: 1.0 = full damage, 0.8 = dodged
    damage, effectiveness, is_crit = calculate_damage(attacker, defender, move, dodge_mult)

    if is_crit:
        events.append({"type": "critical_hit"})
    if effectiveness > 1.0:
        events.append({"type": "effectiveness", "value": "super_effective"})
    elif effectiveness < 1.0 and effectiveness > 0:
        events.append({"type": "effectiveness", "value": "not_very_effective"})
    elif effectiveness == 0:
        events.append({"type": "effectiveness", "value": "no_effect"})
        return events

    defender.current_hp = max(0, defender.current_hp - damage)
    events.append({"type": "damage", "side": defender_side, "pokemon": defender.name, "damage": damage,
                   "hp": defender.current_hp, "max_hp": defender.max_hp})

    if defender.current_hp <= 0:
        defender.is_fainted = True
        events.append({"type": "faint", "side": defender_side, "pokemon": defender.name})

    # Deduct PP
    if move.get("id") != "struggle":
        move["current_pp"] = max(0, move["current_pp"] - 1)

    return events


def _wild_attacks(encounter):
    """Wild Pokemon takes a turn attacking the player's active Pokemon."""
    wild = encounter.wild
    target = encounter.get_active()
    if wild.is_fainted or target.is_fainted:
        return []

    if wild.has_usable_moves():
        wild_usable = [m for m in wild.moves if m["current_pp"] > 0]
        wild_move = random.choice(wild_usable)
    else:
        wild_move = STRUGGLE

    dodge_mult = 1.0  # No dodge for wild attacks outside battle turns
    return _resolve_single_move(wild, target, wild_move, dodge_mult, "wild")


def _award_rare_candy(player, battle_type, tournament_round=None):
    """Award Rare Candy based on battle type. Returns quantity awarded (0 if none).
    Drop rates: wild=10%, gym=100% x1, e4=100% x2, champion/masters=100% x3,
    tournament=round+2 (2,3,4,5)."""
    if battle_type == "wild":
        if random.random() < 0.10:
            qty = 1
        else:
            return 0
    elif battle_type == "gym":
        qty = 1
    elif battle_type == "e4":
        qty = 2
    elif battle_type in ("champion", "masters"):
        qty = 3
    elif battle_type == "tournament":
        # Determine round from active tournament state
        ts = active_tournaments.get(player.account_id)
        rnd = ts.current_round if ts else (tournament_round or 0)
        qty = RARE_CANDY_TOURNAMENT[min(rnd, 3)]
    else:
        return 0
    account_mgr.add_item(player.account_id, "rare_candy", qty)
    return qty


def _award_encounter_xp(encounter, defeated):
    """Award XP to all alive team Pokemon (EXP Share).
    Active Pokemon gets 100% XP, alive bench Pokemon get 50%.
    Lucky Egg doubles all XP if the player owns one."""
    results = []
    active = encounter.get_active()

    base_exp = defeated.species.get("base_experience", 64)
    is_gym = getattr(encounter, 'is_gym', False)
    full_xp = calc_xp_yield(defeated.level, base_exp, is_wild=not is_gym)

    # Lucky Egg: 2x XP if player owns one (passive held item, not consumed)
    has_lucky_egg = False
    player_id = getattr(encounter.player, 'account_id', None)
    if player_id:
        inv = account_mgr.get_inventory(player_id)
        has_lucky_egg = inv.get("lucky-egg", 0) > 0
    if has_lucky_egg:
        full_xp *= 2

    # Training battles give 75% XP
    if getattr(encounter, 'is_training', False):
        full_xp = int(full_xp * 0.75)

    for poke in encounter.team:
        if poke.is_fainted:
            continue
        db_id = getattr(poke, 'db_id', None)
        if not db_id:
            continue

        # Active gets full XP, bench gets half
        xp = full_xp if (active and poke is active) else max(1, full_xp // 2)

        result = account_mgr.award_xp(db_id, xp)
        if not result:
            continue

        if result["leveled_up"]:
            new_moves = pokemon_data.get_new_moves_for_level(
                result["dex_id"], result["old_level"], result["new_level"]
            )
            result["new_moves"] = [
                {"level": m["level"], "move_id": m["move"],
                 "move_data": pokemon_data.MOVES.get(m["move"], {})}
                for m in new_moves if m["move"] in pokemon_data.MOVES
            ]

            # Auto-learn moves if fewer than 4, otherwise prompt player
            if result["new_moves"]:
                all_pokemon = account_mgr.get_all_pokemon(encounter.player.account_id)
                poke_row = next((p for p in all_pokemon if p["id"] == db_id), None)
                current_moves = _get_current_moves(poke_row) if poke_row else []

                auto_learned = []
                pending_learn = []
                for nm in result["new_moves"]:
                    mid = nm["move_id"]
                    if mid in current_moves:
                        continue
                    if len(current_moves) < 4:
                        current_moves.append(mid)
                        auto_learned.append(nm)
                    else:
                        pending_learn.append(nm)

                if auto_learned:
                    account_mgr.update_pokemon_moves(db_id, current_moves)

                result["auto_learned"] = auto_learned
                result["pending_learn"] = pending_learn

            # Check evolution
            evo = pokemon_data.get_evolution(result["dex_id"])
            if evo and evo.get("method") == "level" and result["new_level"] >= evo.get("level", 999):
                result["evolution"] = {
                    "from_dex_id": result["dex_id"],
                    "to_dex_id": evo["evolves_to"],
                    "to_name": pokemon_data.POKEMON.get(evo["evolves_to"], {}).get("name", "???"),
                }
                account_mgr.update_pokemon_species(db_id, evo["evolves_to"])
                # Pokedex: mark evolved form as caught
                if player_id:
                    account_mgr.mark_caught(player_id, evo["evolves_to"])

        result["lucky_egg_active"] = has_lucky_egg
        results.append(result)

    return results


# ─── WebSocket Connection Handler ──────────────────────

async def handler(websocket):
    """Handle a WebSocket connection."""
    player = Player(websocket)
    print(f"[+] Player connected: {player.id}")

    try:
        async for message in websocket:
            await handle_message(player, message, room_manager)
    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        print(f"[!] Error handling player {player.id}: {e}")
    finally:
        print(f"[-] Player disconnected: {player.id}")
        await room_manager.remove_player(player)
        # Clean up trade room if player was in one
        trade_code = player_trade_rooms.get(player.id)
        if trade_code:
            room = trade_rooms.get(trade_code)
            if room:
                opp = room.get_opponent(player)
                if opp:
                    await opp.send({"type": "trade_cancelled", "message": f"{player.name} disconnected."})
            _cleanup_trade_room(trade_code)
        # Clean up tournament state on disconnect
        acct_id = getattr(player, 'account_id', None)
        if acct_id and acct_id in active_tournaments:
            del active_tournaments[acct_id]


# ─── Background Tasks ──────────────────────────────────

async def room_cleanup_task():
    """Periodically clean up old rooms."""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        removed = await room_manager.cleanup_old_rooms()
        if removed:
            print(f"[cleanup] Removed {removed} old room(s)")


# ─── Main ──────────────────────────────────────────────

async def main():
    # Load Pokemon data
    print("Loading Pokemon data...")
    pokemon_data.load_data()

    # Initialize database
    init_db()
    global account_mgr
    account_mgr = AccountManager(DB_PATH)
    print(f"Database initialized at {DB_PATH}")

    # Fix Pokemon with missing, invalid, or sparse moves
    account_mgr.fix_null_moves(pokemon_data)
    account_mgr.fix_invalid_moves(pokemon_data)
    account_mgr.fix_sparse_moves(pokemon_data)

    # Start cleanup task
    asyncio.create_task(room_cleanup_task())

    # Start server
    async with websockets.serve(
        handler,
        "0.0.0.0",
        PORT,
        process_request=process_request,
        max_size=1_000_000,  # 1MB max message
        ping_interval=30,
        ping_timeout=10,
    ) as server:
        print(f"PokeBattle server running on http://0.0.0.0:{PORT}")
        print(f"WebSocket endpoint: ws://0.0.0.0:{PORT}/ws")
        await asyncio.Future()  # Run forever


if __name__ == "__main__":
    asyncio.run(main())
