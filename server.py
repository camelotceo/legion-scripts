#!/usr/bin/env python3
"""
Simple Flask server for Legion's Space Fight game.
Serves static files, provides leaderboard API, and tracks live players.
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import random
import string
import time
import threading
from pathlib import Path
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Paths
BASE_DIR = Path(__file__).parent
GAMES_DIR = BASE_DIR / 'games'
LEADERBOARD_FILE = BASE_DIR / 'leaderboard.json'
MAX_LEADERBOARD_SIZE = 10

# Active players tracking (in-memory)
active_players = {}
players_lock = threading.Lock()
PLAYER_TIMEOUT = 30  # Remove player if no update for 30 seconds

def generate_handle():
    """Generate a random player handle like Player_X7K2."""
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"Player_{suffix}"

def cleanup_stale_players():
    """Remove players who haven't updated in PLAYER_TIMEOUT seconds."""
    current_time = time.time()
    with players_lock:
        stale = [pid for pid, data in active_players.items()
                 if current_time - data['lastUpdate'] > PLAYER_TIMEOUT]
        for pid in stale:
            del active_players[pid]

def get_leaderboard():
    """Read leaderboard from JSON file."""
    if not LEADERBOARD_FILE.exists():
        return []
    try:
        with open(LEADERBOARD_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

def save_leaderboard(leaderboard):
    """Save leaderboard to JSON file."""
    with open(LEADERBOARD_FILE, 'w') as f:
        json.dump(leaderboard, f, indent=2)

@app.route('/games/<path:filename>')
def serve_game(filename):
    """Serve static game files."""
    return send_from_directory(GAMES_DIR, filename)

@app.route('/api/leaderboard', methods=['GET'])
def get_scores():
    """Get all high scores."""
    return jsonify(get_leaderboard())

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

    leaderboard = get_leaderboard()

    # Add new entry
    leaderboard.append({
        'name': name,
        'score': score,
        'difficulty': difficulty,
        'level': level,
        'date': datetime.now().isoformat()
    })

    # Sort by score descending and keep top entries
    leaderboard.sort(key=lambda x: x['score'], reverse=True)
    leaderboard = leaderboard[:MAX_LEADERBOARD_SIZE]

    save_leaderboard(leaderboard)

    return jsonify({'success': True, 'leaderboard': leaderboard})

# === LIVE PLAYERS API ===

@app.route('/api/players/join', methods=['POST'])
def player_join():
    """Register a new player."""
    cleanup_stale_players()
    data = request.get_json() or {}

    name = str(data.get('name', '')).strip()[:12]
    if not name:
        name = generate_handle()

    player_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

    with players_lock:
        active_players[player_id] = {
            'id': player_id,
            'name': name,
            'score': 0,
            'level': 1,
            'difficulty': data.get('difficulty', 'EASY'),
            'color': data.get('color', 'blue'),
            'status': 'lobby',  # lobby, playing, boss
            'lastUpdate': time.time()
        }

    return jsonify({
        'success': True,
        'playerId': player_id,
        'name': name,
        'players': list(active_players.values())
    })

@app.route('/api/players/update', methods=['POST'])
def player_update():
    """Update player's score and status."""
    cleanup_stale_players()
    data = request.get_json() or {}

    player_id = data.get('playerId')
    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    with players_lock:
        if player_id not in active_players:
            return jsonify({'error': 'Player not found'}), 404

        player = active_players[player_id]
        player['score'] = int(data.get('score', player['score']))
        player['level'] = int(data.get('level', player['level']))
        player['status'] = data.get('status', player['status'])
        player['difficulty'] = data.get('difficulty', player['difficulty'])
        player['lastUpdate'] = time.time()

        # Return all active players sorted by score
        players = sorted(active_players.values(), key=lambda x: x['score'], reverse=True)

    return jsonify({'success': True, 'players': players})

@app.route('/api/players/leave', methods=['POST'])
def player_leave():
    """Remove player from active list."""
    data = request.get_json() or {}
    player_id = data.get('playerId')

    with players_lock:
        if player_id in active_players:
            del active_players[player_id]

    return jsonify({'success': True})

@app.route('/api/players/active', methods=['GET'])
def get_active_players():
    """Get all active players."""
    cleanup_stale_players()
    with players_lock:
        players = sorted(active_players.values(), key=lambda x: x['score'], reverse=True)
    return jsonify(players)

@app.route('/')
def index():
    """Redirect to game."""
    return '<a href="/games/fighter-jet-game.html">Play Legion\'s Space Fight</a>'

if __name__ == '__main__':
    # Create empty leaderboard file if it doesn't exist
    if not LEADERBOARD_FILE.exists():
        save_leaderboard([])

    print(f"Serving games from: {GAMES_DIR}")
    print(f"Leaderboard file: {LEADERBOARD_FILE}")
    print("Starting server on http://0.0.0.0:5300")
    app.run(host='0.0.0.0', port=5300, debug=False, threaded=True)
