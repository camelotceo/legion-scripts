"""
PostgreSQL database module for Fighter Jet Game.
Handles player profiles, game sessions, events, and leaderboards.
"""

import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime
from contextlib import contextmanager

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://gameserver:localdev123@localhost:5432/fighter_jet')


@contextmanager
def get_db():
    """Context manager for database connections."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def get_or_create_player(name: str) -> dict:
    """Get existing player or create new one."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Try to get existing player
            cur.execute("SELECT * FROM players WHERE name = %s", (name,))
            player = cur.fetchone()

            if player:
                return dict(player)

            # Create new player
            cur.execute(
                "INSERT INTO players (name) VALUES (%s) RETURNING *",
                (name,)
            )
            return dict(cur.fetchone())


def create_game_session(player_id: int, difficulty: str) -> int:
    """Create a new game session and return its ID."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO game_sessions (player_id, difficulty)
                   VALUES (%s, %s) RETURNING id""",
                (player_id, difficulty)
            )
            return cur.fetchone()[0]


def end_game_session(session_id: int, score: int, level: int, duration: int,
                     death_reason: str, bosses_defeated: int):
    """End a game session with final stats."""
    with get_db() as conn:
        with conn.cursor() as cur:
            # Update session
            cur.execute(
                """UPDATE game_sessions
                   SET ended_at = NOW(), score = %s, level = %s,
                       duration = %s, death_reason = %s, bosses_defeated = %s
                   WHERE id = %s
                   RETURNING player_id""",
                (score, level, duration, death_reason, bosses_defeated, session_id)
            )
            result = cur.fetchone()
            if not result:
                return

            player_id = result[0]

            # Update player stats
            cur.execute(
                """UPDATE players
                   SET total_games = total_games + 1,
                       total_score = total_score + %s,
                       total_playtime = total_playtime + %s,
                       best_score = GREATEST(best_score, %s),
                       best_level = GREATEST(best_level, %s),
                       best_duration = CASE
                           WHEN best_score < %s THEN %s
                           WHEN best_score = %s AND (best_duration IS NULL OR best_duration > %s) THEN %s
                           ELSE best_duration
                       END
                   WHERE id = %s""",
                (score, duration, score, level, score, duration, score, duration, duration, player_id)
            )


def log_game_event(session_id: int, event_type: str, emoji: str, details: dict = None):
    """Log a game event."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO game_events (session_id, event_type, emoji, details)
                   VALUES (%s, %s, %s, %s)""",
                (session_id, event_type, emoji, psycopg2.extras.Json(details or {}))
            )


def get_leaderboard(limit: int = 10) -> list:
    """Get top scores with fastest times."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT name, score, duration, level, difficulty,
                          bosses_defeated, death_reason, date
                   FROM leaderboard_view
                   LIMIT %s""",
                (limit,)
            )
            return [dict(row) for row in cur.fetchall()]


def add_leaderboard_entry(name: str, score: int, duration: int, level: int,
                          difficulty: str, bosses_defeated: int = 0,
                          death_reason: str = 'unknown'):
    """Add a new leaderboard entry directly (creates player and session)."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get or create player
            cur.execute("SELECT id FROM players WHERE name = %s", (name,))
            player = cur.fetchone()

            if not player:
                cur.execute(
                    "INSERT INTO players (name) VALUES (%s) RETURNING id",
                    (name,)
                )
                player = cur.fetchone()

            player_id = player['id']

            # Create a completed game session for the leaderboard entry
            cur.execute(
                """INSERT INTO game_sessions
                   (player_id, difficulty, started_at, ended_at, score, level,
                    duration, death_reason, bosses_defeated)
                   VALUES (%s, %s, NOW() - INTERVAL '%s seconds', NOW(),
                           %s, %s, %s, %s, %s)
                   RETURNING id""",
                (player_id, difficulty, duration, score, level, duration,
                 death_reason, bosses_defeated)
            )
            session_id = cur.fetchone()['id']

            # Update player stats
            cur.execute(
                """UPDATE players
                   SET total_games = total_games + 1,
                       total_score = total_score + %s,
                       total_playtime = total_playtime + %s,
                       best_score = GREATEST(best_score, %s),
                       best_level = GREATEST(best_level, %s)
                   WHERE id = %s""",
                (score, duration, score, level, player_id)
            )

            return session_id


def get_player_history(name: str) -> dict:
    """Get player profile and game history."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get player profile
            cur.execute("SELECT * FROM players WHERE name = %s", (name,))
            player = cur.fetchone()

            if not player:
                return None

            # Get recent games
            cur.execute(
                """SELECT id, started_at, ended_at, duration, score, level,
                          difficulty, death_reason, bosses_defeated
                   FROM game_sessions
                   WHERE player_id = %s AND ended_at IS NOT NULL
                   ORDER BY ended_at DESC
                   LIMIT 50""",
                (player['id'],)
            )
            games = [dict(row) for row in cur.fetchall()]

            return {
                'player': dict(player),
                'games': games
            }


def get_game_events(session_id: int) -> list:
    """Get all events for a game session."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT event_type, emoji, details, created_at
                   FROM game_events
                   WHERE session_id = %s
                   ORDER BY created_at ASC""",
                (session_id,)
            )
            return [dict(row) for row in cur.fetchall()]
