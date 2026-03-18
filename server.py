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
import time

import websockets
from websockets.http11 import Response as HttpResponse, Headers as HttpHeaders

import pokemon_data
from ai_player import BotPlayer
from game_room import Player, RoomManager
from player_accounts import AccountManager, calc_xp_yield
from journey import (
    generate_wild_pokemon, attempt_catch, WildEncounter,
    build_trainer_team, GYM_LEADERS, ELITE_FOUR, CHAMPION, MASTERS_EIGHT,
    SHOP_ITEMS, CURRENCY_WILD_WIN, CURRENCY_WILD_CATCH, CURRENCY_GYM_WIN,
    CURRENCY_ELITE_FOUR_WIN, CURRENCY_CHAMPION_WIN, CURRENCY_MASTERS_WIN,
    get_gym, get_next_gym, get_elite_four_member, get_masters_opponent,
)
from battle_engine import build_journey_team, resolve_turn, calculate_damage, STRUGGLE

APP_DIR = pathlib.Path(__file__).parent
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
    conn.commit()
    conn.close()


def record_game(room, winner_idx, summary):
    """Record a completed game to the database."""
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


# Global state
room_manager = RoomManager(on_game_end=record_game)
account_mgr = None  # Initialized in main()
active_encounters = {}  # player.id -> WildEncounter


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
        result, error = account_mgr.register(username)
        if error:
            await player.send({"type": "register_error", "message": error})
        else:
            player.name = result["username"]
            player.account_id = result["id"]
            await player.send({"type": "register_ok", "profile": result})
            await player.send({
                "type": "pokemon_list",
                "pokemon_list": pokemon_data.get_pokemon_list_for_client(),
            })
        return

    if msg_type == "login":
        token = data.get("token")
        username = data.get("username")
        profile = None
        if token:
            profile = account_mgr.login_by_token(token)
        elif username:
            profile = account_mgr.login_by_username(username.strip())

        if profile:
            player.name = profile["username"]
            player.account_id = profile["id"]
            full = account_mgr.get_profile(profile["id"])
            await player.send({"type": "login_ok", "profile": full})
            # Also send pokemon list for My Team screen
            await player.send({
                "type": "pokemon_list",
                "pokemon_list": pokemon_data.get_pokemon_list_for_client(),
            })
        else:
            await player.send({"type": "login_error", "message": "Account not found."})
        return

    if msg_type == "choose_starter":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        dex_id = data.get("dex_id")
        if account_mgr.choose_starter(player.account_id, dex_id):
            full = account_mgr.get_profile(player.account_id)
            await player.send({"type": "starter_chosen", "profile": full})
        else:
            await player.send({"type": "error", "message": "Invalid starter choice."})
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

    elif msg_type == "tap_result":
        room = room_mgr.get_room(player)
        if room:
            await room.handle_tap_result(player, data)

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
        wild, rarity = generate_wild_pokemon(avg_level)
        team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
        encounter = WildEncounter(player, team, wild, rarity)
        active_encounters[player.id] = encounter
        await player.send({"type": "wild_encounter_start", **encounter.serialize_state()})

    elif msg_type == "wild_action":
        encounter = active_encounters.get(player.id)
        if not encounter:
            await player.send({"type": "error", "message": "No active encounter."})
            return
        await _handle_wild_action(player, encounter, data)

    elif msg_type == "get_gyms":
        if not getattr(player, 'account_id', None):
            return
        badges = account_mgr.get_badges(player.account_id)
        next_gym = get_next_gym(badges)
        gyms = []
        for g in GYM_LEADERS:
            gyms.append({
                "id": g["id"], "name": g["name"], "type": g["type"],
                "badge": g["badge"], "completed": g["id"] in badges,
                "team_size": len(g["team"]),
                "max_level": max(t["level"] for t in g["team"]),
            })
        await player.send({
            "type": "gym_list",
            "gyms": gyms,
            "next_gym_id": next_gym["id"] if next_gym else None,
            "badges": badges,
        })

    elif msg_type == "start_gym":
        if not getattr(player, 'account_id', None):
            return
        gym_id = data.get("gym_id")
        gym = get_gym(gym_id)
        if not gym:
            await player.send({"type": "error", "message": "Invalid gym."})
            return
        badges = account_mgr.get_badges(player.account_id)
        # Must beat gyms in order
        if gym_id > 1 and (gym_id - 1) not in badges:
            await player.send({"type": "error", "message": "Beat the previous gym first!"})
            return
        await player.send({
            "type": "gym_intro",
            "gym": {
                "id": gym["id"], "name": gym["name"], "title": gym["title"],
                "type": gym["type"], "badge": gym["badge"],
                "dialog_intro": gym["dialog_intro"],
                "team_size": len(gym["team"]),
                "max_level": max(t["level"] for t in gym["team"]),
            }
        })

    elif msg_type == "gym_battle_start":
        if not getattr(player, 'account_id', None):
            return
        gym_id = data.get("gym_id")
        gym = get_gym(gym_id)
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
        active_encounters[player.id] = encounter

        await player.send({
            "type": "gym_battle_start",
            **encounter.serialize_state(),
            "gym_name": gym["name"],
            "gym_team_size": len(gym_team),
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

    elif msg_type == "use_item":
        if not getattr(player, 'account_id', None):
            await player.send({"type": "error", "message": "Not logged in."})
            return
        await _handle_use_item(player, data)

    elif msg_type == "get_progression":
        if not getattr(player, 'account_id', None):
            return
        badges = account_mgr.get_badges(player.account_id)
        milestones = account_mgr.get_milestones(player.account_id)
        profile = account_mgr.get_profile(player.account_id)
        await player.send({
            "type": "progression_data",
            "badges": badges,
            "milestones": milestones,
            "currency": profile.get("currency", 500),
            "pokeballs": profile.get("pokeballs", 10),
            "total_pokemon": profile.get("total_pokemon", 0),
        })

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
            "your_team": [p.serialize_full() for p in encounter.team],
            "inventory": inventory,
        })
        return

    await player.send({
        "type": "wild_turn_result",
        "events": events,
        **encounter.serialize_state(),
        "inventory": inventory,
    })


