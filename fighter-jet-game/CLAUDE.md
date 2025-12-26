# Fighter Jet Game - Claude Development Notes

## Deployment Process

### Production Server
- **Host**: felican.ai (SSH: `ssh felican.ai`)
- **Path**: `/home/dev/legion-scripts/fighter-jet-game`
- **Git repo**: https://github.com/camelotceo/legion-scripts.git

### Deployment Steps

1. **Commit and push changes locally:**
   ```bash
   git add . && git commit -m "Your message" && git push origin main
   ```

2. **SSH to server and pull:**
   ```bash
   ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && git pull origin main"
   ```

3. **Rebuild and restart containers (if Python files changed):**
   ```bash
   ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && docker compose build --no-cache && docker compose up -d"
   ```

4. **For HTML-only changes (no rebuild needed):**
   The `fighter-jet-game.html` file is volume mounted, so changes take effect immediately after `git pull`.

### Important Notes
- Use `docker compose` (with space) on server, NOT `docker-compose`
- HTML file is volume mounted at `./fighter-jet-game.html:/app/fighter-jet-game.html:ro`
- Data persists in `./data` directory (volume mounted)
- Redis data persists in `redis_data` Docker volume

## Debug Modes

- **`ending123`** - Enter as player name to immediately see victory screen
- **`invincible`** - Enter as player name for unlimited lives

## Architecture

- **Frontend**: Single HTML file with embedded JS/CSS
- **Backend**: Flask (Python) with Gunicorn
- **Cache**: Redis for live player state, spectating, comments
- **Database**: PostgreSQL for leaderboards, player history, game sessions
- **Backup**: Local backups every minute, Backblaze B2 offload every 6 hours
