# PokeBattle

> **Workflow**: When done building a plan's implementation, always commit and push so Tyler can deploy.

Gen 1 & Gen 2 Pokemon multiplayer battle game with WebSocket real-time gameplay.

## Architecture

- **Backend**: Python 3 + `websockets` library (single external dependency)
- **Frontend**: Vanilla JS, single `index.html` + `admin.html`, no build tools
- **Database**: SQLite (game history)
- **Data**: Static Gen 1+2 JSON dataset (251 Pokemon, 180+ moves, 17-type chart incl. Dark & Steel)

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
| `data/pokemon.json` | 251 Pokemon (Gen 1+2): stats, types, 4 moves each |
| `data/moves.json` | 180+ moves: power, accuracy, PP, type, effect |
| `data/typechart.json` | 17x17 type effectiveness matrix (incl. Dark & Steel) |
| `sprites/front/` | 251 front sprites (PNG) |
| `sprites/back/` | 251 back sprites (PNG) |

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

### EC2 git authentication
The server needs an authenticated remote URL to push. Generate it locally using the git credential manager:
```bash
TOKEN=$(echo -e "protocol=https\nhost=github.com\n" | git credential fill | grep "^password=" | cut -d= -f2)
git remote set-url origin "https://tylerrbrown:${TOKEN}@github.com/tylerrbrown/pokebattle.git"
```
Run those on the EC2 server, then `git push` works. The token comes from Tyler's local Windows git credential manager - if it expires, regenerate the same way.

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
- **Never use `cd` in Bash commands** — `Bash(cd *)` permission patterns don't match chained commands (`cd path && python ...`). Always use absolute paths so commands start with the Python executable and match `Bash("/c/Users/..." *)`. Example: `"/c/.../python.exe" $CLAUDE_HOME/apps/pokebattle/tests/test_battle_engine.py` instead of `cd "$CLAUDE_HOME/apps/pokebattle" && python tests/test_battle_engine.py`
- **Learnset invalid moves**: 274 moves in `data/learnsets.json` don't exist in `data/moves.json` (e.g., `force-palm`, `close-combat`, `crabhammer`). All move-loading code must filter against `pokemon_data.MOVES`. Startup migration `fix_invalid_moves` cleans these from the DB. The `_get_current_moves()` helper in `server.py` also filters at runtime.
- **12 Pokemon have genuinely <4 moves** (e.g., Beldum, Silcoon, Cosmog) - this is accurate to the real games and is NOT a bug. The move management screen correctly shows "No learnable moves" for these.

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
- **Dodge mechanic** replaces old tap-to-power system (see below)
- **Diamond move selector** - 4 moves arranged in a diamond/cross pattern (top/left/right/bottom)
- Speed determines move order
- PP tracking with Struggle fallback
- **EXP Share**: All alive team Pokemon earn XP from defeats - active gets 100%, bench gets 50%, fainted get 0%
- `_award_encounter_xp()` loops all `encounter.team`; frontend `processAllXpResults()` chains level-up overlays for multiple Pokemon

## Dodge Mechanic

Replaces the old tap-to-power system. Instead of tapping to fill a meter, players dodge incoming attacks.

- **Journey mode**: After picking a move, a 1.5-second dodge window appears with left/right arrow buttons. One direction is highlighted (correct). Tap the correct direction to dodge, reducing incoming damage by 20% (0.8x multiplier). Wrong direction or timeout = full damage (1.0x).
- **PvP mode**: Server sends `dodge_phase` message with opponent's move name. Each player who is being attacked by a damage move gets the dodge prompt. Dodge result (`dodge_result` message with `dodged: true/false`) sent back before turn resolves.
- **Bot AI**: Bots have a difficulty-scaled dodge chance (15%-45% based on difficulty level)
- **Backend**: `calculate_damage()` takes `dodge_multiplier` param (1.0 = full damage, 0.8 = dodged). `resolve_turn()` maps defender dodge mult to incoming attacks.
- **Frontend**: Shared `doDodge()` function handles both PvP and journey contexts via `window._dodgeState`. Journey sets `pendingMsg` in state; PvP sends `dodge_result` directly.

