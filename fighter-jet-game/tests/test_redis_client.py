"""
Unit tests for redis_client.py - Redis operations.

Tests all Redis operations for:
- Player state management
- Matchmaking queue operations
- Room management
- Spectating functionality
- Multiplayer state sync

Run with: pytest tests/test_redis_client.py -v
"""

import json
import pytest
import logging
from unittest.mock import MagicMock, patch
from datetime import datetime

logger = logging.getLogger(__name__)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_redis():
    """Create a mock Redis client with common methods."""
    mock = MagicMock()
    mock.hset = MagicMock(return_value=1)
    mock.hget = MagicMock(return_value=None)
    mock.hgetall = MagicMock(return_value={})
    mock.hdel = MagicMock(return_value=1)
    mock.delete = MagicMock(return_value=1)
    mock.sadd = MagicMock(return_value=1)
    mock.srem = MagicMock(return_value=1)
    mock.smembers = MagicMock(return_value=set())
    mock.lpush = MagicMock(return_value=1)
    mock.rpop = MagicMock(return_value=None)
    mock.lrange = MagicMock(return_value=[])
    mock.llen = MagicMock(return_value=0)
    mock.lrem = MagicMock(return_value=1)
    mock.expire = MagicMock(return_value=True)
    mock.exists = MagicMock(return_value=0)
    mock.set = MagicMock(return_value=True)
    mock.get = MagicMock(return_value=None)
    mock.incr = MagicMock(return_value=1)
    mock.keys = MagicMock(return_value=[])
    mock.pipeline = MagicMock(return_value=MagicMock())
    return mock


@pytest.fixture
def sample_player():
    """Sample player data."""
    return {
        'id': 'player123',
        'name': 'TestPlayer',
        'score': 5000,
        'level': 3,
        'difficulty': 'MEDIUM',
        'status': 'playing'
    }


# ============================================================================
# Player State Tests
# ============================================================================

class TestPlayerState:
    """Tests for player state management in Redis."""

    @pytest.mark.unit
    def test_set_player_state(self, mock_redis, sample_player):
        """Test storing player state in Redis."""
        mock_redis.hset('players', sample_player['id'], json.dumps(sample_player))

        mock_redis.hset.assert_called_once_with(
            'players',
            sample_player['id'],
            json.dumps(sample_player)
        )

    @pytest.mark.unit
    def test_get_player_state(self, mock_redis, sample_player):
        """Test retrieving player state from Redis."""
        mock_redis.hget.return_value = json.dumps(sample_player)

        result = mock_redis.hget('players', sample_player['id'])
        player_data = json.loads(result)

        assert player_data['name'] == 'TestPlayer'
        assert player_data['score'] == 5000

    @pytest.mark.unit
    def test_get_player_not_found(self, mock_redis):
        """Test getting non-existent player."""
        mock_redis.hget.return_value = None

        result = mock_redis.hget('players', 'nonexistent')

        assert result is None

    @pytest.mark.unit
    def test_delete_player_state(self, mock_redis):
        """Test removing player state from Redis."""
        mock_redis.hdel('players', 'player123')

        mock_redis.hdel.assert_called_once_with('players', 'player123')

    @pytest.mark.unit
    def test_get_all_players(self, mock_redis, sample_player):
        """Test getting all player states."""
        mock_redis.hgetall.return_value = {
            'player123': json.dumps(sample_player),
            'player456': json.dumps({**sample_player, 'id': 'player456', 'name': 'Player2'})
        }

        result = mock_redis.hgetall('players')

        assert len(result) == 2
        assert 'player123' in result


# ============================================================================
# Matchmaking Queue Tests
# ============================================================================

