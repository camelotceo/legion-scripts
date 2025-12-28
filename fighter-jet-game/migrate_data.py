#!/usr/bin/env python3
"""
Migration script to import existing JSON data into PostgreSQL.

Migrates:
- leaderboard.json -> leaderboard_entries + players tables
- player_progress.json -> players + continue_keys tables

Run with: python3 migrate_data.py

Requires DATABASE_URL environment variable.
"""

import os
import json
import sys
from pathlib import Path
from datetime import datetime
import hashlib
import string
import random

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary")
    sys.exit(1)

DATA_DIR = Path(__file__).parent / 'data'
LEADERBOARD_FILE = DATA_DIR / 'leaderboard.json'
PLAYER_PROGRESS_FILE = DATA_DIR / 'player_progress.json'


def get_db_connection():
    """Get database connection from environment."""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        print("Error: DATABASE_URL environment variable not set")
        print("Example: export DATABASE_URL='postgresql://fighter:password@localhost:5432/fighter_jet'")
        sys.exit(1)
    return psycopg2.connect(db_url)


def generate_uuid():
    """Generate a random UUID."""
    import uuid
    return str(uuid.uuid4())


def hash_token(token):
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_new_continue_key():
    """Generate a new 12-character continue key."""
    chars = string.ascii_uppercase + string.digits
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '').replace('L', '')
    return 'FJ-' + ''.join(random.choices(chars, k=9))


def sanitize_username(name):
    """Sanitize username to only allow alphanumeric and underscore."""
    import re
    # Replace invalid chars with underscore
    sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name)
    # Remove consecutive underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Remove leading/trailing underscores
    sanitized = sanitized.strip('_')
    # Ensure not empty
    if not sanitized:
        sanitized = 'Player_' + ''.join(random.choices(string.ascii_uppercase, k=4))
    return sanitized[:12]


def migrate_leaderboard(conn):
    """Migrate leaderboard.json to database."""
    if not LEADERBOARD_FILE.exists():
        print("No leaderboard.json found, skipping...")
        return 0

    print(f"Reading {LEADERBOARD_FILE}...")
    with open(LEADERBOARD_FILE) as f:
        data = json.load(f)

    # Handle both flat array and difficulty-keyed formats
    if isinstance(data, list):
        data = {'EASY': data}

    cur = conn.cursor(cursor_factory=RealDictCursor)
    migrated = 0
    players_created = set()

    for difficulty, entries in data.items():
        for entry in entries:
            display_name = entry.get('name', 'Anonymous')[:12]
            username = sanitize_username(display_name)
            score = entry.get('score', 0)
            level = entry.get('level', 1)
            duration = entry.get('duration', 0)
            date_str = entry.get('date')

            # Parse date
            created_at = datetime.now()
            if date_str:
                try:
                    created_at = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
                except:
                    pass

            # Get or create player
            cur.execute(
                "SELECT id FROM players WHERE LOWER(username) = LOWER(%s)",
                (username,)
            )
            player = cur.fetchone()

            if player:
                player_id = player['id']
            else:
                player_id = generate_uuid()
                cur.execute("""
                    INSERT INTO players (id, username, display_name, first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (username) DO UPDATE SET last_seen = EXCLUDED.last_seen
                    RETURNING id
                """, (player_id, username, display_name, created_at, created_at))
                result = cur.fetchone()
                if result:
                    player_id = result['id']
                players_created.add(username)

            # Create game session for leaderboard entry
            game_session_id = generate_uuid()
            cur.execute("""
                INSERT INTO game_sessions
                (id, player_id, difficulty, game_mode, started_at, ended_at, duration,
                 final_score, final_level, score_validated, validation_score)
                VALUES (%s, %s, %s, 'single', %s, %s, %s, %s, %s, true, %s)
            """, (
                game_session_id, player_id, difficulty.upper(),
                created_at, created_at, duration, score, level, score
            ))

            # Create leaderboard entry
            cur.execute("""
                INSERT INTO leaderboard_entries
                (player_id, game_session_id, score, duration, level, difficulty,
                 is_validated, validated_at)
                VALUES (%s, %s, %s, %s, %s, %s, true, %s)
                ON CONFLICT DO NOTHING
            """, (
                player_id, game_session_id, score, duration, level,
                difficulty.upper(), created_at
            ))

            migrated += 1

    conn.commit()
    print(f"Migrated {migrated} leaderboard entries, created {len(players_created)} new players")
    return migrated


