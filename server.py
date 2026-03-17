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
import re
import sqlite3
import time

import websockets
from websockets.http11 import Response as HttpResponse, Headers as HttpHeaders

import pokemon_data
from game_room import Player, RoomManager

APP_DIR = pathlib.Path(__file__).parent
DB_PATH = APP_DIR / "pokebattle.db"
PORT = int(os.environ.get("POKEBATTLE_PORT", 5060))
ADMIN_SECRET = os.environ.get("POKEBATTLE_ADMIN_SECRET", "pb-x9f2k7m4-admin-2024")

# Global state
room_manager = RoomManager()


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

    else:
        await player.send({"type": "error", "message": f"Unknown message type: {msg_type}"})


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
        # Record game if it just ended
        room = room_manager.get_room(player)
        if room and room.state == "GAME_OVER":
            # Game was already recorded
            pass
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
