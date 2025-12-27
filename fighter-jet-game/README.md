# Legion's Space Fight

A fast-paced space shooter game with boss battles, multiple weapons, live multiplayer scoring, and persistent leaderboards.

**Live URL:** https://fighter-jet-game.felican.ai

## Game Features

### Difficulty Modes
| Mode   | Speed | Lives | Enemy Fire Rate |
|--------|-------|-------|-----------------|
| Easy   | 1.6x  | 5     | 1.4x            |
| Medium | 2.0x  | 3     | 2.0x            |
| Hard   | 2.6x  | 2     | 3.0x            |
| Expert | 3.2x  | 1     | 4.0x            |

### Weapons
- **Pistol** - Default weapon, balanced fire rate
- **Machine Gun** - Rapid dual shots
- **Bazooka** - Slow but explosive damage
- **Laser Beam** - Continuous beam attack

Collect weapon power-ups (MG, BZ, LS) during gameplay to unlock.

### Boss Battles
Each level ends with a unique boss fight:
1. **Dark Phantom** - Spread attack pattern
2. **Cyber Kraken** - Spiral attack pattern
3. **Inferno Titan** - Wave attack pattern
4. **Quantum Destroyer** - Random attack pattern
5. **Omega Overlord** - All attack patterns combined

Bosses taunt the player and have dedicated boss music.

### Multiplayer Features
- Enter your name or get a random handle (e.g., "Player_X7K2")
- See other players online in real-time
- Live score updates during gameplay
- Player status indicators (lobby/playing/boss fight)
- Persistent top 10 leaderboard

## How to Play

### Controls
| Action       | Keyboard        | Mobile          |
|--------------|-----------------|-----------------|
| Move         | WASD / Arrows   | Virtual joystick|
| Shoot        | Space           | FIRE button     |
| Swap Weapon  | Q               | SWAP button     |
| Pause        | P / ESC         | Pause button    |

### Gameplay
1. Select difficulty and jet color in the setup modal
2. Destroy enemies, avoid bombs and walls
3. Collect power-ups for shields and weapons
4. Defeat the boss to advance to the next level
5. Score points to climb the leaderboard

## Architecture

### File Structure
```
fighter-jet-game/
├── Dockerfile              # Python 3.11-slim + gunicorn
├── docker-compose.yml      # Docker Compose config
├── requirements.txt        # Flask, flask-cors, gunicorn
├── server.py              # Flask API server
├── fighter-jet-game.html  # Complete game (HTML/CSS/JS)
├── data/
│   └── leaderboard.json   # Persistent leaderboard data
└── README.md
```

### API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Serve game HTML |
| `/fighter-jet-game.html` | GET | Serve game HTML |
| `/api/leaderboard` | GET | Get top 10 scores |
| `/api/leaderboard` | POST | Submit new score |
| `/api/players/join` | POST | Register as active player |
| `/api/players/update` | POST | Update score/status |
| `/api/players/leave` | POST | Leave game session |
| `/api/players/active` | GET | Get all active players |

### Tech Stack
- **Frontend:** Vanilla HTML5 Canvas, Web Audio API
- **Backend:** Flask + gunicorn
- **Container:** Docker with Python 3.11-slim
- **Proxy:** Nginx Proxy Manager
- **Network:** Docker `proxy-network`

## Deployment

### Infrastructure
- **Host:** felican.ai (178.156.205.104)
- **Domain:** games.felican.ai
- **SSL:** Let's Encrypt via NPM
- **Container Port:** 8080 (internal only)

### Deploy Commands
```bash
# Push changes
git push origin main

# Deploy on server
ssh felican.ai
cd /home/dev/legion-scripts/fighter-jet-game
git pull
docker compose up -d --build
```

### View Logs
```bash
ssh felican.ai "docker logs -f fighter-jet-game"
```

### Restart Container
```bash
ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && docker compose restart"
```

## Local Development

```bash
# Run locally
cd fighter-jet-game
python3 server.py

# Access at http://localhost:8080
```

## Data Persistence

Leaderboard data is stored in `./data/leaderboard.json` and mounted as a Docker volume. This persists across container rebuilds.

Active player tracking is in-memory only (resets on container restart). Players are automatically removed after 30 seconds of inactivity.
