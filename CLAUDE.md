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
| `pokemon_data.py` | Load/validate JSON data at startup |
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

1. Title → Create/Join room (4-letter code)
2. Team Select → Pick 6 Pokemon simultaneously (90s timer)
3. Battle → Turn-based with tap phase for damage moves
4. Game Over → Rematch or new game

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
