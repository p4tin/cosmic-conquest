# Cosmic Conquest - Main Game Server
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Dict, Optional
import os
import random
import uvicorn
import json
import redis
from dotenv import load_dotenv

try:
    from .cylon import CylonAI
    from . import auth
    from .auth import BearerTokenMiddleware
except ImportError:
    from cylon import CylonAI
    import auth
    from auth import BearerTokenMiddleware

# Load Redis credentials
load_dotenv()

r = redis.Redis(
    host=os.getenv('REDIS_HOST'),
    port=int(os.getenv('REDIS_PORT')),
    password=os.getenv('REDIS_PASSWORD'),
    username=os.getenv('REDIS_USER'),
    decode_responses=True
)


@asynccontextmanager
async def lifespan(app):
    # Startup: validate required env vars
    await validate_env()
    yield
    # Shutdown: nothing to clean up


async def validate_env():
    """Raise RuntimeError if required Gmail env vars are missing."""
    missing = [v for v in ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD") if not os.getenv(v)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {missing}")


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "..", "static")), name="static")

MAX_FUEL = 35

BASE_PATH = os.getenv("BASE_PATH", "").rstrip("/")


class PrefixMiddleware:
    """Strip BASE_PATH from incoming request scope so existing routes work
    unchanged, and re-add it on the response so redirects and Location
    headers stay correct."""

    def __init__(self, app, prefix: str = ""):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if self.prefix and scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path.startswith(self.prefix):
                scope = dict(scope)
                scope["path"] = path[len(self.prefix):] or "/"
                scope["raw_path"] = scope["raw_path"][len(self.prefix):] or b"/"
        await self.app(scope, receive, send)


if BASE_PATH:
    app.add_middleware(PrefixMiddleware, prefix=BASE_PATH)

# Inject the shared Redis client into the auth module (avoids circular import)
auth.set_redis(r)

# Register auth router (/api/auth/*)
app.include_router(auth.router)

# BearerTokenMiddleware is added after PrefixMiddleware so it executes inside
# it (FastAPI applies middleware in reverse registration order — last added
# runs first, so this one wraps the inner app after the prefix is stripped).
app.add_middleware(BearerTokenMiddleware, redis_client=r)


# --- Domain Models ---
class Sector(BaseModel):
    x: int
    y: int
    planets: int
    owner: str
    enemy_ships: int
    scanned: bool
    is_gas_cloud: bool = False
    anomaly: Optional[str] = None
    infrastructure: Optional[str] = None

class Player(BaseModel):
    x: int
    y: int
    ships: int
    turns: int
    alive: bool
    fuel: int = 20
    hull: int = 100
    in_nebula: bool = False
    combat_boost: bool = False
    fuel_efficiency: bool = False

class GameState(BaseModel):
    player: Player
    galaxy: Dict[str, Sector]
    log: str
    game_over: bool
    victory: bool
    planets_owned: int = 0
    gas_clouds_owned: int = 0
    hunter_active: bool = False
    hunter_x: int = -1
    hunter_y: int = -1
    hunter_cooldown: int = 0
    ls_maintenance: int = 0
    difficulty: str = "EASY"

class ActionRequest(BaseModel):
    direction: Optional[str] = None
    build_type: Optional[str] = None

