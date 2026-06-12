# Cosmic Conquest

A retro 1980s-styled turn-based strategy game in the browser. Pilot a lone flagship, colonize neutral worlds, fight Cylon raiders, and survive the Hunter-Killer long enough to liberate the galaxy.

The project is a **FastAPI** backend with **Pydantic** models and a **Vanilla HTML/CSS/JS** frontend served as static files.

## Quick Start

Requires Python 3.10+ and a reachable Redis instance (see `.env`).

```bash
./setup.sh                  # creates ./venv and installs requirements
./manage.sh start           # launches the server in the background
open http://127.0.0.1:8000   # play
./manage.sh stop            # shut down
```

The server writes its PID to `.app.pid` and logs to `app.log`. Use `./manage.sh restart` or `./manage.sh status` for the other lifecycle commands.

## Project Layout

```
cosmic-conquest/
├── src/
│   ├── main.py        FastAPI app, Game class, REST endpoints
│   └── cylon.py       CylonAI: enemy production, expansion, combat
├── static/
│   ├── index.html     Page shell + difficulty modal markup
│   ├── index.css      All styles (CRT theme, modals, map)
│   └── index.js       Client logic, API calls, render, key bindings
├── manage.sh          Background start/stop wrapper
├── setup.sh           Virtualenv provisioning
├── requirements.txt   Pinned runtime dependencies
├── CLAUDE.md          Project notes for AI coding assistants
└── README.md          This file
```

## How to Play

A full controls reference lives in the in-game manual (press **M**). The short version:

- **WASD** / arrows: move (costs fuel)
- **R**: report the sector you're standing on
- **P**: pulse scan (reveals a 5×5 area, costs ships + fuel)
- **C**: colonize a neutral sector
- **F**: fight Cylon ships in your sector
- **X**: scrap 10 ships for 25 fuel
- **H**: emergency hull repair
- **1/2/3**: build a shipyard, sensor array, or orbital battery on a colonized sector
- **N**: start a new game (pops the difficulty picker)
- **M**: open the Commander's Manual

Goal: **take over every Cylon base**. Lose condition: ships or hull drop to zero.

### Difficulty

Press **N** to start a new game — a modal asks for **EASY / MEDIUM / HARD**. Higher difficulties lower the Cylon expansion threshold and cost (so they attack more often and recover faster) and bring the Hunter-Killer online earlier. EASY preserves the original casual pacing.

### Anomalies

Three anomaly types spawn at low density in every game and stay neutral forever (neither side can capture them):

- **B (Black Hole)** — visible from the start. Damages hull and teleports you somewhere random.
- **N (Nebula)** — hides you from the Hunter-Killer. Disables your sensors while inside.
- **R (Ancient Relic)** — grants a permanent power-up. Both the player and the Cylons can claim the same relic independently (each side gets one buff from it).

## Architecture

### Backend (`src/main.py`)

- **`Game`** is the core state container. It owns the galaxy dict, the player, the CylonAI instance, and the hunter state. It exposes `move`, `pulse`, `colonize`, `fight`, `build`, `repair`, and `scrap` methods, each ending in a call to `end_turn` to advance the simulation.
- **`GameState`** (Pydantic) is the serializable projection of `Game` returned to the client. Tuple keys are converted to `"x,y"` strings so the dict round-trips through JSON.
- **Difficulty** is stored on `GameState` and passed into `CylonAI`, which reads it as instance attributes (`expansion_threshold`, `expansion_cost`, `hunter_spawn_turn`).
- **Persistence** is Redis-backed via `save_game` / `get_game`. Each `session_id` is a UUID; the entire serialized `GameState` is stored under `session:<id>` with a 24-hour TTL.
- **Session safety**: `end_turn` snapshots the set of valid sector keys before invoking the AI and deletes any sectors the AI created outside that set, so a malicious or buggy AI can't smuggle new cells into the galaxy.

### Cylon AI (`src/cylon.py`)

`CylonAI` runs once per turn. For each Cylon-owned sector:

1. **Production**: gain `int(planets * 1.5)` ships.
2. **Expansion**: if the sector has more than the difficulty's `expansion_threshold` ships, pick a random 8-direction neighbor and act:
   - empty cell → create a new Cylon sector
   - neutral → take it over
   - relic → claim the relic buff, leave the sector alone
   - black hole / nebula → skip (anomalies are not capturable)
   - player → 50% chance to capture (consumes 12 ships), else raid for 5–12 player ship damage. Orbital batteries in the target halve raid damage and block captures.
3. **Relic buff** for the Cylon: +15 ships on the claiming sector.

### Frontend (`static/`)

- **`index.html`** — page shell, HUD, map, log, controls, and the two modals (manual + difficulty picker).
- **`index.css`** — single CRT-themed stylesheet covering layout, the retro glow, the two modal dialogs, and the difficulty options.
- **`index.js`** — keyboard handler, API client, render function, manual markdown, and the difficulty modal logic. The manual markdown is parsed at load time via the `marked` CDN script.

### REST Endpoints

| Method | Path                | Body / Query                     | Purpose                                |
| ------ | ------------------- | -------------------------------- | -------------------------------------- |
| GET    | `/`                 | —                                | Serves `index.html`                    |
| GET    | `/api/state`        | `?session_id=...`                | Reload an existing game                |
| POST   | `/api/new_game`     | `?difficulty=EASY\|MEDIUM\|HARD` | Start a new session, return GameState  |
| POST   | `/api/move`         | `{ session_id, direction }`      | Move one tile (WASD)                   |
| POST   | `/api/pulse`        | `{ session_id }`                 | Pulse scan                             |
| POST   | `/api/report`       | `{ session_id }`                 | Inspect current sector                 |
| POST   | `/api/fight`        | `{ session_id }`                 | Fight Cylons in current sector         |
| POST   | `/api/colonize`     | `{ session_id }`                 | Take over the current sector           |
| POST   | `/api/scrap`        | `{ session_id }`                 | Convert 10 ships into 25 fuel          |
| POST   | `/api/build`        | `{ session_id, build_type }`     | Build infrastructure (`shipyard`/`sensor`/`battery`) |
| POST   | `/api/repair`       | `{ session_id }`                 | Spend 20 ships to repair +20% hull     |

## Environment

Create a `.env` in the project root with your Redis connection details:

```
REDIS_HOST=...
REDIS_PORT=...
REDIS_PASSWORD=...
REDIS_USER=default
```

`python-dotenv` loads this file at startup.

### Deploying Behind a Reverse-Proxy Prefix

If you expose the app under a path prefix (e.g. Tailscale Funnel with `-set-path /cosmic-conquest`, or nginx with a `location /cosmic-conquest/` block), set the `BASE_PATH` env var to the same prefix when launching the server:

```bash
export BASE_PATH=/cosmic-conquest
./manage.sh start
```

A small ASGI middleware (`PrefixMiddleware` in `src/main.py`) reads `BASE_PATH` and strips the prefix from incoming request scopes so the existing route table is unchanged. Leave `BASE_PATH` unset (or empty) for local development at `http://127.0.0.1:8000/`.

## License

Brought to you by Bitbandit.
