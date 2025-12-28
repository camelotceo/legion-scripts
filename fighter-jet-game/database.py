"""
PostgreSQL database module for Fighter Jet Game.
Handles player authentication, sessions, game events, score validation,
continue keys, rate limiting, and audit logging.
"""

import os
import secrets
import hashlib
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from typing import Optional, Dict, List, Any
import bcrypt

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://gameserver:localdev123@localhost:5432/fighter_jet')

# Score values for server-side validation
SCORE_VALUES = {
    'enemy_killed': 100,
    'boss_damaged': 50,
    'boss_defeated': 1000,
    'level_completed': 500,
    'powerup_collected': 0,
}

# Rate limit configurations
RATE_LIMITS = {
    'player_join': {'max': 5, 'window': 60},  # 5 per minute
    'leaderboard_submit': {'max': 3, 'window': 300},  # 3 per 5 min
    'request_key': {'max': 3, 'window': 3600},  # 3 per hour
    'validate_key': {'max': 10, 'window': 900},  # 10 per 15 min
    'request_login': {'max': 3, 'window': 900},  # 3 per 15 min
    'verify_login': {'max': 10, 'window': 900},  # 10 per 15 min
    'game_event': {'max': 600, 'window': 60},  # 600 per minute
    'room_create': {'max': 5, 'window': 300},  # 5 per 5 min
}


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


def hash_token(token: str) -> str:
    """Hash a token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_session_token() -> str:
    """Generate a cryptographically secure session token."""
    return secrets.token_urlsafe(32)


def generate_continue_key() -> Dict[str, str]:
    """Generate a secure continue key."""
    chars = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'  # No ambiguous chars
    key_random = ''.join(secrets.choice(chars) for _ in range(12))
    full_key = f'FJ-{key_random}'
    return {
        'display_key': full_key,
        'key_hash': hash_token(full_key),
        'key_suffix': key_random[-6:]
    }


def generate_device_fingerprint(ip: str, user_agent: str, accept_language: str = '') -> str:
    """Generate a device fingerprint from request headers."""
    components = [
        ip,
        hashlib.md5(user_agent.encode()).hexdigest()[:16] if user_agent else '',
        hashlib.md5(accept_language.encode()).hexdigest()[:8] if accept_language else ''
    ]
    return hashlib.sha256('|'.join(components).encode()).hexdigest()[:32]


# =============================================================================
# PLAYER MANAGEMENT
# =============================================================================

def get_player_by_id(player_id: str) -> Optional[Dict]:
    """Get player by UUID."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM players WHERE id = %s", (player_id,))
            player = cur.fetchone()
            return dict(player) if player else None


def get_player_by_username(username: str) -> Optional[Dict]:
    """Get player by username."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM players WHERE username = %s", (username.lower(),))
            player = cur.fetchone()
            return dict(player) if player else None


def get_player_by_fingerprint(fingerprint: str) -> Optional[Dict]:
    """Get player by device fingerprint."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM players WHERE device_fingerprint = %s ORDER BY last_seen DESC LIMIT 1",
                (fingerprint,)
            )
            player = cur.fetchone()
            return dict(player) if player else None


def create_player(username: str, display_name: str, device_fingerprint: str = None,
                  ip_address: str = None, email: str = None) -> Dict:
    """Create a new player."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO players
                   (username, display_name, device_fingerprint, last_ip, email)
                   VALUES (%s, %s, %s, %s, %s)
                   RETURNING *""",
                (username.lower(), display_name, device_fingerprint, ip_address, email)
            )
            return dict(cur.fetchone())


def update_player_last_seen(player_id: str, ip_address: str = None):
    """Update player's last seen timestamp and IP."""
    with get_db() as conn:
        with conn.cursor() as cur:
            if ip_address:
                cur.execute(
                    "UPDATE players SET last_seen = NOW(), last_ip = %s WHERE id = %s",
                    (ip_address, player_id)
                )
            else:
                cur.execute(
                    "UPDATE players SET last_seen = NOW() WHERE id = %s",
                    (player_id,)
                )


def update_player_stats(player_id: str, score: int, level: int, duration: int,
                        is_victory: bool = False):
    """Update player's aggregate stats."""
    with get_db() as conn:
        with conn.cursor() as cur:
            victory_increment = 1 if is_victory else 0
            cur.execute(
                """UPDATE players SET
                   total_games = total_games + 1,
                   total_score = total_score + %s,
                   total_playtime = total_playtime + %s,
                   best_score = GREATEST(best_score, %s),
                   best_level = GREATEST(best_level, %s),
                   victories = victories + %s,
                   last_seen = NOW()
                   WHERE id = %s""",
                (score, duration, score, level, victory_increment, player_id)
            )


