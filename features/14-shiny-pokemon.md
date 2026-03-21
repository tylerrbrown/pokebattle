# Shiny Pokemon

## Overview

Shiny variants with sparkle visual effects at battle start and ~10% encounter rate. Per Liam: "Add shiny so when you see when there's like sparkles everywhere in the beginning of the battle and also make them a little more frequent like 10% more chance of finding them."

Shiny is a boolean property on each Pokemon instance. Wild encounters roll at 10% (1-in-10). If caught, the shiny flag persists. Shiny Pokemon use the same sprites with CSS `hue-rotate(180deg) saturate(1.3)` for a color-shifted appearance, plus a sparkle particle animation on battle entry.

## Database Changes

Add `is_shiny INTEGER DEFAULT 0` to `player_pokemon` table.

Migration in `_migrate()`:
```python
if "is_shiny" not in cols:
    conn.execute("ALTER TABLE player_pokemon ADD COLUMN is_shiny INTEGER DEFAULT 0")
```

## Backend Changes

### `battle_engine.py`

- Add `self.is_shiny = False` to `PokemonInstance.__init__()`
- Add `"is_shiny": self.is_shiny` to `serialize_full()` and `serialize_public()`
- In `build_journey_team()`: `inst.is_shiny = bool(p.get("is_shiny", 0))`

### `journey.py`

In `generate_wild_pokemon()`, roll for shiny:
```python
is_shiny = random.random() < 0.10  # 10% shiny rate
wild.is_shiny = is_shiny
```

Add `"is_shiny": getattr(self.wild, 'is_shiny', False)` to `WildEncounter.serialize_state()` wild_pokemon dict.

### `player_accounts.py`

Add `is_shiny` parameter to `catch_pokemon()`:
```python
def catch_pokemon(self, player_id, dex_id, level, is_shiny=False):
    # Include int(is_shiny) in INSERT
```

### `server.py`

Pass `is_shiny=getattr(wild, 'is_shiny', False)` to `catch_pokemon()`. Add `is_shiny` to `wild_caught` response.

## Frontend Changes

### CSS

```css
.shiny-sprite { filter: hue-rotate(180deg) saturate(1.3); }
.shiny-star { color: #ffd700; font-size: var(--fs-sm); margin-left: 4px;
  text-shadow: 0 0 4px rgba(255,215,0,0.7); }

@keyframes shiny-sparkle {
  0% { opacity: 0; transform: scale(0) rotate(0deg); }
  50% { opacity: 1; transform: scale(1) rotate(180deg); }
  100% { opacity: 0; transform: scale(0.5) rotate(360deg); }
}
.shiny-sparkle-particle {
  position: absolute; width: 8px; height: 8px;
  background: radial-gradient(circle, #fff 30%, #ffd700 70%);
  border-radius: 50%; pointer-events: none;
  animation: shiny-sparkle 0.8s ease-out forwards;
}
```

### JavaScript

- `playShinySparkle(spriteId)` — spawns 14 sparkle particles around a sprite, removed after 1.2s
- `setupJourneyBattle()` — if `wild_pokemon.is_shiny`, add `shiny-sprite` class to opponent sprite, play sparkle, show "★ SHINY! ★" in encounter text
- `renderJourneyBattle()` / `renderBattleSprites()` — add/remove `shiny-sprite` class based on `is_shiny`
- `renderMyTeam()` / `_bpCard()` — add `★` shiny star badge next to name, apply `shiny-sprite` to card sprites
- `showCaughtResult()` — mention "★ Shiny" in catch text
- Switch-in sparkle: play sparkle when shiny Pokemon switches into battle

## Edge Cases

- Starters are never shiny (INSERT defaults to 0)
- Gym/trainer Pokemon are never shiny (PokemonInstance defaults to False)
- Traded Pokemon keep shiny status (`trade_pokemon` doesn't touch `is_shiny`)
- Evolution preserves shiny (`update_dex_id` only changes `dex_id`)
- Quick Battle Pokemon are not shiny (temporary picks)
- Existing DB Pokemon: `is_shiny` is NULL/0, both evaluate to False

## Performance

- CSS `hue-rotate` is GPU-composited, zero CPU cost
- 14 sparkle particles with CSS-only animation, removed after 1.2s
- No additional network requests or sprite assets

## Implementation Steps

1. `battle_engine.py` — Add `is_shiny` to PokemonInstance + serializers + `build_journey_team`
2. `player_accounts.py` — DB migration, `catch_pokemon` parameter
3. `journey.py` — Shiny roll in `generate_wild_pokemon`, serialize in WildEncounter
4. `server.py` — Pass shiny flag through catch flow
5. `index.html` — CSS + sparkle animation + rendering hooks

## Critical Files

- `battle_engine.py` — PokemonInstance `is_shiny`, serialize, `build_journey_team`
- `player_accounts.py` — DB schema, `catch_pokemon`
- `journey.py` — Shiny roll, `WildEncounter.serialize_state`
- `server.py` — Catch flow passthrough
- `index.html` — All frontend rendering
