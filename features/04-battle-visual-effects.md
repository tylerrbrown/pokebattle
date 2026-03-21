# Battle Visual Effects

## Overview

Type-specific visual effects play when Pokemon use attacking moves during battle. Fire moves show flames, water moves show splashes, electric moves show lightning, etc. Effects are CSS-only animations using dynamically injected DOM particles, requiring zero external libraries. They play during the `move_use` event phase in both PvP and journey battle paths.

## Approach: CSS-Only with Dynamic DOM Injection

- Entire app is single `index.html` with inline CSS/JS -- no build tools
- Existing effects (hit-flash, faint-anim, dynamax-pulse) are all CSS `@keyframes`
- CSS animations are hardware-accelerated on mobile (GPU compositing)
- No extra network requests for effect sprites

**Technique:** A reusable `#battle-vfx-layer` div overlays the battle arena at `z-index: 40`. When an effect triggers, 6-8 particle elements are injected with type-specific CSS classes. Each particle uses `@keyframes` for movement/scaling/opacity. After animation completes (600ms), particles are removed from DOM.

## Backend Change (Minimal)

**Problem:** Journey-mode `_resolve_single_move()` in `server.py` emits `move_use` events WITHOUT `move_type`. PvP-mode `resolve_move()` in `battle_engine.py` DOES include `move_type`.

**Fix:**
- `server.py` line ~2065: Add `"move_type": move["type"]` and `"is_damage_move": move.get("power", 0) > 0` to the event dict
- `battle_engine.py` line ~554: Add `"is_damage_move": move["power"] > 0` to the existing event dict

## Effect Design Per Type

| Type | Particles | Shape | Motion | Color |
|------|-----------|-------|--------|-------|
| Normal | 5 circles | border-radius: 50% | Radiate outward, fade | White/gray |
| Fire | 8 teardrops | Rounded rect | Rise upward with wobble | Orange-red |
| Water | 8 droplets | CSS drop shape | Arc outward, fall | Blue |
| Electric | 6 zigzags | Thin rotated rects | Flash, jitter | Yellow |
| Grass | 7 leaves | Rotated ellipses | Spiral outward | Green |
| Ice | 6 crystals | Rotated squares | Converge inward, shatter | Light blue |
| Fighting | 5 stars | CSS cross shape | Rapid burst | Red-brown |
| Poison | 6 blobs | Circles with wobble | Float upward, pulse | Purple |
| Ground | 8 chunks | Irregular rects | Rise from bottom, arc | Brown |
| Flying | 6 crescents | Box-shadow trick | Sweep horizontally | Light purple |
| Psychic | 6 rings | Bordered circles | Expand outward, rotate | Pink |
| Bug | 7 dots | Tiny circles | Swarm converge | Yellow-green |
| Rock | 5 chunks | Irregular rects | Fall from above | Brown-gold |
| Ghost | 5 wisps | Circles with blur | Float up, sine-wave | Purple |
| Dragon | 6 shards | CSS triangles | Spiral inward | Purple-blue |
| Dark | 6 shadows | Circles with blur | Converge from edges | Dark gray |
| Steel | 6 metallic | Small rectangles | Fly straight | Silver-gray |

## CSS Architecture

### VFX Layer
```css
#battle-vfx-layer {
  position: absolute; inset: 0;
  pointer-events: none; z-index: 40; overflow: hidden;
}
```

### Per-Type Pattern
Each type defines:
- `.vfx-{type}` class for particle appearance (size, color, shape)
- `@keyframes vfx-{type}` for motion path
- Animation duration: 600ms (fits within 800ms `move_use` window)

### Randomization via CSS Custom Properties
Each particle gets inline `--vfx-delay`, `--vfx-rot`, `--vfx-dx`, `--vfx-dy` for per-particle variation without needing unique keyframes.

## Frontend Changes

### HTML
- Add `<div id="battle-vfx-layer"></div>` inside `battle-arena`

### CSS (~200 lines)
- `#battle-vfx-layer` positioning
- `.vfx-particle` base class with `will-change: transform, opacity`
- 17 `.vfx-{type}` appearance classes
- 17 `@keyframes vfx-{type}` animations

### JavaScript

**`playMoveEffect(moveType, isAttackerPlayer)` (~60 lines):**
- Determines target position based on attacker (player sprite area or opponent sprite area)
- Creates 6-8 particle divs with type class and randomized CSS custom properties
- Appends to `#battle-vfx-layer`
- Sets timeout (850ms) to remove particles

**Hook into PvP path (`playTurnEvents`):**
- At `move_use` case: call `playMoveEffect(evt.move_type, !isOpponentPokemon(evt.pokemon))` if `evt.is_damage_move`

**Hook into Journey path (`playJourneyEvents`):**
- At `move_use` case: call `playMoveEffect(evt.move_type, evt.side === 'player')` if `evt.is_damage_move`

## Performance
- Max 8 particles per effect (simple divs)
- `will-change: transform, opacity` triggers GPU compositing
- No blur filters except ghost (subtle 2px on 5 particles)
- DOM cleanup after each effect
- 600ms animation fits existing timing windows
- All motion is CSS keyframes (no JS animation loops)
- `pointer-events: none` prevents interaction interference

## Implementation Steps

1. Backend: Add `move_type` and `is_damage_move` to journey events (`server.py`)
2. Backend: Add `is_damage_move` to PvP events (`battle_engine.py`)
3. HTML: Add `#battle-vfx-layer` div in `battle-arena`
4. CSS: Add particle styles and 17 type keyframes
5. JS: Add `playMoveEffect()` function
6. JS: Hook into PvP turn events (`playTurnEvents`)
7. JS: Hook into Journey turn events (`playJourneyEvents`)
8. Test: all battle modes, all types, mobile performance, status moves (no effect)

## Critical Files
- `index.html` -- All CSS styles, HTML structure, JavaScript (single-file frontend)
- `server.py` -- Add `move_type` and `is_damage_move` to journey `move_use` events
- `battle_engine.py` -- Add `is_damage_move` to PvP `move_use` events
