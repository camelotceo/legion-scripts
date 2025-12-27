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

```bash
# Deploy changes
git add . && git commit -m "message" && git push origin main
ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && git pull origin main"

# Rebuild containers (if Python files changed)
ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && docker compose build --no-cache && docker compose up -d"
```

**Note:** HTML-only changes take effect immediately after `git pull` (volume mounted).

## Architecture

### File Responsibilities

| File | Purpose |
|------|---------|
| `fighter-jet-game.html` | Complete frontend (HTML/CSS/JS in single file, ~7000 lines) |
| `server.py` | Flask API server, all HTTP endpoints |
| `websocket_handler.py` | Flask-SocketIO handlers for real-time multiplayer |
| `redis_client.py` | Redis operations: live players, rooms, matchmaking, spectating |
| `database.py` | PostgreSQL operations: leaderboards, player history, sessions |
| `backup.py` | Scheduled backups to local JSON and Backblaze B2 |

### Data Flow

- **Redis** (real-time): Active players, game state for spectating, multiplayer rooms, matchmaking queues
- **JSON files** (production): `data/leaderboard.json`, `data/player_progress.json`
- **PostgreSQL** (local dev only): Leaderboards, player profiles, game sessions - not deployed to production

### Multiplayer Modes

1. **Tag Team (Coop)**: 2 players vs enemies, relay-style respawns (5s delay, max 3 per player), shared score
2. **1v1 (Versus)**: 2 players shoot each other, hazard point system, best-of-3 rounds

Multiplayer uses WebSocket for real-time position sync (50ms intervals) and room state management.

### Continue Key System

Players who die can request a continue key via email. Keys are formatted `FJ-XXXXXX` and tracked in `data/player_progress.json` with limited respawns per key.

## Debug Modes

Enter these as player name:
- `ending123` - Skip to victory screen
- `invincible` - Unlimited lives

## Environment Variables

Server-side (in `.env` on production):
- `REDIS_URL` - Redis connection string
- `DATABASE_URL` - PostgreSQL connection string (local dev only)
- `B2_BUCKET`, `B2_KEY_ID`, `B2_APP_KEY` - Backblaze B2 backup credentials
- `RESEND_API_KEY` - Email service for continue keys

## Key API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/api/leaderboard` | GET/POST leaderboard scores |
| `/api/players/active` | Live player list for spectating |
| `/api/player/request-key` | Request continue key via email |
| `/api/player/validate-key` | Validate continue key for respawn |
| `/api/rooms/create` | Create multiplayer room |
| `/api/rooms/join/{code}` | Join room by 6-char code |
| `/api/matchmaking/join` | Quick match queue |

## Important Notes

- Use `docker compose` (with space), not `docker-compose`
- HTML file is read-only volume mounted in production
- Redis data persists in `redis_data` Docker volume
- Game data persists in `./data` directory
