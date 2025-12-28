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
