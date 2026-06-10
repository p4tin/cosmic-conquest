# Cosmic Conquest: Development Notes

## Current State
A turn-based strategy game with a **FastAPI** backend and a **Vanilla HTML/CSS/JS** frontend served as static files. Sessions are persisted in Redis (24h TTL); game state is round-tripped through a Pydantic `GameState` model.

### Architecture
- **Backend** — `src/main.py` defines the `Game` class (state, sector dict, player, Cylon AI, hunter) and the FastAPI routes under `/api/*`. `to_state` / `load_from_state` convert between the in-memory `Game` and the JSON-serializable `GameState`.
- **Cylon AI** — `src/cylon.py` defines `CylonAI`, a per-turn AI that produces, expands into random neighbors, captures neutrals, raids player sectors, and claims relic buffs. Difficulty is read from instance attributes set in `__init__`.
- **Frontend** — `static/index.html` (shell + modals), `static/index.css` (all styles), `static/index.js` (keyboard handler, render, API client, manual). The manual markdown is parsed at load time via the `marked` CDN script.
- **Session safety** — `end_turn` snapshots the set of valid sector keys before invoking the Cylon AI and deletes any sectors the AI created outside that snapshot, so the AI cannot smuggle new cells into the galaxy.
- **Anomaly invariant** — black holes, nebulas, and relics always stay `owner="Neutral"`. The Cylon AI is explicitly forbidden from claiming them; the player cannot colonize a sector with an anomaly. The relic's `anomaly` field is never cleared, so both sides can claim the same relic independently and receive one buff each.
- **Difficulty** — `Game(difficulty="EASY"|"MEDIUM"|"HARD")` is plumbed through `GameState` to `CylonAI`. The three tiers tune `expansion_threshold` (15/11/8), `expansion_cost` (10/8/6), and `hunter_spawn_turn` (20/15/10). The probability gate (`random.random() < 0.4`) was removed — Cylons with sufficient ships act every turn.

### Functional Features
- WASD/arrow movement, drifting when out of fuel (hull -2 per drift).
- Pulse scan (5×5 reveal, costs ships + fuel).
- Sector colonization and per-planet ship production.
- Combat with proportional ship-loss math and orbital-battery mitigation.
- Buildable infrastructure: shipyard, sensor array, orbital battery.
- Three anomaly types: black hole (hull damage + teleport, always visible), nebula (hides from Hunter, disables sensors), ancient relic (random permanent buff; both sides can claim).
- Hunter-Killer: spawns at a difficulty-dependent turn, chases the player, can fall into a black hole (20-turn cooldown + respawn).
- Three difficulty tiers selected via a modal that appears on **N**.

## Future Improvements & Recommendations

### 1. Persistence Layer
Game state currently lives in Redis with a 24h TTL. Sessions expire on their own, but a slow leak or a Redis restart wipes everything. Options to consider:
- **Longer TTL + manual save endpoint** for players who want their game to survive.
- **SQLite or JSON snapshot** as a backup if Redis is unreachable at boot.

### 2. Advanced AI Pathfinding
Cylon expansion is still purely random neighbors. They never coordinate, never reinforce, never target the player. Worth considering:
- **A* pathfinding** so the AI can hunt the player's home sector.
- **Local fleet aggregation** — a Cylon sector that has Cylon neighbors should pool their ships before attacking a player sector.

### 3. Testing
No automated tests. High-leverage places to start:
- `Game._init_galaxy` should produce exactly the right counts (45 regular + 2 gas + 1 each of BH/nebula/relic).
- `CylonAI.process_turn` should never create sectors outside the valid snapshot, never reduce a sector's ship count below 0, and never overwrite an anomaly.
- `check_win_loss` should fire when `cylon_count == 0` and not before turn 2 (the `> 2` guard).

### 4. UI/UX Enhancements
- **WebSockets** for the event log so Cylon raids and Hunter hits show up without polling.
- **CSS scanline shader** and **sound effects** to lean further into the 1980s aesthetic.
- **Save/load buttons** if persistence gets added.

### 5. Tech Debt
- The `static/` files are now split (HTML / CSS / JS) but the manual markdown is still embedded as a JS template literal. Consider moving it to a `.md` file and fetching it.
- `manage.sh` has a hardcoded `APP_DIR` of `/Users/paulfortin/Documents/cosmic-conquest` that doesn't match this checkout (`/Users/p4tin/Dropbox/code/cosmic-conquest`). Worth parameterizing.
- The `.env` file contains live Redis credentials. Add a `.env.example` and confirm `.env` is gitignored.

---
*Maintained with Claude Code.*
