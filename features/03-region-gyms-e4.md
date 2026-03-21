# Region-Specific Gyms & Elite Four

## Overview

Each of the 9 regions gets its own set of 8 gym leaders, 4 Elite Four members, and 1 Champion. The current Kanto gyms/E4/Champion become the first region in a multi-region structure. Players can travel between regions and track badges independently per region. Masters Eight remains a global endgame unlocked after beating any region's Champion.

## Architecture

### Current State
- All gym/E4/Champion data lives as flat global constants in `journey.py`
- `player_badges` table stores integer `gym_id` values 1-8
- Milestones are strings like `"e4_1_defeated"` and `"champion_defeated"`
- Zero region concept

### Target State
- A new `data/regions.json` file defines gyms, E4, champion per region
- Database adds `region` column to `player_badges` and uses region-prefixed milestones
- Server passes region context on all gym/E4/Champion requests
- Frontend adds region selector and renders region-specific progression

## Data Design

### Region Gym Leaders (Canonical)

- **Kanto**: Brock/Rock, Misty/Water, Lt. Surge/Electric, Erika/Grass, Koga/Poison, Sabrina/Psychic, Blaine/Fire, Giovanni/Ground. E4: Lorelei/Ice, Bruno/Fighting, Agatha/Ghost, Lance/Dragon. Champion: Blue.
- **Johto**: Falkner/Flying, Bugsy/Bug, Whitney/Normal, Morty/Ghost, Chuck/Fighting, Jasmine/Steel, Pryce/Ice, Clair/Dragon. E4: Will/Psychic, Koga/Poison, Bruno/Fighting, Karen/Dark. Champion: Lance.
- **Hoenn**: Roxanne/Rock, Brawly/Fighting, Wattson/Electric, Flannery/Fire, Norman/Normal, Winona/Flying, Tate&Liza/Psychic, Wallace/Water. E4: Sidney/Dark, Phoebe/Ghost, Glacia/Ice, Drake/Dragon. Champion: Steven.
- **Sinnoh**: Roark/Rock, Gardenia/Grass, Maylene/Fighting, Crasher Wake/Water, Fantina/Ghost, Byron/Steel, Candice/Ice, Volkner/Electric. E4: Aaron/Bug, Bertha/Ground, Flint/Fire, Lucian/Psychic. Champion: Cynthia.
- **Unova**: Cilan/Grass, Lenora/Normal, Burgh/Bug, Elesa/Electric, Clay/Ground, Skyla/Flying, Brycen/Ice, Drayden/Dragon. E4: Shauntal/Ghost, Grimsley/Dark, Caitlin/Psychic, Marshal/Fighting. Champion: Alder.
- **Kalos**: Viola/Bug, Grant/Rock, Korrina/Fighting, Ramos/Grass, Clemont/Electric, Valerie/Fairy, Olympia/Psychic, Wulfric/Ice. E4: Malva/Fire, Siebold/Water, Wikstrom/Steel, Drasna/Dragon. Champion: Diantha.
- **Alola**: Ilima/Normal, Lana/Water, Kiawe/Fire, Sophocles/Electric, Hala/Fighting, Olivia/Rock, Nanu/Dark, Hapu/Ground. E4: Molayne/Steel, Olivia/Rock, Acerola/Ghost, Kahili/Flying. Champion: Kukui.
- **Galar**: Milo/Grass, Nessa/Water, Kabu/Fire, Bea/Fighting, Allister/Ghost, Opal/Fairy, Gordie/Rock, Raihan/Dragon. E4: Marnie/Dark, Hop/Various, Bede/Fairy, Nessa/Water. Champion: Leon.
- **Paldea**: Katy/Bug, Brassius/Grass, Iono/Electric, Kofu/Water, Larry/Normal, Ryme/Ghost, Tulip/Psychic, Grusha/Ice. E4: Rika/Ground, Poppy/Steel, Larry/Normal, Hassel/Dragon. Champion: Geeta.

Levels scale by region order: Kanto gyms Lv12-50, later regions increment ~5 levels per region.

## Database Changes

### `player_badges` -- Add `region` column
- Migration: Create `player_badges_v2` with `region TEXT DEFAULT 'kanto'` and `UNIQUE(player_id, gym_id, region)`
- Copy existing data with `region = 'kanto'`, drop old table, rename

### `player_progression` -- Prefix milestones with region
- Existing milestones stay as-is (implicitly Kanto)
- New milestones use format `"region:milestone"` (e.g., `"johto:e4_1_defeated"`)
- No schema change needed (milestone is free-text)

### `players` -- Add `current_region`
- `ALTER TABLE players ADD COLUMN current_region TEXT DEFAULT 'kanto'`

### New AccountManager Methods
- `get_badges_for_region(player_id, region)`
- `earn_badge_for_region(player_id, gym_id, region)`
- `get_all_region_badges(player_id)` -- returns `{region: [gym_ids]}`
- `set_current_region(player_id, region)` / `get_current_region(player_id)`

## Backend Changes

### `journey.py`
1. Remove hardcoded `GYM_LEADERS`, `ELITE_FOUR`, `CHAMPION` constants
2. Load region data from `data/regions.json` into `REGIONS` dict
3. Add accessor functions: `get_region()`, `get_region_gym()`, `get_next_region_gym()`, `get_region_elite_four()`, `get_region_champion()`
4. Keep `MASTERS_EIGHT` as-is (global endgame)
5. Add region filter to `generate_wild_pokemon()`

### `server.py`
1. All gym/E4/champion handlers gain optional `region` field (default: player's current_region)
2. Add `get_regions` and `set_region` handlers
3. Victory handling: `earn_badge_for_region()`, region-prefixed milestones
4. Progression gating: E4 requires 8 badges in that region, Champion requires that region's E4
5. Masters Eight: unlocked by any region's champion defeat
6. `get_progression` returns `badges_by_region`, `current_region`, `regions_completed`

## Frontend Changes

### New Screen: Region Selector
- Grid of region cards with badge count (e.g., "3/8 badges") and completion status
- Accessible via "REGIONS" button on hub

### Hub Changes
- Replace "GYM LEADERS" with "REGIONS" button
- Badge display shows current region's badges
- E4/Champion buttons gated per current region
- Region indicator text on hub

### Gym/E4 List Changes
- Title shows region name (e.g., "JOHTO GYM LEADERS")
- Region-specific data displayed

## Implementation Steps

1. Create `data/regions.json` (all 9 regions with gyms/E4/champions and team compositions)
2. Database migration in `player_accounts.py`
3. Update `journey.py` -- load regions, replace hardcoded constants
4. Update `server.py` -- region-aware handlers, victory logic
5. Update `index.html` -- region selector, per-region gym lists, hub changes
6. Backward compatibility testing (existing Kanto data migrated correctly)
7. Full progression testing across multiple regions

## Challenges
- Team composition for 117 trainers (72 gym leaders + 36 E4 + 9 champions)
- Level scaling across freely-accessible regions
- Migration safety for `player_badges` restructure
- Frontend complexity in the already large `index.html`

## Critical Files
- `data/regions.json` -- All region/gym/E4/champion definitions
- `journey.py` -- Replace hardcoded constants with region-loaded data
- `player_accounts.py` -- Database migration and region-aware badge methods
- `server.py` -- Region-aware message handlers and victory logic
- `index.html` -- Region selector, updated gym/E4 flows, hub changes