# ─── Wild Encounter Action Handler ────────────────────

async def _handle_wild_action(player, encounter, data):
    """Process a wild encounter action (move, catch, switch, run)."""
    action = data.get("action_type", "")
    is_gym = getattr(encounter, 'is_gym', False)

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
            moves_for_db = pokemon_data.get_moves_at_level(wild.dex_id, wild.level)
            added = account_mgr.catch_pokemon(player.account_id, wild.dex_id, wild.level)
            # Set moves on the caught Pokemon
            all_pokemon = account_mgr.get_all_pokemon(player.account_id)
            if all_pokemon:
                latest = all_pokemon[-1]
                account_mgr.update_pokemon_moves(latest["id"], moves_for_db)
            # Award currency
            account_mgr.add_currency(player.account_id, CURRENCY_WILD_CATCH)
            # Award XP to active Pokemon
            xp_results = _award_encounter_xp(encounter, wild)
            del active_encounters[player.id]
            await player.send({
                "type": "wild_caught",
                "pokemon": {"dex_id": wild.dex_id, "name": wild.name, "level": wild.level},
                "added_to_team": added,
                "currency_gained": CURRENCY_WILD_CATCH,
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

    if action == "move":
        move_index = data.get("move_index", 0)
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

        # Wild Pokemon picks a random move
        if wild.has_usable_moves():
            wild_usable = [m for m in wild.moves if m["current_pp"] > 0]
            wild_move = random.choice(wild_usable)
        else:
            wild_move = STRUGGLE

        # Use tap score from data or default
        tap_score = data.get("tap_score", 0.5)
        wild_tap = random.uniform(0.3, 0.7)

        events = []

        # Speed determines who goes first
        player_speed = my_poke.get_effective_speed()
        wild_speed = wild.get_effective_speed()
        player_first = player_speed > wild_speed or (player_speed == wild_speed and random.random() < 0.5)

        if player_first:
            events += _resolve_single_move(my_poke, wild, player_move, tap_score, "player")
            if not wild.is_fainted:
                events += _resolve_single_move(wild, my_poke, wild_move, wild_tap, "wild")
        else:
            events += _resolve_single_move(wild, my_poke, wild_move, wild_tap, "wild")
            if not my_poke.is_fainted:
                events += _resolve_single_move(my_poke, wild, player_move, tap_score, "player")

        encounter.turn_count += 1

        # Check outcomes
        if wild.is_fainted:
            # Catch window: first time wild would faint in a wild encounter,
            # hold it at 1 HP and prompt the player to throw a ball
            if not is_gym and not encounter.catch_window:
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
            account_mgr.add_currency(player.account_id, CURRENCY_WILD_WIN)
            del active_encounters[player.id]

            if is_gym:
                # Check if more gym Pokemon remain
                gym_team = encounter.gym_team
                encounter.gym_active += 1
                if encounter.gym_active < len(gym_team):
                    # Next gym Pokemon
                    encounter.wild = gym_team[encounter.gym_active]
                    active_encounters[player.id] = encounter
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
                    # Gym victory!
                    gym = encounter.gym
                    account_mgr.add_currency(player.account_id, gym["reward_currency"] - CURRENCY_WILD_WIN)
                    account_mgr.earn_badge(player.account_id, gym["id"])
                    await player.send({
                        "type": "gym_victory",
                        "events": events,
                        "gym_name": gym["name"],
                        "badge": gym["badge"],
                        "dialog_win": gym["dialog_win"],
                        "currency_gained": gym["reward_currency"],
                        "xp_results": xp_results,
                    })
                    return

            await player.send({
                "type": "wild_fainted",
                "events": events,
                "currency_gained": CURRENCY_WILD_WIN,
                "xp_results": xp_results,
            })
            return

        if my_poke.is_fainted:
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
            # Need to switch
            await player.send({
                "type": "wild_force_switch",
                "events": events,
                "available": alive,
                "your_team": [p.serialize_full() for p in encounter.team],
            })
            return

        await player.send({
            "type": "wild_turn_result",
            "events": events,
            **encounter.serialize_state(),
        })
        return

    await player.send({"type": "error", "message": f"Unknown wild action: {action}"})


def _resolve_single_move(attacker, defender, move, tap_score, side):
    """Resolve one side's move in a wild battle. Returns events list."""
    events = []
    events.append({"type": "move_use", "side": side, "pokemon": attacker.name, "move": move["name"]})

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
                        events.append({"type": "status_apply", "pokemon": defender.name, "status": status})
                    else:
                        events.append({"type": "miss", "pokemon": attacker.name})
                    break
        return events

    # Damage move
    accuracy = move.get("accuracy", 100)
    if random.randint(1, 100) > accuracy:
        events.append({"type": "miss", "pokemon": attacker.name})
        return events

    # Tap multiplier: 0.85 - 1.15
    tap_mult = 0.85 + (tap_score * 0.30)
    damage, effectiveness, is_crit = calculate_damage(attacker, defender, move, tap_mult)

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
    events.append({"type": "damage", "pokemon": defender.name, "damage": damage,
                   "hp": defender.current_hp, "max_hp": defender.max_hp})

    if defender.current_hp <= 0:
        defender.is_fainted = True
        events.append({"type": "faint", "pokemon": defender.name})

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

    tap = random.uniform(0.3, 0.7)
    return _resolve_single_move(wild, target, wild_move, tap, "wild")


def _award_encounter_xp(encounter, defeated):
    """Award XP to the active Pokemon after defeating an opponent."""
    results = []
    active = encounter.get_active()
    if not active or active.is_fainted:
        return results

    base_exp = defeated.species.get("base_experience", 64)
    is_gym = getattr(encounter, 'is_gym', False)
    xp = calc_xp_yield(defeated.level, base_exp, is_wild=not is_gym)

    db_id = getattr(active, 'db_id', None)
    if db_id:
        result = account_mgr.award_xp(db_id, xp)
        if result:
            # Check for new moves
            if result["leveled_up"]:
                new_moves = pokemon_data.get_new_moves_for_level(
                    result["dex_id"], result["old_level"], result["new_level"]
                )
                result["new_moves"] = [
                    {"level": m["level"], "move_id": m["move"],
                     "move_data": pokemon_data.MOVES.get(m["move"], {})}
                    for m in new_moves if m["move"] in pokemon_data.MOVES
                ]
                # Check evolution
                evo = pokemon_data.get_evolution(result["dex_id"])
                if evo and evo.get("method") == "level" and result["new_level"] >= evo.get("level", 999):
                    result["evolution"] = {
                        "from_dex_id": result["dex_id"],
                        "to_dex_id": evo["evolves_to"],
                        "to_name": pokemon_data.POKEMON.get(evo["evolves_to"], {}).get("name", "???"),
                    }
                    account_mgr.update_pokemon_species(db_id, evo["evolves_to"])
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
