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

## Move Learning & Management

- **Move Management Screen**: Accessible from My Team via "MOVES" button on each Pokemon
  - Shows current moves (up to 4) with type badge, power, accuracy, PP
  - Shows all learnable moves from learnset at or below Pokemon's current level
  - Select a current move, then tap a learnable move to swap them
  - If fewer than 4 moves, new moves can be added directly
- **Level-Up Move Learning**: When a Pokemon levels up and learns a new move:
  - If fewer than 4 moves: auto-learned and shown in level-up overlay
  - If already has 4 moves: modal overlay prompts player to choose which move to replace (or skip)
- **WebSocket Messages**:
  - `get_learnable_moves` → `learnable_moves`: returns current + available moves with full details
  - `swap_move` → `swap_move_ok`: validates learnset, updates DB, returns new move list
  - `learn_move_choice` → `learn_move_ok` / `learn_move_skipped`: level-up move replacement decisions
- **Data**: `data/learnsets.json` — dex_id → `[{level, move}]` level-up move lists

## Battle Mechanics

- Faithful Gen 1: damage formula, STAB, type chart, status effects
- All Pokemon at Level 50
- Quick-time tapping: 0.85x–1.15x damage multiplier
- Speed determines move order
- PP tracking with Struggle fallback

## Tests

```bash
python tests/test_battle_engine.py
# 53 tests covering stats, damage, types, status, turns, teams
```