# --- Game Logic Class ---
class Game:
    def __init__(self, difficulty: str = "EASY"):
        self.width = 20
        self.height = 10
        self.player = Player(x=0, y=0, ships=50, turns=1, alive=True, fuel=25, hull=100)
        self.galaxy = {}  # Using Tuple[int, int] for performance
        self.difficulty = difficulty
        if difficulty == "EASY":
            self.log = "WELCOME COMMANDER. DIFFICULTY: EASY. PRESS 'N' FOR A NEW GAME."
        elif difficulty == "MEDIUM":
            self.log = "WELCOME COMMANDER. DIFFICULTY: MEDIUM. CYLONS ARE AGGRESSIVE. PRESS 'N' FOR A NEW GAME."
        else:
            self.log = "WELCOME COMMANDER. DIFFICULTY: HARD. THE HUNTER COMES EARLY. PRESS 'N' FOR A NEW GAME."
        self.game_over = False
        self.victory = False
        self.hunter_active = False
        self.hunter_x = -1
        self.hunter_y = -1
        self.hunter_cooldown = 0
        self.cylon_ai = CylonAI(self.width, self.height, self.difficulty)
        self._init_galaxy()

    def _init_galaxy(self):
        # Regular sectors
        for _ in range(45):
            rx = random.randint(0, self.width - 1)
            ry = random.randint(0, self.height - 1)
            key = (rx, ry)
            
            owner = "Cylon" if random.random() > 0.85 else "Neutral"
            enemy_ships = 15 if owner == "Cylon" else 0
            
            self.galaxy[key] = Sector(
                x=rx, y=ry,
                planets=random.randint(1, 3),
                owner=owner,
                enemy_ships=enemy_ships,
                scanned=False,
                is_gas_cloud=False
            )
        
        # Gas clouds (reduced to 2, scattered in 2 zones of 5x5)
        for gx in range(2):
            rx = random.randint(gx * 5, (gx + 1) * 5 - 1)
            ry = random.randint(0, self.height - 1)
            key = (rx, ry)

            if key not in self.galaxy:
                self.galaxy[key] = Sector(
                    x=rx, y=ry,
                    planets=0,
                    owner="Neutral",
                    enemy_ships=0,
                    scanned=False,
                    is_gas_cloud=True
                )
            else:
                self.galaxy[key].is_gas_cloud = True

        # Add Anomalies on truly empty cells
        empty_cells = [(x, y) for x in range(self.width) for y in range(self.height)
                       if (x, y) not in self.galaxy]
        random.shuffle(empty_cells)

        for anomaly_type, count in [("black_hole", 1), ("nebula", 1), ("relic", 1)]:
            for _ in range(min(count, len(empty_cells))):
                ax, ay = empty_cells.pop()
                self.galaxy[(ax, ay)] = Sector(
                    x=ax, y=ay,
                    planets=0,
                    owner="Neutral",
                    enemy_ships=0,
                    scanned=False,
                    is_gas_cloud=False,
                    anomaly=anomaly_type
                )

    def to_state(self) -> GameState:
        p_count = sum(s.planets for s in self.galaxy.values() if s.owner == "Player")
        g_count = sum(1 for s in self.galaxy.values() if s.owner == "Player" and s.is_gas_cloud)
        
        m_loss = max(1, self.player.ships // 20) if self.player.ships > 0 else 0
        
        # Convert tuple keys back to strings for JSON serialization
        return GameState(
            player=self.player,
            galaxy={f"{k[0]},{k[1]}": v for k, v in self.galaxy.items()},
            log=self.log,
            game_over=self.game_over,
            victory=self.victory,
            planets_owned=p_count,
            gas_clouds_owned=g_count,
            hunter_active=self.hunter_active,
            hunter_x=self.hunter_x,
            hunter_y=self.hunter_y,
            ls_maintenance=m_loss,
            difficulty=self.difficulty
        )

    def load_from_state(self, state: GameState):
        self.player = state.player
        self.log = state.log
        self.game_over = state.game_over
        self.victory = state.victory
        self.hunter_active = state.hunter_active
        self.hunter_x = state.hunter_x
        self.hunter_y = state.hunter_y
        self.difficulty = getattr(state, 'difficulty', 'EASY')
        self.cylon_ai = CylonAI(self.width, self.height, self.difficulty)
        # Convert string keys back to tuples
        self.galaxy = {}
        for k_str, s in state.galaxy.items():
            x, y = map(int, k_str.split(','))
            self.galaxy[(x, y)] = s

    def check_win_loss(self):
        # Clean up empty Cylon sectors so the victory check can fire
        for s in self.galaxy.values():
            if s.owner == "Cylon" and s.enemy_ships <= 0:
                s.owner = "Neutral"

        cylon_count = sum(1 for s in self.galaxy.values() if s.owner == "Cylon")
        if self.player.ships <= 0 or self.player.hull <= 0:
            self.player.alive = False
            self.game_over = True
            if self.player.hull <= 0:
                self.log = "CRITICAL FAILURE: Life support failure. The crew has perished."
            else:
                self.log = "MISSION FAILED: The Cylon fleet has overwhelmed you."
        elif cylon_count == 0 and self.player.turns > 2:
            self.victory = True
            self.game_over = True
            self.log = "GALAXY LIBERATED! Total Victory achieved."

    def end_turn(self):
        if not self.player.alive or self.game_over: return
        self.player.turns += 1
        
        # Maintenance: Lose 1 ship per 20 ships every 10 turns
        if self.player.turns % 10 == 0:
            m_loss = max(1, self.player.ships // 20)
            self.player.ships -= m_loss
            self.log += f" | LIFE SUPPORT MAINTENANCE: -{m_loss} ships."

        production = 0
        for loc, s in list(self.galaxy.items()):
            if s.owner == "Player":
                production += s.planets * 1 # Rebalanced production
                if s.infrastructure == "shipyard":
                    production += 3
                elif s.infrastructure == "sensor":
                    for dy in range(-2, 3):
                        for dx in range(-2, 3):
                            t_loc = (loc[0] + dx, loc[1] + dy)
                            if t_loc in self.galaxy:
                                self.galaxy[t_loc].scanned = True
        
        # Snapshot valid sectors to prevent AI from creating new ones
        valid_sectors = set(self.galaxy.keys())

        # Process Cylon AI
        ai_report = self.cylon_ai.process_turn(self.galaxy, self.player)
        
        # Cleanup: Remove any illegal sectors added by AI
        for loc in list(self.galaxy.keys()):
            if loc not in valid_sectors:
                del self.galaxy[loc]

        self.player.ships += production
        if ai_report:
            self.log += " | " + ai_report
            
        # Hunter Logic
        hst = self.cylon_ai.hunter_spawn_turn
        if self.hunter_active and self.player.turns > hst and self.player.turns % 2 == 0:
            if not self.player.in_nebula:
                hx, hy = self.hunter_x, self.hunter_y
                px, py = self.player.x, self.player.y
                if hx < px: hx += 1
                elif hx > px: hx -= 1
                if hy < py: hy += 1
                elif hy > py: hy -= 1
                self.hunter_x, self.hunter_y = hx, hy

                # Hunter-black hole collision
                hunter_loc = (self.hunter_x, self.hunter_y)
                if hunter_loc in self.galaxy and self.galaxy[hunter_loc].anomaly == "black_hole":
                    self.hunter_active = False
                    self.hunter_cooldown = 20
                    self.log += " | HUNTER FELL INTO BLACK HOLE! Removed for 20 turns."

                if self.hunter_active and (self.hunter_x, self.hunter_y) == (self.player.x, self.player.y):
                    loss = random.randint(15, 30)
                    if self.player.combat_boost: loss = int(loss * 0.7)

                    loc = (self.player.x, self.player.y)
                    if loc in self.galaxy and self.galaxy[loc].infrastructure == "battery":
                        loss = int(loss * 0.4)
                        self.log += f" | HUNTER ENGAGED! Battery defended! Lost {loss} ships."
                    else:
                        self.log += f" | HUNTER ENGAGED! Massive damage! Lost {loss} ships."

                    self.player.ships -= loss
                    self.hunter_x = max(0, self.hunter_x - 3)
        elif not self.hunter_active and self.hunter_cooldown > 0:
            self.hunter_cooldown -= 1
            if self.hunter_cooldown == 0:
                self.hunter_active = True
                self.hunter_x = self.width - 1
                self.hunter_y = self.height - 1
                self.log += " | WARNING: CYLON HUNTER-KILLER RESPAWNED!"
        elif not self.hunter_active and self.player.turns == hst and self.hunter_cooldown == 0:
            self.hunter_active = True
            self.hunter_x = self.width - 1
            self.hunter_y = self.height - 1
            self.log += " | WARNING: CYLON HUNTER-KILLER SPAWNED!"

        self.check_win_loss()

    def move(self, direction: str):
        if self.game_over: return
        
        if self.player.fuel > 0:
            moved = False
            if direction == 'w' and self.player.y > 0:
                self.player.y -= 1; moved = True
            elif direction == 's' and self.player.y < self.height - 1:
                self.player.y += 1; moved = True
            elif direction == 'a' and self.player.x > 0:
                self.player.x -= 1; moved = True
            elif direction == 'd' and self.player.x < self.width - 1:
                self.player.x += 1; moved = True
                
            if moved:
                fuel_cost = 1 if self.player.fuel_efficiency else 2
                self.player.fuel -= fuel_cost
                self.log = "Navigating to new coordinates..."
                
                # Check for anomalies and gas clouds
                loc = (self.player.x, self.player.y)
                if loc in self.galaxy:
                    s = self.galaxy[loc]
                    if s.is_gas_cloud:
                        self.player.fuel = min(MAX_FUEL, self.player.fuel + 25)
                        s.scanned = True
                        self.log += " GAS CLOUD REACHED. Tanks refilled!"
                    
                    if s.anomaly == "nebula":
                        self.player.in_nebula = True
                        self.log += " ENTERED NEBULA: Hidden from Cylons. Sensors disabled."
                        s.scanned = True
                    else:
                        self.player.in_nebula = False

                    if s.anomaly == "black_hole":
                        self.player.hull -= 15
                        self.player.x = random.randint(0, self.width - 1)
                        self.player.y = random.randint(0, self.height - 1)
                        self.player.in_nebula = False
                        self.log += " BLACK HOLE! Hull heavily damaged and teleported randomly!"
                        s.scanned = True

                    if s.anomaly == "relic":
                        buff = random.choice([
                            "combat_boost", "fuel_efficiency",
                            "extra_fuel", "warp_jump",
                            "ship_boost", "hull_repair"
                        ])
                        if buff == "combat_boost":
                            self.player.combat_boost = True
                            self.log += " ANCIENT RELIC FOUND! Weapons upgraded."
                        elif buff == "fuel_efficiency":
                            self.player.fuel_efficiency = True
                            self.log += " ANCIENT RELIC FOUND! Engine efficiency improved."
                        elif buff == "extra_fuel":
                            self.player.fuel = min(MAX_FUEL, self.player.fuel + 25)
                            self.log += " ANCIENT RELIC FOUND! Fuel reserves increased."
                        elif buff == "warp_jump":
                            self.player.x = random.randint(0, self.width - 1)
                            self.player.y = random.randint(0, self.height - 1)
                            self.log += " ANCIENT RELIC FOUND! Warp drive activated - random jump!"
                        elif buff == "ship_boost":
                            self.player.ships += 10
                            self.log += " ANCIENT RELIC FOUND! Ship reinforcements arrived."
                        else:  # hull_repair
                            self.player.hull = min(100, self.player.hull + 20)
                            self.log += " ANCIENT RELIC FOUND! Hull repairs completed."
                        s.scanned = True
                else:
                    self.player.in_nebula = False
                
                self.end_turn()
        else:
            # DRIFTING LOGIC (Penalty: Lose Hull)
            self.player.hull -= 2
            self.log = "OUT OF FUEL! Your ship drifts... HULL INTEGRITY DROPPING!"
            
            # Pick a random direction
            dr = random.choice(['w', 's', 'a', 'd'])
            if dr == 'w' and self.player.y > 0: self.player.y -= 1
            elif dr == 's' and self.player.y < self.height - 1: self.player.y += 1
            elif dr == 'a' and self.player.x > 0: self.player.x -= 1
            elif dr == 'd' and self.player.x < self.width - 1: self.player.x += 1
            
            # Check for gas cloud
            loc = (self.player.x, self.player.y)
            if loc in self.galaxy and self.galaxy[loc].is_gas_cloud:
                self.player.fuel = min(MAX_FUEL, self.player.fuel + 25)
                self.galaxy[loc].scanned = True
                self.log = "STUMBLED into a Gas Cloud while drifting! Refueled."
            
            self.end_turn()

    def pulse(self):
        if self.game_over: return
        if self.player.in_nebula:
            self.log = "SENSORS OFFLINE: Nebula interference prevents pulsing."
            return
        
        fuel_cost = 5 if self.player.fuel_efficiency else 10
        if self.player.ships >= 10 and self.player.fuel >= fuel_cost:
            self.player.ships -= 10
            self.player.fuel -= fuel_cost
            count = 0
            # Rebalanced Pulse: 5x5 area (radius 2)
            for dy in range(-2, 3):
                for dx in range(-2, 3):
                    tx, ty = self.player.x + dx, self.player.y + dy
                    loc = (tx, ty)
                    if loc in self.galaxy:
                        self.galaxy[loc].scanned = True
                        count += 1
            self.log = f"PULSE FIRED. Revealed {count} sectors. Cost: 10 Ships, {fuel_cost} Fuel."
        else:
            self.log = f"INSUFFICIENT RESOURCES FOR PULSE! (Requires 10 Ships, {fuel_cost} Fuel)"

    def report(self):
        loc = (self.player.x, self.player.y)
        if loc in self.galaxy:
            s = self.galaxy[loc]
            s.scanned = True
            inf = f" | Infra: {s.infrastructure}" if s.infrastructure else ""
            anm = f" | Anomaly: {s.anomaly}" if s.anomaly else ""
            self.log = f"REPORT: {s.planets} Planets | Enemies: {s.enemy_ships} | Owner: {s.owner}{inf}{anm}"
        else:
            self.log = "Deep Space: Nothing here."

    def fight(self):
        if self.game_over: return
        loc = (self.player.x, self.player.y)
        if loc in self.galaxy:
            s = self.galaxy[loc]
            if s.enemy_ships > 0:
                # Proportional Combat
                p_loss = random.randint(1, max(1, s.enemy_ships // 5))
                if self.player.combat_boost:
                    p_loss = max(0, int(p_loss * 0.7))
                e_loss = random.randint(5, 12)
                if self.player.combat_boost:
                    e_loss += 5
                
                s.enemy_ships = max(0, s.enemy_ships - e_loss)
                self.player.ships -= p_loss
                
                if s.enemy_ships == 0:
                    self.log = f"VICTORY! Enemy eliminated. Lost {p_loss} ships."
                else:
                    self.log = f"COMBAT! Enemy: {s.enemy_ships} left. You lost {p_loss}."
            else:
                self.log = "No enemies."
        else:
            self.log = "Empty space."

    def colonize(self):
        if self.game_over: return
        loc = (self.player.x, self.player.y)
        if loc in self.galaxy:
            s = self.galaxy[loc]
            if s.enemy_ships <= 0:
                if s.owner != "Player":
                    s.owner = "Player"
                    s.scanned = True
                    self.log = "SYSTEM COLONIZED. Production increasing..."
                    self.end_turn()
                else:
                    self.log = "SYSTEM ALREADY COLONIZED."
            else:
                self.log = "CANNOT COLONIZE: HOSTILES IN SECTOR!"
        else:
            self.log = "Nothing to colonize."

    def build(self, build_type: str):
        if self.game_over: return
        loc = (self.player.x, self.player.y)
        if loc in self.galaxy:
            s = self.galaxy[loc]
            if s.owner != "Player":
                self.log = "MUST COLONIZE SECTOR BEFORE BUILDING."
                return
            if s.infrastructure:
                self.log = "SECTOR ALREADY HAS INFRASTRUCTURE."
                return
            if self.player.ships >= 20:
                self.player.ships -= 20
                s.infrastructure = build_type
                self.log = f"CONSTRUCTED {build_type.upper()}. Cost: 20 Ships."
                self.end_turn()
            else:
                self.log = "INSUFFICIENT SHIPS (20 REQ) TO BUILD."
        else:
            self.log = "Cannot build in deep space."

    def repair(self):
        if self.game_over: return
        if self.player.hull >= 100:
            self.log = "HULL IS ALREADY AT MAXIMUM INTEGRITY."
            return
        if self.player.ships >= 20:
            self.player.ships -= 20
            self.player.hull = min(100, self.player.hull + 20)
            self.log = "EMERGENCY REPAIRS COMPLETE: +20% Hull. Cost: 20 Ships."
            self.end_turn()
        else:
            self.log = "INSUFFICIENT SHIPS FOR REPAIRS! (Requires 20)"

# --- Game Session Management ---
def get_game(email: str) -> Game:
    game_json = r.get(f"session:{email}")
    if not game_json:
        raise HTTPException(status_code=404, detail="Session not found")
    
    state = GameState.model_validate_json(game_json)
    game = Game()
    game.load_from_state(state)
    return game

def save_game(email: str, game: Game):
    state_json = game.to_state().model_dump_json()
    r.set(f"session:{email}", state_json, ex=604800)  # 7-day expiry

# --- Endpoints ---
@app.get("/")
async def read_root():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "static", "index.html"))

@app.get("/api/state")
def get_state(request: Request):
    email = request.state.player_email
    game = get_game(email)
    return game.to_state()

@app.post("/api/new_game")
def new_game(request: Request, difficulty: str = Query("EASY")):
    email = request.state.player_email
    difficulty = difficulty.upper()
    if difficulty not in CylonAI.DIFFICULTY_PRESETS:
        difficulty = "EASY"
    game = Game(difficulty=difficulty)
    save_game(email, game)
    return game.to_state()

@app.post("/api/move")
def move_player(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    game.move(req.direction)
    save_game(email, game)
    return game.to_state()

@app.post("/api/pulse")
def pulse_scan(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    game.pulse()
    save_game(email, game)
    return game.to_state()

@app.post("/api/report")
def report_sector(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    game.report()
    save_game(email, game)
    return game.to_state()

@app.post("/api/fight")
def fight_sector(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    game.fight()
    save_game(email, game)
    return game.to_state()

@app.post("/api/colonize")
def colonize_sector(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    game.colonize()
    save_game(email, game)
    return game.to_state()

@app.post("/api/scrap")
def scrap_ships(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    if game.game_over: return game.to_state()
    if game.player.ships >= 10:
        game.player.ships -= 10
        game.player.fuel += 25
        game.log = "SCRAPPED 10 SHIPS: Converted to 25 Fuel."
        game.end_turn()
    else:
        game.log = "NOT ENOUGH SHIPS TO SCRAP! (Requires 10)"
    save_game(email, game)
    return game.to_state()

@app.post("/api/build")
def build_infrastructure(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    if req.build_type:
        game.build(req.build_type)
    save_game(email, game)
    return game.to_state()

@app.post("/api/repair")
def repair_hull(req: ActionRequest, request: Request):
    email = request.state.player_email
    game = get_game(email)
    game.repair()
    save_game(email, game)
    return game.to_state()

if __name__ == "__main__":
    print("\n🚀 Cosmic Conquest Server Running!")
    print("👉 Open http://127.0.0.1:8000 in your browser to play.\n")
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, reload=True)
