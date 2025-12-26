#!/usr/bin/env python3
"""
Flask server for Legion's Space Fight game.
Serves static files, provides APIs for leaderboard, live players, and spectating.
Uses Redis for real-time state and PostgreSQL for persistent data.
"""

import os
import json
import random
import string
import time
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pathlib import Path
from datetime import datetime

# Try to import database modules (graceful fallback for development)
try:
    import redis_client
    import database
    USE_REDIS = True
    USE_POSTGRES = True
except ImportError:
    USE_REDIS = False
    USE_POSTGRES = False
    print("Warning: Redis/Postgres modules not available, using in-memory fallback")

app = Flask(__name__)
CORS(app)

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
LEADERBOARD_FILE = DATA_DIR / 'leaderboard.json'
MAX_LEADERBOARD_SIZE = 10

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)

# In-memory fallback for active players (used if Redis not available)
active_players = {}
import threading
players_lock = threading.Lock()
PLAYER_TIMEOUT = 30

# Action types with emojis
ACTIONS = {
    'started_game': '🚀',
    'got_powerup': '⭐',
    'got_shield': '🛡️',
    'got_machinegun': '🔫',
    'got_bazooka': '💣',
    'got_laser': '⚡',
    'fighting_boss': '👹',
    'boss_low_health': '🔥',
    'defeated_boss': '🏆',
    'killed_by_boss': '💀',
    'killed_by_drone': '💀',
    'crashed_wall': '💥',
    'hit_bomb': '💥',
    'level_up': '🆙',
    'game_over': '🎮'
}


def generate_handle():
    """Generate a random player handle like Player_X7K2."""
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"Player_{suffix}"


# === FALLBACK FUNCTIONS (when Redis/Postgres not available) ===

def cleanup_stale_players():
    """Remove players who haven't updated in PLAYER_TIMEOUT seconds."""
    current_time = time.time()
    with players_lock:
        stale = [pid for pid, data in active_players.items()
                 if current_time - data.get('lastUpdate', 0) > PLAYER_TIMEOUT]
        for pid in stale:
            del active_players[pid]