## Move Selector (2x2 Grid)

Moves displayed in a standard 2x2 grid layout (mobile-friendly):
- CSS grid: 2 columns, auto-flow rows - moves fill naturally left-to-right, top-to-bottom
- Works for 1-4 moves (single move centered across both columns)
- Same onclick behavior, just grid layout
- Touch-friendly: 8px border-radius, min-height 54px, scale on hover/active

## XP Bar System

- XP formula: medium-fast growth `(4/5) * N^3` total XP at level N
- **XP yield has 1.5x base boost** for faster kid-friendly progression; trainer battles get additional 1.5x
- **Lucky Egg**: passive inventory item ($1500 in shop) — owning one doubles all XP (stacks with base boost)
- Effective XP rates: Lv20->21 takes ~4 wild fights (2 with Lucky Egg), Lv49->50 takes ~9 fights (5 with egg)
- `xp_progress_info()` in `player_accounts.py` computes progress float (0-1), XP to next, XP thresholds
- `_enrich_pokemon_xp()` adds XP fields to all Pokemon dicts from `get_team()`, `get_all_pokemon()`, `get_storage()`, `get_profile()`
- `award_xp()` returns `xp_progress`, `xp_for_current_level`, `xp_for_next_level`
- `build_journey_team()` computes `xp_progress` inline (avoids circular import)
- Frontend: thin blue XP bar on My Team cards (with "X XP to next" text) and battle HUD (journey mode)

## Dynamax / Gigantamax

- **Data**: `data/dynamax.json` — max move power lookup, max move names by type, G-Max Pokemon list
- **PokemonInstance state**: `is_dynamaxed`, `dynamax_turns_left`, `pre_dynamax_hp/max_hp`
- **Mechanics**: `dynamax()` doubles HP for 3 turns; `revert_dynamax()` restores HP proportionally; `tick_dynamax()` decrements turns, auto-reverts at 0
- **Server**: `dynamax` action in `_handle_wild_action()`, moves converted to Max Moves when dynamaxed (power from lookup table), G-Max moves for eligible Pokemon
- **Mutual exclusivity**: Dynamax, Mega Evolution, and Z-Move are mutually exclusive per battle (server enforces, frontend hides buttons)
- **Frontend**: DYNAMAX button in battle, sprite scale(1.5) + red glow, turn counter badge, Max Move names replace normal moves
- **Edge cases**: fainted Pokemon don't tick/revert; forced switch after faint — new Pokemon is NOT dynamaxed; `_dynamaxUsed` reset in `setupJourneyBattle()`

## Shop & Items

- **Shop** sells Poke Balls, healing items, and held items (SHOP_ITEMS in `journey.py`)
- **Poke Balls**: Poke Ball ($200), Great Ball ($600), Ultra Ball ($1200) — stored in `players.pokeballs`
- **Healing Items**: Potion ($300, 20HP), Super Potion ($700, 50HP), Hyper Potion ($1200, 200HP), Revive ($1500, 50% HP), Full Restore ($3000, full HP + cure status)
- **Held Items**: Lucky Egg ($1500) — passive item, owning one doubles all XP gains (not consumed on use)
- **Rare Candy**: Tiered level-up items usable from My Team screen only (not in battle)
  - Rare Candy ($500, +1 level), Rare Candy XL ($2000, +5 levels), Rare Candy XXL ($5000, +10 levels), Rare Candy Ultra ($20000, +50 levels)
  - Uses `use_rare_candy` WebSocket message with `pokemon_id` and `item_type`
  - Calculates XP to reach target level (capped at 100), awards via `award_xp()`, checks move learning + chain evolution at each level
  - Returns `rare_candy_result` with `xp_result` containing level-up/move/evolution data; frontend chains existing overlays via `processAllXpResults()`
  - Category: `"rare_candy"` in SHOP_ITEMS; stored in `player_inventory` table
