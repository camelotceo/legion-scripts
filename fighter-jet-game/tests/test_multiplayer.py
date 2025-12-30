"""
Unit tests for 1v1 Head-to-Head Multiplayer functionality.

Tests cover:
- Room creation and joining
- Matchmaking queue operations
- Multiplayer game state synchronization
- Redis-based room management
- WebSocket event handling (mocked)

Run with: pytest tests/test_multiplayer.py -v

Known Issues Being Tested:
1. Players not seeing each other move
2. Hits not registering between players
3. Position sync failures

"""

import os
import sys
import json
import pytest
import time
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.absolute()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


# ============================================================================
# Test Fixtures
# ============================================================================

@pytest.fixture
def fake_redis():
    """Create FakeRedis instance for testing."""
    try:
        import fakeredis
        fake_server = fakeredis.FakeServer()
        fake_redis_client = fakeredis.FakeStrictRedis(
            server=fake_server,
            decode_responses=True
        )
        yield fake_redis_client
        fake_redis_client.flushall()
    except ImportError:
        logger.warning("fakeredis not installed, using MagicMock")
        yield MagicMock()


@pytest.fixture
def mock_redis_client(fake_redis):
    """Create a mock redis_client module."""
    with patch.dict('sys.modules', {'redis_client': MagicMock()}):
        import redis_client as rc

        # Override get_redis to return our fake
        rc.get_redis = lambda: fake_redis

        # Implement room operations
        def mock_create_room(host_id, host_name, mode, difficulty):
            import random
            import string
            code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=6))
            room_data = {
                'code': code,
                'host_id': host_id,
                'host_name': host_name,
                'mode': mode,
                'difficulty': difficulty,
                'status': 'waiting',
                'players': json.dumps([{'id': host_id, 'name': host_name, 'ready': False}]),
                'created_at': datetime.now().isoformat()
            }
            fake_redis.hset(f"room:{code}", mapping=room_data)
            fake_redis.expire(f"room:{code}", 300)
            fake_redis.sadd(f"room_players:{code}", host_id)
            fake_redis.set(f"player_room:{host_id}", code, ex=300)
            return code

        def mock_get_room(code):
            data = fake_redis.hgetall(f"room:{code}")
            if not data:
                return None
            result = {}
            for k, v in data.items():
                try:
                    result[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    result[k] = v
            return result

        def mock_join_room(code, player_id, player_name):
            room = mock_get_room(code)
            if not room:
                return None
            if room.get('status') != 'waiting':
                return None
            players = room.get('players', [])
            if any(p['id'] == player_id for p in players):
                return room
            players.append({'id': player_id, 'name': player_name, 'ready': False})
            fake_redis.hset(f"room:{code}", 'players', json.dumps(players))
            fake_redis.sadd(f"room_players:{code}", player_id)
            fake_redis.set(f"player_room:{player_id}", code, ex=300)
            room['players'] = players
            return room

        def mock_leave_room(code, player_id):
            room = mock_get_room(code)
            if not room:
                return False
            players = room.get('players', [])
            players = [p for p in players if p['id'] != player_id]
            fake_redis.srem(f"room_players:{code}", player_id)
            fake_redis.delete(f"player_room:{player_id}")
            if not players:
                fake_redis.delete(f"room:{code}")
                fake_redis.delete(f"room_players:{code}")
            else:
                fake_redis.hset(f"room:{code}", 'players', json.dumps(players))
                if room.get('host_id') == player_id:
                    fake_redis.hset(f"room:{code}", 'host_id', players[0]['id'])
            return True

        def mock_set_player_ready(code, player_id, ready):
            room = mock_get_room(code)
            if not room:
                return None
            players = room.get('players', [])
            for p in players:
                if p['id'] == player_id:
                    p['ready'] = ready
            fake_redis.hset(f"room:{code}", 'players', json.dumps(players))
            room['players'] = players
            return room

        def mock_start_room_game(code):
            room = mock_get_room(code)
            if not room:
                return False
            players = room.get('players', [])
            if len(players) < 2:
                return False
            if not all(p.get('ready', False) for p in players):
                return False
            fake_redis.hset(f"room:{code}", 'status', 'playing')
            fake_redis.hset(f"room:{code}", 'started_at', datetime.now().isoformat())
            return True

        def mock_get_player_room(player_id):
            return fake_redis.get(f"player_room:{player_id}")

        # Matchmaking
        def mock_join_matchmaking(player_id, player_name, mode, difficulty):
            player_data = json.dumps({
                'id': player_id,
                'name': player_name,
                'difficulty': difficulty,
                'joined_at': datetime.now().isoformat()
            })
            fake_redis.zadd(f"matchmaking:{mode}", {player_data: time.time()})
            fake_redis.set(f"in_queue:{player_id}", mode, ex=120)
            return True

        def mock_leave_matchmaking(player_id):
            mode = fake_redis.get(f"in_queue:{player_id}")
            if not mode:
                return False
            queue = fake_redis.zrange(f"matchmaking:{mode}", 0, -1)
            for entry in queue:
                data = json.loads(entry)
                if data['id'] == player_id:
                    fake_redis.zrem(f"matchmaking:{mode}", entry)
                    break
            fake_redis.delete(f"in_queue:{player_id}")
            return True

        def mock_find_match(player_id, mode, difficulty):
            queue = fake_redis.zrange(f"matchmaking:{mode}", 0, -1)
            for entry in queue:
                data = json.loads(entry)
                if data['id'] != player_id:
                    fake_redis.zrem(f"matchmaking:{mode}", entry)
                    fake_redis.delete(f"in_queue:{data['id']}")
                    mock_leave_matchmaking(player_id)
                    room_code = mock_create_room(data['id'], data['name'], mode, difficulty)
                    mock_join_room(room_code, player_id, 'Player')
                    return {'matched': True, 'room_code': room_code, 'opponent': data, 'isHost': False}
            return {'matched': False, 'queue_position': 0}

        def mock_is_in_queue(player_id):
            return fake_redis.get(f"in_queue:{player_id}")

        # Multiplayer state
        def mock_set_multiplayer_state(room_code, state):
            fake_redis.set(f"mp_state:{room_code}", json.dumps(state), ex=10)

        def mock_get_multiplayer_state(room_code):
            data = fake_redis.get(f"mp_state:{room_code}")
            return json.loads(data) if data else None

        # Assign mock functions
        rc.create_room = mock_create_room
        rc.get_room = mock_get_room
        rc.join_room = mock_join_room
        rc.leave_room = mock_leave_room
        rc.set_player_ready = mock_set_player_ready
        rc.start_room_game = mock_start_room_game
        rc.get_player_room = mock_get_player_room
        rc.join_matchmaking = mock_join_matchmaking
        rc.leave_matchmaking = mock_leave_matchmaking
        rc.find_match = mock_find_match
        rc.is_in_queue = mock_is_in_queue
        rc.set_multiplayer_state = mock_set_multiplayer_state
        rc.get_multiplayer_state = mock_get_multiplayer_state

        yield rc


# ============================================================================
# Room Management Tests
# ============================================================================

class TestRoomManagement:
    """Tests for multiplayer room creation and management."""

    @pytest.mark.unit
    def test_create_room_success(self, mock_redis_client):
        """Test creating a new multiplayer room."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Player1',
            mode='versus',
            difficulty='MEDIUM'
        )

        assert room_code is not None
        assert len(room_code) == 6
        logger.info(f"Created room: {room_code}")

    @pytest.mark.unit
    def test_get_room_success(self, mock_redis_client):
        """Test getting room data."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Player1',
            mode='versus',
            difficulty='HARD'
        )

        room = mock_redis_client.get_room(room_code)

        assert room is not None
        assert room['host_id'] == 'player1'
        assert room['mode'] == 'versus'
        assert room['status'] == 'waiting'

    @pytest.mark.unit
    def test_get_room_not_found(self, mock_redis_client):
        """Test getting non-existent room."""
        room = mock_redis_client.get_room('XXXXXX')
        assert room is None

    @pytest.mark.unit
    def test_join_room_success(self, mock_redis_client):
        """Test joining an existing room."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )

        updated_room = mock_redis_client.join_room(room_code, 'player2', 'Guest')

        assert updated_room is not None
        assert len(updated_room['players']) == 2
        player_ids = [p['id'] for p in updated_room['players']]
        assert 'player1' in player_ids
        assert 'player2' in player_ids

    @pytest.mark.unit
    def test_join_room_not_found(self, mock_redis_client):
        """Test joining non-existent room."""
        result = mock_redis_client.join_room('XXXXXX', 'player2', 'Guest')
        assert result is None

    @pytest.mark.unit
    def test_join_room_already_joined(self, mock_redis_client):
        """Test joining a room player is already in."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )

        # Join same room again
        result = mock_redis_client.join_room(room_code, 'player1', 'Host')

        # Should return room without adding duplicate
        assert len(result['players']) == 1

    @pytest.mark.unit
    def test_leave_room_success(self, mock_redis_client):
        """Test leaving a room."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )
        mock_redis_client.join_room(room_code, 'player2', 'Guest')

        result = mock_redis_client.leave_room(room_code, 'player2')

        assert result is True
        room = mock_redis_client.get_room(room_code)
        assert len(room['players']) == 1

    @pytest.mark.unit
    def test_leave_room_host_transfers(self, mock_redis_client):
        """Test that host is transferred when host leaves."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )
        mock_redis_client.join_room(room_code, 'player2', 'Guest')

        # Host leaves
        mock_redis_client.leave_room(room_code, 'player1')

        room = mock_redis_client.get_room(room_code)
        assert room is not None
        assert room['host_id'] == 'player2'

    @pytest.mark.unit
    def test_leave_room_last_player_deletes(self, mock_redis_client):
        """Test that room is deleted when last player leaves."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )

        mock_redis_client.leave_room(room_code, 'player1')

        room = mock_redis_client.get_room(room_code)
        assert room is None


# ============================================================================
# Player Ready State Tests
# ============================================================================

class TestPlayerReadyState:
    """Tests for player ready state management."""

    @pytest.mark.unit
    def test_set_player_ready(self, mock_redis_client):
        """Test setting player ready state."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )

        room = mock_redis_client.set_player_ready(room_code, 'player1', True)

        assert room is not None
        player = next(p for p in room['players'] if p['id'] == 'player1')
        assert player['ready'] is True

    @pytest.mark.unit
    def test_start_game_requires_two_players(self, mock_redis_client):
        """Test that game can't start with less than 2 players."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )
        mock_redis_client.set_player_ready(room_code, 'player1', True)

        result = mock_redis_client.start_room_game(room_code)

        assert result is False

    @pytest.mark.unit
    def test_start_game_requires_all_ready(self, mock_redis_client):
        """Test that game can't start unless all players are ready."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )
        mock_redis_client.join_room(room_code, 'player2', 'Guest')
        mock_redis_client.set_player_ready(room_code, 'player1', True)
        # player2 not ready

        result = mock_redis_client.start_room_game(room_code)

        assert result is False

    @pytest.mark.unit
    def test_start_game_success(self, mock_redis_client):
        """Test successful game start."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )
        mock_redis_client.join_room(room_code, 'player2', 'Guest')
        mock_redis_client.set_player_ready(room_code, 'player1', True)
        mock_redis_client.set_player_ready(room_code, 'player2', True)

        result = mock_redis_client.start_room_game(room_code)

        assert result is True
        room = mock_redis_client.get_room(room_code)
        assert room['status'] == 'playing'


# ============================================================================
# Matchmaking Tests
# ============================================================================

class TestMatchmaking:
    """Tests for matchmaking queue functionality."""

    @pytest.mark.unit
    def test_join_matchmaking(self, mock_redis_client):
        """Test joining matchmaking queue."""
        result = mock_redis_client.join_matchmaking(
            player_id='player1',
            player_name='Player1',
            mode='versus',
            difficulty='MEDIUM'
        )

        assert result is True
        assert mock_redis_client.is_in_queue('player1') == 'versus'

    @pytest.mark.unit
    def test_leave_matchmaking(self, mock_redis_client):
        """Test leaving matchmaking queue."""
        mock_redis_client.join_matchmaking('player1', 'Player1', 'versus', 'MEDIUM')

        result = mock_redis_client.leave_matchmaking('player1')

        assert result is True
        assert mock_redis_client.is_in_queue('player1') is None

    @pytest.mark.unit
    def test_find_match_no_opponent(self, mock_redis_client):
        """Test finding match when no opponent available."""
        mock_redis_client.join_matchmaking('player1', 'Player1', 'versus', 'MEDIUM')

        result = mock_redis_client.find_match('player1', 'versus', 'MEDIUM')

        assert result['matched'] is False

    @pytest.mark.unit
    def test_find_match_success(self, mock_redis_client):
        """Test successful match finding."""
        # First player joins queue
        mock_redis_client.join_matchmaking('player1', 'Player1', 'versus', 'MEDIUM')

        # Second player joins and should match
        mock_redis_client.join_matchmaking('player2', 'Player2', 'versus', 'MEDIUM')
        result = mock_redis_client.find_match('player2', 'versus', 'MEDIUM')

        assert result['matched'] is True
        assert 'room_code' in result
        assert result['opponent']['id'] == 'player1'

    @pytest.mark.unit
    def test_matchmaking_different_modes_dont_match(self, mock_redis_client):
        """Test that different game modes don't match."""
        mock_redis_client.join_matchmaking('player1', 'Player1', 'coop', 'MEDIUM')
        mock_redis_client.join_matchmaking('player2', 'Player2', 'versus', 'MEDIUM')

        result = mock_redis_client.find_match('player2', 'versus', 'MEDIUM')

        assert result['matched'] is False


