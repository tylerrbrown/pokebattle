# Tournament Mode

## Overview

Tournament Mode is a post-Elite Four endgame feature: a 4-round bracket (Quarterfinal, Semifinal, Final, Championship) where the player battles AI opponents of escalating difficulty. Each successive round features higher-level Pokemon, smarter AI behavior, and stronger team compositions. Winning the full tournament awards substantial currency, Rare Candy, and a unique milestone.

Fits into the existing journey progression as an additional challenge alongside Masters Eight. Implementation follows the same patterns used for Gym Leaders, Elite Four, Champion, and Masters Eight -- the `WildEncounter` "trainer battle" pattern with `encounter.is_gym = True` and a `trainer_category` tag.

## Architecture

**Tournament Structure:**
- 4 rounds: Quarterfinal, Semifinal, Final, Championship
- Each round is a single battle against a generated AI trainer
- Player must win all 4 consecutively; losing resets the tournament (back to round 1)
- Unlocked after beating at least 1 Masters Eight opponent (milestone check: `champion_defeated`)
- Can be re-entered and re-won for rewards each time (repeatable endgame content)

**Opponent Generation:**
- Opponents are NOT pre-defined static data like Gym Leaders. Instead, a `generate_tournament_opponent(round_num, player_avg_level)` function builds a random trainer with a themed name, dialog, and a team that scales with both round number and player level.
- Round 1 (Quarterfinal): 4 Pokemon, levels = player_avg + 2-5, random pool
- Round 2 (Semifinal): 5 Pokemon, levels = player_avg + 5-10, curated stronger species
- Round 3 (Final): 6 Pokemon, levels = player_avg + 10-15, pseudo-legendary + legendary pool
- Round 4 (Championship): 6 Pokemon, levels = player_avg + 15-20, top-tier only (Dragonite, Mewtwo, etc.)

**AI Difficulty Scaling (`ai_player.py`):**
- New `BotPlayer.set_difficulty(level)` method adjusting three knobs:
  - `switch_awareness`: probability of switching on bad matchup (increases per round)
  - `move_randomness`: range of random factor in `_score_move` (decreases per round = more optimal)
  - `tap_range`: tuple for `get_tap_score()` random range (increases per round = harder taps)
- Difficulty presets per tournament round:
  - Round 1: difficulty=0.4 (noise=0.80-1.20, switch=25%, tap=0.3-0.7)
  - Round 2: difficulty=0.6 (noise=0.88-1.12, switch=35%, tap=0.4-0.8)
  - Round 3: difficulty=0.8 (noise=0.92-1.08, switch=45%, tap=0.5-0.85)
  - Round 4: difficulty=1.0 (noise=0.95-1.05, switch=55%, tap=0.6-0.9)

**Rewards Per Round:**

| Round | Currency | Rare Candy |
|-------|----------|------------|
| Quarterfinal | $1,000 | 2 |
| Semifinal | $2,000 | 3 |
| Final | $3,000 | 4 |
| Championship | $10,000 | 5 |
| **Total** | **$16,000** | **14** |

Winning the Championship also records a `tournament_champion` milestone and gives a one-time bonus of 5 Rare Candy XL on first completion.

## Backend Changes

### `journey.py`

1. Add `CURRENCY_TOURNAMENT = [1000, 2000, 3000, 10000]` constant
2. Add `TOURNAMENT_NAMES` -- themed trainer name pools per round
3. Add `TOURNAMENT_STRONG_POKEMON` -- dex_id lists by tier for team generation
4. Add `generate_tournament_opponent(round_num, player_avg_level)` function:
   - Selects name from round's pool
   - Generates team of appropriate size (4/5/6/6 Pokemon)
   - Levels scale: `player_avg_level + round_offset + random(-2, 2)`
   - Returns dict matching trainer format: `{id, name, title, type, team, reward_currency, dialog_intro, dialog_win, dialog_lose}`

### `ai_player.py`

1. Add `difficulty` parameter to `BotPlayer.__init__()` (default 0.5)
2. Add `set_difficulty(level)` method adjusting switch_threshold, score_noise, tap range
3. Modify `decide_action()` to use parameterized switch threshold
4. Modify `_score_move()` to use parameterized noise range
5. Modify `get_tap_score()` to use parameterized tap range
6. Default values maintain current behavior (backward compatible)

### `server.py`

1. Add `TournamentState` class and `active_tournaments` dict (in-memory, ephemeral)
2. Add WebSocket message handlers:
   - `get_tournament` -- Check eligibility, return tournament state
   - `start_tournament` -- Create TournamentState, generate first opponent, return trainer_intro
   - `tournament_battle_start` -- Build teams, create WildEncounter with `trainer_category = "tournament"`
   - `tournament_continue` -- Advance to next round or declare victory
3. Add `"tournament"` case to trainer victory handling (award currency, rare candy, milestone)
4. Add `"tournament"` case to trainer defeat handling (reset tournament, delete state)
5. Update `_award_rare_candy()` for tournament: `qty = round + 2` (yields 2, 3, 4, 5)

## Frontend Changes

### HTML
- Add `TOURNAMENT` button to hub screen (hidden until `champion_defeated` milestone)
- Add `screen-tournament` with bracket display (4 rounds: QF, SF, F, Championship)

### CSS
- `.btn-tournament { background: linear-gradient(135deg, #ffd700, #f59e0b); color: #1a1a2e; }`

### JavaScript
- `showTournament()` -- Navigate to tournament screen, send `get_tournament`
- `renderTournament(data)` -- Render 4-round bracket with status (locked/current/completed)
- `startTournament()` -- Send `start_tournament`
- Handle `tournament_data` in WebSocket switch
- Modify `showTrainerIntro()` for `category === 'tournament'`
- Modify `showTrainerVictory()` for tournament (show "ROUND X COMPLETE!" or "TOURNAMENT CHAMPION!")
- Modify `showTrainerDefeat()` for tournament ("Tournament Over - start from Quarterfinals")

## Database Changes

**None required.** Existing tables handle everything:
- `player_progression` stores `tournament_champion` milestone
- `player_inventory` stores Rare Candy rewards
- `players.currency` tracks currency rewards
- Tournament state is ephemeral (in-memory dict)

## Implementation Steps

1. `journey.py` -- Add tournament data constants and `generate_tournament_opponent()` function
2. `ai_player.py` -- Add difficulty scaling system to BotPlayer
3. `server.py` -- Add TournamentState, message handlers, victory/defeat logic
4. `index.html` -- Add tournament screen HTML/CSS/JS, hub button, trainer flow modifications
5. Test: eligibility gating, all 4 rounds, defeat reset, rewards, re-entry

## Critical Files
- `journey.py` -- Tournament opponent generation, currency constants
- `ai_player.py` -- BotPlayer difficulty scaling
- `server.py` -- Tournament state management, WebSocket handlers, victory/defeat logic
- `index.html` -- All frontend: screen, bracket rendering, hub button, trainer flow