def is_username_available(username: str) -> bool:
    """Check if a username is available."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM players WHERE username = %s",
                (username.lower(),)
            )
            return cur.fetchone() is None


def set_player_email(player_id: str, email: str) -> bool:
    """Set or update player's email and generate verification token."""
    verification_token = secrets.token_urlsafe(32)
    expires = datetime.now() + timedelta(hours=24)

    with get_db() as conn:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """UPDATE players SET
                       email = %s,
                       email_verified = FALSE,
                       email_verification_token = %s,
                       email_verification_expires = %s
                       WHERE id = %s""",
                    (email.lower(), verification_token, expires, player_id)
                )
                return True
            except psycopg2.IntegrityError:
                return False  # Email already in use


def verify_player_email(token: str) -> Optional[Dict]:
    """Verify player's email with token."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """UPDATE players SET
                   email_verified = TRUE,
                   email_verification_token = NULL,
                   email_verification_expires = NULL
                   WHERE email_verification_token = %s
                   AND email_verification_expires > NOW()
                   RETURNING id, username, email""",
                (token,)
            )
            result = cur.fetchone()
            return dict(result) if result else None


def get_player_by_email(email: str) -> Optional[Dict]:
    """Get player by email address."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT id, username, display_name, email, email_verified,
                          device_fingerprint, is_banned, ban_reason
                   FROM players WHERE LOWER(email) = LOWER(%s)""",
                (email,)
            )
            result = cur.fetchone()
            return dict(result) if result else None


def create_email_login_token(player_id: str, ip_address: str = None,
                              expires_minutes: int = 15) -> Dict:
    """Create a one-time email login token.

    Returns the plaintext token (to be sent via email) and expiry time.
    Token is stored hashed in the database.
    """
    token = generate_session_token()  # Reuse secure token generator
    token_hash = hash_token(token)
    expires_at = datetime.now() + timedelta(minutes=expires_minutes)

    with get_db() as conn:
        with conn.cursor() as cur:
            # Invalidate any existing unused tokens for this player
            cur.execute(
                """UPDATE email_login_tokens
                   SET is_used = TRUE
                   WHERE player_id = %s AND is_used = FALSE""",
                (player_id,)
            )

            # Create new token
            cur.execute(
                """INSERT INTO email_login_tokens
                   (player_id, token_hash, expires_at, created_from_ip)
                   VALUES (%s, %s, %s, %s)
                   RETURNING id""",
                (player_id, token_hash, expires_at, ip_address)
            )

    return {
        'token': token,
        'expires_at': expires_at.isoformat()
    }


def verify_email_login_token(token: str, ip_address: str = None,
                              device_fingerprint: str = None,
                              user_agent: str = None) -> Optional[Dict]:
    """Verify an email login token and create a session.

    Returns session info if valid, None if invalid/expired.
    Token can only be used once.
    """
    token_hash = hash_token(token)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find and mark token as used atomically
            cur.execute(
                """UPDATE email_login_tokens SET
                   is_used = TRUE,
                   used_at = NOW(),
                   used_from_ip = %s
                   WHERE token_hash = %s
                   AND is_used = FALSE
                   AND expires_at > NOW()
                   RETURNING player_id""",
                (ip_address, token_hash)
            )
            result = cur.fetchone()

            if not result:
                return None

            player_id = str(result['player_id'])

            # Get player info
            cur.execute(
                """SELECT id, username, display_name, email, email_verified
                   FROM players WHERE id = %s""",
                (player_id,)
            )
            player = cur.fetchone()

            if not player:
                return None

            # Update device fingerprint if provided (links new device to account)
            if device_fingerprint:
                cur.execute(
                    """UPDATE players SET
                       device_fingerprint = %s,
                       last_seen = NOW(),
                       last_ip = %s
                       WHERE id = %s""",
                    (device_fingerprint, ip_address, player_id)
                )

    # Create a new session for this player
    session = create_session(
        player_id=player_id,
        ip_address=ip_address or '0.0.0.0',
        user_agent=user_agent,
        device_fingerprint=device_fingerprint
    )

    return {
        'player_id': player_id,
        'username': player['username'],
        'display_name': player['display_name'],
        'email': player['email'],
        'token': session['token'],
        'expires_at': session['expires_at']
    }


def ban_player(player_id: str, reason: str, expires: datetime = None):
    """Ban a player."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE players SET
                   is_banned = TRUE,
                   ban_reason = %s,
                   ban_expires = %s
                   WHERE id = %s""",
                (reason, expires, player_id)
            )


# =============================================================================
# PASSWORD-BASED AUTHENTICATION
# =============================================================================

def generate_verification_code() -> str:
    """Generate a 6-digit verification code."""
    return ''.join(secrets.choice('0123456789') for _ in range(6))


def register_player_with_password(username: str, email: str, password: str,
                                   device_fingerprint: str = None,
                                   ip_address: str = None) -> Dict:
    """Register a new player with email/password authentication.

    Returns player data and sends verification code via email.
    """
    # Hash password with bcrypt
    password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt(12)).decode('utf-8')

    # Generate 6-digit verification code
    verification_code = generate_verification_code()
    code_expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                # Check if username already exists
                cur.execute("SELECT 1 FROM players WHERE username = %s", (username.lower(),))
                if cur.fetchone():
                    return {'error': 'Username already taken', 'success': False}

                # Check if email already exists
                cur.execute("SELECT 1 FROM players WHERE LOWER(email) = LOWER(%s)", (email,))
                if cur.fetchone():
                    return {'error': 'Email already registered', 'success': False}

                # Create player with password
                cur.execute(
                    """INSERT INTO players
                       (username, display_name, email, password_hash,
                        email_verification_code, verification_code_expires,
                        device_fingerprint, last_ip, tokens)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 100)
                       RETURNING id, username, display_name, email, tokens""",
                    (username.lower(), username, email.lower(), password_hash,
                     verification_code, code_expires, device_fingerprint, ip_address)
                )
                player = dict(cur.fetchone())

                return {
                    'success': True,
                    'player_id': str(player['id']),
                    'username': player['username'],
                    'email': player['email'],
                    'verification_code': verification_code,  # To be sent via email
                    'code_expires_at': code_expires.isoformat()
                }
            except psycopg2.IntegrityError as e:
                return {'error': 'Registration failed - duplicate entry', 'success': False}


def verify_email_with_code(email: str, code: str, ip_address: str = None,
                           user_agent: str = None, device_fingerprint: str = None) -> Dict:
    """Verify email with 6-digit code and create session.

    Returns session token on success.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find player with matching email and code
            cur.execute(
                """SELECT id, username, display_name, email, email_verification_code,
                          verification_code_expires, tokens
                   FROM players
                   WHERE LOWER(email) = LOWER(%s)
                   AND email_verification_code = %s""",
                (email, code)
            )
            player = cur.fetchone()

            if not player:
                return {'error': 'Invalid verification code', 'success': False}

            # Check if code expired
            if player['verification_code_expires']:
                if player['verification_code_expires'].replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                    return {'error': 'Verification code expired', 'success': False}

            # Mark email as verified and clear the code
            cur.execute(
                """UPDATE players SET
                   email_verified = TRUE,
                   email_verification_code = NULL,
                   verification_code_expires = NULL,
                   device_fingerprint = COALESCE(%s, device_fingerprint),
                   last_seen = NOW(),
                   last_ip = %s
                   WHERE id = %s""",
                (device_fingerprint, ip_address, player['id'])
            )

    # Create session for auto-login
    session = create_session(
        player_id=str(player['id']),
        ip_address=ip_address or '0.0.0.0',
        user_agent=user_agent,
        device_fingerprint=device_fingerprint
    )

    return {
        'success': True,
        'player_id': str(player['id']),
        'username': player['username'],
        'display_name': player['display_name'],
        'email': player['email'],
        'tokens': player['tokens'],
        'token': session['token'],
        'expires_at': session['expires_at']
    }


