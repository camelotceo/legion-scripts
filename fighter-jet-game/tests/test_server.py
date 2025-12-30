"""
Unit tests for server.py - Flask API endpoints.

Tests all API endpoints for:
- Leaderboard management
- Player management (join, update, leave)
- Action reporting
- Session management
- Spectator functionality
- Victory saving

Run with: pytest tests/test_server.py -v
"""

import json
import pytest
import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Leaderboard API Tests
# ============================================================================

class TestLeaderboardAPI:
    """Tests for leaderboard endpoints."""

    @pytest.mark.unit
    def test_get_empty_leaderboard(self, client):
        """Test GET /api/leaderboard with empty leaderboard."""
        response = client.get('/api/leaderboard')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 0
        logger.info("Empty leaderboard test passed")

    @pytest.mark.unit
    def test_add_score_success(self, client):
        """Test POST /api/leaderboard with valid data."""
        score_data = {
            'name': 'TestPlayer',
            'score': 10000,
            'difficulty': 'HARD',
            'level': 5,
            'duration': 300,
            'bossesDefeated': 4,
            'deathReason': 'killed_by_boss'
        }

        response = client.post(
            '/api/leaderboard',
            data=json.dumps(score_data),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert 'leaderboard' in data
        logger.info("Add score test passed")

    @pytest.mark.unit
    def test_add_score_missing_score(self, client):
        """Test POST /api/leaderboard without score."""
        score_data = {
            'name': 'TestPlayer'
        }

        response = client.post(
            '/api/leaderboard',
            data=json.dumps(score_data),
            content_type='application/json'
        )

        assert response.status_code == 400

    @pytest.mark.unit
    def test_leaderboard_sorting(self, client):
        """Test that leaderboard is sorted by score descending."""
        import time
        scores = [
            {'name': 'SortPlayer1', 'score': 5000, 'difficulty': 'EASY', 'level': 2, 'duration': 100},
            {'name': 'SortPlayer2', 'score': 15000, 'difficulty': 'HARD', 'level': 5, 'duration': 300},
            {'name': 'SortPlayer3', 'score': 10000, 'difficulty': 'MEDIUM', 'level': 4, 'duration': 200},
        ]

        for score_data in scores:
            response = client.post(
                '/api/leaderboard',
                data=json.dumps(score_data),
                content_type='application/json'
            )
            assert response.status_code == 200
            time.sleep(0.01)  # Small delay to ensure unique timestamps

        response = client.get('/api/leaderboard')
        data = json.loads(response.data)

        # Verify scores are sorted descending
        assert len(data) >= 1, "Leaderboard should have at least one entry"
        if len(data) >= 2:
            for i in range(len(data) - 1):
                assert data[i]['score'] >= data[i+1]['score'], "Leaderboard should be sorted by score descending"


# ============================================================================
# Player API Tests
# ============================================================================

class TestPlayerAPI:
    """Tests for player management endpoints."""

    @pytest.mark.unit
    def test_player_join_success(self, client):
        """Test POST /api/players/join with valid data."""
        join_data = {
            'name': 'TestPlayer',
            'difficulty': 'MEDIUM',
            'color': 'blue'
        }

        response = client.post(
            '/api/players/join',
            data=json.dumps(join_data),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert 'playerId' in data
        assert data['name'] == 'TestPlayer'
        logger.info(f"Player joined with ID: {data['playerId']}")

    @pytest.mark.unit
    def test_player_join_generates_handle(self, client):
        """Test that joining without name generates a handle."""
        response = client.post(
            '/api/players/join',
            data=json.dumps({}),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['name'].startswith('Player_')
        assert len(data['name']) == 11  # Player_ + 4 chars

    @pytest.mark.unit
    def test_player_update_score(self, client):
        """Test POST /api/players/update with score update."""
        # First join
        join_response = client.post(
            '/api/players/join',
            data=json.dumps({'name': 'UpdateTest'}),
            content_type='application/json'
        )
        player_id = json.loads(join_response.data)['playerId']

        # Update score
        update_data = {
            'playerId': player_id,
            'score': 5000,
            'level': 3,
            'status': 'playing'
        }

        response = client.post(
            '/api/players/update',
            data=json.dumps(update_data),
            content_type='application/json'
        )

        assert response.status_code == 200

    @pytest.mark.unit
    def test_player_update_missing_id(self, client):
        """Test POST /api/players/update without playerId."""
        update_data = {
            'score': 5000
        }

        response = client.post(
            '/api/players/update',
            data=json.dumps(update_data),
            content_type='application/json'
        )

        assert response.status_code == 400

    @pytest.mark.unit
    def test_player_leave(self, client):
        """Test POST /api/players/leave."""
        # First join
        join_response = client.post(
            '/api/players/join',
            data=json.dumps({'name': 'LeaveTest'}),
            content_type='application/json'
        )
        player_id = json.loads(join_response.data)['playerId']

        # Leave
        response = client.post(
            '/api/players/leave',
            data=json.dumps({'playerId': player_id}),
            content_type='application/json'
        )

        assert response.status_code == 200

    @pytest.mark.unit
    def test_get_active_players(self, client):
        """Test GET /api/players/active."""
        # Join some players
        for i in range(3):
            client.post(
                '/api/players/join',
                data=json.dumps({'name': f'Player{i}'}),
                content_type='application/json'
            )

        response = client.get('/api/players/active')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)
        assert len(data) == 3


# ============================================================================
# Action API Tests
# ============================================================================

class TestActionAPI:
    """Tests for action reporting endpoint."""

    @pytest.mark.unit
    def test_report_action_success(self, client):
        """Test POST /api/players/action with valid action."""
        # First join
        join_response = client.post(
            '/api/players/join',
            data=json.dumps({'name': 'ActionTest'}),
            content_type='application/json'
        )
        player_id = json.loads(join_response.data)['playerId']

        # Report action
        action_data = {
            'playerId': player_id,
            'action': 'started_game'
        }

        response = client.post(
            '/api/players/action',
            data=json.dumps(action_data),
            content_type='application/json'
        )

        assert response.status_code == 200
        data = json.loads(response.data)
        assert data['success'] is True
        assert data['emoji'] == '🚀'

    @pytest.mark.unit
    def test_report_action_missing_fields(self, client):
        """Test POST /api/players/action with missing fields."""
        response = client.post(
            '/api/players/action',
            data=json.dumps({'playerId': 'test123'}),
            content_type='application/json'
        )
        assert response.status_code == 400


# ============================================================================
# Session API Tests
# ============================================================================

class TestSessionAPI:
    """Tests for session management endpoints."""

    @pytest.mark.unit
    def test_start_session_missing_name(self, client):
        """Test POST /api/players/start-session without name."""
        response = client.post(
            '/api/players/start-session',
            data=json.dumps({'difficulty': 'EASY'}),
            content_type='application/json'
        )

        assert response.status_code == 400

    @pytest.mark.unit
    def test_end_session_missing_id(self, client):
        """Test POST /api/players/end-session without sessionId."""
        response = client.post(
            '/api/players/end-session',
            data=json.dumps({'score': 10000}),
            content_type='application/json'
        )

        assert response.status_code == 400


# ============================================================================
# Spectator API Tests
# ============================================================================

class TestSpectatorAPI:
    """Tests for spectator endpoints."""

    @pytest.mark.unit
    def test_update_game_state_missing_fields(self, client):
        """Test POST /api/players/gamestate with missing fields."""
        response = client.post(
            '/api/players/gamestate',
            data=json.dumps({'playerId': 'test123'}),
            content_type='application/json'
        )
        assert response.status_code == 400

    @pytest.mark.unit
    def test_leave_spectate(self, client):
        """Test POST /api/players/spectate/<id>/leave."""
        response = client.post(
            '/api/players/spectate/test123/leave',
            data=json.dumps({'spectatorId': 'spec456'}),
            content_type='application/json'
        )

        assert response.status_code == 200

    @pytest.mark.unit
    def test_add_empty_comment(self, client):
        """Test POST /api/players/spectate/<id>/comment with empty message."""
        comment_data = {
            'from': 'Spectator1',
            'message': ''
        }

        response = client.post(
            '/api/players/spectate/test123/comment',
            data=json.dumps(comment_data),
            content_type='application/json'
        )

        assert response.status_code == 400

    @pytest.mark.unit
    def test_get_comments(self, client):
        """Test GET /api/players/spectate/<id>/comments."""
        response = client.get('/api/players/spectate/test123/comments')

        assert response.status_code == 200
        data = json.loads(response.data)
        assert isinstance(data, list)


# ============================================================================
# Player History API Tests
# ============================================================================

class TestPlayerHistoryAPI:
    """Tests for player history endpoint."""

    @pytest.mark.unit
    def test_get_history_not_available(self, client):
        """Test GET /api/players/history when database unavailable."""
        response = client.get('/api/players/history/TestPlayer')

        # Without Postgres, should return 503
        assert response.status_code == 503


# ============================================================================
# Victory API Tests
# ============================================================================

class TestVictoryAPI:
    """Tests for victory endpoint."""

    @pytest.mark.unit
    def test_save_victory_success(self, client):
        """Test POST /api/victory/save."""
        victory_data = {
            'name': 'Champion',
            'email': 'champion@test.com',
            'score': 50000,
            'enemiesKilled': 500,
            'duration': 600
        }

        response = client.post(
            '/api/victory/save',
            data=json.dumps(victory_data),
            content_type='application/json'
        )

        assert response.status_code == 200


# ============================================================================
# Complete Game Flow Test
# ============================================================================

class TestPlayerJourney:
    """End-to-end tests for typical player journeys."""

    @pytest.mark.unit
    def test_complete_game_flow(self, client):
        """Test a complete game flow from join to game over."""
        # 1. Join game
        join_response = client.post(
            '/api/players/join',
            data=json.dumps({
                'name': 'FlowTest',
                'difficulty': 'MEDIUM',
                'color': 'blue'
            }),
            content_type='application/json'
        )
        assert join_response.status_code == 200
        player_id = json.loads(join_response.data)['playerId']

        # 2. Start game
        action_response = client.post(
            '/api/players/action',
            data=json.dumps({
                'playerId': player_id,
                'action': 'started_game'
            }),
            content_type='application/json'
        )
        assert action_response.status_code == 200

        # 3. Update score
        update_response = client.post(
            '/api/players/update',
            data=json.dumps({
                'playerId': player_id,
                'score': 5000,
                'level': 2,
                'status': 'playing'
            }),
            content_type='application/json'
        )
        assert update_response.status_code == 200

        # 4. Get powerup
        powerup_response = client.post(
            '/api/players/action',
            data=json.dumps({
                'playerId': player_id,
                'action': 'got_shield'
            }),
            content_type='application/json'
        )
        assert powerup_response.status_code == 200

        # 5. Game over
        over_response = client.post(
            '/api/players/action',
            data=json.dumps({
                'playerId': player_id,
                'action': 'game_over'
            }),
            content_type='application/json'
        )
        assert over_response.status_code == 200

        # 6. Submit score
        score_response = client.post(
            '/api/leaderboard',
            data=json.dumps({
                'name': 'FlowTest',
                'score': 15000,
                'difficulty': 'MEDIUM',
                'level': 3,
                'duration': 180
            }),
            content_type='application/json'
        )
        assert score_response.status_code == 200

        # 7. Leave
        leave_response = client.post(
            '/api/players/leave',
            data=json.dumps({'playerId': player_id}),
            content_type='application/json'
        )
        assert leave_response.status_code == 200

        logger.info("Complete game flow test passed")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
