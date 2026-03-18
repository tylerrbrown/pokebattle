# PokeBattle v2: Liam's Journey Mode

## Context

PokeBattle is a working Pokémon battle game at pokebattle.tylerrbrown.com. Code lives at `C:\Claude\apps\pokebattle`. My 10-year-old son Liam has been playing it and has a vision for what he wants it to become. This is his game — respect his creative vision even when it's ambitious.

### What Already Exists
- **151 Gen 1 Pokémon** with sprites, stats, 4 moves each
- **2-player multiplayer** via WebSocket room codes — faithful Gen 1 battle mechanics with tap phase
- **Account system** (`player_accounts.py`) — token-based login, starter selection (Bulbasaur/Charmander/Squirtle), Poké Ball inventory, Pokémon collection with levels/XP in SQLite
- **AI opponent** (`ai_player.py`) — basic bot for single-player
- **UI screens** — login, starter selection, my team view, battle, game over
- **Tech stack** — Vanilla JS frontend (single index.html), Python websockets backend, SQLite, retro 8-bit pixel aesthetic with "Press Start 2P" font

### What Liam Wants

Liam wants PokeBattle to feel like a **Pokémon journey**, not just a battle simulator. In his words, "it's a journey basically." He wants to start with a starter, encounter and catch wild Pokémon, level them up, watch them evolve, learn new moves, battle through gyms, take on the Elite Four and Champion, and ultimately compete in the Masters Eight. All progress saved. All Pokémon — not just Gen 1.

## Feature Requests (All Messages Consolidated)

### 1. Wild Pokémon Encounters & Catching
> "There needs to be a button that you click where you search for a random Pokémon to battle against and weaken out of all the Pokémon that exist and then you have Pokémon balls and then you throw it at them"

- A "Wild Encounter" button — click it and a random wild Pokémon appears
- Battle it with your lead Pokémon to weaken it
- Throw a Poké Ball to attempt capture
- **Catch rate scales with HP** — lower HP = easier catch (like the real games)
- Poké Ball inventory (foundation exists in `player_accounts.py`)
- Caught Pokémon join your collection; carry up to 6 on your team, store the rest

### 2. Rarity Tiers & Legendary Pokémon
> "If they're a legendary, it's really rare to find one and really hard to catch it and then if it's something that's not a legendary and a common or an uncommon then it should be pretty easy"

- **Common** — appear frequently, easy to catch
- **Uncommon** — appear less often, moderate catch difficulty
- **Rare** — infrequent encounters, harder to catch
- **Legendary** — very rare to encounter AND very hard to catch even when found (includes legendaries from games, shows, AND movies — e.g., Mewtwo, Lugia, Deoxys, Arceus)
- Rarity should affect both encounter rate and catch rate independently

### 3. Remove Manual Team Picking in Journey Mode
> "When you go to battle you still choose your Pokémon and basically I need you to take away that... the Pokémon that you catch in the game you use"

- In Journey Mode, you battle with your own caught/leveled Pokémon — no picking from a list
- The existing "pick 6 from all 151" should remain as a separate **Quick Battle** mode for when friends just want to jump in
- Before a PvP or gym battle, you select which of your caught Pokémon to bring (up to 6)

### 4. XP & Leveling System
> "Every Pokémon needs to have levels so say you win a battle you get experience and depending on how much you get in that battle that's how much you get and then you get to a higher level... and also it gets stronger"

- Pokémon earn XP from all battles (wild encounters, AI, PvP, gyms)
- XP amount scales with opponent difficulty
- Level up when XP threshold reached (standard Pokémon EXP curve or simplified)
- Stats scale with level using Gen 1 stat formulas (engine already supports variable levels)
- Levels 1–100, starters begin at Level 5
- Display level prominently on all Pokémon cards, team view, and battle UI
- DB schema for level/xp already exists in `player_pokemon` table

### 5. Evolution
> "When your Pokémon is at a level where it can evolve, then it should"

- When a Pokémon reaches its evolution level, trigger evolution
- Show an evolution animation/screen ("What? Charmander is evolving!")
- Species changes, stats recalculate, sprite updates
- Evolution data available from PokéAPI (evolution chains, level triggers)
- Start with **level-based evolution only** — stone/trade evolutions can come later

### 6. Move Learning & Management
> "Your Pokémon doesn't have to start with so many moves. It could start with one or two and it can learn new moves on the way and you can also like update the move list"

- Pokémon start with **1–2 moves** based on their species' level-1 learnset (not 4)
- Learn new moves as they level up, matching the official Pokémon learnset data
- When a new move is available and the Pokémon already has 4 moves, prompt: "Forget a move to learn [new move]?"
- **Move management screen** — view all current moves, swap/forget moves
- Move data per-Pokémon persisted to save file (not just species defaults)