def login_with_password(email_or_username: str, password: str, ip_address: str = None,
                        user_agent: str = None, device_fingerprint: str = None) -> Dict:
    """Login with email/username and password.

    Returns session token on success.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find player by email or username
            cur.execute(
                """SELECT id, username, display_name, email, email_verified,
                          password_hash, is_banned, ban_reason, tokens,
                          saved_level, saved_score, saved_difficulty, continues_this_level
                   FROM players
                   WHERE LOWER(email) = LOWER(%s) OR username = %s""",
                (email_or_username, email_or_username.lower())
            )
            player = cur.fetchone()

            if not player:
                return {'error': 'Invalid credentials', 'success': False}

            # Check if player has a password set
            if not player['password_hash']:
                return {'error': 'No password set for this account', 'success': False}

            # Verify password
            if not bcrypt.checkpw(password.encode('utf-8'), player['password_hash'].encode('utf-8')):
                return {'error': 'Invalid credentials', 'success': False}

            # Check if email is verified
            if not player['email_verified']:
                return {'error': 'Email not verified', 'success': False, 'needs_verification': True}

            # Check if banned
            if player['is_banned']:
                return {'error': f'Account banned: {player["ban_reason"]}', 'success': False}

            # Update last seen and device fingerprint
            cur.execute(
                """UPDATE players SET
                   last_seen = NOW(),
                   last_ip = %s,
                   device_fingerprint = COALESCE(%s, device_fingerprint)
                   WHERE id = %s""",
                (ip_address, device_fingerprint, player['id'])
            )

    # Create session
    session = create_session(
        player_id=str(player['id']),
        ip_address=ip_address or '0.0.0.0',
        user_agent=user_agent,
        device_fingerprint=device_fingerprint
    )

    return {
        'success': True,
        'player_id': str(player['id']),
        'username': player['username'],
        'display_name': player['display_name'],
        'email': player['email'],
        'tokens': player['tokens'],
        'saved_level': player['saved_level'],
        'saved_score': player['saved_score'],
        'saved_difficulty': player['saved_difficulty'],
        'continues_this_level': player['continues_this_level'],
        'token': session['token'],
        'expires_at': session['expires_at']
    }


def resend_verification_code(email: str) -> Dict:
    """Resend a new verification code to email.

    Returns the new code (to be sent via email).
    """
    verification_code = generate_verification_code()
    code_expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """UPDATE players SET
                   email_verification_code = %s,
                   verification_code_expires = %s
                   WHERE LOWER(email) = LOWER(%s)
                   AND email_verified = FALSE
                   RETURNING id, username, email""",
                (verification_code, code_expires, email)
            )
            result = cur.fetchone()

            if not result:
                return {'error': 'Email not found or already verified', 'success': False}

            return {
                'success': True,
                'player_id': str(result['id']),
                'username': result['username'],
                'email': result['email'],
                'verification_code': verification_code,
                'code_expires_at': code_expires.isoformat()
            }


# =============================================================================
# TOKEN ECONOMY & CONTINUES
# =============================================================================

def use_continue_token(player_id: str) -> Dict:
    """Use 1 token for a continue.

    Deducts 1 token and increments continues_this_level.
    Returns new token balance and continues count.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get current token count
            cur.execute(
                "SELECT tokens, continues_this_level FROM players WHERE id = %s",
                (player_id,)
            )
            player = cur.fetchone()

            if not player:
                return {'error': 'Player not found', 'success': False}

            if player['tokens'] <= 0:
                return {'error': 'No tokens remaining', 'success': False, 'tokens': 0}

            # Deduct token and increment continues
            cur.execute(
                """UPDATE players SET
                   tokens = tokens - 1,
                   continues_this_level = continues_this_level + 1
                   WHERE id = %s
                   RETURNING tokens, continues_this_level""",
                (player_id,)
            )
            result = cur.fetchone()

            return {
                'success': True,
                'tokens': result['tokens'],
                'continues_this_level': result['continues_this_level']
            }


