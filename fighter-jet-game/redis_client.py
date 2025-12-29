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


# === MULTIPLAYER ROOMS ===

ROOM_TTL = 300  # 5 minutes for inactive rooms
MATCHMAKING_TTL = 120  # 2 minutes in queue before timeout


def generate_room_code() -> str:
    """Generate a 6-character room code (no confusing characters)."""
    import random
    chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    return ''.join(random.choices(chars, k=6))


def create_room(host_id: str, host_name: str, mode: str, difficulty: str) -> str:
    """Create a new multiplayer room. Returns room code."""
    r = get_redis()

    # Generate unique room code
    for _ in range(10):  # Try up to 10 times
        code = generate_room_code()
        if not r.exists(f"room:{code}"):
            break

    room_data = {
        'code': code,
        'mode': mode,  # 'coop' or 'versus'
        'host_id': host_id,
        'host_name': host_name,
        'status': 'waiting',
        'difficulty': difficulty,
        'created_at': datetime.now().isoformat(),
        'players': json.dumps([{'id': host_id, 'name': host_name, 'ready': False, 'slot': 1}])
    }

    r.hset(f"room:{code}", mapping=room_data)
    r.expire(f"room:{code}", ROOM_TTL)
    r.sadd(f"room_players:{code}", host_id)
    r.expire(f"room_players:{code}", ROOM_TTL)

    # Track which room this player is in
    r.set(f"player_room:{host_id}", code, ex=ROOM_TTL)

    return code


def get_room(code: str) -> dict:
    """Get room data by code."""
    r = get_redis()
    data = r.hgetall(f"room:{code}")
    if not data:
        return None

    # Parse JSON fields
    if 'players' in data:
        data['players'] = json.loads(data['players'])
    return data


def join_room(code: str, player_id: str, player_name: str) -> dict:
    """Join an existing room. Returns room data or None if failed."""
    r = get_redis()
    room = get_room(code)

    if not room:
        return {'error': 'Room not found'}

    if room['status'] != 'waiting':
        return {'error': 'Game already started'}

    players = room['players']
    if len(players) >= 2:
        return {'error': 'Room is full'}

    # Check if player already in room
    if any(p['id'] == player_id for p in players):
        return room

    # Add player
    players.append({'id': player_id, 'name': player_name, 'ready': False, 'slot': 2})
    r.hset(f"room:{code}", 'players', json.dumps(players))
    r.sadd(f"room_players:{code}", player_id)
    r.expire(f"room:{code}", ROOM_TTL)
    r.expire(f"room_players:{code}", ROOM_TTL)

    # Track which room this player is in
    r.set(f"player_room:{player_id}", code, ex=ROOM_TTL)

    room['players'] = players
    return room


def leave_room(code: str, player_id: str) -> bool:
    """Leave a room. Returns True if successful."""
    r = get_redis()
    room = get_room(code)

    if not room:
        return False

    players = room['players']
    players = [p for p in players if p['id'] != player_id]

    r.srem(f"room_players:{code}", player_id)
    r.delete(f"player_room:{player_id}")

    if len(players) == 0:
        # Delete empty room
        r.delete(f"room:{code}")
        r.delete(f"room_players:{code}")
    else:
        # Update room
        r.hset(f"room:{code}", 'players', json.dumps(players))
        # If host left, make other player host
        if room['host_id'] == player_id and players:
            r.hset(f"room:{code}", 'host_id', players[0]['id'])
            r.hset(f"room:{code}", 'host_name', players[0]['name'])

    return True


def set_player_ready(code: str, player_id: str, ready: bool) -> dict:
    """Toggle player ready status. Returns updated room."""
    r = get_redis()
    room = get_room(code)

    if not room:
        return None

    players = room['players']
    for p in players:
        if p['id'] == player_id:
            p['ready'] = ready
            break

    r.hset(f"room:{code}", 'players', json.dumps(players))
    r.expire(f"room:{code}", ROOM_TTL)

    room['players'] = players
    return room


def start_room_game(code: str) -> bool:
    """Mark room as game started. Returns True if successful."""
    r = get_redis()
    room = get_room(code)

    if not room:
        return False

    # Check all players ready
    players = room['players']
    if len(players) < 2:
        return False

    if not all(p['ready'] for p in players):
        return False

    r.hset(f"room:{code}", 'status', 'playing')
    r.hset(f"room:{code}", 'started_at', datetime.now().isoformat())
    r.expire(f"room:{code}", ROOM_TTL * 4)  # Extend TTL for gameplay

    return True


