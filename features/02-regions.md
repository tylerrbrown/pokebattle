# Regions

## Overview

Players can travel between the 9 canonical Pokemon regions (Kanto, Johto, Hoenn, Sinnoh, Unova, Kalos, Alola, Galar, Paldea). Each region has its own pool of wild Pokemon encounters, so players in Kanto encounter primarily Gen 1 Pokemon, players in Johto encounter primarily Gen 2, and so on. Players can travel freely between regions at any time from the hub screen. The current region is persisted per player in the database and displayed on the hub.

## Architecture

**Data-driven approach**: A new static JSON file `data/regions.json` defines each region with its name, generation range, featured dex IDs, and flavor metadata. The `generate_wild_pokemon()` function gains an optional `region` parameter that filters the encounter pool. The player's current region is stored as a new column on the `players` table and included in the profile payload.

**Region-to-Pokemon mapping:**

| Region | Gen | Dex IDs | Pokemon Count |
|--------|-----|---------|---------------|
| Kanto | 1 | 1-151 | 151 |
| Johto | 2 | 152-251 | 100 |
| Hoenn | 3 | 252-386 | 135 |
| Sinnoh | 4 | 387-493 | 107 |
| Unova | 5 | 494-649 | 156 |
| Kalos | 6 | 650-721 | 72 |
| Alola | 7 | 722-809 | 88 |
| Galar | 8 | 810-905 | 96 |
| Paldea | 9 | 906-1025 | 120 |

**Cross-region encounters**: Each region encounter has a 15% chance of pulling from the global pool instead of the regional pool. This keeps encounters fresh while still making region travel meaningful.

## Data Changes

### New file: `data/regions.json`

Each region entry contains:
- `id` -- short key (e.g., "kanto")
- `name` -- display name
- `generation` -- which gen it corresponds to
- `dex_range` -- `[min_dex_id, max_dex_id]` inclusive range
- `description` -- flavor text
- `color` -- hex color for UI theming
- `icon_dex_id` -- representative Pokemon for region icon sprite

## Backend Changes

### `pokemon_data.py`
- Add `REGIONS` global list, load from `data/regions.json` in `load_data()`
- Add `get_region(region_id)` and `get_region_pokemon_ids(region_id)` helpers

### `player_accounts.py`
- Migration: `ALTER TABLE players ADD COLUMN current_region TEXT DEFAULT 'kanto'`
- Add `get_region(player_id)` and `set_region(player_id, region_id)` methods
- Include `current_region` in `get_profile()` response

### `journey.py`
- Add `region` parameter to `generate_wild_pokemon()`
- Implement 85/15 regional/global encounter split
- Filter `pokemon_list` by region dex range before rarity selection

### `server.py`
- Modify `wild_encounter` handler to pass region to `generate_wild_pokemon()`
- Add `get_regions` handler: returns all regions with summary data + player's current region
- Add `travel_to_region` handler: validates region, updates DB, sends confirmation
- Include region name in `wild_encounter_start` response

## Frontend Changes

### New Screen: `screen-regions`
- Grid of region cards showing: region name, representative Pokemon sprite, description, Pokemon count, "YOU ARE HERE" indicator on current region
- Tap a region to travel (instant)

### Hub Changes
- Add "TRAVEL" button to hub (teal gradient)
- Show current region in profile bar
- Update wild encounter button text to include region name

### CSS
- `.btn-travel` -- teal gradient
- `.region-grid` -- responsive grid (3 cols desktop, 2 cols mobile)
- `.region-card` -- card with sprite, name, description, border highlight for current

### JavaScript
- `showRegions()` -- show screen, send `get_regions`
- `renderRegions(data)` -- render grid with current region highlighted
- `travelToRegion(regionId)` -- send `travel_to_region`
- Handle `regions_list` and `region_changed` messages
- Update `updateHubProfile()` to show current region

## Implementation Steps

1. Create `data/regions.json` with all 9 regions
2. `pokemon_data.py` -- Load regions, add helpers
3. `player_accounts.py` -- Add column migration, get/set methods, profile update
4. `journey.py` -- Add region parameter to `generate_wild_pokemon()`
5. `server.py` -- Modify wild_encounter, add get_regions and travel_to_region handlers
6. `index.html` -- Add screen HTML/CSS/JS, hub button, profile indicator
7. Test: travel between regions, verify encounter pools, verify persistence

## Interactions with Existing Systems
- **Gyms/E4/Champion/Masters**: NOT affected by region (separate feature 03)
- **Trading**: Unaffected (trade any Pokemon regardless of region)
- **Shop/PvP/Evolution/Move Learning**: Unaffected

## Critical Files
- `journey.py` -- `generate_wild_pokemon()` region filtering
- `player_accounts.py` -- DB persistence for current_region
- `server.py` -- WebSocket handlers for region travel and filtered encounters
- `pokemon_data.py` -- Region data loading and helpers
- `index.html` -- Region selector screen and hub integration