def save_player_progress(player_id: str, level: int, score: int, difficulty: str) -> Dict:
    """Save player's game progress and reset continues for new level.

    Called when player advances to a new level.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """UPDATE players SET
                   saved_level = %s,
                   saved_score = %s,
                   saved_difficulty = %s,
                   continues_this_level = 0
                   WHERE id = %s
                   RETURNING saved_level, saved_score, saved_difficulty, tokens""",
                (level, score, difficulty.upper(), player_id)
            )
            result = cur.fetchone()

            if not result:
                return {'error': 'Player not found', 'success': False}

            return {
                'success': True,
                'saved_level': result['saved_level'],
                'saved_score': result['saved_score'],
                'saved_difficulty': result['saved_difficulty'],
                'tokens': result['tokens']
            }


def reset_continues_for_level(player_id: str) -> Dict:
    """Reset continues_this_level to 0.

    Called when player restarts at level beginning after using 3 continues.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """UPDATE players SET continues_this_level = 0
                   WHERE id = %s
                   RETURNING continues_this_level""",
                (player_id,)
            )
            result = cur.fetchone()

            if not result:
                return {'error': 'Player not found', 'success': False}

            return {'success': True, 'continues_this_level': 0}


def get_player_profile(player_id: str) -> Dict:
    """Get full player profile with stats, tokens, and game history."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get player data
            cur.execute(
                """SELECT id, username, display_name, email, email_verified,
                          tokens, saved_level, saved_score, saved_difficulty,
                          continues_this_level, total_games, total_score,
                          total_playtime, best_score, best_level, victories,
                          first_seen, last_seen
                   FROM players WHERE id = %s""",
                (player_id,)
            )
            player = cur.fetchone()

            if not player:
                return None

            # Get recent game history
            cur.execute(
                """SELECT id, started_at, ended_at, duration, final_score as score,
                          final_level as level, difficulty, death_reason,
                          bosses_defeated, enemies_killed, is_victory
                   FROM game_sessions
                   WHERE player_id = %s AND ended_at IS NOT NULL
                   ORDER BY ended_at DESC
                   LIMIT 50""",
                (player_id,)
            )
            games = [dict(row) for row in cur.fetchall()]

            return {
                'player': {
                    'id': str(player['id']),
                    'username': player['username'],
                    'display_name': player['display_name'],
                    'email': player['email'],
                    'email_verified': player['email_verified'],
                    'tokens': player['tokens'],
                    'saved_level': player['saved_level'],
                    'saved_score': player['saved_score'],
                    'saved_difficulty': player['saved_difficulty'],
                    'continues_this_level': player['continues_this_level']
                },
                'stats': {
                    'total_games': player['total_games'],
                    'total_score': player['total_score'],
                    'total_playtime': player['total_playtime'],
                    'best_score': player['best_score'],
                    'best_level': player['best_level'],
                    'victories': player['victories'],
                    'first_seen': player['first_seen'].isoformat() if player['first_seen'] else None,
                    'last_seen': player['last_seen'].isoformat() if player['last_seen'] else None
                },
                'games': games
            }


def get_player_tokens(player_id: str) -> int:
    """Get player's current token balance."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tokens FROM players WHERE id = %s", (player_id,))
            result = cur.fetchone()
            return result[0] if result else 0