def end_room_game(code: str, winner_id: str = None):
    """Mark room game as finished."""
    r = get_redis()
    r.hset(f"room:{code}", 'status', 'finished')
    r.hset(f"room:{code}", 'ended_at', datetime.now().isoformat())
    if winner_id:
        r.hset(f"room:{code}", 'winner_id', winner_id)
    r.expire(f"room:{code}", 60)  # Keep for 1 minute after game ends


def get_player_room(player_id: str) -> str:
    """Get the room code a player is currently in."""
    r = get_redis()
    return r.get(f"player_room:{player_id}")


# === MATCHMAKING QUEUE ===

def join_matchmaking(player_id: str, player_name: str, mode: str, difficulty: str) -> bool:
    """Add player to matchmaking queue."""
    r = get_redis()

    # Store player data for matching
    player_data = json.dumps({
        'id': player_id,
        'name': player_name,
        'difficulty': difficulty,
        'joined_at': datetime.now().isoformat()
    })

    # Add to sorted set (score = timestamp for FIFO)
    r.zadd(f"matchmaking:{mode}", {player_data: datetime.now().timestamp()})
    r.expire(f"matchmaking:{mode}", MATCHMAKING_TTL)

    # Track that this player is in queue
    r.set(f"in_queue:{player_id}", mode, ex=MATCHMAKING_TTL)

    return True


def leave_matchmaking(player_id: str) -> bool:
    """Remove player from matchmaking queue."""
    r = get_redis()
    mode = r.get(f"in_queue:{player_id}")

    if not mode:
        return False

    # Find and remove player from queue
    queue = r.zrange(f"matchmaking:{mode}", 0, -1)
    for entry in queue:
        data = json.loads(entry)
        if data['id'] == player_id:
            r.zrem(f"matchmaking:{mode}", entry)
            break

    r.delete(f"in_queue:{player_id}")
    return True


def find_match(player_id: str, mode: str, difficulty: str) -> dict:
    """Try to find a match for the player. Returns match info or None."""
    r = get_redis()

    # Get oldest player in queue (excluding self)
    queue = r.zrange(f"matchmaking:{mode}", 0, -1)

    for entry in queue:
        data = json.loads(entry)
        if data['id'] != player_id:
            # Found a match! Remove both from queue
            r.zrem(f"matchmaking:{mode}", entry)
            r.delete(f"in_queue:{data['id']}")  # Also remove matched player's queue tracking
            leave_matchmaking(player_id)

            # Create room for them
            room_code = create_room(data['id'], data['name'], mode, difficulty)
            player_data = get_player(player_id)
            player_name = player_data.get('name', 'Player') if player_data else 'Player'
            join_room(room_code, player_id, player_name)

            return {
                'matched': True,
                'room_code': room_code,
                'opponent': data,
                'isHost': False  # The player who finds the match joined the room
            }

    return {'matched': False, 'queue_position': get_queue_position(player_id, mode)}


def get_queue_position(player_id: str, mode: str) -> int:
    """Get player's position in matchmaking queue."""
    r = get_redis()
    queue = r.zrange(f"matchmaking:{mode}", 0, -1)

    for i, entry in enumerate(queue):
        data = json.loads(entry)
        if data['id'] == player_id:
            return i + 1

    return 0


def is_in_queue(player_id: str) -> str:
    """Check if player is in a matchmaking queue. Returns mode or None."""
    r = get_redis()
    return r.get(f"in_queue:{player_id}")


# === MULTIPLAYER GAME STATE ===

MULTIPLAYER_STATE_TTL = 10  # 10 seconds for multiplayer state


def set_multiplayer_state(room_code: str, state: dict):
    """Store multiplayer game state."""
    r = get_redis()
    r.set(f"mp_state:{room_code}", json.dumps(state), ex=MULTIPLAYER_STATE_TTL)


def get_multiplayer_state(room_code: str) -> dict:
    """Get multiplayer game state."""
    r = get_redis()
    data = r.get(f"mp_state:{room_code}")
    if data:
        return json.loads(data)
    return None


def delete_multiplayer_state(room_code: str):
    """Remove multiplayer game state."""
    r = get_redis()
    r.delete(f"mp_state:{room_code}")
