# Legendary Encounter System

## Overview

Two combined requests from Liam:
1. "Every 50 times the wild encounter button is collected there should be a legendary" — pity system
2. "Decrease the chances finding legendary" — lower random rate

Lower the base random legendary rate from 3% to ~1%, add a per-player pity counter that guarantees a legendary every 50 wild encounters.

## Current System

Wild encounter generation lives in `generate_wild_pokemon()` in `journey.py`. Uses `RARITY_WEIGHTS = {"common": 60, "uncommon": 25, "rare": 12, "legendary": 3}` for weighted random selection. Current legendary rate is 3%.

Pokemon dataset has 1,025 Pokemon: 782 common, 136 uncommon, 19 rare, 88 legendary.

## Database Changes

Add `encounters_since_legendary INTEGER DEFAULT 0` to `players` table.

Migration in `_migrate()`:
```python
if "encounters_since_legendary" not in cols:
    conn.execute("ALTER TABLE players ADD COLUMN encounters_since_legendary INTEGER DEFAULT 0")
```

New methods on `AccountManager`:
- `get_encounter_counter(player_id)` — returns current counter value
- `increment_encounter_counter(player_id)` — +1, returns new value
- `reset_encounter_counter(player_id)` — sets to 0

## Backend Changes

### `journey.py` — Lower rate + pity system

```python
RARITY_WEIGHTS = {"common": 60, "uncommon": 25, "rare": 13, "legendary": 1}
PITY_THRESHOLD = 50

def generate_wild_pokemon(player_team_avg_level, pity_counter=0):
    force_legendary = pity_counter >= PITY_THRESHOLD
    if force_legendary:
        rarity = "legendary"
    else:
        # existing weighted random selection with new weights
```

### `server.py` — Wire pity counter

In `wild_encounter` handler:
1. Read counter: `pity_counter = account_mgr.get_encounter_counter(player.account_id)`
2. Pass to generation: `generate_wild_pokemon(avg_level, pity_counter=pity_counter)`
3. After generation:
   - If `rarity == "legendary"`: `account_mgr.reset_encounter_counter(player.account_id)`
   - Else: `account_mgr.increment_encounter_counter(player.account_id)`

## Frontend Changes

### CSS — Legendary battle theme

```css
.battle-arena[data-bg-theme="legendary-shrine"] {
  background: linear-gradient(180deg, #0a0020 0%, #1a0040 50%, #0a0020 100%);
}
/* Purple/gold sacred atmosphere with pulsing radial glow */
```

### JavaScript

- Add `BATTLE_THEMES.legendary = 'legendary-shrine'`
- In `applyBattleBackground()`: if wild encounter with `rarity === 'legendary'`, use legendary theme
- In `setupJourneyBattle()`: dramatic encounter text "A legendary X appeared!", screen shake animation
- Golden name shimmer CSS class for legendary opponent name

```css
.legendary-name {
  color: #ffd700 !important;
  text-shadow: 0 0 6px rgba(255,215,0,0.6);
  animation: legendary-text-shimmer 2s ease-in-out infinite;
}
.legendary-shake { animation: screen-shake 0.8s ease-out; }
```

## Edge Cases

- Counter persists in DB across sessions
- Random legendary before pity threshold still resets counter
- Training mode should NOT increment counter (only real wild encounters)
- Gym/E4/Champion battles don't affect counter
- New players start at 0, guaranteed legendary within 50 encounters

## Implementation Steps

1. `player_accounts.py` — Add column, migration, counter methods
2. `journey.py` — Lower legendary weight, add `PITY_THRESHOLD`, add pity param
3. `server.py` — Wire counter into wild encounter handler
4. `index.html` — Legendary battle theme CSS, shimmer, shake, encounter text
5. Test: verify ~1% base rate, forced at 50, counter reset, special UI

## Critical Files

- `journey.py` — Rarity weights, pity threshold, `generate_wild_pokemon`
- `player_accounts.py` — DB schema, counter methods
- `server.py` — Wild encounter handler wiring
- `index.html` — Legendary theme CSS + encounter UI