def add_tokens(player_id: str, amount: int) -> Dict:
    """Add tokens to player's balance (for future token purchases)."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """UPDATE players SET tokens = tokens + %s
                   WHERE id = %s
                   RETURNING tokens""",
                (amount, player_id)
            )
            result = cur.fetchone()

            if not result:
                return {'error': 'Player not found', 'success': False}

            return {'success': True, 'tokens': result['tokens']}


# =============================================================================
# SESSION MANAGEMENT
# =============================================================================

def create_session(player_id: str, ip_address: str, user_agent: str = None,
                   device_fingerprint: str = None, expires_days: int = 30) -> Dict:
    """Create a new player session."""
    token = generate_session_token()
    token_hash = hash_token(token)
    expires_at = datetime.now() + timedelta(days=expires_days)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO player_sessions
                   (player_id, token_hash, ip_address, user_agent, device_fingerprint, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (player_id, token_hash, ip_address, user_agent, device_fingerprint, expires_at)
            )
            session_id = cur.fetchone()['id']

            return {
                'session_id': str(session_id),
                'token': token,
                'expires_at': expires_at.isoformat()
            }


def validate_session(token: str) -> Optional[Dict]:
    """Validate a session token and return session + player data."""
    token_hash = hash_token(token)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT s.*, p.id as player_id, p.username, p.display_name,
                          p.email, p.email_verified, p.is_banned, p.ban_reason
                   FROM player_sessions s
                   JOIN players p ON s.player_id = p.id
                   WHERE s.token_hash = %s
                   AND s.is_active = TRUE
                   AND s.expires_at > NOW()""",
                (token_hash,)
            )
            result = cur.fetchone()

            if not result:
                return None

            # Update last used
            cur.execute(
                "UPDATE player_sessions SET last_used = NOW() WHERE id = %s",
                (result['id'],)
            )

            return {
                'session_id': str(result['id']),
                'player_id': str(result['player_id']),
                'username': result['username'],
                'display_name': result['display_name'],
                'email': result['email'],
                'email_verified': result['email_verified'],
                'is_banned': result['is_banned'],
                'ban_reason': result['ban_reason']
            }


def revoke_session(session_id: str, reason: str = 'user_logout'):
    """Revoke a session."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE player_sessions SET
                   is_active = FALSE,
                   revoked_at = NOW(),
                   revoke_reason = %s
                   WHERE id = %s""",
                (reason, session_id)
            )


def revoke_all_sessions(player_id: str, reason: str = 'security'):
    """Revoke all sessions for a player."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE player_sessions SET
                   is_active = FALSE,
                   revoked_at = NOW(),
                   revoke_reason = %s
                   WHERE player_id = %s AND is_active = TRUE""",
                (reason, player_id)
            )


# =============================================================================
# GAME SESSION MANAGEMENT
# =============================================================================

def create_game_session(player_id: str, player_session_id: str, difficulty: str,
                        game_mode: str = 'single', room_code: str = None,
                        client_ip: str = None) -> str:
    """Create a new game session."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO game_sessions
                   (player_id, player_session_id, difficulty, game_mode, room_code, client_ip)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (player_id, player_session_id, difficulty.upper(), game_mode, room_code, client_ip)
            )
            return str(cur.fetchone()['id'])


def end_game_session(game_session_id: str, client_score: int, level: int,
                     duration: int, death_reason: str = None, bosses_defeated: int = 0,
                     enemies_killed: int = 0, is_victory: bool = False) -> Dict:
    """End a game session with validation."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Calculate server score from events
            cur.execute(
                "SELECT calculate_game_score(%s) as server_score",
                (game_session_id,)
            )
            server_score = cur.fetchone()['server_score'] or 0

            # Calculate discrepancy
            discrepancy = abs(client_score - server_score)
            max_tolerance = max(server_score * 0.05, 100)  # 5% or 100 points
            is_validated = discrepancy <= max_tolerance

            # Determine final score
            final_score = server_score if is_validated else min(client_score, server_score)

            # Update game session
            cur.execute(
                """UPDATE game_sessions SET
                   ended_at = NOW(),
                   duration = %s,
                   final_score = %s,
                   final_level = %s,
                   bosses_defeated = %s,
                   enemies_killed = %s,
                   death_reason = %s,
                   is_victory = %s,
                   score_validated = %s,
                   validation_score = %s,
                   validation_discrepancy = %s,
                   is_flagged = %s
                   WHERE id = %s
                   RETURNING player_id""",
                (duration, final_score, level, bosses_defeated, enemies_killed,
                 death_reason, is_victory, is_validated, server_score, discrepancy,
                 not is_validated, game_session_id)
            )
            result = cur.fetchone()

            if result:
                # Update player stats
                update_player_stats(
                    str(result['player_id']), final_score, level, duration, is_victory
                )

            return {
                'final_score': final_score,
                'server_score': server_score,
                'is_validated': is_validated,
                'discrepancy': discrepancy
            }