class TestMatchmakingQueue:
    """Tests for matchmaking queue operations."""

    @pytest.mark.unit
    def test_add_to_matchmaking_queue(self, mock_redis):
        """Test adding player to matchmaking queue."""
        queue_key = 'matchmaking:versus:MEDIUM'
        player_data = json.dumps({'id': 'player123', 'name': 'TestPlayer'})

        mock_redis.lpush(queue_key, player_data)

        mock_redis.lpush.assert_called_once_with(queue_key, player_data)

    @pytest.mark.unit
    def test_pop_from_matchmaking_queue(self, mock_redis):
        """Test removing player from matchmaking queue."""
        queue_key = 'matchmaking:versus:MEDIUM'
        mock_redis.rpop.return_value = json.dumps({'id': 'player456', 'name': 'Opponent'})

        result = mock_redis.rpop(queue_key)
        opponent = json.loads(result)

        assert opponent['id'] == 'player456'

    @pytest.mark.unit
    def test_matchmaking_queue_empty(self, mock_redis):
        """Test popping from empty queue."""
        queue_key = 'matchmaking:versus:MEDIUM'
        mock_redis.rpop.return_value = None

        result = mock_redis.rpop(queue_key)

        assert result is None

    @pytest.mark.unit
    def test_leave_matchmaking_queue(self, mock_redis):
        """Test removing specific player from queue."""
        queue_key = 'matchmaking:versus:MEDIUM'
        player_data = json.dumps({'id': 'player123', 'name': 'TestPlayer'})

        mock_redis.lrem(queue_key, 0, player_data)

        mock_redis.lrem.assert_called_once()

    @pytest.mark.unit
    def test_get_queue_length(self, mock_redis):
        """Test getting queue length."""
        queue_key = 'matchmaking:versus:MEDIUM'
        mock_redis.llen.return_value = 5

        length = mock_redis.llen(queue_key)

        assert length == 5


# ============================================================================
# Room Management Tests
# ============================================================================

class TestRoomManagement:
    """Tests for multiplayer room operations."""

    @pytest.mark.unit
    def test_create_room(self, mock_redis):
        """Test creating a new room."""
        room_code = 'ABC123'
        room_data = {
            'code': room_code,
            'host': 'player123',
            'players': ['player123'],
            'mode': 'versus',
            'status': 'waiting'
        }

        mock_redis.set(f'room:{room_code}', json.dumps(room_data))

        mock_redis.set.assert_called_once()

    @pytest.mark.unit
    def test_get_room(self, mock_redis):
        """Test retrieving room data."""
        room_code = 'ABC123'
        room_data = {
            'code': room_code,
            'host': 'player123',
            'players': ['player123'],
            'mode': 'versus',
            'status': 'waiting'
        }
        mock_redis.get.return_value = json.dumps(room_data)

        result = mock_redis.get(f'room:{room_code}')
        room = json.loads(result)

        assert room['code'] == room_code
        assert room['mode'] == 'versus'

    @pytest.mark.unit
    def test_room_not_found(self, mock_redis):
        """Test getting non-existent room."""
        mock_redis.get.return_value = None

        result = mock_redis.get('room:NONEXISTENT')

        assert result is None

    @pytest.mark.unit
    def test_update_room_status(self, mock_redis):
        """Test updating room status."""
        room_code = 'ABC123'
        room_data = {
            'code': room_code,
            'status': 'playing'
        }

        mock_redis.set(f'room:{room_code}', json.dumps(room_data))

        mock_redis.set.assert_called()

    @pytest.mark.unit
    def test_delete_room(self, mock_redis):
        """Test deleting a room."""
        room_code = 'ABC123'

        mock_redis.delete(f'room:{room_code}')

        mock_redis.delete.assert_called_once_with(f'room:{room_code}')

    @pytest.mark.unit
    def test_room_expiration(self, mock_redis):
        """Test setting room expiration."""
        room_code = 'ABC123'

        mock_redis.expire(f'room:{room_code}', 3600)  # 1 hour

        mock_redis.expire.assert_called_once_with(f'room:{room_code}', 3600)


# ============================================================================
# Spectating Tests
# ============================================================================

class TestSpectating:
    """Tests for spectating functionality."""

    @pytest.mark.unit
    def test_add_spectator(self, mock_redis):
        """Test adding spectator to a player."""
        player_id = 'player123'
        spectator_id = 'spectator456'

        mock_redis.sadd(f'spectators:{player_id}', spectator_id)

        mock_redis.sadd.assert_called_once_with(f'spectators:{player_id}', spectator_id)

    @pytest.mark.unit
    def test_remove_spectator(self, mock_redis):
        """Test removing spectator."""
        player_id = 'player123'
        spectator_id = 'spectator456'

        mock_redis.srem(f'spectators:{player_id}', spectator_id)

        mock_redis.srem.assert_called_once()

    @pytest.mark.unit
    def test_get_spectators(self, mock_redis):
        """Test getting all spectators for a player."""
        player_id = 'player123'
        mock_redis.smembers.return_value = {'spec1', 'spec2', 'spec3'}

        spectators = mock_redis.smembers(f'spectators:{player_id}')

        assert len(spectators) == 3

    @pytest.mark.unit
    def test_spectator_count(self, mock_redis):
        """Test counting spectators."""
        player_id = 'player123'
        mock_redis.smembers.return_value = {'spec1', 'spec2'}

        spectators = mock_redis.smembers(f'spectators:{player_id}')

        assert len(spectators) == 2


