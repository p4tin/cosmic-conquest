# Cosmic Conquest - Cylon AI Logic
import random
from typing import Dict

class CylonAI:
    DIFFICULTY_PRESETS = {
        "EASY":   {"expansion_threshold": 15, "expansion_cost": 10, "hunter_spawn_turn": 20},
        "MEDIUM": {"expansion_threshold": 11, "expansion_cost": 8,  "hunter_spawn_turn": 15},
        "HARD":   {"expansion_threshold": 8,  "expansion_cost": 6,  "hunter_spawn_turn": 10},
    }

    def __init__(self, width: int, height: int, difficulty: str = "EASY"):
        self.width = width
        self.height = height
        preset = self.DIFFICULTY_PRESETS.get(difficulty.upper(), self.DIFFICULTY_PRESETS["EASY"])
        self.expansion_threshold = preset["expansion_threshold"]
        self.expansion_cost = preset["expansion_cost"]
        self.hunter_spawn_turn = preset["hunter_spawn_turn"]

    def process_turn(self, galaxy: Dict[str, any], player: any) -> str:
        messages = []
        # We operate on a copy of keys to allow adding new sectors (expansion)
        sectors = list(galaxy.values())

        for s in sectors:
            if s.owner == "Cylon":
                # 1. Production: Grow ships based on planets (Rebalanced: 1.5x planets)
                s.enemy_ships += int(s.planets * 1.5)

                # 2. Expansion/Raiding Logic
                # Aggressive Expansion: act whenever the sector can afford it
                if s.enemy_ships > self.expansion_threshold:
                    target_x, target_y = self._get_random_neighbor(s.x, s.y)
                    key = (target_x, target_y)

                    if key not in galaxy:
                        # Expand into empty space
                        from .main import Sector # Local import to avoid circular dependency
                        galaxy[key] = Sector(
                            x=target_x, y=target_y,
                            planets=random.randint(1, 3),
                            owner="Cylon",
                            enemy_ships=self.expansion_cost,
                            scanned=False
                        )
                        s.enemy_ships -= self.expansion_cost
                    else:
                        target = galaxy[key]
                        if target.anomaly == "relic":
                            self._apply_relic_buff(s, messages)
                            continue
                        if target.anomaly is not None:
                            # Black holes and nebulas are not capturable
                            continue
                        if target.owner == "Neutral":
                            # Take over neutral system
                            target.owner = "Cylon"
                            target.enemy_ships = self.expansion_cost
                            s.enemy_ships -= self.expansion_cost
                        elif target.owner == "Player":
                            # Raid/Re-capture player territory
                            # 50% chance to capture if they have more ships
                            if random.random() < 0.5:
                                if getattr(target, 'infrastructure', None) == 'battery':
                                    messages.append(f"CYLON INVASION REPELLED at [{target.x},{target.y}] by Orbital Battery!")
                                    s.enemy_ships -= 5
                                else:
                                    target.owner = "Cylon"
                                    target.enemy_ships = self.expansion_cost + 2
                                    s.enemy_ships -= self.expansion_cost + 2
                                    messages.append(f"CYLON INVASION! Sector [{target.x},{target.y}] was RECAPTURED!")
                            else:
                                damage = random.randint(5, 12) # Increased damage
                                if getattr(target, 'infrastructure', None) == 'battery':
                                    damage = max(0, damage - 6)
                                    messages.append(f"CYLON RAID from [{s.x},{s.y}] mitigated by Battery! Lost {damage} ships.")
                                else:
                                    messages.append(f"CYLON RAID from [{s.x},{s.y}]! Lost {damage} ships.")
                                player.ships -= damage

        return " | ".join(messages) if messages else ""

    def _apply_relic_buff(self, sector, messages):
        sector.enemy_ships += 15
        messages.append(f"CYLON RELIC at [{sector.x},{sector.y}]: +15 ships!")

    def _get_random_neighbor(self, x: int, y: int):
        dx, dy = random.choice([
            (0, 1), (0, -1), (1, 0), (-1, 0), 
            (1, 1), (1, -1), (-1, 1), (-1, -1)
        ])
        nx = max(0, min(self.width - 1, x + dx))
        ny = max(0, min(self.height - 1, y + dy))
        return nx, ny