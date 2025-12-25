#!/usr/bin/env python3
"""
Simple Flask server for Legion's Space Fight game.
Serves static files and provides leaderboard API with JSON file storage.
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import json
import os
from pathlib import Path
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Paths
BASE_DIR = Path(__file__).parent
GAMES_DIR = BASE_DIR / 'games'
LEADERBOARD_FILE = BASE_DIR / 'leaderboard.json'
MAX_LEADERBOARD_SIZE = 10

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
    app.run(host='0.0.0.0', port=5300, debug=False)
