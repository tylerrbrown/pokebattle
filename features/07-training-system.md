# Training System

## Overview

A practice/training mode where players pick any Pokemon from the full Pokedex as an opponent, choose a level, and battle using their journey team. Reuses the existing wild encounter infrastructure (`WildEncounter` class) with a `is_training = True` flag.

**Key rules:**
- No catching (no balls, no catch window)
- No currency reward
- Reduced XP (75% of wild rate)
- No Rare Candy drops
- Full EXP Share, move learning, evolution, and level-up mechanics
- Player can run at any time

## Architecture

Training battles are a specialized variant of wild encounters, flagged with `is_training = True` on the `WildEncounter` object. This follows the exact pattern used for gym battles (`encounter.is_gym = True`).

**Flow:**
1. Player clicks TRAINING on hub -> sees Pokemon picker grid + level slider
2. Selects a Pokemon and level, clicks "START TRAINING"
3. Client sends `start_training` with `{dex_id, level}`
4. Server creates `WildEncounter` with `is_training = True`, builds opponent at specified level
5. Battle proceeds via existing `_handle_wild_action` with guard clauses
6. Victory: XP at 75%, no currency, no Rare Candy
7. Frontend: hides BALL button, shows "Training complete!" on victory

## Backend Changes

### `server.py`

**New handler: `start_training`**
- Validate login, team, dex_id, level (clamped 1-100)
- Build `PokemonInstance` at specified level with correct learnset moves
- Create `WildEncounter` with `is_training = True`
- Send `training_battle_start` with encounter state

**Modify `_handle_wild_action`:**
- Read `is_training = getattr(encounter, 'is_training', False)`
- Block ball action: return error if `is_training`
- Suppress catch window: skip catch window if `is_training`
- Training victory branch: `currency_gained: 0`, `rare_candy_gained: 0`, `is_training: True`

**Modify `_award_encounter_xp`:**
- After Lucky Egg doubling, if `is_training`: `full_xp = int(full_xp * 0.75)`

**Import:** Add `PokemonInstance` to battle_engine imports

## Frontend Changes

### CSS
- `.btn-training { background: linear-gradient(135deg, #14b8a6, #0d9488); color: #fff; }`
- Training screen styles: search input, responsive grid, card styles, level slider

### HTML: `screen-training`
- Header with "TRAINING" title and BACK button
- Description text
- Search input for filtering Pokemon
- Responsive grid of all Pokemon (48x48 sprites, name)
- Level slider (1-100, default = team average)
- "TRAIN vs [Pokemon]" start button (disabled until selection)

### Hub
- Add TRAINING button (teal) to hub button group

### JavaScript
- `showTraining()` -- show screen, render grid, set slider to team avg level
- `renderTrainingGrid()` -- render all Pokemon cards with search filter
- `selectTrainingPokemon(dexId)` -- highlight selected, update start button
- `startTrainingBattle()` -- send `start_training` message
- Handle `training_battle_start` message: set `S.encounterType = 'training'`, call `setupJourneyBattle()`
- Modify action bar: BALL button for `'wild'` only, RUN button for `'wild' || 'training'`
- Modify `showJourneyVictory()`: show "Training complete!" for training battles (no currency/candy text)

## Design Decisions

- **75% XP**: Training useful for leveling but doesn't replace wild encounters (which also offer catching + currency)
- **No currency/Rare Candy**: Prevents exploiting training as a farm
- **Level slider default = team avg**: Convenient initial experience
- **Reuse WildEncounter + flag**: Consistent with gym pattern, minimal code changes
- **All battle mechanics work**: Dynamax, Mega, Z-Moves, items, switching, status effects -- only catching disabled

## Implementation Steps

1. Backend: Import `PokemonInstance` in `server.py`
2. Backend: Add `start_training` handler
3. Backend: Add `is_training` guards in `_handle_wild_action` (ball, catch window, victory)
4. Backend: Reduce XP in `_award_encounter_xp` for training
5. Frontend: Add CSS for training button and screen
6. Frontend: Add `screen-training` HTML
7. Frontend: Add TRAINING button to hub
8. Frontend: Add `training_battle_start` message handler
9. Frontend: Add training JS functions
10. Frontend: Modify action bar (ball/run conditions)
11. Frontend: Modify victory display for training
12. Test: battle flow, XP awards, no catching, run works, level slider

## Critical Files
- `server.py` -- `start_training` handler, `_handle_wild_action` guards, XP reduction
- `index.html` -- Training screen HTML/CSS/JS, hub button, battle flow modifications
- `journey.py` -- Reference: WildEncounter class (no changes needed)
- `battle_engine.py` -- Reference: PokemonInstance constructor (no changes needed)