def get_game_session(game_session_id: str) -> Optional[Dict]:
    """Get a game session by ID."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM game_sessions WHERE id = %s",
                (game_session_id,)
            )
            result = cur.fetchone()
            return dict(result) if result else None


# =============================================================================
# GAME EVENTS (for score validation)
# =============================================================================

def log_game_event(game_session_id: str, event_type: str, game_timestamp: int,
                   level: int = 1, position_x: int = None, position_y: int = None,
                   details: Dict = None) -> Dict:
    """Log a game event and return score delta."""
    score_delta = SCORE_VALUES.get(event_type, 0)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get running score
            cur.execute(
                """SELECT COALESCE(SUM(score_delta), 0) as current_score
                   FROM game_events WHERE game_session_id = %s""",
                (game_session_id,)
            )
            current_score = cur.fetchone()['current_score']
            running_score = current_score + score_delta

            # Insert event
            cur.execute(
                """INSERT INTO game_events
                   (game_session_id, event_type, game_timestamp, score_delta,
                    running_score, level, position_x, position_y, details)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (game_session_id, event_type, game_timestamp, score_delta,
                 running_score, level, position_x, position_y, Json(details or {}))
            )

            return {
                'event_id': str(cur.fetchone()['id']),
                'score_delta': score_delta,
                'running_score': running_score
            }


def get_game_events(game_session_id: str) -> List[Dict]:
    """Get all events for a game session."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT event_type, event_subtype, game_timestamp, score_delta,
                          running_score, level, details
                   FROM game_events
                   WHERE game_session_id = %s
                   ORDER BY game_timestamp ASC""",
                (game_session_id,)
            )
            return [dict(row) for row in cur.fetchall()]


# =============================================================================
# CONTINUE KEYS
# =============================================================================

def get_or_create_player_key(player_id: str, level: int, score: int, difficulty: str,
                              ip_address: str = None) -> Dict:
    """Get existing active key for player, or create a new one.

    Each player has ONE continue key that tracks their progress.
    If they have an active key, return it. Otherwise create a new one.
    """
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check for existing active key (not expired, not exhausted)
            cur.execute(
                """SELECT id, key_suffix, saved_level, saved_score, saved_difficulty,
                          total_respawns, respawns_used
                   FROM continue_keys
                   WHERE player_id = %s
                     AND (expires_at IS NULL OR expires_at > NOW())
                     AND respawns_used < total_respawns
                   ORDER BY created_at DESC
                   LIMIT 1""",
                (player_id,)
            )
            existing = cur.fetchone()

            if existing:
                # Update the saved progress on existing key
                cur.execute(
                    """UPDATE continue_keys SET
                       saved_level = %s,
                       saved_score = %s,
                       saved_difficulty = %s
                       WHERE id = %s""",
                    (level, score, difficulty.upper(), existing['id'])
                )
                # We can't return the full key (it's hashed), but we return the suffix
                # The user must get the key from email
                return {
                    'key_id': str(existing['id']),
                    'key': None,  # Can't recover - must use email
                    'key_suffix': existing['key_suffix'],
                    'level': level,
                    'score': score,
                    'difficulty': difficulty,
                    'is_existing': True,
                    'respawns_remaining': existing['total_respawns'] - existing['respawns_used']
                }

            # No active key - create new one
            key_data = generate_continue_key()
            cur.execute(
                """INSERT INTO continue_keys
                   (player_id, key_hash, key_suffix, saved_level, saved_score,
                    saved_difficulty, created_from_ip)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (player_id, key_data['key_hash'], key_data['key_suffix'],
                 level, score, difficulty.upper(), ip_address)
            )
            key_id = cur.fetchone()['id']

            return {
                'key_id': str(key_id),
                'key': key_data['display_key'],
                'key_suffix': key_data['key_suffix'],
                'level': level,
                'score': score,
                'difficulty': difficulty,
                'is_existing': False,
                'respawns_remaining': 3
            }


def update_player_key_progress(player_id: str, level: int, score: int, difficulty: str) -> bool:
    """Update the saved progress on a player's active continue key."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE continue_keys SET
                   saved_level = %s,
                   saved_score = %s,
                   saved_difficulty = %s
                   WHERE player_id = %s
                     AND (expires_at IS NULL OR expires_at > NOW())
                     AND respawns_used < total_respawns""",
                (level, score, difficulty.upper(), player_id)
            )
            return cur.rowcount > 0


def reset_player_key_respawns(player_id: str) -> bool:
    """Reset respawns on player's key (called when starting a new level)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE continue_keys SET
                   respawns_used = 0
                   WHERE player_id = %s
                     AND (expires_at IS NULL OR expires_at > NOW())""",
                (player_id,)
            )
            return cur.rowcount > 0


def create_continue_key(player_id: str, level: int, score: int, difficulty: str,
                        ip_address: str = None) -> Dict:
    """Create a new continue key for a player."""
    key_data = generate_continue_key()

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """INSERT INTO continue_keys
                   (player_id, key_hash, key_suffix, saved_level, saved_score,
                    saved_difficulty, created_from_ip)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (player_id, key_data['key_hash'], key_data['key_suffix'],
                 level, score, difficulty.upper(), ip_address)
            )
            key_id = cur.fetchone()['id']

            return {
                'key_id': str(key_id),
                'key': key_data['display_key'],
                'level': level,
                'score': score,
                'difficulty': difficulty
            }


