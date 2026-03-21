# Thematic Backgrounds

## Overview

Different battle backgrounds depending on the battle context. Each gym leader, Elite Four member, and battle type gets a distinct visual theme for its battle screen using CSS gradients and pseudo-elements. Zero image assets required -- pure CSS approach matching the existing single-file architecture.

## Design

**Data attribute approach**: Set `data-bg-theme` on `.battle-arena` to drive CSS. Without the attribute, the original dark gradient remains as fallback.

### Theme Mapping

| Context | Theme | Visual Concept |
|---------|-------|----------------|
| Wild encounter | `grassland` | Green grass with pale blue-dark sky gradient |
| Gym 1 - Brock (Rock) | `rock-cave` | Brown/gray rocky cave |
| Gym 2 - Misty (Water) | `aquarium` | Deep blue with water patterns |
| Gym 3 - Lt. Surge (Electric) | `power-plant` | Dark with yellow electric arcs |
| Gym 4 - Erika (Grass) | `garden` | Lush green with flower accents |
| Gym 5 - Koga (Poison) | `dojo-poison` | Purple-tinted dojo |
| Gym 6 - Sabrina (Psychic) | `psychic-chamber` | Pink/magenta ethereal glow |
| Gym 7 - Blaine (Fire) | `volcano` | Red/orange volcanic heat |
| Gym 8 - Giovanni (Ground) | `earth-fortress` | Brown earth, dark dramatic sky |
| Elite Four | `indigo-plateau` | Deep indigo/navy regal atmosphere |
| Champion | `hall-of-fame` | Gold and white with radial glow |
| Masters Eight | `world-stage` | Black with gold/red dramatic lighting |
| PvP / Quick Battle | `stadium` | Neutral gray stadium with spotlights |

### CSS Technique

Each theme uses:
- Main background gradient on `.battle-arena`
- `::before` pseudo-element for ground/platform visual (bottom ~40%)
- `::after` pseudo-element for ambient effects (glow, patterns)

All colors are muted/desaturated to maintain sprite visibility. Semi-transparent dark overlay on ground ensures text readability.

## Frontend Changes (index.html only -- no backend)

### CSS (~100 lines)

```css
.battle-arena[data-bg-theme]::before,
.battle-arena[data-bg-theme]::after {
  content: ''; position: absolute; inset: 0;
  z-index: 0; pointer-events: none;
}
/* Ensure sprites sit above backgrounds */
.battle-arena[data-bg-theme] .opponent-side,
.battle-arena[data-bg-theme] .player-side { z-index: 1; position: relative; }
```

Then one CSS rule per theme (13 themes, ~4-6 lines each).

### JavaScript (~40 lines)

**`BATTLE_THEMES` constant:**
```javascript
const BATTLE_THEMES = {
  wild: 'grassland',
  gym: { 1:'rock-cave', 2:'aquarium', 3:'power-plant', 4:'garden',
         5:'dojo-poison', 6:'psychic-chamber', 7:'volcano', 8:'earth-fortress' },
  e4: 'indigo-plateau', champion: 'hall-of-fame',
  masters: 'world-stage', pvp: 'stadium',
};
```

**`applyBattleBackground()` function:**
- Reads `S.encounterType`, `_currentTrainer.category`, `S.currentGymId`
- Sets `data-bg-theme` attribute on `#battle-arena`

**Call sites:**
- `setupJourneyBattle()` after `showScreen('battle')`
- PvP `battle_start` handler
- `goHome()` and `backToTitle()` -- remove attribute (cleanup)

### Future Integration
- When regions feature (02) ships, wild encounter theme can be driven by `S.currentRegion` instead of always "grassland"

## Asset Requirements

None. 100% CSS gradient-based.

## Implementation Steps

1. Add CSS theme rules (~100 lines after Battle Screen section)
2. Add JS theme mapping and `applyBattleBackground()` function
3. Wire up: call in `setupJourneyBattle()`, PvP battle start, cleanup in `goHome()`
4. Test all battle contexts (wild, each gym, E4, champion, masters, PvP)
5. Update CLAUDE.md

## Critical Files
- `index.html` -- All changes (CSS themes, JS mapping/application, cleanup)
- `journey.py` -- Reference for gym IDs and trainer categories (read-only)