### 7. Gym Leaders
> "I need you to put gym leaders and stuff so you go against the bug type gym and stuff like that like you know all the gyms in Pokémon you go and beat them"

- **8 type-themed Gym Leaders** (follow the original Kanto gym order or similar):
  - Bug, Rock, Water, Electric, Grass, Poison, Psychic, Fire (or adapt as needed)
- Each gym leader has a themed team at scaling difficulty
- Must beat each gym to progress to the next
- Earn a **badge** for each gym victory (tracked in profile, displayed in UI)
- Gyms function as AI battles with pre-built themed teams

### 8. Elite Four, Champion & Masters Eight
> "In the final of the Journeys series, you can go battle on the Masters Eight if you win against the Elite Four and then the Champion"

- After all 8 gym badges: unlock the **Elite Four** — 4 consecutive battles, must win all
- After Elite Four: face the **Champion** — single high-difficulty battle
- After Champion: unlock the **Masters Eight** — ultimate endgame challenge (inspired by Pokémon Journeys anime)
- This is the full progression arc: Gyms → Elite Four → Champion → Masters Eight
- Each tier should have noticeably harder teams with better AI strategy

### 9. In-Game Currency
> "It's also not actual money like Pokémon money"

- Pokémon-style currency (PokéDollars or similar) — NOT real money
- Earn from winning battles (wild, gym, PvP, AI)
- Spend on: Poké Balls, Great Balls, Ultra Balls, possibly healing items
- Displayed in player profile/HUD

### 10. Bigger Font
> "Make the font bigger"

- Current font sizes are 6–10px in most places — tiny on a phone
- Scale up: body text ~12px minimum, Pokémon names ~10px minimum, move buttons ~11px, headers bigger proportionally
- Keep the pixel font aesthetic, just bigger. Comfortable to read on a phone without squinting.

### 11. All Pokémon Ever Created
> "Make all of the Pokémon that were ever created into the game"

- Currently Gen 1 only (151). Liam wants **all 1025+** across all 9 generations.
- The data pipeline already uses PokéAPI and can fetch any generation
- **Phased rollout recommended:**
  - **Gen 2** first (Johto, #152–251): adds Dark/Steel types, Sp.Atk/Sp.Def split, new starters
  - **Gen 3–5** next: abilities, natures
  - **Gen 6–9** last: Mega Evolution, Z-Moves, Dynamax, Fairy type
- Consider **unlocking generations as progression rewards** ("You beat the Gen 1 Champion! Gen 2 Pokémon now appear in the wild!")
- Each generation needs: Pokémon data, sprites, moves, type chart updates

### 12. Save All Progress
> "Make sure that you save all the progress"

- Foundation exists (`player_accounts.py` has players + player_pokemon tables)
- Must persist: caught Pokémon with individual levels/XP/moves, gym badges earned, Poké Ball + item inventory, currency balance, journey progress (which gym you're on), team composition
- Auto-save after every meaningful action (catch, battle, evolution, level up)
- Resume seamlessly on return via token

## Implementation Priority

The dependency chain:
1. **Leveling + XP** — foundation for everything (DB schema exists, needs battle integration)
2. **Wild encounters + catching + rarity tiers** — the core loop
3. **Move learning + management** — makes leveling meaningful
4. **Evolution** — reward for leveling
5. **In-game currency + Poké Ball shop** — economy layer
6. **Gym Leaders** — structured progression
7. **Elite Four → Champion → Masters Eight** — endgame
8. **Remove manual picking in Journey Mode** — once the full loop works
9. **Bigger font** — quick UI fix, do anytime
10. **Multi-generation expansion** — biggest scope, layer on last

## Implementation Notes

- **Don't break multiplayer.** Quick Battle mode (pick 6, all Level 50) must always work alongside Journey Mode.
- **Keep it simple.** Liam is 10. UX should be obvious and satisfying — big buttons, clear feedback, animations for catches and evolutions.
- **The battle engine is solid.** Gen 1 damage calc, status effects, type chart, turn resolution all work. Build on top, don't rewrite.
- **Mobile-first.** Liam plays on a phone/tablet. Every UI addition should be touch-friendly with big tap targets.
- **"Press Start 2P" pixel font stays** — just bigger.
- **PokéAPI is the data source** for evolution chains, learnsets, multi-gen Pokémon data.
- **Wild Pokémon levels should scale** near the player's team average so encounters stay relevant.
- **Gym leader teams should be pre-built** with thematic Pokémon at appropriate levels, getting harder as you progress.