def validate_continue_key(key: str, ip_address: str = None) -> Optional[Dict]:
    """Validate a continue key and return saved progress."""
    key_upper = key.upper().strip()
    key_hash = hash_token(key_upper)

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Find the key
            cur.execute(
                """SELECT ck.*, p.username, p.display_name
                   FROM continue_keys ck
                   JOIN players p ON ck.player_id = p.id
                   WHERE ck.key_hash = %s""",
                (key_hash,)
            )
            key_record = cur.fetchone()

            if not key_record:
                return None

            now = datetime.now(timezone.utc)

            # Check if expired
            if key_record['expires_at'] and key_record['expires_at'] < now:
                return {'error': 'Key expired', 'valid': False}

            # Check if locked
            if key_record['locked_until'] and key_record['locked_until'] > now:
                return {'error': 'Key temporarily locked', 'valid': False}

            # Check if exhausted
            if key_record['respawns_used'] >= key_record['total_respawns']:
                return {'error': 'No respawns remaining', 'valid': False}

            # Use the key
            cur.execute(
                """UPDATE continue_keys SET
                   respawns_used = respawns_used + 1,
                   last_used_at = NOW(),
                   exhausted_at = CASE
                       WHEN respawns_used + 1 >= total_respawns THEN NOW()
                       ELSE exhausted_at
                   END
                   WHERE id = %s""",
                (key_record['id'],)
            )

            # Log the use
            cur.execute(
                """INSERT INTO continue_key_uses
                   (continue_key_id, used_from_ip, level_at_use, score_at_use)
                   VALUES (%s, %s, %s, %s)""",
                (key_record['id'], ip_address, key_record['saved_level'],
                 key_record['saved_score'])
            )

            return {
                'valid': True,
                'player_id': str(key_record['player_id']),
                'username': key_record['username'],
                'display_name': key_record['display_name'],
                'level': key_record['saved_level'],
                'score': key_record['saved_score'],
                'difficulty': key_record['saved_difficulty'],
                'respawns_remaining': key_record['total_respawns'] - key_record['respawns_used'] - 1
            }


def increment_key_failure(key: str, ip_address: str = None):
    """Increment failed attempts for a key (for rate limiting)."""
    key_hash = hash_token(key.upper().strip())

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE continue_keys SET
                   failed_attempts = failed_attempts + 1,
                   locked_until = CASE
                       WHEN failed_attempts >= 4 THEN NOW() + INTERVAL '1 hour'
                       ELSE locked_until
                   END
                   WHERE key_hash = %s""",
                (key_hash,)
            )


# =============================================================================
# LEADERBOARD
# =============================================================================

def get_leaderboard(difficulty: str = None, limit: int = 10) -> List[Dict]:
    """Get leaderboard entries, optionally filtered by difficulty."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if difficulty:
                cur.execute(
                    """SELECT username, display_name, score, duration, level,
                              difficulty, bosses_defeated, date, rank
                       FROM leaderboard_view
                       WHERE difficulty = %s AND rank <= %s
                       ORDER BY rank""",
                    (difficulty.upper(), limit)
                )
            else:
                cur.execute(
                    """SELECT username, display_name, score, duration, level,
                              difficulty, bosses_defeated, date, rank
                       FROM leaderboard_view
                       WHERE rank <= %s
                       ORDER BY difficulty, rank""",
                    (limit,)
                )
            return [dict(row) for row in cur.fetchall()]


