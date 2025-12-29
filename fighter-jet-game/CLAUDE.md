# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Legion's Space Fight - a multiplayer space shooter game with boss battles, live spectating, and persistent leaderboards.

**Live URL:** https://fighter-jet-game.felican.ai

## Local Development

```bash
# Start all services (gameserver, Redis, PostgreSQL)
docker compose -f docker-compose.local.yml up -d

# Access at http://localhost:8080

# View logs
docker logs -f fighter-jet-game-local

# Run server directly (no Docker, Redis/Postgres features disabled)
python3 server.py
```

## Deployment

**Production Server:** felican.ai (SSH: `ssh felican.ai`)
**Path:** `/home/dev/legion-scripts/fighter-jet-game`

### Deployment Steps (MUST FOLLOW ALL STEPS)

```bash
# 1. Commit and push changes
git add . && git commit -m "message" && git push origin main

# 2. Pull changes on production
ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && git pull origin main"

# 3. Restart container (ALWAYS required for HTML changes to take effect reliably)
ssh felican.ai "docker compose -f /home/dev/legion-scripts/fighter-jet-game/docker-compose.yml restart fighter-jet-game"

# 4. VERIFY deployment - check that changes are actually in the container
ssh felican.ai "docker exec fighter-jet-game grep 'UNIQUE_STRING_FROM_YOUR_CHANGE' /app/fighter-jet-game.html"
```

### When to Rebuild (not just restart)

```bash
# If Python files changed (server.py, database.py, etc.)
ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && docker compose build --no-cache && docker compose up -d"
```

### CRITICAL: Always Verify Deployments

**After EVERY deployment, you MUST:**
1. Restart or rebuild the container
2. Verify changes are live by grepping for a unique string from your changes inside the container
3. Only report deployment as complete after verification passes

**Do NOT assume volume mounts reflect changes immediately - always restart and verify.**

## Architecture

### File Responsibilities

| File | Purpose |
|------|---------|
| `fighter-jet-game.html` | Complete frontend (HTML/CSS/JS in single file, ~7000 lines) |
| `server.py` | Flask API server with eventlet, all HTTP endpoints, auth middleware |
| `websocket_handler.py` | Flask-SocketIO handlers for real-time multiplayer |
| `redis_client.py` | Redis operations: live players, rooms, matchmaking, spectating |
| `database.py` | PostgreSQL operations: players, sessions, leaderboards, continue keys |
| `backup.py` | Scheduled backups to local JSON and Backblaze B2 |
| `init_db.sql` | PostgreSQL schema (auto-runs on container init) |
| `migrate_data.py` | One-time migration script from JSON to PostgreSQL |

### Data Flow

- **PostgreSQL** (primary): Players, sessions, leaderboards, continue keys, game events, audit logs
- **Redis** (real-time): Active players, game state for spectating, multiplayer rooms, matchmaking queues
- **JSON files** (legacy fallback): `data/leaderboard.json`, `data/player_progress.json`

### Authentication Model

Session-based auth with device fingerprinting:
1. **Anonymous**: Pick username + device fingerprint → temp session
2. **Verified**: Email verification → continue keys, leaderboard name lock
3. **Full**: Password (optional) → multi-device access via email login links

### Continue Key System

- Each player has ONE continue key (12 chars: `FJ-XXXXXXXXX`)
- Keys sent via email only (never displayed on screen)
- Stored hashed in PostgreSQL
- 3 continues per level max, then restart at level beginning
- Key persists in `sessionStorage` for auto-continue within session

### Game Mechanics

- **Boss spawns** after 25 enemy kills per level (not score-based)
- **Fire button**: Tap-to-fire only (no hold-to-fire)
- **Multiplayer modes**:
  - Tag Team (Coop): 2 players vs enemies, relay respawns, shared score
  - 1v1 (Versus): 2 players shoot each other, hazard points, best-of-3

## Debug Modes

Enter these as player name:
- `ending123` - Skip to victory screen
- `invincible` - Unlimited lives

## Environment Variables