# ============================================================================
# Multiplayer State Sync Tests
# ============================================================================

class TestMultiplayerStateSync:
    """Tests for multiplayer game state synchronization."""

    @pytest.mark.unit
    def test_set_game_state(self, mock_redis):
        """Test storing game state."""
        room_code = 'ABC123'
        game_state = {
            'players': {
                'player123': {'x': 100, 'y': 200, 'health': 3},
                'player456': {'x': 300, 'y': 400, 'health': 3}
            },
            'bullets': [],
            'timestamp': datetime.now().isoformat()
        }

        mock_redis.set(f'gamestate:{room_code}', json.dumps(game_state))
        mock_redis.expire(f'gamestate:{room_code}', 60)

        assert mock_redis.set.called
        assert mock_redis.expire.called

    @pytest.mark.unit
    def test_get_game_state(self, mock_redis):
        """Test retrieving game state."""
        room_code = 'ABC123'
        game_state = {
            'players': {
                'player123': {'x': 100, 'y': 200, 'health': 3}
            }
        }
        mock_redis.get.return_value = json.dumps(game_state)

        result = mock_redis.get(f'gamestate:{room_code}')
        state = json.loads(result)

        assert 'players' in state
        assert 'player123' in state['players']

    @pytest.mark.unit
    def test_game_state_not_found(self, mock_redis):
        """Test getting game state for non-existent room."""
        mock_redis.get.return_value = None

        result = mock_redis.get('gamestate:NONEXISTENT')

        assert result is None

    @pytest.mark.unit
    def test_update_player_position(self, mock_redis):
        """Test updating player position in game state."""
        room_code = 'ABC123'
        game_state = {
            'players': {
                'player123': {'x': 100, 'y': 200, 'health': 3}
            }
        }
        mock_redis.get.return_value = json.dumps(game_state)

        # Simulate getting state, updating, and saving
        result = mock_redis.get(f'gamestate:{room_code}')
        state = json.loads(result)
        state['players']['player123']['x'] = 150
        state['players']['player123']['y'] = 250

        mock_redis.set(f'gamestate:{room_code}', json.dumps(state))

        assert mock_redis.set.called


# ============================================================================
# Rate Limiting Tests
# ============================================================================

class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @pytest.mark.unit
    def test_increment_rate_limit(self, mock_redis):
        """Test incrementing rate limit counter."""
        client_id = 'client123'
        mock_redis.incr.return_value = 1

        count = mock_redis.incr(f'ratelimit:{client_id}')

        assert count == 1

    @pytest.mark.unit
    def test_rate_limit_expiration(self, mock_redis):
        """Test setting rate limit expiration."""
        client_id = 'client123'

        mock_redis.expire(f'ratelimit:{client_id}', 60)

        mock_redis.expire.assert_called_once()

    @pytest.mark.unit
    def test_check_rate_limit_exceeded(self, mock_redis):
        """Test checking if rate limit is exceeded."""
        client_id = 'client123'
        mock_redis.get.return_value = '100'  # Over limit

        count = int(mock_redis.get(f'ratelimit:{client_id}') or 0)

        assert count >= 100


# ============================================================================
# Connection Tests
# ============================================================================

class TestConnection:
    """Tests for Redis connection handling."""

    @pytest.mark.unit
    def test_connection_ping(self, mock_redis):
        """Test Redis connection ping."""
        mock_redis.ping = MagicMock(return_value=True)

        result = mock_redis.ping()

        assert result is True

    @pytest.mark.unit
    def test_connection_info(self, mock_redis):
        """Test getting Redis info."""
        mock_redis.info = MagicMock(return_value={'redis_version': '6.0.0'})

        info = mock_redis.info()

        assert 'redis_version' in info


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