def get_leaderboard_fallback():
    """Read leaderboard from JSON file."""
    if not LEADERBOARD_FILE.exists():
        return []
    try:
        with open(LEADERBOARD_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def save_leaderboard_fallback(leaderboard):
    """Save leaderboard to JSON file."""
    with open(LEADERBOARD_FILE, 'w') as f:
        json.dump(leaderboard, f, indent=2)


# === STATIC FILES ===

@app.route('/')
def index():
    """Serve the main game file."""
    return send_from_directory(BASE_DIR, 'fighter-jet-game.html')


@app.route('/fighter-jet-game.html')
def serve_game():
    """Serve the game file directly."""
    return send_from_directory(BASE_DIR, 'fighter-jet-game.html')


# === LEADERBOARD API ===

@app.route('/api/leaderboard', methods=['GET'])
def get_scores():
    """Get top scores."""
    if USE_POSTGRES:
        try:
            scores = database.get_leaderboard(MAX_LEADERBOARD_SIZE)
            # Convert datetime objects to strings
            for s in scores:
                if s.get('date'):
                    s['date'] = s['date'].isoformat() if hasattr(s['date'], 'isoformat') else str(s['date'])
            return jsonify(scores)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify(get_leaderboard_fallback())


@app.route('/api/leaderboard', methods=['POST'])
def add_score():
    """Add a new high score."""
    data = request.get_json()

    if not data or 'name' not in data or 'score' not in data:
        return jsonify({'error': 'Missing name or score'}), 400

    name = str(data['name'])[:12].strip() or 'Anonymous'
    score = int(data.get('score', 0))
    difficulty = str(data.get('difficulty', 'EASY'))[:10]
    level = int(data.get('level', 1))
    duration = int(data.get('duration', 0))
    bosses_defeated = int(data.get('bossesDefeated', 0))
    death_reason = str(data.get('deathReason', 'unknown'))[:50]

    # Try to save to PostgreSQL first
    if USE_POSTGRES:
        try:
            database.add_leaderboard_entry(
                name=name,
                score=score,
                duration=duration,
                level=level,
                difficulty=difficulty,
                bosses_defeated=bosses_defeated,
                death_reason=death_reason
            )
            # Get updated leaderboard from database
            leaderboard = database.get_leaderboard(MAX_LEADERBOARD_SIZE)
            for s in leaderboard:
                if s.get('date'):
                    s['date'] = s['date'].isoformat() if hasattr(s['date'], 'isoformat') else str(s['date'])
            return jsonify({'success': True, 'leaderboard': leaderboard})
        except Exception as e:
            print(f"Database error saving score: {e}")
            # Fall through to JSON fallback

    # Fallback: Use JSON storage
    leaderboard = get_leaderboard_fallback()
    leaderboard.append({
        'name': name,
        'score': score,
        'difficulty': difficulty,
        'level': level,
        'duration': duration,
        'date': datetime.now().isoformat()
    })

    # Sort by score desc, then duration asc
    leaderboard.sort(key=lambda x: (-x['score'], x.get('duration', 9999)))
    leaderboard = leaderboard[:MAX_LEADERBOARD_SIZE]
    save_leaderboard_fallback(leaderboard)

    return jsonify({'success': True, 'leaderboard': leaderboard})


# === LIVE PLAYERS API ===

@app.route('/api/players/join', methods=['POST'])
def player_join():
    """Register a new player."""
    data = request.get_json() or {}

    name = str(data.get('name', '')).strip()[:12]
    if not name:
        name = generate_handle()

    player_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    difficulty = data.get('difficulty', 'EASY')
    color = data.get('color', 'blue')

    player_data = {
        'id': player_id,
        'name': name,
        'score': 0,
        'level': 1,
        'difficulty': difficulty,
        'color': color,
        'status': 'lobby',
        'lastAction': 'joined',
        'lastActionEmoji': '👋',
        'lastActionTime': datetime.now().isoformat(),
        'gameStartTime': None,
        'gameDuration': 0,
        'shieldActive': False,
        'currentWeapon': 'pistol',
        'bossHealth': None,
        'isNew': True,
        'defeatedBoss': False,
        'sessionId': None,
        'lastUpdate': time.time()
    }

    if USE_REDIS:
        try:
            redis_client.set_player(player_id, player_data)
            redis_client.mark_new_player(player_id)
            players = redis_client.get_all_players()
        except Exception as e:
            print(f"Redis error: {e}")
            players = []
    else:
        cleanup_stale_players()
        with players_lock:
            active_players[player_id] = player_data
            players = list(active_players.values())

    # Create player in database
    if USE_POSTGRES:
        try:
            database.get_or_create_player(name)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify({
        'success': True,
        'playerId': player_id,
        'name': name,
        'players': players
    })


@app.route('/api/players/update', methods=['POST'])
def player_update():
    """Update player's score, status, and action."""
    data = request.get_json() or {}

    player_id = data.get('playerId')
    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    updates = {
        'lastUpdate': time.time()
    }

    # Update fields if provided
    for field in ['score', 'level', 'status', 'difficulty', 'gameDuration',
                  'shieldActive', 'currentWeapon', 'bossHealth', 'gameStartTime']:
        if field in data:
            updates[field] = data[field]

    if USE_REDIS:
        try:
            if not redis_client.update_player(player_id, updates):
                return jsonify({'error': 'Player not found'}), 404
            players = redis_client.get_all_players()
        except Exception as e:
            print(f"Redis error: {e}")
            return jsonify({'error': 'Server error'}), 500
    else:
        cleanup_stale_players()
        with players_lock:
            if player_id not in active_players:
                return jsonify({'error': 'Player not found'}), 404

            active_players[player_id].update(updates)
            players = sorted(active_players.values(),
                           key=lambda x: x.get('score', 0), reverse=True)

    return jsonify({'success': True, 'players': players})


@app.route('/api/players/action', methods=['POST'])
def player_action():
    """Report a player action."""
    data = request.get_json() or {}

    player_id = data.get('playerId')
    action = data.get('action')
    session_id = data.get('sessionId')

    if not player_id or not action:
        return jsonify({'error': 'Missing playerId or action'}), 400

    emoji = ACTIONS.get(action, '❓')

    updates = {
        'lastAction': action,
        'lastActionEmoji': emoji,
        'lastActionTime': datetime.now().isoformat(),
        'lastUpdate': time.time()
    }

    # Handle special actions
    if action == 'started_game':
        updates['status'] = 'playing'
        updates['gameStartTime'] = datetime.now().isoformat()

    elif action == 'fighting_boss':
        updates['status'] = 'boss'

    elif action == 'defeated_boss':
        updates['defeatedBoss'] = True
        boss_level = data.get('bossLevel', 1)
        if USE_REDIS:
            try:
                redis_client.mark_boss_defeat(player_id, boss_level)
            except:
                pass

    elif action in ['killed_by_boss', 'killed_by_drone', 'crashed_wall', 'hit_bomb', 'game_over']:
        updates['status'] = 'dead'

    elif action == 'got_shield':
        updates['shieldActive'] = True

    elif action in ['got_machinegun', 'got_bazooka', 'got_laser']:
        weapon = action.replace('got_', '')
        updates['currentWeapon'] = weapon

    # Update Redis
    if USE_REDIS:
        try:
            redis_client.update_player(player_id, updates)
        except Exception as e:
            print(f"Redis error: {e}")

    # Log to database
    if USE_POSTGRES and session_id:
        try:
            database.log_game_event(session_id, action, emoji, data.get('details'))
        except Exception as e:
            print(f"Database error: {e}")

    # Fallback
    if not USE_REDIS:
        with players_lock:
            if player_id in active_players:
                active_players[player_id].update(updates)

    return jsonify({'success': True, 'emoji': emoji})


@app.route('/api/players/start-session', methods=['POST'])
def start_session():
    """Start a new game session for database tracking."""
    data = request.get_json() or {}

    player_name = data.get('name')
    difficulty = data.get('difficulty', 'EASY')

    if not player_name:
        return jsonify({'error': 'Missing name'}), 400

    session_id = None
    if USE_POSTGRES:
        try:
            player = database.get_or_create_player(player_name)
            session_id = database.create_game_session(player['id'], difficulty)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify({'success': True, 'sessionId': session_id})


@app.route('/api/players/end-session', methods=['POST'])
def end_session():
    """End a game session with final stats."""
    data = request.get_json() or {}

    session_id = data.get('sessionId')
    if not session_id:
        return jsonify({'error': 'Missing sessionId'}), 400

    score = int(data.get('score', 0))
    level = int(data.get('level', 1))
    duration = int(data.get('duration', 0))
    death_reason = data.get('deathReason', 'unknown')
    bosses_defeated = int(data.get('bossesDefeated', 0))

    if USE_POSTGRES:
        try:
            database.end_game_session(session_id, score, level, duration,
                                      death_reason, bosses_defeated)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify({'success': True})


@app.route('/api/players/leave', methods=['POST'])
def player_leave():
    """Remove player from active list."""
    data = request.get_json() or {}
    player_id = data.get('playerId')

    if USE_REDIS:
        try:
            redis_client.delete_player(player_id)
            redis_client.delete_game_state(player_id)
        except:
            pass

    with players_lock:
        if player_id in active_players:
            del active_players[player_id]

    return jsonify({'success': True})


@app.route('/api/players/active', methods=['GET'])
def get_active_players():
    """Get all active players with their status."""
    if USE_REDIS:
        try:
            players = redis_client.get_all_players()
            new_players = redis_client.get_new_players()

            for p in players:
                p['isNew'] = p.get('id') in new_players
                p['defeatedBoss'] = redis_client.has_defeated_boss(p.get('id', ''))
                p['spectatorCount'] = redis_client.get_spectator_count(p.get('id', ''))

            return jsonify(players)
        except Exception as e:
            print(f"Redis error: {e}")

    cleanup_stale_players()
    with players_lock:
        players = sorted(active_players.values(),
                        key=lambda x: x.get('score', 0), reverse=True)
    return jsonify(players)


# === SPECTATOR API ===

@app.route('/api/players/gamestate', methods=['POST'])
def update_game_state():
    """Update game state for spectators."""
    data = request.get_json() or {}

    player_id = data.get('playerId')
    state = data.get('state')

    if not player_id or not state:
        return jsonify({'error': 'Missing playerId or state'}), 400

    if USE_REDIS:
        try:
            redis_client.set_game_state(player_id, state)
        except Exception as e:
            print(f"Redis error: {e}")

    return jsonify({'success': True})


@app.route('/api/players/spectate/<player_id>', methods=['GET'])
def spectate_player(player_id):
    """Get game state for spectating a player."""
    spectator_id = request.args.get('spectatorId')

    if USE_REDIS:
        try:
            # Track spectator
            if spectator_id:
                redis_client.add_spectator(player_id, spectator_id)

            # Get game state
            state = redis_client.get_game_state(player_id)
            player = redis_client.get_player(player_id)

            if not state:
                return jsonify({'error': 'No game state available'}), 404

            return jsonify({
                'success': True,
                'player': player,
                'state': state
            })
        except Exception as e:
            print(f"Redis error: {e}")
            return jsonify({'error': 'Server error'}), 500

    return jsonify({'error': 'Spectating not available'}), 503


@app.route('/api/players/spectate/<player_id>/leave', methods=['POST'])
def leave_spectate(player_id):
    """Stop spectating a player."""
    data = request.get_json() or {}
    spectator_id = data.get('spectatorId')

    if USE_REDIS and spectator_id:
        try:
            redis_client.remove_spectator(player_id, spectator_id)
        except:
            pass

    return jsonify({'success': True})


# === SPECTATOR COMMENTS API ===

@app.route('/api/players/spectate/<player_id>/comment', methods=['POST'])
def add_comment(player_id):
    """Add a comment for a player being watched."""
    data = request.get_json() or {}

    author = str(data.get('from', 'Anonymous'))[:12]
    message = str(data.get('message', ''))[:100].strip()

    if not message:
        return jsonify({'error': 'Empty message'}), 400

    comment = {
        'from': author,
        'message': message,
        'time': datetime.now().isoformat(),
        'isPlayer': data.get('isPlayer', False)
    }

    if USE_REDIS:
        try:
            redis_client.add_comment(player_id, comment)
        except Exception as e:
            print(f"Redis error: {e}")
            return jsonify({'error': 'Server error'}), 500

    return jsonify({'success': True})


@app.route('/api/players/spectate/<player_id>/comments', methods=['GET'])
def get_comments(player_id):
    """Get recent comments for a player being watched."""
    if not USE_REDIS:
        return jsonify([])

    try:
        comments = redis_client.get_comments(player_id)
        return jsonify(comments)
    except Exception as e:
        print(f"Redis error: {e}")
        return jsonify([])


# === PLAYER HISTORY API ===

@app.route('/api/players/history/<name>', methods=['GET'])
def get_player_history(name):
    """Get player profile and game history."""
    if not USE_POSTGRES:
        return jsonify({'error': 'History not available'}), 503

    try:
        history = database.get_player_history(name)
        if not history:
            return jsonify({'error': 'Player not found'}), 404

        # Convert datetime objects
        for game in history.get('games', []):
            for key in ['started_at', 'ended_at']:
                if game.get(key) and hasattr(game[key], 'isoformat'):
                    game[key] = game[key].isoformat()

        player = history.get('player', {})
        if player.get('first_seen') and hasattr(player['first_seen'], 'isoformat'):
            player['first_seen'] = player['first_seen'].isoformat()

        return jsonify(history)
    except Exception as e:
        print(f"Database error: {e}")
        return jsonify({'error': 'Server error'}), 500


# === BACKUP SCHEDULER ===

def init_backup_scheduler():
    """Initialize background backup scheduler (only in one process)."""
    # Use a lock file to ensure only one scheduler runs across workers
    lock_file = DATA_DIR / '.scheduler.lock'

    try:
        import fcntl
        lock_fd = open(lock_file, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError, ImportError):
        # Another process has the lock or fcntl not available
        print("Backup scheduler: another process is running scheduler")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        import backup

        scheduler = BackgroundScheduler()

        # Local backup every minute
        scheduler.add_job(
            backup.local_backup,
            'interval',
            minutes=1,
            id='local_backup',
            name='Local backup every minute'
        )

        # Offload to Backblaze every 6 hours
        scheduler.add_job(
            backup.offload_to_backblaze,
            'interval',
            hours=6,
            id='b2_offload',
            name='Backblaze offload every 6 hours'
        )

        scheduler.start()
        print("Backup scheduler started (1min local, 6hr B2 offload)")
        return scheduler

    except ImportError as e:
        print(f"Backup scheduler not available: {e}")
        return None


# === BACKUP API ===

@app.route('/api/backup/list', methods=['GET'])
def list_backups():
    """List available backups."""
    try:
        import backup
        backups = backup.list_backups()
        return jsonify(backups)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backup/restore', methods=['POST'])
def restore_backup():
    """Restore from latest backup."""
    try:
        import backup
        if backup.restore_latest():
            return jsonify({'success': True})
        return jsonify({'error': 'No backups available'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Initialize scheduler when running with gunicorn
backup_scheduler = None


if __name__ == '__main__':
    # Create empty leaderboard file if it doesn't exist
    if not LEADERBOARD_FILE.exists():
        save_leaderboard_fallback([])

    print(f"Leaderboard file: {LEADERBOARD_FILE}")
    print(f"Redis enabled: {USE_REDIS}")
    print(f"PostgreSQL enabled: {USE_POSTGRES}")

    # Start backup scheduler
    backup_scheduler = init_backup_scheduler()

    print("Starting server on http://0.0.0.0:8080")
    app.run(host='0.0.0.0', port=8080, debug=True, threaded=True)
else:
    # Running under gunicorn - start scheduler
    backup_scheduler = init_backup_scheduler()