Server-side (in `.env` on production):
- `REDIS_URL` - Redis connection string
- `DATABASE_URL` - PostgreSQL connection string
- `DB_PASSWORD` - PostgreSQL password (used in docker-compose.yml)
- `B2_BUCKET`, `B2_KEY_ID`, `B2_APP_KEY` - Backblaze B2 backup credentials
- `RESEND_API_KEY` - Email service for continue keys and login links

## Key API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/api/leaderboard` | GET/POST leaderboard scores |
| `/api/players/active` | Live player list for spectating |
| `/api/player/request-key` | Request continue key via email (one per player) |
| `/api/player/validate-key` | Validate continue key for respawn |
| `/api/auth/request-login-link` | Email login link for multi-device |
| `/api/auth/verify-login-link` | Verify email login token |
| `/api/rooms/create` | Create multiplayer room |
| `/api/rooms/join/{code}` | Join room by 6-char code |
| `/api/matchmaking/join` | Quick match queue |

## Important Notes

- Use `docker compose` (with space), not `docker-compose`
- HTML file is read-only volume mounted in production
- WebSocket requires eventlet worker: `gunicorn --worker-class eventlet -w 1`
- PostgreSQL data persists in `postgres_data` Docker volume
- Redis data persists in `redis_data` Docker volume
- Rate limiting applied to sensitive endpoints (see `RATE_LIMITS` in database.py)

---

## Testing

```bash
# Install test dependencies
./venv/bin/pip install pytest pytest-mock fakeredis eventlet flask-socketio

# Run all tests
./venv/bin/python -m pytest tests/ -v

# Run specific test file
./venv/bin/python -m pytest tests/test_multiplayer.py -v

# Run with coverage
./venv/bin/python -m pytest tests/ --cov=. --cov-report=html
```

Test files:
- `tests/test_server.py` - Flask API endpoint tests
- `tests/test_redis_client.py` - Redis operations tests
- `tests/test_multiplayer.py` - 1v1/coop multiplayer tests

---

## Multiplayer Architecture (1v1 and Coop)

### Key Concepts

**Position Mirroring (Versus Mode Only):**
- Each player sees themselves at BOTTOM, opponent at TOP
- Y positions are mirrored: `mirroredY = logicalHeight - originalY`
- Opponent jet is rotated 180° to face downward
- Applied in `updateOtherPlayer()` and `handleOtherPlayerShoot()`

**WebSocket Flow:**
1. Player creates/joins room → gets `myPlayerId` from auth
2. `connectWebSocket()` joins Socket.IO room with same `playerId`
3. `startPositionSync()` sends position every 50ms via `player_state` event
4. Server broadcasts to room via `game_update` event
5. `handleMultiplayerGameUpdate()` routes by `data.type`

**Critical:** In multiplayer mode, `startGame()` skips `joinAsPlayer()` to preserve the playerId used for room joining.

### Key Code Locations

| Function | File:Line | Purpose |
|----------|-----------|---------|
| `connectWebSocket()` | html:~3625 | WebSocket connection and room join |
| `startPositionSync()` | html:~3946 | Position sync interval (50ms) |
| `updateOtherPlayer()` | html:~3831 | Receives/mirrors opponent position |
| `handleOtherPlayerShoot()` | html:~3874 | Creates opponent bullets (mirrored) |
| `drawOtherPlayer()` | html:~7697 | Renders opponent jet (rotated in versus) |
| `handle_player_state()` | websocket_handler.py:81 | Server broadcasts position |
| `handle_player_shoot()` | websocket_handler.py:116 | Server broadcasts shots |

### Debug Logging

**Server-side (docker logs):**
```bash
ssh felican.ai "docker logs fighter-jet-game -f 2>&1 | grep -E 'CLIENT-LOG|player_state|player_shoot|joined room'"
```

**Client logs sent to server via `/api/debug/log`:**
- `WebSocket room joined` - Confirms room join with playerId
- `Position sync started` - Shows playerId and roomCode
- `First opponent update received` - First position from opponent
- `Created opponent bullet` - Bullet created from opponent shot

### PvP Collision Detection

Located at `fighter-jet-game.html:~9037`:
- Our bullets hit opponent: `!bullet.isEnemy && !bullet.fromPartner`
- Opponent bullets hit us: `bullet.fromPartner`
- Requires `otherPlayer.id` set and `versusState.roundActive` true
