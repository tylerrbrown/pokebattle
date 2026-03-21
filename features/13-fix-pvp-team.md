# Fix PvP Forces Team Pick

## Overview

PvP (Quick Battle) forces players to pick a team instead of using their saved journey team. Liam: "When I battle somebody I expect to use my Pokémon, not click on Pokémon."

## Root Cause

1. `server.py` lines ~707 and ~736: PvP create/join only pre-sets the journey team if the player has **>= 6** Pokemon. Players with 1-5 journey Pokemon fall through to team select.
2. `game_room.py` `handle_rematch()`: always resets `player.team = None` and calls `_start_team_select()` without re-loading journey teams. Every rematch forces a full re-pick.

## Fix Strategy

Remove the `>= 6` gate and add an `on_rematch` callback to reload journey teams before `_start_team_select()`.

## Backend Changes

### `server.py` — Remove `>= 6` gate

```python
# BEFORE (lines ~707, ~736)
if team_data and len(team_data) >= 6:
# AFTER
if team_data:
```

### `server.py` — Define `reload_journey_teams` callback

```python
def reload_journey_teams(room):
    """Re-load journey teams for logged-in players on rematch."""
    for p in room.players:
        if p and not p.is_bot and not p.ready and getattr(p, 'account_id', None):
            team_data = account_mgr.get_team(p.account_id)
            if team_data:
                journey_team = build_journey_team(team_data, pokemon_data.POKEMON, pokemon_data.MOVES)
                p.team = journey_team
                p.team_dex_ids = [pkmn.dex_id for pkmn in journey_team]
                p.team_name = f"{p.name}'s Team"
                p.ready = True
```

Pass to RoomManager: `RoomManager(on_game_end=record_game, on_rematch=reload_journey_teams)`

### `game_room.py` — Add `on_rematch` callback

- Add `self.on_rematch = None` to `GameRoom.__init__`
- Add `on_rematch` parameter to `RoomManager.__init__`, set on `create_room`
- Invoke before `_start_team_select()` in `handle_rematch()` (both bot and PvP paths)

## Frontend Changes

### `index.html` — Update Quick Battle instructions

In `updateTitleGreeting()`, change instructions text for logged-in users to "Battle with your Journey team!" instead of "Pick 6 and battle!"

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Not logged in | No `account_id`, falls through to team select (unchanged) |
| Logged in, 0 Pokemon | `get_team()` returns `[]` (falsy), falls through to team select |
| Logged in, 1-5 Pokemon | Journey team loaded, battle starts with partial team |
| Mixed lobby | Logged-in player auto-ready, other sees team select |
| Quick Battle AI (not journey) | Team select (unchanged) |
| Rematch | `on_rematch` reloads journey teams, team select skipped |

## Implementation Steps

1. `game_room.py` — Add `on_rematch` callback pattern
2. `server.py` — Define `reload_journey_teams`, remove `>= 6` gate, pass callback
3. `index.html` — Update Quick Battle instructions text
4. Test PvP with <6 Pokemon, rematch, mixed lobby

## Critical Files

- `server.py` — Remove `>= 6` gate, define callback, pass to RoomManager
- `game_room.py` — Add `on_rematch` to GameRoom/RoomManager, invoke in `handle_rematch`
- `index.html` — Update instructions text
