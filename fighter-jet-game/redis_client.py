"""
Redis client module for Fighter Jet Game.
Handles real-time player state, game state for spectating, and pub/sub.
"""

import os
import json
import redis
from datetime import datetime

REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379')
PLAYER_TTL = 30  # seconds before player expires
GAMESTATE_TTL = 5  # seconds for game state snapshots

# Redis connection pool
pool = redis.ConnectionPool.from_url(REDIS_URL, decode_responses=True)


def get_redis():
    """Get Redis connection from pool."""
    return redis.Redis(connection_pool=pool)


# === ACTIVE PLAYERS ===

def set_player(player_id: str, data: dict):
    """Set player data with TTL."""
    r = get_redis()
    r.hset(f"player:{player_id}", mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in data.items()})
    r.expire(f"player:{player_id}", PLAYER_TTL)


def get_player(player_id: str) -> dict:
    """Get player data."""
    r = get_redis()
    data = r.hgetall(f"player:{player_id}")
    if not data:
        return None

    # Parse JSON fields
    result = {}
    for k, v in data.items():
        try:
            result[k] = json.loads(v)
        except (json.JSONDecodeError, TypeError):
            result[k] = v
    return result


def update_player(player_id: str, updates: dict):
    """Update specific player fields and refresh TTL."""
    r = get_redis()
    if not r.exists(f"player:{player_id}"):
        return False

    r.hset(f"player:{player_id}", mapping={k: json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v) for k, v in updates.items()})
    r.expire(f"player:{player_id}", PLAYER_TTL)
    return True


def delete_player(player_id: str):
    """Remove player."""
    r = get_redis()
    r.delete(f"player:{player_id}")


def get_all_players() -> list:
    """Get all active players."""
    r = get_redis()
    players = []
    for key in r.scan_iter("player:*"):
        player_id = key.split(":")[1]
        player = get_player(player_id)
        if player:
            player['id'] = player_id
            players.append(player)

    # Sort by score descending
    players.sort(key=lambda x: int(x.get('score', 0)), reverse=True)
    return players


def set_player_action(player_id: str, action: str, emoji: str):
    """Update player's last action."""
    update_player(player_id, {
        'lastAction': action,
        'lastActionEmoji': emoji,
        'lastActionTime': datetime.now().isoformat()
    })


# === GAME STATE FOR SPECTATING ===

def set_game_state(player_id: str, state: dict):
    """Store game state for spectators."""
    r = get_redis()
    r.set(f"gamestate:{player_id}", json.dumps(state), ex=GAMESTATE_TTL)


def get_game_state(player_id: str) -> dict:
    """Get game state for spectating."""
    r = get_redis()
    data = r.get(f"gamestate:{player_id}")
    if data:
        return json.loads(data)
    return None


def delete_game_state(player_id: str):
    """Remove game state."""
    r = get_redis()
    r.delete(f"gamestate:{player_id}")


# === SPECTATOR TRACKING ===

def add_spectator(target_player_id: str, spectator_id: str):
    """Add a spectator to a player."""
    r = get_redis()
    r.sadd(f"spectators:{target_player_id}", spectator_id)
    r.expire(f"spectators:{target_player_id}", PLAYER_TTL)


def remove_spectator(target_player_id: str, spectator_id: str):
    """Remove a spectator from a player."""
    r = get_redis()
    r.srem(f"spectators:{target_player_id}", spectator_id)


def get_spectator_count(player_id: str) -> int:
    """Get number of spectators for a player."""
    r = get_redis()
    return r.scard(f"spectators:{player_id}")


# === NEW PLAYER NOTIFICATIONS ===

def mark_new_player(player_id: str):
    """Mark player as new (for flash animation)."""
    r = get_redis()
    r.set(f"newplayer:{player_id}", "1", ex=10)  # expires after 10 seconds


def is_new_player(player_id: str) -> bool:
    """Check if player is new."""
    r = get_redis()
    return r.exists(f"newplayer:{player_id}")


def get_new_players() -> list:
    """Get list of new player IDs."""
    r = get_redis()
    new_players = []
    for key in r.scan_iter("newplayer:*"):
        new_players.append(key.split(":")[1])
    return new_players


# === BOSS DEFEAT TRACKING ===

def mark_boss_defeat(player_id: str, boss_level: int):
    """Mark that player defeated a boss."""
    r = get_redis()
    r.sadd(f"bossdefeats:{player_id}", boss_level)
    r.expire(f"bossdefeats:{player_id}", 300)  # highlight for 5 minutes


def get_boss_defeats(player_id: str) -> list:
    """Get bosses defeated by player."""
    r = get_redis()
    defeats = r.smembers(f"bossdefeats:{player_id}")
    return [int(d) for d in defeats] if defeats else []


def has_defeated_boss(player_id: str) -> bool:
    """Check if player has defeated any boss."""
    r = get_redis()
    return r.exists(f"bossdefeats:{player_id}")


# === SPECTATOR COMMENTS ===

def add_comment(player_id: str, comment: dict):
    """Add a comment for a player being watched."""
    r = get_redis()
    r.lpush(f"comments:{player_id}", json.dumps(comment))
    r.ltrim(f"comments:{player_id}", 0, 49)  # Keep last 50 comments
    r.expire(f"comments:{player_id}", 300)  # 5 minute TTL


def get_comments(player_id: str, limit: int = 50) -> list:
    """Get recent comments for a player."""
    r = get_redis()
    comments = r.lrange(f"comments:{player_id}", 0, limit - 1)
    return [json.loads(c) for c in comments]


def clear_comments(player_id: str):
    """Clear all comments for a player."""
    r = get_redis()
    r.delete(f"comments:{player_id}")
