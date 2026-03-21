# Pokedex

## Overview

A browsable encyclopedia accessible from the hub screen displaying all 1,025 Pokemon with sprites, types, stats, and flavor text. Tracks which Pokemon each player has "seen" (encountered) and "caught" (owns/has owned). The grid renders entirely on the frontend using the already-cached `S.pokemonList`. Server provides seen/caught tracking and flavor text data.

## Architecture

### Data Flow
- **Pokemon master data**: Already sent on login via `pokemon_list` message, cached in `S.pokemonList`
- **Flavor text**: New `data/pokedex.json` mapping dex_id to `{description, category, height, weight, generation}`
- **Seen/caught tracking**: New `player_pokedex` table. Server sends player's set on login and on changes.
  - "Seen" = appeared in wild encounter, gym/trainer battle, or trade
  - "Caught" = caught wild, received via trade, chose as starter

## Database Changes

### New Table: `player_pokedex`
```sql
CREATE TABLE IF NOT EXISTS player_pokedex (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id INTEGER NOT NULL,
    dex_id INTEGER NOT NULL,
    seen INTEGER DEFAULT 0,
    caught INTEGER DEFAULT 0,
    first_seen_at INTEGER,
    first_caught_at INTEGER,
    FOREIGN KEY (player_id) REFERENCES players(id),
    UNIQUE(player_id, dex_id)
);
```

Add to `_init_tables()` in `player_accounts.py` (CREATE IF NOT EXISTS, no migration needed).

## Data Requirements

### New File: `data/pokedex.json`
```json
{
  "1": {
    "description": "A strange seed was planted on its back at birth...",
    "category": "Seed Pokemon",
    "height": 0.7,
    "weight": 6.9,
    "generation": 1
  }
}
```

Generate via one-time script pulling from PokeAPI (`pokemon-species/{id}` for flavor text/genera, `pokemon/{id}` for height/weight). ~150-200KB total.

## Backend Changes

### `pokemon_data.py`
- Add `POKEDEX_INFO = {}` global, load from `data/pokedex.json` in `load_data()`
- Add `get_pokedex_info(dex_id)` helper

### `player_accounts.py`
New methods on `AccountManager`:
- `mark_seen(player_id, dex_id)` -- UPSERT, set seen=1
- `mark_caught(player_id, dex_id)` -- UPSERT, set seen=1 + caught=1
- `mark_seen_batch(player_id, dex_ids)` -- batch version for trainer battles
- `get_pokedex(player_id)` -- returns `{seen: [ids], caught: [ids]}`
- `get_pokedex_counts(player_id)` -- returns `{seen_count, caught_count}`

### `server.py`
1. Send `pokedex_info` in `pokemon_list` message on login
2. Add `get_pokedex` message handler
3. Record "seen" at: wild_encounter start, gym/trainer battle starts
4. Record "caught" at: catch success, trade completion, starter selection, evolution
5. Include pokedex counts in `get_profile` response
6. One-time backfill: on first `get_pokedex` call, if empty but player has Pokemon, mark_caught for all owned dex_ids

## Frontend Changes

### State
```javascript
pokedexSeen: new Set(),
pokedexCaught: new Set(),
_pokedexInfo: {},
```

### New Screen: `screen-pokedex`
- Header with title, seen/caught counts, BACK button
- Filters: search input, generation dropdown, type dropdown, status dropdown (All/Seen/Caught/Not Caught)
- Grid: `repeat(auto-fill, minmax(72px, 1fr))`, scrollable
- Each card: 48x48 sprite, dex number (#001), name (or "???" if unseen)
  - Unseen: dimmed (opacity 0.35), silhouette (`filter: brightness(0)`)
  - Seen but uncaught: blue border
  - Caught: green border

### Detail Overlay
- Large 120x120 sprite
- Name + dex number + type badges
- Category + flavor text description
- Height / Weight
- Base stat bars (HP, Atk, Def, Special, Speed) with colored fills
- Status badge (Caught/Seen/Unknown)
- Prev/Next navigation arrows

### Hub Changes
- Add red POKEDEX button to hub
- Show `Pokedex: X/1025` in profile stats

### JavaScript Functions
- `showPokedex()` -- show screen, send `get_pokedex`
- `renderPokedexGrid()` -- render filtered grid from `S.pokemonList`
- `filterPokedex()` -- apply search/gen/type/status filters
- `showPokedexDetail(dexId)` -- show detail overlay
- `closePokedexDetail()` -- hide overlay

## Implementation Steps

1. Generate `data/pokedex.json` via script from PokeAPI
2. `pokemon_data.py` -- Load pokedex info
3. `player_accounts.py` -- Add table and tracking methods
4. `server.py` -- Add handler, insert mark_seen/mark_caught calls at all encounter/catch/trade points
5. `index.html` -- Add screen HTML/CSS/JS, hub button, message handler
6. Test: grid rendering, filters, seen/caught tracking, detail view, backfill

## Edge Cases
- **Performance**: 1,025 grid cards -- use `innerHTML` (fast), `loading="lazy"` on images
- **Unseen silhouettes**: CSS `filter: brightness(0)` (no extra images)
- **Backfill**: Existing players get caught credit for currently-owned Pokemon on first access
- **Evolution**: New form marked as caught when evolution occurs

## Critical Files
- `player_accounts.py` -- Table, mark_seen/mark_caught methods
- `server.py` -- Handler, tracking calls at encounter/catch/trade/evolution/starter points
- `index.html` -- Full frontend (grid, filters, detail overlay, hub button)
- `pokemon_data.py` -- Load `pokedex.json`
- `data/pokedex.json` -- Flavor text data file
