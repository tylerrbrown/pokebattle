# Fix Faint Animation on Wrong Pokemon

## Overview

When you defeat the opponent's Pokemon, it looks like YOUR Pokemon faints instead. Reported by Makoo.

## Root Cause

**Two distinct bugs:**

### Bug 1: Journey/Gym (Primary)

In gym battles with multiple Pokemon, when a non-final gym Pokemon faints, the server sends `wild_turn_result` with `serialize_state()` containing the NEXT gym Pokemon in the `wild_pokemon` field (because `encounter.wild` is already swapped server-side). The frontend calls `updateJourneyState(msg)` BEFORE `playJourneyEvents(msg.events)`, which updates `S.oppTeam[0]` to the next Pokemon. When the faint animation event plays, it checks:

```javascript
const isOppF = S.oppTeam[0] && S.oppTeam[0].name === evt.pokemon;
```

Since `S.oppTeam[0].name` is now the NEXT Pokemon's name and `evt.pokemon` is the FAINTED Pokemon's name, `isOppF` is `false`, and the faint animation targets the player's sprite instead.

### Bug 2: PvP (Edge Case)

`isOpponentPokemon()` compares by name only. If both players have the same species active, faint/damage animations target the wrong sprite.

## Fix Strategy

Add a `side` field to ALL events in journey mode, and use `player_index` + `your_player_index` in PvP mode, eliminating name-based matching.

## Backend Changes

### `server.py` — Add `side` to journey battle events

In `_resolve_single_move()`, add `"side"` to `damage`, `faint`, `status_apply`, and `miss` events:

```python
def _resolve_single_move(attacker, defender, move, tap_score, side):
    defender_side = "wild" if side == "player" else "player"
    # Add "side": defender_side to damage/faint events
    # Add "side": side to miss/move_use events
```

### `game_room.py` — Add `your_player_index` to PvP

Add `"your_player_index": i` to `turn_result` messages sent to each player. Remove post-hoc name-based `player_index` tagging.

### `battle_engine.py` — Propagate player indices

Modify `resolve_move()` to accept `attacker_idx`/`defender_idx` and include `"player_index"` on all events.

## Frontend Changes

### `index.html` — Use `side`/`player_index` instead of name matching

**Journey mode `playJourneyEvents()`:**
```javascript
// BEFORE: const isOpp = S.oppTeam[0] && S.oppTeam[0].name === evt.pokemon;
// AFTER:
const isOpp = evt.side !== 'player';
```

**PvP mode `playTurnEvents()`:**
- Store `S.myPlayerIndex = msg.your_player_index` from `turn_result`
- Use `evt.player_index !== S.myPlayerIndex` instead of `isOpponentPokemon()`

## Edge Cases

- Same-name Pokemon in PvP: Fixed by `player_index`
- Gym multi-Pokemon: Fixed by `side` field set at event creation time
- Self-destruct/recoil: Player index propagation handles correctly
- Bot PvP: Bot's `send()` is no-op, `your_player_index` is harmless

## Implementation Steps

1. `battle_engine.py` — Add `attacker_idx`/`defender_idx` to `resolve_move()` and `resolve_turn()`
2. `server.py` — Add `"side"` to events in `_resolve_single_move()`
3. `game_room.py` — Add `your_player_index` to `turn_result`, remove name-based tagging
4. `index.html` — Use `evt.side` in `playJourneyEvents()`, `evt.player_index` in `playTurnEvents()`
5. Test gym battles and PvP with same-species Pokemon

## Critical Files

- `index.html` — `playTurnEvents()`, `playJourneyEvents()`, `isOpponentPokemon()`
- `server.py` — `_resolve_single_move()` event creation
- `game_room.py` — `turn_result` message construction, player_index tagging
- `battle_engine.py` — `resolve_move()` and `resolve_turn()` player index propagation
