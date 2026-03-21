# Fix Learn Move Bug

## Overview

When a player clicks to learn a move from the Move Management screen, it doesn't actually add the move to the Pokemon's moveset. Reported 3 times (Liam x2, Tyler x1).

## Root Cause

**NULL moves treated as empty list instead of species defaults.** When `moves` is NULL in the `player_pokemon` DB table (the state for all newly caught/starter Pokemon), the server's move management handlers treat the Pokemon as having 0 current moves (`[]`). However, in battle, `build_journey_team()` correctly falls back to species default moves (4 moves from `pokemon.json`). This creates a mismatch:

- Move Management screen shows species defaults via the frontend, but server sees `[]`
- When the user learns a move, the DB stores ONLY that single move (e.g., `["razor-leaf"]`), overriding the 4 species defaults
- The Pokemon then enters battle with only 1 move instead of 4

The pattern `json.loads(poke_row["moves"]) if poke_row.get("moves") else []` appears in 5 locations in `server.py`, all producing `[]` instead of the species defaults.

**Secondary:** The `showError()` function in the frontend doesn't handle the `movemgmt` screen, so server errors during move operations are silently written to the hidden battle text element.

## Backend Changes

### `server.py` — Add helper + fix 5 locations

Add a helper function near the top:

```python
def _get_current_moves(poke_row):
    """Get a Pokemon's current move list, falling back to species defaults if DB is NULL."""
    if poke_row.get("moves"):
        return json.loads(poke_row["moves"])
    species = pokemon_data.POKEMON.get(poke_row["dex_id"])
    if species:
        return list(species["moves"])  # Copy to avoid mutating static data
    return []
```

Replace the pattern in all 5 locations:

| Line | Context | Change |
|------|---------|--------|
| ~1155 | `get_learnable_moves` handler | `current_moves = _get_current_moves(poke_row)` |
| ~1218 | `swap_move` handler | same |
| ~1269 | `learn_move_choice` handler | same |
| ~1673 | Rare Candy level-up | `current_moves = _get_current_moves(poke_row) if poke_row else []` |
| ~2263 | XP award level-up | same |

### `player_accounts.py` — Initialize moves on catch/starter

Modify `catch_pokemon()` to accept and store initial moves:

```python
def catch_pokemon(self, player_id, dex_id, level, default_moves=None):
    moves_json = json.dumps(default_moves) if default_moves else None
    # Add moves_json to INSERT
```

Similarly update `choose_starter()` to set moves column.

Update callers in `server.py` to pass `default_moves=species["moves"]`.

## Frontend Changes

### `index.html` — Fix error visibility on movemgmt screen

Add `movemgmt` case to `showError()`:

```javascript
} else if (S.screen === 'movemgmt') {
    showToast(msg);
}
```

## Edge Cases

- **Existing Pokemon with NULL moves:** `_get_current_moves()` handles at runtime by falling back to species defaults. No migration needed. Once a move swap occurs, DB gets initialized with full list.
- **Evolved Pokemon with NULL moves:** Uses current `dex_id` for species defaults (acceptable).
- **Double-click race:** Second `swap_move` hits "Already knows that move" — now visible as toast.

## Implementation Steps

1. Add `_get_current_moves(poke_row)` helper to `server.py`
2. Replace all 5 `current_moves = json.loads(...)` patterns with the helper
3. Update `catch_pokemon()` and `choose_starter()` in `player_accounts.py` to accept/store default moves
4. Update callers in `server.py` to pass species default moves
5. Add `movemgmt` case to `showError()` in `index.html`
6. Test all move learning paths

## Critical Files

- `server.py` — 5 instances of the buggy pattern + helper function
- `player_accounts.py` — `catch_pokemon()` and `choose_starter()` moves initialization
- `index.html` — `showError()` movemgmt screen handling