def migrate_player_progress(conn):
    """Migrate player_progress.json to database.

    Note: Existing continue keys will be regenerated for security.
    Players will need new keys.
    """
    if not PLAYER_PROGRESS_FILE.exists():
        print("No player_progress.json found, skipping...")
        return 0

    print(f"Reading {PLAYER_PROGRESS_FILE}...")
    with open(PLAYER_PROGRESS_FILE) as f:
        data = json.load(f)

    cur = conn.cursor(cursor_factory=RealDictCursor)
    migrated = 0
    keys_to_email = []

    for name, progress in data.items():
        display_name = name[:12]
        username = sanitize_username(display_name)
        email = progress.get('email')
        difficulty = progress.get('difficulty', 'EASY')
        current_level = progress.get('currentLevel', 1)
        current_score = progress.get('currentScore', 0)
        created_at_str = progress.get('createdAt')

        created_at = datetime.now()
        if created_at_str:
            try:
                created_at = datetime.fromisoformat(created_at_str)
            except:
                pass

        # Get or create player
        cur.execute(
            "SELECT id FROM players WHERE LOWER(username) = LOWER(%s)",
            (username,)
        )
        player = cur.fetchone()

        if player:
            player_id = player['id']
            # Update email if provided
            if email:
                cur.execute("""
                    UPDATE players SET email = %s WHERE id = %s AND email IS NULL
                """, (email, player_id))
        else:
            player_id = generate_uuid()
            cur.execute("""
                INSERT INTO players (id, username, display_name, email, first_seen, last_seen)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (username) DO UPDATE SET
                    email = COALESCE(players.email, EXCLUDED.email),
                    last_seen = EXCLUDED.last_seen
                RETURNING id
            """, (player_id, username, display_name, email, created_at, datetime.now()))
            result = cur.fetchone()
            if result:
                player_id = result['id']

        # Generate new continue key (old keys are insecure 6-char format)
        # Only if player had active keys
        active_keys = [k for k in progress.get('keys', [])
                       if not k.get('used', False) and k.get('respawnsRemaining', 0) > 0]

        if active_keys and email:
            # Generate one new key for the player
            new_key = generate_new_continue_key()
            key_hash = hash_token(new_key)
            key_suffix = new_key[-6:]

            cur.execute("""
                INSERT INTO continue_keys
                (id, player_id, key_hash, key_suffix, saved_level, saved_score,
                 saved_difficulty, total_respawns, respawns_used, created_from_ip)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 3, 0, 'migrated')
            """, (
                generate_uuid(), player_id, key_hash, key_suffix,
                current_level, current_score, difficulty
            ))

            keys_to_email.append({
                'email': email,
                'name': name,
                'key': new_key,
                'level': current_level
            })

        migrated += 1

    conn.commit()
    print(f"Migrated {migrated} player progress entries")

    # Output keys that need to be emailed
    if keys_to_email:
        print(f"\n{len(keys_to_email)} players need new continue keys emailed:")
        print("-" * 60)
        for info in keys_to_email:
            print(f"  {info['name']} ({info['email']}): {info['key']} (Level {info['level']})")
        print("-" * 60)
        print("Note: Old 6-char keys are no longer valid. Email these new keys to players.")

    return migrated


def verify_migration(conn):
    """Verify migration completed successfully."""
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT COUNT(*) as count FROM players")
    players = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) as count FROM leaderboard_entries")
    leaderboard = cur.fetchone()['count']

    cur.execute("SELECT COUNT(*) as count FROM continue_keys WHERE respawns_used < total_respawns")
    active_keys = cur.fetchone()['count']

    print(f"\nMigration verification:")
    print(f"  - Players: {players}")
    print(f"  - Leaderboard entries: {leaderboard}")
    print(f"  - Active continue keys: {active_keys}")


def main():
    print("=" * 60)
    print("Fighter Jet Game - Data Migration to PostgreSQL")
    print("=" * 60)

    # Check files exist
    if not LEADERBOARD_FILE.exists() and not PLAYER_PROGRESS_FILE.exists():
        print("No data files found to migrate.")
        print(f"Expected: {LEADERBOARD_FILE} and/or {PLAYER_PROGRESS_FILE}")
        return

    # Connect to database
    print("\nConnecting to database...")
    conn = get_db_connection()
    print("Connected!")

    try:
        # Run migrations
        print("\n--- Migrating Leaderboard ---")
        migrate_leaderboard(conn)

        print("\n--- Migrating Player Progress ---")
        migrate_player_progress(conn)

        # Verify
        verify_migration(conn)

        print("\n" + "=" * 60)
        print("Migration complete!")
        print("=" * 60)

    except Exception as e:
        conn.rollback()
        print(f"\nError during migration: {e}")
        raise
    finally:
        conn.close()


if __name__ == '__main__':
    main()
