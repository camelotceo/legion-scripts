"""
Pytest configuration and shared fixtures for Fighter Jet Game tests.

This module provides:
- Flask test client fixture
- FakeRedis fixture for mocking Redis
- Mock database fixtures
- Common test utilities
"""

import os
import sys
import json
import pytest
import tempfile
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

# Add project root to path BEFORE any other imports
PROJECT_ROOT = Path(__file__).parent.absolute()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging for tests
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


# ============================================================================
# Environment Setup
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Set up test environment variables."""
    os.environ['FLASK_ENV'] = 'testing'
    os.environ['TESTING'] = 'true'
    logger.info("Test environment configured")
    yield
    logger.info("Test environment cleanup complete")


# ============================================================================
# Flask App Fixtures
# ============================================================================

@pytest.fixture
def app():
    """Create Flask application for testing."""
    # Create temp directory for data
    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir) / 'data'
        data_dir.mkdir(exist_ok=True)

        # Patch module-level imports before importing server
        with patch.dict('sys.modules', {
            'redis_client': MagicMock(),
            'database': MagicMock()
        }):
            # Clear cached import
            if 'server' in sys.modules:
                del sys.modules['server']

            import server

            # Override paths
            server.DATA_DIR = data_dir
            server.LEADERBOARD_FILE = data_dir / 'leaderboard.json'
            server.USE_REDIS = False
            server.USE_POSTGRES = False
            server.active_players.clear()

            # Create empty leaderboard
            with open(server.LEADERBOARD_FILE, 'w') as f:
                json.dump([], f)

            server.app.config.update({
                'TESTING': True,
                'DEBUG': True,
            })

            logger.info(f"Flask app created with data dir: {data_dir}")
            yield server.app


@pytest.fixture
def client(app):
    """Create Flask test client."""
    with app.test_client() as client:
        logger.debug("Test client created")
        yield client


@pytest.fixture
def runner(app):
    """Create Flask CLI test runner."""
    return app.test_cli_runner()


# ============================================================================
# FakeRedis Fixtures
# ============================================================================

@pytest.fixture
def fake_redis():
    """Create FakeRedis instance for testing."""
    try:
        import fakeredis
        fake_server = fakeredis.FakeServer()
        fake_redis_client = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        logger.debug("FakeRedis instance created")
        yield fake_redis_client
        fake_redis_client.flushall()
    except ImportError:
        logger.warning("fakeredis not installed, using MagicMock")
        yield MagicMock()


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_player_data():
    """Sample player data for tests."""
    return {
        'id': 'test123',
        'name': 'TestPlayer',
        'score': 5000,
        'level': 3,
        'difficulty': 'MEDIUM',
        'color': 'blue',
        'status': 'playing',
        'lastAction': 'started_game',
        'lastActionEmoji': '🚀',
        'lastActionTime': datetime.now().isoformat(),
        'gameStartTime': datetime.now().isoformat(),
        'gameDuration': 120,
        'shieldActive': False,
        'currentWeapon': 'pistol',
        'bossHealth': None,
        'isNew': True,
        'defeatedBoss': False,
        'sessionId': None,
        'lastUpdate': 1703859600
    }


@pytest.fixture
def sample_leaderboard_entry():
    """Sample leaderboard entry for tests."""
    return {
        'name': 'Champion',
        'score': 15000,
        'difficulty': 'HARD',
        'level': 5,
        'duration': 300,
        'bossesDefeated': 4,
        'deathReason': 'killed_by_boss'
    }


@pytest.fixture
def sample_game_state():
    """Sample game state for spectator tests."""
    return {
        'player': {
            'x': 400,
            'y': 500,
            'health': 3,
            'score': 5000
        },
        'enemies': [
            {'x': 100, 'y': 100, 'type': 'drone'},
            {'x': 200, 'y': 150, 'type': 'drone'}
        ],
        'bullets': [
            {'x': 400, 'y': 480, 'dy': -10}
        ],
        'boss': None,
        'powerups': []
    }


# ============================================================================
# Cleanup Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def cleanup_after_test():
    """Clean up after each test."""
    yield
    # Any cleanup code here
    logger.debug("Test cleanup complete")


# ============================================================================
# Test Markers
# ============================================================================

def pytest_configure(config):
    """Configure custom pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "redis: Tests requiring Redis")
    config.addinivalue_line("markers", "database: Tests requiring PostgreSQL")