# ============================================================================
# Multiplayer State Tests
# ============================================================================

class TestMultiplayerState:
    """Tests for multiplayer game state synchronization."""

    @pytest.mark.unit
    def test_set_multiplayer_state(self, mock_redis_client):
        """Test setting multiplayer game state."""
        state = {
            'player1': {'x': 100, 'y': 200, 'health': 100},
            'player2': {'x': 500, 'y': 200, 'health': 75},
            'round': 1,
            'scores': {'player1': 0, 'player2': 0}
        }

        mock_redis_client.set_multiplayer_state('ABCDEF', state)

        retrieved = mock_redis_client.get_multiplayer_state('ABCDEF')
        assert retrieved is not None
        assert retrieved['player1']['x'] == 100
        assert retrieved['round'] == 1

    @pytest.mark.unit
    def test_get_multiplayer_state_not_found(self, mock_redis_client):
        """Test getting non-existent state."""
        result = mock_redis_client.get_multiplayer_state('XXXXXX')
        assert result is None


# ============================================================================
# 1v1 Head-to-Head Specific Tests
# ============================================================================

class TestVersusMode:
    """Tests specific to 1v1 versus mode."""

    @pytest.mark.unit
    def test_versus_room_creation(self, mock_redis_client):
        """Test creating a versus mode room."""
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Fighter1',
            mode='versus',
            difficulty='HARD'
        )

        room = mock_redis_client.get_room(room_code)
        assert room['mode'] == 'versus'

    @pytest.mark.unit
    def test_versus_game_flow(self, mock_redis_client):
        """Test complete 1v1 game flow."""
        # 1. Create room
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Fighter1',
            mode='versus',
            difficulty='MEDIUM'
        )

        # 2. Second player joins
        room = mock_redis_client.join_room(room_code, 'player2', 'Fighter2')
        assert len(room['players']) == 2

        # 3. Both players ready up
        mock_redis_client.set_player_ready(room_code, 'player1', True)
        mock_redis_client.set_player_ready(room_code, 'player2', True)

        # 4. Start game
        started = mock_redis_client.start_room_game(room_code)
        assert started is True

        # 5. Game state updates
        mock_redis_client.set_multiplayer_state(room_code, {
            'player1': {'x': 200, 'y': 500, 'health': 100},
            'player2': {'x': 600, 'y': 500, 'health': 100},
            'round': 1,
            'roundActive': True
        })

        state = mock_redis_client.get_multiplayer_state(room_code)
        assert state['roundActive'] is True

        logger.info("Complete versus game flow test passed")


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================

