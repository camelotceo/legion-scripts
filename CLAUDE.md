# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository contains browser-based games, each in its own self-contained folder with Docker deployment. Games are served via `games.felican.ai`.

## Architecture

Each game follows this structure:
```
<game-name>/
├── Dockerfile           # Python 3.11-slim, gunicorn
├── docker-compose.yml   # Connects to proxy-network, exposes 8080
├── requirements.txt     # Flask, flask-cors, gunicorn
├── server.py           # Flask API server
├── <game>.html         # Game frontend (single HTML file)
└── data/               # Persistent storage (mounted volume)
```

Games run as Docker containers on the `proxy-network` (shared with Nginx Proxy Manager) and are accessed via subdomains configured in NPM.

## Development Commands

### Local Development
```bash
cd <game-name>
python3 server.py                    # Run locally on port 8080
```

### Docker (Production)
```bash
cd <game-name>
docker compose up -d --build         # Build and start container
docker compose logs -f               # View logs
docker compose down                  # Stop container
```

### Deployment to Live Server
```bash
git push origin main
ssh felican.ai "cd /home/dev/legion-scripts/<game-name> && git pull && docker compose up -d --build"
```

## Key Technical Details

- **Networking**: Containers use `proxy-network` (external Docker network shared with NPM)
- **Ports**: Internal port 8080, not exposed to host (NPM handles routing)
- **Persistence**: Game data stored in `./data/` volume mount (e.g., leaderboard.json)
- **Server**: Flask with gunicorn (2 workers, 4 threads)
- **CORS**: Enabled for all origins

## Adding a New Game

1. Create folder: `<game-name>/`
2. Copy Docker files from existing game
3. Create `server.py` with Flask routes and `<game>.html`
4. Push to git, pull on server, run `docker compose up -d --build`
5. Add proxy host in NPM for the subdomain
