# Remove Ultra Candy

## Overview

Remove the "Rare Candy Ultra" item ($20,000, +50 levels) from the shop per Liam's request ("take down the ultra candies"). The other Rare Candy tiers (+1, +5, +10) remain. This is a 2-line deletion across 2 source files plus 2 documentation updates.

## Files to Change

| File | Change |
|------|--------|
| `journey.py` | Delete `rare_candy_ultra` entry from `SHOP_ITEMS` dict (line ~70) |
| `index.html` | Delete `rare_candy_ultra` entry from `RARE_CANDY_ITEMS` const (line ~3500) |
| `CLAUDE.md` | Update 2 references that list the tiered candy tiers |

## Backend Changes

### `journey.py` (line ~70)

Delete this line from `SHOP_ITEMS`:
```python
"rare_candy_ultra":{"name": "Rare Candy Ultra", "price": 20000, "category": "rare_candy", "levels": 50},
```

**Why this is sufficient:**
- Shop display (`server.py`): iterates `SHOP_ITEMS.items()` -- key gone = not displayed
- Buy item (`server.py`): checks `item_type not in SHOP_ITEMS` -- returns "Invalid item" error
- Use rare candy (`server.py`): checks `if item_type in SHOP_ITEMS` -- falls through to "Invalid item"
- `player_accounts.py`: No references (stores/retrieves items generically)

## Frontend Changes

### `index.html` (line ~3500)

Delete this line from `RARE_CANDY_ITEMS`:
```javascript
rare_candy_ultra: {name:'Rare Candy Ultra',  levels:50, desc:'+50 Levels'},
```

**Why this is sufficient:**
- My Team item panel: iterates `Object.entries(RARE_CANDY_ITEMS)` -- key gone = no button rendered
- Shop rendering: filters items from server-sent list -- server no longer sends it
- Other UI (hub stats, shop summary): only references plain `rare_candy`, not tiered items

## Edge Cases

**Players with existing `rare_candy_ultra` inventory:**

Recommended approach: **Let existing stock become inert.** The frontend won't render a button for it (key not in `RARE_CANDY_ITEMS`), the server won't let them use it (key not in `SHOP_ITEMS`), and the shop won't display it. The quantity sits harmlessly in the `player_inventory` table. No migration needed.

Alternative (not recommended): Convert existing stock to 50x regular Rare Candy via SQL migration. Adds complexity with UPSERT logic for minimal benefit.

## Implementation Steps

1. Edit `journey.py` -- Delete `rare_candy_ultra` line from `SHOP_ITEMS`
2. Edit `index.html` -- Delete `rare_candy_ultra` line from `RARE_CANDY_ITEMS`
3. Edit `CLAUDE.md` -- Remove "Rare Candy Ultra" from tiered candy lists (lines ~164 and ~244)
4. Test: verify shop shows only 3 candy tiers, My Team shows only 3 tiers
5. Commit and push

## Critical Files
- `journey.py` -- `SHOP_ITEMS` dict (line ~70)
- `index.html` -- `RARE_CANDY_ITEMS` const (line ~3500)
- `CLAUDE.md` -- Documentation references
- `server.py` -- No changes needed (verify generic handlers work after removal)