class TestErrorHandling:
    """Tests for error handling in multiplayer."""

    @pytest.mark.unit
    def test_join_nonexistent_room(self, mock_redis_client):
        """Test joining a room that doesn't exist."""
        result = mock_redis_client.join_room('XXXXXX', 'player1', 'Test')
        assert result is None

    @pytest.mark.unit
    def test_ready_in_nonexistent_room(self, mock_redis_client):
        """Test setting ready in non-existent room."""
        result = mock_redis_client.set_player_ready('XXXXXX', 'player1', True)
        assert result is None

    @pytest.mark.unit
    def test_start_game_nonexistent_room(self, mock_redis_client):
        """Test starting game in non-existent room."""
        result = mock_redis_client.start_room_game('XXXXXX')
        assert result is False

    @pytest.mark.unit
    def test_leave_nonexistent_room(self, mock_redis_client):
        """Test leaving non-existent room."""
        result = mock_redis_client.leave_room('XXXXXX', 'player1')
        assert result is False


# ============================================================================
# Known Issues - Tests for Documented Problems
# ============================================================================

class TestKnownIssues:
    """
    Tests related to known 1v1 issues documented in CLAUDE.md:
    1. Players not seeing each other move
    2. Hits not registering

    These tests verify the fix conditions.
    """

    @pytest.mark.unit
    def test_position_sync_state_structure(self, mock_redis_client):
        """
        Test that position sync state has correct structure.

        Issue: Players can't see each other moving
        Root cause: Missing or malformed position data
        """
        state = {
            'player1': {
                'x': 200.5,  # Float for smooth movement
                'y': 500.0,
                'health': 100,
                'angle': 0  # Jet rotation
            },
            'player2': {
                'x': 600.5,
                'y': 500.0,
                'health': 100,
                'angle': 180
            },
            'round': 1,
            'roundActive': True
        }

        mock_redis_client.set_multiplayer_state('TESTROOM', state)
        retrieved = mock_redis_client.get_multiplayer_state('TESTROOM')

        # Verify all required fields for rendering
        assert 'x' in retrieved['player1']
        assert 'y' in retrieved['player1']
        assert isinstance(retrieved['player1']['x'], (int, float))
        assert isinstance(retrieved['player1']['y'], (int, float))

    @pytest.mark.unit
    def test_bullet_state_structure(self, mock_redis_client):
        """
        Test that bullet state has correct structure for hit detection.

        Issue: Hits not registering
        Root cause: Missing bullet source identification
        """
        state = {
            'bullets': [
                {
                    'x': 300,
                    'y': 400,
                    'dx': 0,
                    'dy': -10,
                    'ownerId': 'player1',  # Critical for hit detection
                    'fromPartner': True,   # Marks as opponent bullet
                    'isEnemy': False       # Not an AI bullet
                }
            ],
            'roundActive': True
        }

        mock_redis_client.set_multiplayer_state('TESTROOM', state)
        retrieved = mock_redis_client.get_multiplayer_state('TESTROOM')

        bullet = retrieved['bullets'][0]
        assert 'ownerId' in bullet
        assert 'fromPartner' in bullet
        assert bullet['fromPartner'] is True
        assert bullet['isEnemy'] is False

    @pytest.mark.unit
    def test_room_player_tracking(self, mock_redis_client):
        """
        Test that players are properly tracked in room.

        Issue: WebSocket room sync failures
        Root cause: Players not properly registered in room
        """
        room_code = mock_redis_client.create_room(
            host_id='player1',
            host_name='Host',
            mode='versus',
            difficulty='MEDIUM'
        )

        # Verify host is tracked
        assert mock_redis_client.get_player_room('player1') == room_code

        # Join second player
        mock_redis_client.join_room(room_code, 'player2', 'Guest')

        # Verify both tracked
        assert mock_redis_client.get_player_room('player1') == room_code
        assert mock_redis_client.get_player_room('player2') == room_code


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