- **Inventory**: `player_inventory` table (player_id, item_type, quantity) with UPSERT pattern
- **Item use during battle**: costs a turn (wild/gym Pokemon attacks back); sent via `use_item` message type, NOT through `wild_action`
- **Item use from My Team**: consumes item from inventory (HP doesn't persist between battles, so mainly cosmetic outside battle)
- **NOT usable in PvP**: items only work in wild encounters and gym battles
- Items are categorized: `"category": "ball"` (Poke Balls use `pokeballs` column) vs `"category": "healing"` (use `player_inventory` table) vs `"category": "held"` (passive effects)
- **Rare Candy**: Battle reward (not purchasable), stored as `rare_candy` in `player_inventory`
  - Drop rates: wild encounter 10%, gym 100% x1, Elite Four 100% x2, Champion/Masters 100% x3
  - Also awarded on wild catch (same 10% rate)
  - `_award_rare_candy(player, battle_type)` in `server.py` handles drops
  - Usage: `use_rare_candy` message → `_handle_use_rare_candy()` → awards exact XP for +1 level, handles move learning + evolution
  - Frontend: purple "RARE CANDY (N)" button on My Team cards, count shown in hub + stats line + shop summary
  - Victory messages include `rare_candy_gained` field; UI shows "+X Rare Candy" alongside currency

## Pokemon Trading

- **Trade button** on hub screen opens trade menu (create or join with 4-letter code)
- **TradeRoom** class in `server.py` — lightweight 2-player room separate from battle rooms
- **Flow**: Create/join trade room -> both see their Pokemon -> tap to offer -> both confirm -> swap executed
- **WebSocket Messages**:
  - `create_trade` -> `trade_room_created` (code)
  - `join_trade` -> `trade_room_joined` / `trade_partner_joined`
  - `trade_offer` -> `trade_offer_set` / `trade_partner_offer`
  - `trade_confirm` -> `trade_partner_confirmed` / `trade_complete`
  - `trade_cancel` -> `trade_cancelled` / `trade_left`
- **Database**: `trade_pokemon()` in `player_accounts.py` swaps `player_id` on both rows; both go to storage after trade; handles edge case where traded Pokemon was the last team member
- **Cleanup**: trade rooms auto-cleaned on disconnect; separate from battle room namespace
- **Frontend screens**: `screen-trademenu` (create/join), `screen-tradewait` (waiting for partner), `screen-trade` (active trade with offer/confirm)

## Legendary Pity System

- **Threshold**: Every 50 non-legendary wild encounters guarantees a legendary
- **Counter**: `encounters_since_legendary` column in `players` table, managed by `get_encounter_counter()`, `increment_encounter_counter()`, `reset_encounter_counter()` in `player_accounts.py`
- **Logic**: `generate_wild_pokemon()` in `journey.py` accepts `pity_counter` param; forces `rarity = "legendary"` when `>= PITY_THRESHOLD` (50)
- **Server wiring**: `server.py` reads counter before encounter, passes to generator, resets on legendary (natural or pity), increments otherwise
- **Atmospheric hints**: At 40+ encounters, `wild_encounter_start` includes `pity_hint` text ("You sense something powerful nearby..." at 40+, "The air crackles with strange energy..." at 45+); frontend shows as a fading golden italic overlay on the battle arena
- **Natural legendaries also reset**: If RNG rolls legendary before hitting 50, counter resets too

## Tests

```bash
python tests/test_battle_engine.py
# 53 tests covering stats, damage, types, status, turns, teams
```

## Liam's Contact (iMessage)

- **iCloud email**: liam4now@icloud.com (this is how his messages appear in iMessage DB)
- **Phone**: (785) 761-6790 — but messages come through iCloud, not phone number
- **To pull his messages**: Use `mcp__imessage__tool_get_recent_messages` with NO contact filter (omit the contact param), then visually scan for `liam4now@icloud.com` entries. Do NOT filter by contact name or phone number — his messages route through iCloud, not his phone number, so contact-filtered queries miss them. Do NOT rely on `fuzzy_search_messages` alone — it only finds messages matching a keyword and will miss messages on other topics.

## Feature Request Tracker (from Liam, 3/17–3/18/2026)

### Already Implemented (do not re-build)
- Wild encounters with battle flow (attack, weaken, catch, run)
- Catch rate scaling with HP (weaker = easier to catch, ball modifiers)
- Gym leaders (8 gyms with teams, badges, dialog, rewards, sequential progression)
- In-game currency (earn from battles, spend in shop)
- Shop with Poke Balls, potions, evolution stones
- Evolution system (level-based + stone-based)
- Move learning system (level-up + manual swap)
- Save/persistence (SQLite DB, token-based sessions)
- Back/navigation buttons throughout UI

### Bugs Liam Reported
- ~~**Wild encounter attack bug**: fixed 3/18/2026~~
- ~~**Gym leader no-AI bug**: fixed 3/18/2026~~
- ~~**Faint/switch freeze**: `wild_force_switch` was missing encounter state; fixed 3/19/2026~~
- ~~**Only active Pokemon earned XP**: added EXP Share (100% active, 50% bench); fixed 3/19/2026~~
- **PvP forces team pick**: "When I battle somebody I expect to use my Pokémon, not click on Pokémon" — PvP should use saved journey team, not re-pick (OPEN)
- ~~**Learn Move doesn't apply**: Root cause was invalid learnset moves (274 moves in learnsets.json not in moves.json) stored in DB, causing move count mismatch between frontend (filtered) and backend (unfiltered). Fixed 3/23/2026~~
- **Faint animation plays on wrong Pokemon**: When you defeat the opponent's Pokemon, it looks like YOUR Pokemon faints instead (reported by Makoo - 3/20/2026) (OPEN)
- ~~**Pokemon stuck with <4 moves ("move lock")**: Same root cause as learn move bug. `_get_current_moves` now filters invalid moves; `get_moves_at_level` filters and deduplicates; startup migration `fix_invalid_moves` cleans DB; level-up overlay shows "LEARN" button when <4 moves. Fixed 3/23/2026~~

### Feature Requests (3/21/2026 — from Liam)
- **Shiny Pokemon**: Sparkle effect at battle start, ~10% encounter rate
- ~~**Legendary pity system**: Guaranteed legendary encounter every 50 wild encounters~~ (DONE 3/23/2026)
- **Decrease legendary random rate**: Lower the base random chance of finding legendaries

### Implemented in "Liam's Feature Pack" (3/19/2026)
- Backpack/PC Storage UI (swap Pokemon between team and storage)
- Elite Four → Champion → Masters Eight progression path
- All 1,025 Pokemon (Gen 1-9) with sprites, learnsets, evolutions
- Z-Moves & Mega Evolution battle mechanics
- Font size bump (+2px via CSS variables)
- PvP uses saved journey team

### Implemented 3/19/2026
1. **XP bar UI** — Thin blue XP bar on My Team cards and battle HUD; shows "X XP to next" text; XP scaling via medium-fast growth formula
2. **Move learning UX fix** — Prominent hint box at top, client-side selection (no server round-trip), inline confirmation "Replace X with Y? [YES] [NO]" for 4-move swaps, green LEARN button for <4 moves
3. **Gigantamax/Dynamax** — DYNAMAX button in battle, 3-turn HP doubling, Max Move names/powers, G-Max moves for eligible Pokemon, sprite scale+glow effect, mutually exclusive with Mega/Z-Move
4. **Bug fixes** — Faint/switch freeze (wild_force_switch missing encounter state), EXP Share (all alive team Pokemon earn XP: 100% active, 50% bench)
5. **Faster XP progression** — 1.5x base XP boost to `calc_xp_yield()`, Lucky Egg shop item ($1500) doubles all XP gains passively, "Lucky Egg: 2x XP!" shown in victory text
6. **Tiered Rare Candy shop** — Rare Candy ($500, +1 lv), XL ($2000, +5), XXL ($5000, +10), Ultra ($20000, +50); chain evolution support
7. **Rare Candy battle drops** — 10% wild, 100% gym x1, 100% E4 x2, 100% Champion/Masters x3; usable from My Team
8. **PvP currency rewards** — $500 for beating a human, $300 for beating AI bot
9. **Pokemon Trading** — TRADE button on hub, 4-letter room codes, select Pokemon to offer, both confirm, swap ownership in DB; traded Pokemon go to storage