def add_leaderboard_entry(game_session_id: str) -> Optional[str]:
    """Add a validated game session to the leaderboard."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            try:
                cur.execute(
                    "SELECT insert_validated_score(%s) as entry_id",
                    (game_session_id,)
                )
                result = cur.fetchone()
                return str(result['entry_id']) if result else None
            except Exception:
                return None


# =============================================================================
# RATE LIMITING
# =============================================================================

def check_rate_limit(identifier_type: str, identifier_value: str,
                     action: str) -> bool:
    """Check if action is within rate limits."""
    config = RATE_LIMITS.get(action, {'max': 100, 'window': 60})

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT check_rate_limit(%s, %s, %s, %s, %s) as allowed",
                (identifier_type, identifier_value, action,
                 config['max'], config['window'])
            )
            return cur.fetchone()['allowed']


def get_rate_limit_status(identifier_type: str, identifier_value: str,
                          action: str) -> Dict:
    """Get current rate limit status."""
    config = RATE_LIMITS.get(action, {'max': 100, 'window': 60})

    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """SELECT request_count, window_start
                   FROM rate_limits
                   WHERE identifier_type = %s
                   AND identifier_value = %s
                   AND action_type = %s
                   AND window_start > NOW() - (%s || ' seconds')::INTERVAL""",
                (identifier_type, identifier_value, action, config['window'])
            )
            result = cur.fetchone()

            if result:
                return {
                    'count': result['request_count'],
                    'limit': config['max'],
                    'remaining': max(0, config['max'] - result['request_count']),
                    'reset_at': (result['window_start'] + timedelta(seconds=config['window'])).isoformat()
                }
            else:
                return {
                    'count': 0,
                    'limit': config['max'],
                    'remaining': config['max'],
                    'reset_at': None
                }


# =============================================================================
# AUDIT LOGGING
# =============================================================================

def log_audit(action: str, player_id: str = None, session_id: str = None,
              ip_address: str = None, resource_type: str = None,
              resource_id: str = None, old_value: Dict = None,
              new_value: Dict = None, success: bool = True,
              error_message: str = None):
    """Log an audit event."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit_log
                   (player_id, session_id, ip_address, action, resource_type,
                    resource_id, old_value, new_value, success, error_message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (player_id, session_id, ip_address, action, resource_type,
                 resource_id, Json(old_value) if old_value else None,
                 Json(new_value) if new_value else None, success, error_message)
            )


# =============================================================================
# VICTORY RECORDS
# =============================================================================

def save_victory(player_id: str, game_session_id: str, score: int,
                 enemies_killed: int, duration: int, difficulty: str,
                 email: str = None) -> str:
    """Save a victory record."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Encrypt email if provided
            encrypted_email = None
            if email:
                # Use pgcrypto for encryption (requires setting up encryption key)
                cur.execute(
                    "SELECT pgp_sym_encrypt(%s, %s) as encrypted",
                    (email, os.environ.get('ENCRYPTION_KEY', 'fighter_jet_secret'))
                )
                encrypted_email = cur.fetchone()['encrypted']

            cur.execute(
                """INSERT INTO victory_records
                   (player_id, game_session_id, final_score, enemies_killed,
                    duration, difficulty, encrypted_email)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   RETURNING id""",
                (player_id, game_session_id, score, enemies_killed, duration,
                 difficulty.upper(), encrypted_email)
            )
            return str(cur.fetchone()['id'])


# =============================================================================
# PLAYER HISTORY
# =============================================================================

def get_player_history(player_id: str, limit: int = 50) -> Dict:
    """Get player profile and game history."""
    with get_db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Get player from stats view
            cur.execute(
                "SELECT * FROM player_stats_view WHERE id = %s",
                (player_id,)
            )
            player = cur.fetchone()

            if not player:
                return None

            # Get recent games
            cur.execute(
                """SELECT id, started_at, ended_at, duration, final_score as score,
                          final_level as level, difficulty, death_reason,
                          bosses_defeated, is_victory, score_validated
                   FROM game_sessions
                   WHERE player_id = %s AND ended_at IS NOT NULL
                   ORDER BY ended_at DESC
                   LIMIT %s""",
                (player_id, limit)
            )
            games = [dict(row) for row in cur.fetchall()]

            return {
                'player': dict(player),
                'games': games
            }


# =============================================================================
# IP TRACKING
# =============================================================================

def track_ip(ip_address: str, player_id: str = None):
    """Track an IP address."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ip_addresses (ip, total_players, total_games)
                   VALUES (%s, %s, 0)
                   ON CONFLICT (ip) DO UPDATE SET
                   last_seen = NOW(),
                   total_players = ip_addresses.total_players + EXCLUDED.total_players""",
                (ip_address, 1 if player_id else 0)
            )


def is_ip_banned(ip_address: str) -> bool:
    """Check if an IP is banned."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM ip_addresses
                   WHERE ip = %s AND is_banned = TRUE
                   AND (ban_expires IS NULL OR ban_expires > NOW())""",
                (ip_address,)
            )
            return cur.fetchone() is not None


def ban_ip(ip_address: str, reason: str, expires: datetime = None):
    """Ban an IP address."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO ip_addresses (ip, is_banned, ban_reason, ban_expires)
                   VALUES (%s, TRUE, %s, %s)
                   ON CONFLICT (ip) DO UPDATE SET
                   is_banned = TRUE,
                   ban_reason = EXCLUDED.ban_reason,
                   ban_expires = EXCLUDED.ban_expires""",
                (ip_address, reason, expires)
            )
