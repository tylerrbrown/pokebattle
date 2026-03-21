# Fix Pokemon Stuck with <4 Moves ("Move Lock")

## Overview

Some Pokemon only have 2-3 moves and cannot learn any new moves from the Move Management screen. Reported by Makoo (3/20) and Liam (3/21, calls it "move lock").

## Root Cause

**Two distinct bugs combining:**

### Bug A: NULL moves column causes move wipe on first level-up

When a Pokemon is added via `choose_starter()`, the `moves` column is never set (remains NULL). Battle works fine because `build_journey_team()` falls back to `species_data["moves"]`. But on first level-up, auto-learn code reads NULL as empty list:

```python
current_moves = json.loads(poke_row["moves"]) if poke_row and poke_row.get("moves") else []
```

The new move gets appended to `[]` instead of the existing 4 defaults, then saved. Pokemon now has only 1 move.

### Bug B: Sparse learnsets cap available moves below 4

30 Pokemon (2.9%) have fewer than 4 unique moves in `learnsets.json`. Even at level 100, no additional moves are available. Examples: Raichu (3), Exeggutor (3), Abra (1), Magikarp (2). The `pokemon.json` defaults include additional moves not in the learnset, but the Move Management screen only consults `learnsets.json`.

## Fix Strategy

1. Prevent Bug A for new Pokemon (initialize moves on creation)
2. Supplement sparse learnsets with `pokemon.json` defaults
3. Remediate existing affected Pokemon in DB

## Backend Changes

### `pokemon_data.py` — New helper

```python
def get_initial_moves(dex_id, level):
    """Get initial moves, guaranteeing up to 4 when possible.
    Uses learnset first, then supplements from pokemon.json defaults."""
    moves = get_moves_at_level(dex_id, level)
    if len(moves) < 4:
        species = POKEMON.get(dex_id)
        if species:
            for mid in species["moves"]:
                if mid not in moves and mid in MOVES:
                    moves.append(mid)
                    if len(moves) >= 4:
                        break
    return moves
```

### `player_accounts.py` — Initialize moves on creation

Update `choose_starter()` and `catch_pokemon()` to set the `moves` column with initial moves.

Add migration methods:
- `fix_null_moves()` — queries all `player_pokemon` where `moves IS NULL`, initializes from `get_initial_moves()`
- `fix_sparse_moves()` — queries rows with `moves IS NOT NULL` but `len(moves) < 4`, supplements up to 4

### `server.py` — Fix NULL fallbacks + expand learnable moves

1. Fix 3 NULL-moves fallback locations (~1269, ~1673, ~2263) to use `get_initial_moves()` instead of `[]`
2. Expand `get_learnable_moves` handler to include `pokemon.json` default moves alongside learnset moves
3. Expand `swap_move` validation to accept default moves as valid targets
4. Call migration methods on startup

## Edge Cases

- Pokemon with <4 valid moves across BOTH learnset AND defaults (e.g., Blipbug): acceptable, game handles via Struggle fallback
- Evolved Pokemon: uses current `dex_id` for lookup (correct)
- Traded Pokemon: retain their moves (unaffected)
- Migration idempotency: safe to run multiple times

## Implementation Steps

1. Add `get_initial_moves()` to `pokemon_data.py`
2. Fix `choose_starter()` in `player_accounts.py` to set moves on INSERT
3. Fix the three NULL-moves fallback locations in `server.py`
4. Update catch path to use `get_initial_moves()`
5. Expand `get_learnable_moves` and `swap_move` to include `pokemon.json` defaults
6. Add `fix_null_moves()` and `fix_sparse_moves()` migrations
7. Call migrations on server startup
8. Test starter → level up → verify 4 moves maintained

## Critical Files

- `server.py` — 3 NULL-moves fallbacks, `get_learnable_moves`, `swap_move` handlers
- `pokemon_data.py` — New `get_initial_moves()` helper
- `player_accounts.py` — `choose_starter()`, `catch_pokemon()`, migration methods
