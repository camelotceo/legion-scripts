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

### Environment Variables Setup (First-time or when adding new secrets)

**IMPORTANT: Never commit API keys to git!** All secrets are stored in `.env` on the server.

1. **Create/update .env file on server:**
   ```bash
   ssh felican.ai "cat > /home/dev/legion-scripts/fighter-jet-game/.env << 'EOF'
   B2_BUCKET=fighter-game-backup
   B2_KEY_ID=<your_b2_key_id>
   B2_APP_KEY=<your_b2_app_key>
   RESEND_API_KEY=<your_resend_api_key>
   EOF"
   ```

2. **Restart containers to pick up new env vars:**
   ```bash
   ssh felican.ai "cd /home/dev/legion-scripts/fighter-jet-game && docker compose up -d"
   ```

### Important Notes
- **Never commit .env or API keys to git** - use `.env.example` as reference
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
