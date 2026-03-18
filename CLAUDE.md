# PokeBattle

Gen 1 Pokemon multiplayer battle game with WebSocket real-time gameplay.

## Architecture

- **Backend**: Python 3 + `websockets` library (single external dependency)
- **Frontend**: Vanilla JS, single `index.html` + `admin.html`, no build tools
- **Database**: SQLite (game history)
- **Data**: Static Gen 1 JSON dataset (151 Pokemon, 117 moves, 15-type chart)

## Files

| File | Purpose |
|------|---------|
| `server.py` | WebSocket + HTTP static file server (entry point) |
| `battle_engine.py` | Gen 1 damage calc, turn resolution, status effects |
| `game_room.py` | Room management, game state machine |
| `ai_player.py` | BotPlayer AI opponent for single-player mode |
| `pokemon_data.py` | Load/validate JSON data at startup |
| `player_accounts.py` | Account registration, login, starter selection, team management |
| `index.html` | Full client (all screens, CSS, JS inline) |
| `admin.html` | Admin panel (game history, active rooms, stats) |
| `data/pokemon.json` | 151 Pokemon: stats, types, 4 moves each |
| `data/moves.json` | 117 moves: power, accuracy, PP, type, effect |
| `data/typechart.json` | 15x15 type effectiveness matrix |
| `sprites/front/` | 151 front sprites (PNG) |
| `sprites/back/` | 151 back sprites (PNG) |

## Running

```bash
# Install dependency
pip install websockets

# Start server
python server.py
# Server runs on http://localhost:5060
# WebSocket at ws://localhost:5060/ws
```

Environment variables:
- `POKEBATTLE_PORT` — server port (default: 5060)
- `POKEBATTLE_ADMIN_SECRET` — admin panel key (default: `pb-x9f2k7m4-admin-2024`)

## Deployment

- **Port**: 5060
- **Domain**: pokebattle.tylerrbrown.com
- **Repo**: https://github.com/tylerrbrown/pokebattle
- **Server path**: `/opt/pokebattle/`
- **Service**: `pokebattle.service` (systemd)
- **Proxy**: HAProxy with `mode http` + `timeout tunnel 3600s` for WebSocket
- **Python**: 3.10 on EC2 (Ubuntu 22.04 jammy, aarch64)
- **websockets**: Requires v14+ (`pip3 install 'websockets>=14'`). System apt package is 9.1 (too old — different API for HTTP serving).

```bash
# On EC2
cd /opt && git clone https://github.com/tylerrbrown/pokebattle.git
pip3 install 'websockets>=14'
cp pokebattle.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pokebattle
```

### HAProxy backend
```
backend web-pokebattle
    mode http
    timeout tunnel 3600s
    server pokebattle 127.0.0.1:5060 check fall 3 rise 1
```

### Deploy updates
```bash
cd /opt/pokebattle && git pull && systemctl restart pokebattle
```

## Admin

- URL: `https://pokebattle.tylerrbrown.com/admin.html?k=pb-x9f2k7m4-admin-2024`
- API: `/api/admin/rooms`, `/api/admin/history`, `/api/admin/stats`

## Game Flow

1. Title → Create/Join room (4-letter code) or "Battle AI" for single-player
2. Team Select → Pick 6 Pokemon simultaneously (90s timer); bot auto-picks
3. Battle → Turn-based with tap phase for damage moves
4. Game Over → Rematch or new game

## AI Single-Player Mode

- `BotPlayer` in `ai_player.py` duck-types the `Player` interface (`is_bot = True`, `send()` is no-op)
- Bot auto-handles all phases: team select, action choice, tap phase, force switch, rematch
- Move AI scores by `power * accuracy * type_effectiveness * STAB` with randomness
- Switches on type disadvantage (30% chance if matchup ≤0.5x)
- Tap score: random 0.3–0.8 (human has advantage)
- `GameRoom` checks `player.is_bot` at each decision point and immediately sets asyncio Events
- Game recording uses `on_game_end` callback on `GameRoom`, set via `RoomManager`, to avoid circular imports between `game_room.py` and `server.py`

## Gotchas

- **Module init order**: In `server.py`, `room_manager = RoomManager(...)` must come AFTER all functions it references (e.g., `record_game`) are defined — Python executes top-level statements in order
- **Query strings in static serving**: `request.path` in websockets includes query string; must strip `?...` before file path resolution or `admin.html?k=SECRET` returns 404
- **websockets version**: EC2 system apt has v9.1 (incompatible API); must use `pip3 install 'websockets>=14'`

## Battle Mechanics

- Faithful Gen 1: damage formula, STAB, type chart, status effects
- All Pokemon at Level 50
- Quick-time tapping: 0.85x–1.15x damage multiplier
- Speed determines move order
- PP tracking with Struggle fallback

## Shop & Items

- **Shop** sells Poke Balls and healing items (SHOP_ITEMS in `journey.py`)
- **Poke Balls**: Poke Ball ($200), Great Ball ($600), Ultra Ball ($1200) — stored in `players.pokeballs`
- **Healing Items**: Potion ($300, 20HP), Super Potion ($700, 50HP), Hyper Potion ($1200, 200HP), Revive ($1500, 50% HP), Full Restore ($3000, full HP + cure status)
- **Inventory**: `player_inventory` table (player_id, item_type, quantity) with UPSERT pattern
- **Item use during battle**: costs a turn (wild/gym Pokemon attacks back); sent via `use_item` message type, NOT through `wild_action`
- **Item use from My Team**: consumes item from inventory (HP doesn't persist between battles, so mainly cosmetic outside battle)
- **NOT usable in PvP**: items only work in wild encounters and gym battles
- Items are categorized: `"category": "ball"` (Poke Balls use `pokeballs` column) vs `"category": "healing"` (use `player_inventory` table)

## Tests

```bash
python tests/test_battle_engine.py
# 53 tests covering stats, damage, types, status, turns, teams
```
