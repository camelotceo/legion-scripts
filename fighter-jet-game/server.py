#!/usr/bin/env python3
"""
Flask server for Legion's Space Fight game.
Serves static files, provides APIs for leaderboard, live players, and spectating.
Uses Redis for real-time state and PostgreSQL for persistent data.

Authentication: Session-based with device fingerprinting.
"""

# Monkey-patch for eventlet (must be first!)
import eventlet
eventlet.monkey_patch()

import os
import json
import random
import string
import time
import hashlib
import re
import logging
import traceback
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g
from flask_cors import CORS
from pathlib import Path
from datetime import datetime

# === ERROR LOGGING SETUP ===
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

# Configure file logging
file_handler = logging.FileHandler(LOG_DIR / 'error.log')
file_handler.setLevel(logging.ERROR)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)s | %(message)s\n%(exc_info)s\n---'
))

# Configure general log
general_handler = logging.FileHandler(LOG_DIR / 'server.log')
general_handler.setLevel(logging.INFO)
general_handler.setFormatter(logging.Formatter(
    '%(asctime)s | %(levelname)s | %(message)s'
))

# Setup logger
logger = logging.getLogger('fighter_jet')
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(general_handler)

# Also log to console
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
logger.addHandler(console_handler)


def log_error(endpoint: str, error: Exception, extra_info: dict = None):
    """Log an error with context."""
    error_data = {
        'endpoint': endpoint,
        'error_type': type(error).__name__,
        'error_message': str(error),
        'traceback': traceback.format_exc(),
        'timestamp': datetime.now().isoformat(),
        'ip': get_client_ip() if request else 'N/A',
    }
    if extra_info:
        error_data.update(extra_info)

    logger.error(f"[{endpoint}] {type(error).__name__}: {error}", exc_info=True)

    # Also append to JSON log for easy parsing
    try:
        json_log = LOG_DIR / 'errors.json'
        errors = []
        if json_log.exists():
            try:
                with open(json_log, 'r') as f:
                    errors = json.load(f)
            except:
                errors = []
        errors.append(error_data)
        # Keep last 1000 errors
        errors = errors[-1000:]
        with open(json_log, 'w') as f:
            json.dump(errors, f, indent=2, default=str)
    except Exception as e:
        logger.warning(f"Failed to write to JSON error log: {e}")

# Email service
try:
    import resend
    resend.api_key = os.environ.get('RESEND_API_KEY', '')
    USE_RESEND = bool(resend.api_key)
except ImportError:
    USE_RESEND = False
    print("Warning: Resend not available")

# Try to import database modules (graceful fallback for development)
try:
    import redis_client
    import database
    USE_REDIS = True
    USE_POSTGRES = True
except ImportError:
    USE_REDIS = False
    USE_POSTGRES = False
    print("Warning: Redis/Postgres modules not available, using in-memory fallback")

# Try to import WebSocket handler
try:
    from websocket_handler import init_socketio
    USE_WEBSOCKET = True
except ImportError:
    USE_WEBSOCKET = False
    print("Warning: WebSocket handler not available")

app = Flask(__name__)
CORS(app)

# Initialize WebSocket if available
socketio = None
if USE_WEBSOCKET:
    try:
        socketio = init_socketio(app)
        print("WebSocket support enabled")
    except Exception as e:
        print(f"Warning: WebSocket initialization failed: {e}")
        USE_WEBSOCKET = False

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
LEADERBOARD_FILE = DATA_DIR / 'leaderboard.json'
MAX_LEADERBOARD_SIZE = 10

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)

# In-memory fallback for active players (used if Redis not available)
active_players = {}
import threading
players_lock = threading.Lock()
PLAYER_TIMEOUT = 30

# Action types with emojis
ACTIONS = {
    'started_game': '🚀',
    'got_powerup': '⭐',
    'got_shield': '🛡️',
    'got_machinegun': '🔫',
    'got_bazooka': '💣',
    'got_laser': '⚡',
    'fighting_boss': '👹',
    'boss_low_health': '🔥',
    'defeated_boss': '🏆',
    'killed_by_boss': '💀',
    'killed_by_drone': '💀',
    'crashed_wall': '💥',
    'hit_bomb': '💥',
    'level_up': '🆙',
    'game_over': '🎮'
}


def generate_handle():
    """Generate a random player handle like Player_X7K2."""
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"Player_{suffix}"


# === AUTHENTICATION HELPERS ===

def get_client_ip():
    """Get client IP, handling proxies."""
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    return request.remote_addr


def get_device_fingerprint():
    """Get device fingerprint from request."""
    # Use custom header if provided, otherwise generate from request
    fingerprint = request.headers.get('X-Device-Fingerprint')
    if fingerprint:
        return fingerprint

    # Generate from available headers
    if USE_POSTGRES:
        return database.generate_device_fingerprint(
            get_client_ip(),
            request.headers.get('User-Agent', ''),
            request.headers.get('Accept-Language', '')
        )
    return None


def get_session_token():
    """Extract session token from Authorization header."""
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header[7:]
    return None


def validate_username(username):
    """Validate username format."""
    if not username or len(username) < 1 or len(username) > 12:
        return False
    return bool(re.match(r'^[a-zA-Z0-9_]+$', username))


def require_auth(f):
    """Decorator to require valid session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_session_token()

        if not token:
            return jsonify({'error': 'No authorization token'}), 401

        if not USE_POSTGRES:
            # Fallback: trust the token as player_id for non-DB mode
            g.player_id = token
            g.session = {'player_id': token}
            return f(*args, **kwargs)

        session = database.validate_session(token)
        if not session:
            return jsonify({'error': 'Invalid or expired session'}), 401

        if session.get('is_banned'):
            return jsonify({
                'error': 'Account suspended',
                'reason': session.get('ban_reason')
            }), 403

        g.session = session
        g.player_id = session['player_id']
        g.username = session['username']

        return f(*args, **kwargs)
    return decorated


def optional_auth(f):
    """Decorator for optional authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = get_session_token()
        g.session = None
        g.player_id = None

        if token and USE_POSTGRES:
            session = database.validate_session(token)
            if session and not session.get('is_banned'):
                g.session = session
                g.player_id = session['player_id']
                g.username = session.get('username')

        return f(*args, **kwargs)
    return decorated


def rate_limit(action, by='ip'):
    """Decorator for rate limiting."""
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not USE_POSTGRES:
                return f(*args, **kwargs)

            # Determine identifier
            if by == 'ip':
                identifier_type = 'ip'
                identifier_value = get_client_ip()
            elif by == 'session':
                token = get_session_token()
                if token:
                    identifier_type = 'session'
                    identifier_value = database.hash_token(token)[:32]
                else:
                    identifier_type = 'ip'
                    identifier_value = get_client_ip()
            else:
                identifier_type = 'ip'
                identifier_value = get_client_ip()

            if not database.check_rate_limit(identifier_type, identifier_value, action):
                status = database.get_rate_limit_status(identifier_type, identifier_value, action)
                return jsonify({
                    'error': 'Too many requests',
                    'retry_after': status.get('reset_at')
                }), 429

            return f(*args, **kwargs)
        return decorated
    return decorator


# === FALLBACK FUNCTIONS (when Redis/Postgres not available) ===

def cleanup_stale_players():
    """Remove players who haven't updated in PLAYER_TIMEOUT seconds."""
    current_time = time.time()
    with players_lock:
        stale = [pid for pid, data in active_players.items()
                 if current_time - data.get('lastUpdate', 0) > PLAYER_TIMEOUT]
        for pid in stale:
            del active_players[pid]


VALID_DIFFICULTIES = ['EASY', 'MEDIUM', 'HARD', 'EXPERT']

def get_all_leaderboards():
    """Read all leaderboards from JSON file (organized by difficulty)."""
    if not LEADERBOARD_FILE.exists():
        return {"EASY": [], "MEDIUM": [], "HARD": [], "EXPERT": []}
    try:
        with open(LEADERBOARD_FILE, 'r') as f:
            data = json.load(f)
            # Handle legacy format (flat array) - migrate to new format
            if isinstance(data, list):
                return {"EASY": data, "MEDIUM": [], "HARD": [], "EXPERT": []}
            # Handle old EXTREME key -> rename to EXPERT
            if "EXTREME" in data and "EXPERT" not in data:
                data["EXPERT"] = data.pop("EXTREME")
            return data
    except (json.JSONDecodeError, IOError):
        return {"EASY": [], "MEDIUM": [], "HARD": [], "EXPERT": []}


def get_leaderboard_fallback(difficulty='EASY'):
    """Read leaderboard for a specific difficulty."""
    all_boards = get_all_leaderboards()
    return all_boards.get(difficulty.upper(), [])


def save_leaderboard_fallback(leaderboard, difficulty='EASY'):
    """Save leaderboard for a specific difficulty."""
    all_boards = get_all_leaderboards()
    all_boards[difficulty.upper()] = leaderboard
    with open(LEADERBOARD_FILE, 'w') as f:
        json.dump(all_boards, f, indent=2)


# === STATIC FILES ===

@app.route('/')
def index():
    """Serve the main game file."""
    return send_from_directory(BASE_DIR, 'fighter-jet-game.html')


@app.route('/fighter-jet-game.html')
def serve_game():
    """Serve the game file directly."""
    return send_from_directory(BASE_DIR, 'fighter-jet-game.html')


# === AUTHENTICATION API ===

@app.route('/api/auth/register', methods=['POST'])
@rate_limit('player_join', by='ip')
def auth_register():
    """Register a new player or get existing session.

    Creates a player with username + device fingerprint.
    Returns a session token for future requests.
    """
    data = request.get_json() or {}

    username = str(data.get('username', '')).strip()
    display_name = str(data.get('displayName', username)).strip()[:12]

    # Generate username if not provided
    if not username:
        username = generate_handle()
        display_name = username
    else:
        username = username[:12]
        # Validate username format
        if not validate_username(username):
            return jsonify({
                'error': 'Invalid username. Use only letters, numbers, and underscores.'
            }), 400

    if not USE_POSTGRES:
        # Fallback mode - just generate a player ID
        player_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return jsonify({
            'success': True,
            'playerId': player_id,
            'username': username,
            'displayName': display_name,
            'token': player_id,
            'isNew': True
        })

    ip_address = get_client_ip()
    fingerprint = get_device_fingerprint()
    user_agent = request.headers.get('User-Agent', '')

    try:
        # Check if username exists
        existing = database.get_player_by_username(username)

        if existing:
            # Username taken - check if same device
            if fingerprint and existing.get('device_fingerprint') == fingerprint:
                # Same device - create new session
                session = database.create_session(
                    str(existing['id']), ip_address, user_agent, fingerprint
                )
                database.update_player_last_seen(str(existing['id']), ip_address)

                return jsonify({
                    'success': True,
                    'playerId': str(existing['id']),
                    'username': existing['username'],
                    'displayName': existing['display_name'],
                    'token': session['token'],
                    'expiresAt': session['expires_at'],
                    'isNew': False,
                    'emailVerified': existing.get('email_verified', False)
                })
            else:
                # Different device - username taken
                return jsonify({
                    'error': 'Username already taken',
                    'suggestion': generate_handle()
                }), 409

        # Create new player
        player = database.create_player(
            username=username,
            display_name=display_name or username,
            device_fingerprint=fingerprint,
            ip_address=ip_address
        )

        # Create session
        session = database.create_session(
            str(player['id']), ip_address, user_agent, fingerprint
        )

        # Track IP
        database.track_ip(ip_address, str(player['id']))

        # Audit log
        database.log_audit(
            action='player_registered',
            player_id=str(player['id']),
            ip_address=ip_address,
            new_value={'username': username}
        )

        return jsonify({
            'success': True,
            'playerId': str(player['id']),
            'username': player['username'],
            'displayName': player['display_name'],
            'token': session['token'],
            'expiresAt': session['expires_at'],
            'isNew': True
        })

    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({'error': 'Registration failed'}), 500


@app.route('/api/auth/login', methods=['POST'])
@rate_limit('player_join', by='ip')
def auth_login():
    """Login with existing session token or username + device fingerprint."""
    data = request.get_json() or {}

    # Option 1: Login with existing token
    token = data.get('token') or get_session_token()
    if token and USE_POSTGRES:
        session = database.validate_session(token)
        if session:
            return jsonify({
                'success': True,
                'playerId': session['player_id'],
                'username': session['username'],
                'displayName': session['display_name'],
                'token': token,
                'emailVerified': session.get('email_verified', False)
            })

    # Option 2: Login with username + fingerprint
    username = str(data.get('username', '')).strip()[:12].lower()
    fingerprint = get_device_fingerprint()

    if not USE_POSTGRES:
        return jsonify({'error': 'Login not available'}), 503

    if username:
        player = database.get_player_by_username(username)
        if player and fingerprint and player.get('device_fingerprint') == fingerprint:
            ip_address = get_client_ip()
            session = database.create_session(
                str(player['id']), ip_address,
                request.headers.get('User-Agent', ''), fingerprint
            )
            return jsonify({
                'success': True,
                'playerId': str(player['id']),
                'username': player['username'],
                'displayName': player['display_name'],
                'token': session['token'],
                'expiresAt': session['expires_at'],
                'emailVerified': player.get('email_verified', False)
            })

    return jsonify({'error': 'Invalid credentials'}), 401


@app.route('/api/auth/logout', methods=['POST'])
@require_auth
def auth_logout():
    """Logout and revoke session."""
    token = get_session_token()

    if USE_POSTGRES and g.session:
        database.revoke_session(g.session['session_id'], 'user_logout')
        database.log_audit(
            action='logout',
            player_id=g.player_id,
            session_id=g.session['session_id'],
            ip_address=get_client_ip()
        )

    return jsonify({'success': True})


@app.route('/api/auth/verify-email', methods=['POST'])
def auth_verify_email():
    """Verify email with token."""
    data = request.get_json() or {}
    token = data.get('token')

    if not token:
        return jsonify({'error': 'Token required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    result = database.verify_player_email(token)
    if result:
        return jsonify({
            'success': True,
            'username': result['username'],
            'email': result['email']
        })

    return jsonify({'error': 'Invalid or expired token'}), 400


@app.route('/api/auth/set-email', methods=['POST'])
@require_auth
def auth_set_email():
    """Set email for current player (sends verification)."""
    data = request.get_json() or {}
    email = str(data.get('email', '')).strip()[:100].lower()

    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    if database.set_player_email(g.player_id, email):
        # TODO: Send verification email
        return jsonify({'success': True, 'message': 'Verification email sent'})

    return jsonify({'error': 'Email already in use'}), 409


@app.route('/api/auth/check-username', methods=['GET'])
def auth_check_username():
    """Check if a username is available."""
    username = request.args.get('username', '').strip()[:12]

    if not validate_username(username):
        return jsonify({'available': False, 'error': 'Invalid format'})

    if not USE_POSTGRES:
        return jsonify({'available': True})

    available = database.is_username_available(username)
    return jsonify({'available': available})


@app.route('/api/auth/request-login-link', methods=['POST'])
@rate_limit('request_login', by='ip')
def auth_request_login_link():
    """Request a login link via email.

    Allows players to log in on a new device by verifying email ownership.
    Rate limited to prevent abuse.
    """
    data = request.get_json() or {}
    email = str(data.get('email', '')).strip()[:100].lower()

    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    # Find player by email
    player = database.get_player_by_email(email)
    if not player:
        # Don't reveal whether email exists - just say "sent"
        return jsonify({
            'success': True,
            'message': 'If an account exists with this email, a login link has been sent.'
        })

    if player.get('is_banned'):
        return jsonify({'error': 'Account suspended'}), 403

    # Create login token
    ip_address = get_client_ip()
    token_data = database.create_email_login_token(
        player_id=str(player['id']),
        ip_address=ip_address,
        expires_minutes=15
    )

    # Send email with login link
    login_link = f"{request.url_root}?login_token={token_data['token']}"

    if USE_RESEND:
        try:
            resend.Emails.send({
                "from": "Fighter Jet Game <games@felican.ai>",
                "to": [email],
                "subject": "Your Login Link",
                "html": f"""
                <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; background: #1a1a2e; color: #fff; padding: 30px; border-radius: 15px;">
                    <h1 style="color: #ffd700; text-align: center;">🎮 Fighter Jet Game</h1>
                    <h2 style="color: #4ade80; text-align: center;">Login Link</h2>
                    <p style="text-align: center; color: #aaa;">Click the button below to log in as <strong>{player['username']}</strong></p>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{login_link}" style="background: linear-gradient(135deg, #4ade80, #22c55e); color: #000; padding: 15px 30px; border-radius: 25px; text-decoration: none; font-weight: bold; font-size: 16px;">Log In Now</a>
                    </div>
                    <p style="color: #888; font-size: 12px; text-align: center;">
                        Or copy this code: <strong style="color: #4ade80;">{token_data['token'][:8]}...</strong><br>
                        This link expires in 15 minutes.
                    </p>
                    <hr style="border-color: #333; margin: 20px 0;">
                    <p style="color: #666; font-size: 11px; text-align: center;">
                        If you didn't request this, you can safely ignore this email.
                    </p>
                </div>
                """
            })
        except Exception as e:
            print(f"Failed to send login email: {e}")

    # Audit log
    database.log_audit(
        action='login_link_requested',
        player_id=str(player['id']),
        ip_address=ip_address,
        new_value={'email': email}
    )

    return jsonify({
        'success': True,
        'message': 'If an account exists with this email, a login link has been sent.'
    })


@app.route('/api/auth/verify-login-link', methods=['POST'])
@rate_limit('verify_login', by='ip')
def auth_verify_login_link():
    """Verify a login link token and create a session.

    This allows logging in on a new device. The device fingerprint
    is updated to link the new device to the account.
    """
    data = request.get_json() or {}
    token = str(data.get('token', '')).strip()

    if not token:
        return jsonify({'error': 'Token required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    ip_address = get_client_ip()
    fingerprint = get_device_fingerprint()
    user_agent = request.headers.get('User-Agent', '')

    result = database.verify_email_login_token(
        token=token,
        ip_address=ip_address,
        device_fingerprint=fingerprint,
        user_agent=user_agent
    )

    if not result:
        database.log_audit(
            action='login_link_failed',
            ip_address=ip_address,
            new_value={'token_prefix': token[:8]}
        )
        return jsonify({'error': 'Invalid or expired login link'}), 401

    # Audit log
    database.log_audit(
        action='login_link_verified',
        player_id=result['player_id'],
        ip_address=ip_address,
        new_value={'device_fingerprint': fingerprint}
    )

    return jsonify({
        'success': True,
        'playerId': result['player_id'],
        'username': result['username'],
        'displayName': result['display_name'],
        'email': result['email'],
        'token': result['token'],
        'expiresAt': result['expires_at']
    })


# === PASSWORD-BASED AUTHENTICATION API ===

@app.route('/api/auth/register-password', methods=['POST'])
@rate_limit('player_join', by='ip')
def auth_register_password():
    """Register a new player with email and password.

    Sends 6-digit verification code to email.
    """
    data = request.get_json() or {}

    username = str(data.get('username', '')).strip()[:12]
    email = str(data.get('email', '')).strip()[:100].lower()
    password = str(data.get('password', ''))

    if not username:
        return jsonify({'error': 'Username required'}), 400
    if not validate_username(username):
        return jsonify({'error': 'Invalid username. Use only letters, numbers, and underscores.'}), 400
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    ip_address = get_client_ip()
    fingerprint = get_device_fingerprint()

    try:
        result = database.register_player_with_password(
            username=username,
            email=email,
            password=password,
            device_fingerprint=fingerprint,
            ip_address=ip_address
        )

        if not result.get('success'):
            return jsonify({'error': result.get('error', 'Registration failed')}), 400

        # Send verification code email
        verification_code = result['verification_code']
        if USE_RESEND:
            try:
                resend.Emails.send({
                    "from": "Fighter Jet Game <games@felican.ai>",
                    "to": [email],
                    "subject": f"Your Verification Code: {verification_code}",
                    "html": f"""
                    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; background: #1a1a2e; color: #fff; padding: 30px; border-radius: 15px;">
                        <h1 style="color: #ffd700; text-align: center;">🎮 Fighter Jet Game</h1>
                        <h2 style="color: #4ade80; text-align: center;">Verify Your Email</h2>
                        <p style="text-align: center; color: #aaa;">Enter this code to complete your registration:</p>
                        <div style="background: #2a2a4e; padding: 20px; border-radius: 10px; text-align: center; margin: 20px 0;">
                            <span style="font-size: 36px; font-weight: bold; letter-spacing: 8px; color: #4ade80;">{verification_code}</span>
                        </div>
                        <p style="color: #888; font-size: 12px; text-align: center;">
                            This code expires in 10 minutes.
                        </p>
                        <hr style="border-color: #333; margin: 20px 0;">
                        <p style="color: #666; font-size: 11px; text-align: center;">
                            If you didn't request this, you can safely ignore this email.
                        </p>
                    </div>
                    """
                })
                logger.info(f"Verification code sent to {email}")
            except Exception as e:
                logger.error(f"Failed to send verification email: {e}")

        # Audit log
        database.log_audit(
            action='player_registered_password',
            player_id=result['player_id'],
            ip_address=ip_address,
            new_value={'username': username, 'email': email}
        )

        return jsonify({
            'success': True,
            'playerId': result['player_id'],
            'username': result['username'],
            'email': result['email'],
            'message': 'Verification code sent to your email'
        })

    except Exception as e:
        log_error('auth_register_password', e, {'username': username, 'email': email})
        return jsonify({'error': 'Registration failed'}), 500


@app.route('/api/auth/verify-code', methods=['POST'])
@rate_limit('verify_login', by='ip')
def auth_verify_code():
    """Verify email with 6-digit code.

    Creates a session on success (auto-login).
    """
    data = request.get_json() or {}

    email = str(data.get('email', '')).strip()[:100].lower()
    code = str(data.get('code', '')).strip()[:6]

    if not email or '@' not in email:
        return jsonify({'error': 'Email required'}), 400
    if not code or len(code) != 6:
        return jsonify({'error': '6-digit verification code required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    ip_address = get_client_ip()
    fingerprint = get_device_fingerprint()
    user_agent = request.headers.get('User-Agent', '')

    try:
        result = database.verify_email_with_code(
            email=email,
            code=code,
            ip_address=ip_address,
            user_agent=user_agent,
            device_fingerprint=fingerprint
        )

        if not result.get('success'):
            database.log_audit(
                action='verify_code_failed',
                ip_address=ip_address,
                new_value={'email': email}
            )
            return jsonify({'error': result.get('error', 'Verification failed')}), 400

        # Audit log
        database.log_audit(
            action='email_verified',
            player_id=result['player_id'],
            ip_address=ip_address
        )

        return jsonify({
            'success': True,
            'playerId': result['player_id'],
            'username': result['username'],
            'displayName': result['display_name'],
            'email': result['email'],
            'tokens': result['tokens'],
            'token': result['token'],
            'expiresAt': result['expires_at']
        })

    except Exception as e:
        log_error('auth_verify_code', e, {'email': email})
        return jsonify({'error': 'Verification failed'}), 500


@app.route('/api/auth/login-password', methods=['POST'])
@rate_limit('player_join', by='ip')
def auth_login_password():
    """Login with email/username and password."""
    data = request.get_json() or {}

    email_or_username = str(data.get('emailOrUsername', '')).strip()[:100]
    password = str(data.get('password', ''))

    if not email_or_username:
        return jsonify({'error': 'Email or username required'}), 400
    if not password:
        return jsonify({'error': 'Password required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    ip_address = get_client_ip()
    fingerprint = get_device_fingerprint()
    user_agent = request.headers.get('User-Agent', '')

    try:
        result = database.login_with_password(
            email_or_username=email_or_username,
            password=password,
            ip_address=ip_address,
            user_agent=user_agent,
            device_fingerprint=fingerprint
        )

        if not result.get('success'):
            database.log_audit(
                action='login_failed',
                ip_address=ip_address,
                new_value={'identifier': email_or_username}
            )
            response = {'error': result.get('error', 'Login failed')}
            if result.get('needs_verification'):
                response['needsVerification'] = True
            return jsonify(response), 401

        # Audit log
        database.log_audit(
            action='login_password',
            player_id=result['player_id'],
            ip_address=ip_address
        )

        return jsonify({
            'success': True,
            'playerId': result['player_id'],
            'username': result['username'],
            'displayName': result['display_name'],
            'email': result['email'],
            'tokens': result['tokens'],
            'savedLevel': result['saved_level'],
            'savedScore': result['saved_score'],
            'savedDifficulty': result['saved_difficulty'],
            'continuesThisLevel': result['continues_this_level'],
            'token': result['token'],
            'expiresAt': result['expires_at']
        })

    except Exception as e:
        log_error('auth_login_password', e, {'identifier': email_or_username})
        return jsonify({'error': 'Login failed'}), 500


@app.route('/api/auth/resend-code', methods=['POST'])
@rate_limit('request_key', by='ip')
def auth_resend_code():
    """Resend verification code to email."""
    data = request.get_json() or {}

    email = str(data.get('email', '')).strip()[:100].lower()

    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    try:
        result = database.resend_verification_code(email)

        if not result.get('success'):
            return jsonify({'error': result.get('error', 'Failed to resend code')}), 400

        # Send verification code email
        verification_code = result['verification_code']
        if USE_RESEND:
            try:
                resend.Emails.send({
                    "from": "Fighter Jet Game <games@felican.ai>",
                    "to": [email],
                    "subject": f"Your Verification Code: {verification_code}",
                    "html": f"""
                    <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; background: #1a1a2e; color: #fff; padding: 30px; border-radius: 15px;">
                        <h1 style="color: #ffd700; text-align: center;">🎮 Fighter Jet Game</h1>
                        <h2 style="color: #4ade80; text-align: center;">Your New Code</h2>
                        <div style="background: #2a2a4e; padding: 20px; border-radius: 10px; text-align: center; margin: 20px 0;">
                            <span style="font-size: 36px; font-weight: bold; letter-spacing: 8px; color: #4ade80;">{verification_code}</span>
                        </div>
                        <p style="color: #888; font-size: 12px; text-align: center;">
                            This code expires in 10 minutes.
                        </p>
                    </div>
                    """
                })
            except Exception as e:
                logger.error(f"Failed to send verification email: {e}")

        return jsonify({
            'success': True,
            'message': 'Verification code sent to your email'
        })

    except Exception as e:
        log_error('auth_resend_code', e, {'email': email})
        return jsonify({'error': 'Failed to resend code'}), 500


# === PLAYER PROFILE & TOKEN API ===

@app.route('/api/player/profile', methods=['GET'])
@require_auth
def get_player_profile():
    """Get authenticated player's full profile."""
    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    try:
        profile = database.get_player_profile(g.player_id)
        if not profile:
            return jsonify({'error': 'Player not found'}), 404

        # Convert datetime objects in games
        for game in profile.get('games', []):
            for key in ['started_at', 'ended_at']:
                if game.get(key) and hasattr(game[key], 'isoformat'):
                    game[key] = game[key].isoformat()

        return jsonify(profile)

    except Exception as e:
        log_error('get_player_profile', e, {'player_id': g.player_id})
        return jsonify({'error': 'Failed to get profile'}), 500


@app.route('/api/player/use-token', methods=['POST'])
@require_auth
def use_continue_token():
    """Use 1 token for a continue.

    Returns new token balance and continues count.
    """
    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    try:
        result = database.use_continue_token(g.player_id)

        if not result.get('success'):
            return jsonify({
                'error': result.get('error', 'Failed to use token'),
                'tokens': result.get('tokens', 0)
            }), 400

        # Audit log
        database.log_audit(
            action='token_used',
            player_id=g.player_id,
            ip_address=get_client_ip(),
            new_value={'tokens_remaining': result['tokens'], 'continues': result['continues_this_level']}
        )

        return jsonify({
            'success': True,
            'tokens': result['tokens'],
            'continuesThisLevel': result['continues_this_level']
        })

    except Exception as e:
        log_error('use_continue_token', e, {'player_id': g.player_id})
        return jsonify({'error': 'Failed to use token'}), 500


@app.route('/api/player/save-game-progress', methods=['POST'])
@require_auth
def save_game_progress():
    """Save player's game progress (called on level advance).

    Resets continues_this_level to 0.
    """
    data = request.get_json() or {}

    level = int(data.get('level', 1))
    score = int(data.get('score', 0))
    difficulty = str(data.get('difficulty', 'EASY'))[:10].upper()

    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    try:
        result = database.save_player_progress(g.player_id, level, score, difficulty)

        if not result.get('success'):
            return jsonify({'error': result.get('error', 'Failed to save progress')}), 400

        return jsonify({
            'success': True,
            'savedLevel': result['saved_level'],
            'savedScore': result['saved_score'],
            'savedDifficulty': result['saved_difficulty'],
            'tokens': result['tokens']
        })

    except Exception as e:
        log_error('save_game_progress', e, {'player_id': g.player_id})
        return jsonify({'error': 'Failed to save progress'}), 500


@app.route('/api/player/reset-level-continues', methods=['POST'])
@require_auth
def reset_level_continues():
    """Reset continues_this_level to 0.

    Called when player restarts at level beginning after using 3 continues.
    """
    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    try:
        result = database.reset_continues_for_level(g.player_id)

        if not result.get('success'):
            return jsonify({'error': result.get('error', 'Failed to reset continues')}), 400

        return jsonify({
            'success': True,
            'continuesThisLevel': 0
        })

    except Exception as e:
        log_error('reset_level_continues', e, {'player_id': g.player_id})
        return jsonify({'error': 'Failed to reset continues'}), 500


@app.route('/api/player/tokens', methods=['GET'])
@require_auth
def get_player_tokens():
    """Get player's current token balance."""
    if not USE_POSTGRES:
        return jsonify({'error': 'Not available'}), 503

    try:
        tokens = database.get_player_tokens(g.player_id)
        return jsonify({'tokens': tokens})

    except Exception as e:
        log_error('get_player_tokens', e, {'player_id': g.player_id})
        return jsonify({'error': 'Failed to get tokens'}), 500


# === GAME SESSION API ===

@app.route('/api/game/start', methods=['POST'])
@require_auth
def game_start():
    """Start a new game session (server-side tracking)."""
    data = request.get_json() or {}
    difficulty = str(data.get('difficulty', 'EASY'))[:10].upper()
    game_mode = data.get('mode', 'single')
    room_code = data.get('roomCode')

    if difficulty not in VALID_DIFFICULTIES:
        difficulty = 'EASY'

    if not USE_POSTGRES:
        # Fallback: generate random session ID
        session_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))
        return jsonify({'success': True, 'gameSessionId': session_id})

    try:
        game_session_id = database.create_game_session(
            player_id=g.player_id,
            player_session_id=g.session['session_id'],
            difficulty=difficulty,
            game_mode=game_mode,
            room_code=room_code,
            client_ip=get_client_ip()
        )

        return jsonify({
            'success': True,
            'gameSessionId': game_session_id
        })
    except Exception as e:
        print(f"Error starting game: {e}")
        return jsonify({'error': 'Failed to start game'}), 500


@app.route('/api/game/event', methods=['POST'])
@require_auth
@rate_limit('game_event', by='session')
def game_event():
    """Report a game event (for score validation)."""
    data = request.get_json() or {}

    game_session_id = data.get('gameSessionId')
    event_type = data.get('type')
    game_timestamp = int(data.get('timestamp', 0))
    level = int(data.get('level', 1))
    position_x = data.get('x')
    position_y = data.get('y')
    details = data.get('details')

    if not game_session_id or not event_type:
        return jsonify({'error': 'Missing gameSessionId or type'}), 400

    if not USE_POSTGRES:
        return jsonify({'success': True, 'scoreDelta': 0, 'runningScore': 0})

    try:
        result = database.log_game_event(
            game_session_id=game_session_id,
            event_type=event_type,
            game_timestamp=game_timestamp,
            level=level,
            position_x=position_x,
            position_y=position_y,
            details=details
        )

        return jsonify({
            'success': True,
            'scoreDelta': result['score_delta'],
            'runningScore': result['running_score']
        })
    except Exception as e:
        print(f"Error logging event: {e}")
        return jsonify({'error': 'Failed to log event'}), 500


@app.route('/api/game/end', methods=['POST'])
@require_auth
def game_end():
    """End a game session with validation."""
    data = request.get_json() or {}

    game_session_id = data.get('gameSessionId')
    client_score = int(data.get('score', 0))
    level = int(data.get('level', 1))
    duration = int(data.get('duration', 0))
    death_reason = data.get('deathReason', 'unknown')
    bosses_defeated = int(data.get('bossesDefeated', 0))
    enemies_killed = int(data.get('enemiesKilled', 0))
    is_victory = data.get('isVictory', False)

    if not game_session_id:
        return jsonify({'error': 'Missing gameSessionId'}), 400

    if not USE_POSTGRES:
        return jsonify({
            'success': True,
            'finalScore': client_score,
            'isValidated': True
        })

    try:
        result = database.end_game_session(
            game_session_id=game_session_id,
            client_score=client_score,
            level=level,
            duration=duration,
            death_reason=death_reason,
            bosses_defeated=bosses_defeated,
            enemies_killed=enemies_killed,
            is_victory=is_victory
        )

        # Add to leaderboard if validated
        leaderboard_entry = None
        if result['is_validated'] and result['final_score'] > 0:
            leaderboard_entry = database.add_leaderboard_entry(game_session_id)

        return jsonify({
            'success': True,
            'finalScore': result['final_score'],
            'serverScore': result['server_score'],
            'isValidated': result['is_validated'],
            'discrepancy': result['discrepancy'],
            'leaderboardEntry': leaderboard_entry
        })
    except Exception as e:
        print(f"Error ending game: {e}")
        return jsonify({'error': 'Failed to end game'}), 500


# === LEADERBOARD API ===

@app.route('/api/leaderboard/all', methods=['GET'])
def get_all_scores():
    """Get top scores for all difficulties."""
    all_boards = get_all_leaderboards()
    # Limit each to MAX_LEADERBOARD_SIZE
    for diff in all_boards:
        all_boards[diff] = all_boards[diff][:MAX_LEADERBOARD_SIZE]
    return jsonify(all_boards)


@app.route('/api/leaderboard', methods=['GET'])
def get_scores():
    """Get top scores for a specific difficulty."""
    difficulty = request.args.get('difficulty', 'EASY').upper()
    if difficulty not in VALID_DIFFICULTIES:
        difficulty = 'EASY'

    if USE_POSTGRES:
        try:
            scores = database.get_leaderboard(MAX_LEADERBOARD_SIZE)
            # Filter by difficulty and convert datetime objects
            scores = [s for s in scores if s.get('difficulty', 'EASY').upper() == difficulty]
            for s in scores:
                if s.get('date'):
                    s['date'] = s['date'].isoformat() if hasattr(s['date'], 'isoformat') else str(s['date'])
            return jsonify(scores[:MAX_LEADERBOARD_SIZE])
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify(get_leaderboard_fallback(difficulty))


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
    duration = int(data.get('duration', 0))
    bosses_defeated = int(data.get('bossesDefeated', 0))
    death_reason = str(data.get('deathReason', 'unknown'))[:50]

    # Try to save to PostgreSQL first
    if USE_POSTGRES:
        try:
            database.add_leaderboard_entry(
                name=name,
                score=score,
                duration=duration,
                level=level,
                difficulty=difficulty,
                bosses_defeated=bosses_defeated,
                death_reason=death_reason
            )
            # Get updated leaderboard from database
            leaderboard = database.get_leaderboard(MAX_LEADERBOARD_SIZE)
            for s in leaderboard:
                if s.get('date'):
                    s['date'] = s['date'].isoformat() if hasattr(s['date'], 'isoformat') else str(s['date'])
            return jsonify({'success': True, 'leaderboard': leaderboard})
        except Exception as e:
            print(f"Database error saving score: {e}")
            # Fall through to JSON fallback

    # Fallback: Use JSON storage (organized by difficulty)
    difficulty_upper = difficulty.upper()
    if difficulty_upper not in VALID_DIFFICULTIES:
        difficulty_upper = 'EASY'

    leaderboard = get_leaderboard_fallback(difficulty_upper)
    leaderboard.append({
        'name': name,
        'score': score,
        'difficulty': difficulty_upper,
        'level': level,
        'duration': duration,
        'date': datetime.now().isoformat()
    })

    # Sort by score desc, then duration asc
    leaderboard.sort(key=lambda x: (-x['score'], x.get('duration', 9999)))
    leaderboard = leaderboard[:MAX_LEADERBOARD_SIZE]
    save_leaderboard_fallback(leaderboard, difficulty_upper)

    return jsonify({'success': True, 'leaderboard': leaderboard})


# === LIVE PLAYERS API ===

@app.route('/api/players/join', methods=['POST'])
def player_join():
    """Register a new player."""
    data = request.get_json() or {}

    name = str(data.get('name', '')).strip()[:12]
    if not name:
        name = generate_handle()

    player_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
    difficulty = data.get('difficulty', 'EASY')
    color = data.get('color', 'blue')

    player_data = {
        'id': player_id,
        'name': name,
        'score': 0,
        'level': 1,
        'difficulty': difficulty,
        'color': color,
        'status': 'lobby',
        'lastAction': 'joined',
        'lastActionEmoji': '👋',
        'lastActionTime': datetime.now().isoformat(),
        'gameStartTime': None,
        'gameDuration': 0,
        'shieldActive': False,
        'currentWeapon': 'pistol',
        'bossHealth': None,
        'isNew': True,
        'defeatedBoss': False,
        'sessionId': None,
        'lastUpdate': time.time()
    }

    if USE_REDIS:
        try:
            redis_client.set_player(player_id, player_data)
            redis_client.mark_new_player(player_id)
            players = redis_client.get_all_players()
        except Exception as e:
            print(f"Redis error: {e}")
            players = []
    else:
        cleanup_stale_players()
        with players_lock:
            active_players[player_id] = player_data
            players = list(active_players.values())

    # Create player in database
    if USE_POSTGRES:
        try:
            database.get_or_create_player(name)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify({
        'success': True,
        'playerId': player_id,
        'name': name,
        'players': players
    })


@app.route('/api/players/update', methods=['POST'])
def player_update():
    """Update player's score, status, and action."""
    data = request.get_json() or {}

    player_id = data.get('playerId')
    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    updates = {
        'lastUpdate': time.time()
    }

    # Update fields if provided
    for field in ['score', 'level', 'status', 'difficulty', 'gameDuration',
                  'shieldActive', 'currentWeapon', 'bossHealth', 'gameStartTime']:
        if field in data:
            updates[field] = data[field]

    if USE_REDIS:
        try:
            if not redis_client.update_player(player_id, updates):
                return jsonify({'error': 'Player not found'}), 404
            players = redis_client.get_all_players()
        except Exception as e:
            print(f"Redis error: {e}")
            return jsonify({'error': 'Server error'}), 500
    else:
        cleanup_stale_players()
        with players_lock:
            if player_id not in active_players:
                return jsonify({'error': 'Player not found'}), 404

            active_players[player_id].update(updates)
            players = sorted(active_players.values(),
                           key=lambda x: x.get('score', 0), reverse=True)

    return jsonify({'success': True, 'players': players})


@app.route('/api/players/action', methods=['POST'])
def player_action():
    """Report a player action."""
    data = request.get_json() or {}

    player_id = data.get('playerId')
    action = data.get('action')
    session_id = data.get('sessionId')

    if not player_id or not action:
        return jsonify({'error': 'Missing playerId or action'}), 400

    emoji = ACTIONS.get(action, '❓')

    updates = {
        'lastAction': action,
        'lastActionEmoji': emoji,
        'lastActionTime': datetime.now().isoformat(),
        'lastUpdate': time.time()
    }

    # Handle special actions
    if action == 'started_game':
        updates['status'] = 'playing'
        updates['gameStartTime'] = datetime.now().isoformat()

    elif action == 'fighting_boss':
        updates['status'] = 'boss'

    elif action == 'defeated_boss':
        updates['defeatedBoss'] = True
        boss_level = data.get('bossLevel', 1)
        if USE_REDIS:
            try:
                redis_client.mark_boss_defeat(player_id, boss_level)
            except:
                pass

    elif action in ['killed_by_boss', 'killed_by_drone', 'crashed_wall', 'hit_bomb', 'game_over']:
        updates['status'] = 'dead'

    elif action == 'got_shield':
        updates['shieldActive'] = True

    elif action in ['got_machinegun', 'got_bazooka', 'got_laser']:
        weapon = action.replace('got_', '')
        updates['currentWeapon'] = weapon

    # Update Redis
    if USE_REDIS:
        try:
            redis_client.update_player(player_id, updates)
        except Exception as e:
            print(f"Redis error: {e}")

    # Log to database
    if USE_POSTGRES and session_id:
        try:
            database.log_game_event(session_id, action, emoji, data.get('details'))
        except Exception as e:
            print(f"Database error: {e}")

    # Fallback
    if not USE_REDIS:
        with players_lock:
            if player_id in active_players:
                active_players[player_id].update(updates)

    return jsonify({'success': True, 'emoji': emoji})


@app.route('/api/players/start-session', methods=['POST'])
def start_session():
    """Start a new game session for database tracking."""
    data = request.get_json() or {}

    player_name = data.get('name')
    difficulty = data.get('difficulty', 'EASY')

    if not player_name:
        return jsonify({'error': 'Missing name'}), 400

    session_id = None
    if USE_POSTGRES:
        try:
            player = database.get_or_create_player(player_name)
            session_id = database.create_game_session(player['id'], difficulty)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify({'success': True, 'sessionId': session_id})


@app.route('/api/players/end-session', methods=['POST'])
def end_session():
    """End a game session with final stats."""
    data = request.get_json() or {}

    session_id = data.get('sessionId')
    if not session_id:
        return jsonify({'error': 'Missing sessionId'}), 400

    score = int(data.get('score', 0))
    level = int(data.get('level', 1))
    duration = int(data.get('duration', 0))
    death_reason = data.get('deathReason', 'unknown')
    bosses_defeated = int(data.get('bossesDefeated', 0))

    if USE_POSTGRES:
        try:
            database.end_game_session(session_id, score, level, duration,
                                      death_reason, bosses_defeated)
        except Exception as e:
            print(f"Database error: {e}")

    return jsonify({'success': True})


@app.route('/api/players/leave', methods=['POST'])
def player_leave():
    """Remove player from active list."""
    data = request.get_json() or {}
    player_id = data.get('playerId')

    if USE_REDIS:
        try:
            redis_client.delete_player(player_id)
            redis_client.delete_game_state(player_id)
        except:
            pass

    with players_lock:
        if player_id in active_players:
            del active_players[player_id]

    return jsonify({'success': True})


@app.route('/api/players/active', methods=['GET'])
def get_active_players():
    """Get all active players with their status."""
    if USE_REDIS:
        try:
            players = redis_client.get_all_players()
            new_players = redis_client.get_new_players()

            for p in players:
                p['isNew'] = p.get('id') in new_players
                p['defeatedBoss'] = redis_client.has_defeated_boss(p.get('id', ''))
                p['spectatorCount'] = redis_client.get_spectator_count(p.get('id', ''))

            return jsonify(players)
        except Exception as e:
            print(f"Redis error: {e}")

    cleanup_stale_players()
    with players_lock:
        players = sorted(active_players.values(),
                        key=lambda x: x.get('score', 0), reverse=True)
    return jsonify(players)


# === SPECTATOR API ===

@app.route('/api/players/gamestate', methods=['POST'])
def update_game_state():
    """Update game state for spectators."""
    data = request.get_json() or {}

    player_id = data.get('playerId')
    state = data.get('state')

    if not player_id or not state:
        return jsonify({'error': 'Missing playerId or state'}), 400

    if USE_REDIS:
        try:
            redis_client.set_game_state(player_id, state)
        except Exception as e:
            print(f"Redis error: {e}")

    return jsonify({'success': True})


@app.route('/api/players/spectate/<player_id>', methods=['GET'])
def spectate_player(player_id):
    """Get game state for spectating a player."""
    spectator_id = request.args.get('spectatorId')

    if USE_REDIS:
        try:
            # Track spectator
            if spectator_id:
                redis_client.add_spectator(player_id, spectator_id)

            # Get game state
            state = redis_client.get_game_state(player_id)
            player = redis_client.get_player(player_id)

            if not state:
                return jsonify({'error': 'No game state available'}), 404

            return jsonify({
                'success': True,
                'player': player,
                'state': state
            })
        except Exception as e:
            print(f"Redis error: {e}")
            return jsonify({'error': 'Server error'}), 500

    return jsonify({'error': 'Spectating not available'}), 503


@app.route('/api/players/spectate/<player_id>/leave', methods=['POST'])
def leave_spectate(player_id):
    """Stop spectating a player."""
    data = request.get_json() or {}
    spectator_id = data.get('spectatorId')

    if USE_REDIS and spectator_id:
        try:
            redis_client.remove_spectator(player_id, spectator_id)
        except:
            pass

    return jsonify({'success': True})


# === SPECTATOR COMMENTS API ===

@app.route('/api/players/spectate/<player_id>/comment', methods=['POST'])
def add_comment(player_id):
    """Add a comment for a player being watched."""
    data = request.get_json() or {}

    author = str(data.get('from', 'Anonymous'))[:12]
    message = str(data.get('message', ''))[:100].strip()

    if not message:
        return jsonify({'error': 'Empty message'}), 400

    comment = {
        'from': author,
        'message': message,
        'time': datetime.now().isoformat(),
        'isPlayer': data.get('isPlayer', False)
    }

    if USE_REDIS:
        try:
            redis_client.add_comment(player_id, comment)
        except Exception as e:
            print(f"Redis error: {e}")
            return jsonify({'error': 'Server error'}), 500

    return jsonify({'success': True})


@app.route('/api/players/spectate/<player_id>/comments', methods=['GET'])
def get_comments(player_id):
    """Get recent comments for a player being watched."""
    if not USE_REDIS:
        return jsonify([])

    try:
        comments = redis_client.get_comments(player_id)
        return jsonify(comments)
    except Exception as e:
        print(f"Redis error: {e}")
        return jsonify([])


# === PLAYER HISTORY API ===

@app.route('/api/players/history/<name>', methods=['GET'])
def get_player_history(name):
    """Get player profile and game history."""
    if not USE_POSTGRES:
        return jsonify({'error': 'History not available'}), 503

    try:
        history = database.get_player_history(name)
        if not history:
            return jsonify({'error': 'Player not found'}), 404

        # Convert datetime objects
        for game in history.get('games', []):
            for key in ['started_at', 'ended_at']:
                if game.get(key) and hasattr(game[key], 'isoformat'):
                    game[key] = game[key].isoformat()

        player = history.get('player', {})
        if player.get('first_seen') and hasattr(player['first_seen'], 'isoformat'):
            player['first_seen'] = player['first_seen'].isoformat()

        return jsonify(history)
    except Exception as e:
        print(f"Database error: {e}")
        return jsonify({'error': 'Server error'}), 500


# === VICTORY API ===

@app.route('/api/victory/save', methods=['POST'])
def save_victory():
    """Save victory data including player email."""
    data = request.get_json() or {}

    name = str(data.get('name', 'Anonymous'))[:12].strip()
    email = str(data.get('email', ''))[:100].strip()
    score = int(data.get('score', 0))
    enemies_killed = int(data.get('enemiesKilled', 0))
    duration = int(data.get('duration', 0))

    if not name:
        return jsonify({'error': 'Missing player name'}), 400

    if USE_POSTGRES:
        try:
            session_id = database.save_victory(
                name=name,
                email=email,
                score=score,
                enemies_killed=enemies_killed,
                duration=duration
            )
            return jsonify({'success': True, 'sessionId': session_id})
        except Exception as e:
            print(f"Database error saving victory: {e}")
            return jsonify({'error': 'Server error'}), 500

    # Fallback: Just log it
    print(f"Victory: {name} ({email}) - Score: {score}, Enemies: {enemies_killed}")
    return jsonify({'success': True})


# === BACKUP SCHEDULER ===

def init_backup_scheduler():
    """Initialize background backup scheduler (only in one process)."""
    # Use a lock file to ensure only one scheduler runs across workers
    lock_file = DATA_DIR / '.scheduler.lock'

    try:
        import fcntl
        lock_fd = open(lock_file, 'w')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError, ImportError):
        # Another process has the lock or fcntl not available
        print("Backup scheduler: another process is running scheduler")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        import backup

        scheduler = BackgroundScheduler()

        # Local backup every minute
        scheduler.add_job(
            backup.local_backup,
            'interval',
            minutes=1,
            id='local_backup',
            name='Local backup every minute'
        )

        # Offload to Backblaze every 6 hours
        scheduler.add_job(
            backup.offload_to_backblaze,
            'interval',
            hours=6,
            id='b2_offload',
            name='Backblaze offload every 6 hours'
        )

        scheduler.start()
        print("Backup scheduler started (1min local, 6hr B2 offload)")
        return scheduler

    except ImportError as e:
        print(f"Backup scheduler not available: {e}")
        return None


# === BACKUP API ===

@app.route('/api/backup/list', methods=['GET'])
def list_backups():
    """List available backups."""
    try:
        import backup
        backups = backup.list_backups()
        return jsonify(backups)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/backup/restore', methods=['POST'])
def restore_backup():
    """Restore from latest backup."""
    try:
        import backup
        if backup.restore_latest():
            return jsonify({'success': True})
        return jsonify({'error': 'No backups available'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# === RESPAWN SYSTEM API ===

PLAYER_PROGRESS_FILE = DATA_DIR / 'player_progress.json'
FREE_RESPAWNS_PER_LEVEL = 3

def load_player_progress():
    """Load all player progress from JSON file."""
    if not PLAYER_PROGRESS_FILE.exists():
        return {}
    try:
        with open(PLAYER_PROGRESS_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

def save_player_progress(data):
    """Save player progress to JSON file."""
    with open(PLAYER_PROGRESS_FILE, 'w') as f:
        json.dump(data, f, indent=2)

def generate_continue_key_legacy():
    """Generate a unique 8-character continue key (legacy 6-char format for JSON fallback)."""
    chars = string.ascii_uppercase + string.digits
    # Remove ambiguous characters
    chars = chars.replace('O', '').replace('0', '').replace('I', '').replace('1', '').replace('L', '')
    return 'FJ-' + ''.join(random.choices(chars, k=6))

def send_continue_key_email(email: str, key: str, player_name: str, level: int):
    """Send continue key via Resend."""
    if not USE_RESEND:
        print(f"[DEBUG] Would send key {key} to {email}")
        return True

    try:
        resend.Emails.send({
            "from": "Fighter Jet Game <games@felican.ai>",
            "to": [email],
            "subject": f"Your Continue Key - Level {level}",
            "html": f"""
            <div style="font-family: Arial, sans-serif; max-width: 500px; margin: 0 auto; background: #1a1a2e; color: #fff; padding: 30px; border-radius: 15px;">
                <h1 style="color: #ffd700; text-align: center;">🎮 Fighter Jet Game</h1>
                <h2 style="color: #4ade80; text-align: center;">Continue Key for {player_name}</h2>
                <p style="text-align: center; color: #aaa;">Use this key to continue from Level {level}</p>
                <div style="background: #2a2a4e; padding: 20px; border-radius: 10px; text-align: center; margin: 20px 0;">
                    <span style="font-size: 32px; font-weight: bold; letter-spacing: 3px; color: #4ade80;">{key}</span>
                </div>
                <p style="color: #888; font-size: 12px; text-align: center;">
                    This key gives you 3 more respawns at Level {level}.<br>
                    You can use this key anytime to resume your game.
                </p>
                <hr style="border-color: #333; margin: 20px 0;">
                <p style="color: #666; font-size: 11px; text-align: center;">
                    Good luck, pilot! 🚀
                </p>
            </div>
            """
        })
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False


@app.route('/api/player/check-name', methods=['POST'])
def check_player_name():
    """Check if a username is available or belongs to a returning player."""
    data = request.get_json() or {}
    name = str(data.get('name', '')).strip()[:12].lower()

    if not name:
        return jsonify({'error': 'Name required'}), 400

    progress = load_player_progress()

    if name in progress:
        # Name exists - return saved progress so player can continue
        player_data = progress[name]
        return jsonify({
            'available': False,
            'hasKey': True,
            'savedLevel': player_data.get('currentLevel', 1),
            'savedScore': player_data.get('currentScore', 0),
            'savedDifficulty': player_data.get('difficulty', 'EASY'),
            'message': 'Welcome back! Enter your key to continue.'
        })

    return jsonify({'available': True})


@app.route('/api/player/request-key', methods=['POST'])
@rate_limit('request_key', by='ip')
def request_continue_key():
    """Request a continue key - gets existing key or creates new one.

    Key is returned in response AND sent via email.
    Each player has ONE key that tracks their progress.
    Key should be stored in sessionStorage (cleared when session ends).
    """
    data = request.get_json() or {}
    name = str(data.get('name', '')).strip()[:12].lower()
    email = str(data.get('email', '')).strip()[:100]
    level = int(data.get('level', 1))
    score = int(data.get('score', 0))
    difficulty = str(data.get('difficulty', 'EASY'))[:10].upper()

    logger.info(f"Continue key requested: name={name}, email={email}, level={level}")

    if not name:
        return jsonify({'error': 'Name required'}), 400
    if not email or '@' not in email:
        return jsonify({'error': 'Valid email required'}), 400

    # Try database first
    if USE_POSTGRES:
        try:
            # Get or create player
            player = database.get_player_by_username(name)
            if not player:
                # Create player if doesn't exist
                fingerprint = get_device_fingerprint()
                player = database.create_player(
                    username=name,
                    display_name=name,
                    device_fingerprint=fingerprint,
                    ip_address=get_client_ip(),
                    email=email
                )
                logger.info(f"Created new player: {name}")
            else:
                # Update email if not set
                if not player.get('email'):
                    database.set_player_email(str(player['id']), email)

            # Get existing key or create new one
            key_data = database.get_or_create_player_key(
                player_id=str(player['id']),
                level=level,
                score=score,
                difficulty=difficulty,
                ip_address=get_client_ip()
            )

            # If existing key, we can't recover it - create new one
            if key_data['is_existing']:
                # Player already has an exhausted or existing key - create fresh one
                # Clear existing keys first
                key_data = database.create_continue_key(
                    player_id=str(player['id']),
                    level=level,
                    score=score,
                    difficulty=difficulty,
                    ip_address=get_client_ip()
                )
                logger.info(f"Created new continue key for {name}")

            the_key = key_data.get('key')
            if not the_key:
                logger.error(f"No key returned from database for {name}")
                return jsonify({'error': 'Failed to generate key'}), 500

            # Send email as backup
            email_sent = send_continue_key_email(email, the_key, name, level)
            logger.info(f"Email sent: {email_sent} to {email}")

            # Audit log
            database.log_audit(
                action='continue_key_requested',
                player_id=str(player['id']),
                ip_address=get_client_ip(),
                new_value={'level': level, 'score': score, 'email_sent': email_sent}
            )

            # Return key in response (display on screen)
            return jsonify({
                'success': True,
                'key': the_key,  # Return the key to display on screen
                'emailSent': email_sent,
                'message': 'Your continue key is ready!' if email_sent else 'Key generated (email failed)',
                'respawnsRemaining': 3
            })

        except Exception as e:
            log_error('request_continue_key', e, {'name': name, 'email': email, 'level': level})
            # Fall through to JSON fallback

    # Fallback to JSON storage
    progress = load_player_progress()

    # Check if player already has an active key with respawns left
    existing_key = None
    if name in progress and progress[name].get('keys'):
        active_keys = [k for k in progress[name]['keys']
                       if not k.get('used', False) and k.get('respawnsRemaining', 0) > 0]
        if active_keys:
            existing_key = active_keys[0]['key']

    # Use existing key or generate new one
    if existing_key:
        key = existing_key
        respawns_remaining = next(
            (k.get('respawnsRemaining', 3) for k in progress[name]['keys'] if k['key'] == key), 3
        )
    else:
        # Generate new key (old 6-char format for fallback)
        key = generate_continue_key_legacy()
        respawns_remaining = 3

        # Store player progress
        if name not in progress:
            progress[name] = {
                'email': email,
                'keys': [],
                'currentLevel': level,
                'currentScore': score,
                'difficulty': difficulty,
                'respawnsUsed': {},
                'totalRespawns': 0,
                'keyRequests': 0,
                'history': [],
                'createdAt': datetime.now().isoformat()
            }

        # Update player data
        player_data = progress[name]
        player_data['email'] = email
        player_data['currentLevel'] = level
        player_data['currentScore'] = score
        player_data['difficulty'] = difficulty
        player_data['keys'].append({
            'key': key,
            'level': level,
            'createdAt': datetime.now().isoformat(),
            'used': False,
            'respawnsRemaining': 3
        })
        player_data['keyRequests'] += 1
        player_data['history'].append({
            'action': 'key_requested',
            'level': level,
            'score': score,
            'timestamp': datetime.now().isoformat()
        })

        player_data['respawnsUsed'][str(level)] = 0
        save_player_progress(progress)

    # Send email as backup
    email_sent = send_continue_key_email(email, key, name, level)

    # Return key in response (display on screen)
    return jsonify({
        'success': True,
        'key': key,  # Return the key to display on screen
        'emailSent': email_sent,
        'message': 'Your continue key is ready!',
        'respawnsRemaining': respawns_remaining
    })


@app.route('/api/player/validate-key', methods=['POST'])
@rate_limit('validate_key', by='ip')
def validate_continue_key():
    """Validate a continue key and return player progress.

    Uses database for secure key validation if available, falls back to JSON.
    Rate limited to 10 attempts per 15 min per IP.
    """
    data = request.get_json() or {}
    name = str(data.get('name', '')).strip()[:12].lower() if data.get('name') else None
    key = str(data.get('key', '')).strip().upper()

    if not key:
        return jsonify({'error': 'Key required'}), 400

    ip_address = get_client_ip()

    # Try database first
    if USE_POSTGRES:
        try:
            result = database.validate_continue_key(key, ip_address)

            if result is None:
                # Key not found or expired or locked
                database.log_audit(
                    action='continue_key_failed',
                    ip_address=ip_address,
                    new_value={'key_prefix': key[:6] if len(key) >= 6 else key}
                )
                return jsonify({'valid': False, 'error': 'Invalid or expired key'}), 401

            if result.get('error'):
                return jsonify({'valid': False, 'error': result['error']}), 401

            # Log successful validation
            database.log_audit(
                action='continue_key_validated',
                player_id=result.get('player_id'),
                ip_address=ip_address,
                new_value={'level': result['level'], 'respawns_left': result['respawns_remaining']}
            )

            return jsonify({
                'valid': True,
                'name': result['username'],
                'level': result['level'],
                'score': result['score'],
                'difficulty': result['difficulty'],
                'respawnsRemaining': result['respawns_remaining'],
                'keyRespawnsLeft': result['respawns_remaining']
            })

        except Exception as e:
            print(f"Database error validating key: {e}")
            # Fall through to JSON fallback

    # Fallback to JSON storage
    progress = load_player_progress()

    # If name provided, look up that specific player
    # Otherwise search all players for the key
    player = None
    player_name = None
    valid_key = None

    if name and name in progress:
        player = progress[name]
        player_name = name
        for k in player['keys']:
            if k['key'] == key:
                valid_key = k
                break
    else:
        # Search all players for this key
        for pname, pdata in progress.items():
            for k in pdata.get('keys', []):
                if k['key'] == key:
                    player = pdata
                    player_name = pname
                    valid_key = k
                    break
            if valid_key:
                break

    if not valid_key or not player:
        return jsonify({'valid': False, 'error': 'Invalid key'}), 401

    # Check respawns remaining on key (default to 3 for old keys without this field)
    respawns_on_key = valid_key.get('respawnsRemaining', 3 if not valid_key.get('used') else 0)

    if respawns_on_key <= 0:
        return jsonify({'valid': False, 'error': 'Key exhausted (0 respawns left). Request a new key.'}), 401

    # Decrement respawns on this key
    valid_key['respawnsRemaining'] = respawns_on_key - 1
    valid_key['lastUsedAt'] = datetime.now().isoformat()

    # Mark as fully used when respawns hit 0
    if valid_key['respawnsRemaining'] <= 0:
        valid_key['used'] = True
        valid_key['usedAt'] = datetime.now().isoformat()

    # Reset respawns for the current level (gives 1 respawn per key use)
    player['respawnsUsed'][str(player['currentLevel'])] = 0

    player['history'].append({
        'action': 'key_validated',
        'key': key,
        'level': player['currentLevel'],
        'respawnsLeftOnKey': valid_key['respawnsRemaining'],
        'timestamp': datetime.now().isoformat()
    })

    save_player_progress(progress)

    return jsonify({
        'valid': True,
        'name': player_name,
        'level': player['currentLevel'],
        'score': player['currentScore'],
        'difficulty': player['difficulty'],
        'respawnsRemaining': valid_key['respawnsRemaining'],
        'keyRespawnsLeft': valid_key['respawnsRemaining']
    })


@app.route('/api/player/save-progress', methods=['POST'])
def save_progress():
    """Save player's current progress (called on respawn)."""
    data = request.get_json() or {}
    name = str(data.get('name', '')).strip()[:12].lower()
    level = int(data.get('level', 1))
    score = int(data.get('score', 0))
    difficulty = str(data.get('difficulty', 'EASY'))[:10].upper()
    respawnsUsed = int(data.get('respawnsUsed', 0))

    if not name:
        return jsonify({'error': 'Name required'}), 400

    progress = load_player_progress()

    if name not in progress:
        # Create new player entry
        progress[name] = {
            'email': None,
            'keys': [],
            'currentLevel': level,
            'currentScore': score,
            'difficulty': difficulty,
            'respawnsUsed': {},
            'totalRespawns': 0,
            'keyRequests': 0,
            'history': [],
            'createdAt': datetime.now().isoformat()
        }

    player = progress[name]
    player['currentLevel'] = level
    player['currentScore'] = score
    player['difficulty'] = difficulty
    player['respawnsUsed'][str(level)] = respawnsUsed
    player['totalRespawns'] += 1
    player['lastUpdate'] = datetime.now().isoformat()

    save_player_progress(progress)

    # Calculate remaining respawns
    level_respawns = player['respawnsUsed'].get(str(level), 0)
    remaining = max(0, FREE_RESPAWNS_PER_LEVEL - level_respawns)

    return jsonify({
        'success': True,
        'respawnsRemaining': remaining,
        'needsKey': remaining <= 0
    })


@app.route('/api/player/get-progress', methods=['POST'])
def get_progress():
    """Get player's saved progress."""
    data = request.get_json() or {}
    name = str(data.get('name', '')).strip()[:12].lower()

    if not name:
        return jsonify({'error': 'Name required'}), 400

    progress = load_player_progress()

    if name not in progress:
        return jsonify({
            'found': False,
            'respawnsUsed': {},
            'respawnsRemaining': FREE_RESPAWNS_PER_LEVEL
        })

    player = progress[name]

    return jsonify({
        'found': True,
        'level': player.get('currentLevel', 1),
        'score': player.get('currentScore', 0),
        'difficulty': player.get('difficulty', 'EASY'),
        'respawnsUsed': player.get('respawnsUsed', {}),
        'hasKeys': len(player.get('keys', [])) > 0
    })


# === MULTIPLAYER ROOM API ===

@app.route('/api/rooms/create', methods=['POST'])
def create_room():
    """Create a new multiplayer room."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')
    player_name = data.get('playerName', 'Player')
    mode = data.get('mode', 'coop')  # 'coop' or 'versus'
    difficulty = data.get('difficulty', 'MEDIUM')

    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    if mode not in ['coop', 'versus']:
        return jsonify({'error': 'Invalid mode'}), 400

    try:
        room_code = redis_client.create_room(player_id, player_name, mode, difficulty)
        room = redis_client.get_room(room_code)
        return jsonify({
            'success': True,
            'roomCode': room_code,
            'room': room
        })
    except Exception as e:
        print(f"Error creating room: {e}")
        return jsonify({'error': 'Failed to create room'}), 500


@app.route('/api/rooms/<code>', methods=['GET'])
def get_room(code):
    """Get room status."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    room = redis_client.get_room(code.upper())
    if not room:
        return jsonify({'error': 'Room not found'}), 404

    return jsonify(room)


@app.route('/api/rooms/join/<code>', methods=['POST'])
def join_room(code):
    """Join an existing room."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')
    player_name = data.get('playerName', 'Player')

    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    try:
        result = redis_client.join_room(code.upper(), player_id, player_name)
        if 'error' in result:
            return jsonify(result), 400
        return jsonify({'success': True, 'room': result})
    except Exception as e:
        print(f"Error joining room: {e}")
        return jsonify({'error': 'Failed to join room'}), 500


@app.route('/api/rooms/leave', methods=['POST'])
def leave_room():
    """Leave current room."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')

    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    room_code = redis_client.get_player_room(player_id)
    if room_code:
        redis_client.leave_room(room_code, player_id)

    return jsonify({'success': True})


@app.route('/api/rooms/<code>/ready', methods=['POST'])
def toggle_ready(code):
    """Toggle player ready status."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')
    ready = data.get('ready', True)

    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    room = redis_client.set_player_ready(code.upper(), player_id, ready)
    if not room:
        return jsonify({'error': 'Room not found'}), 404

    return jsonify({'success': True, 'room': room})


@app.route('/api/rooms/<code>/start', methods=['POST'])
def start_game(code):
    """Start the game (host only)."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')

    room = redis_client.get_room(code.upper())
    if not room:
        return jsonify({'error': 'Room not found'}), 404

    if room['host_id'] != player_id:
        return jsonify({'error': 'Only host can start'}), 403

    if not redis_client.start_room_game(code.upper()):
        return jsonify({'error': 'Cannot start: need 2 ready players'}), 400

    return jsonify({'success': True, 'room': redis_client.get_room(code.upper())})


# === MATCHMAKING API ===

@app.route('/api/matchmaking/join', methods=['POST'])
def join_matchmaking():
    """Join quick match queue."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')
    player_name = data.get('playerName', 'Player')
    mode = data.get('mode', 'coop')
    difficulty = data.get('difficulty', 'MEDIUM')

    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    if mode not in ['coop', 'versus']:
        return jsonify({'error': 'Invalid mode'}), 400

    # Check if already in queue
    existing_queue = redis_client.is_in_queue(player_id)
    if existing_queue:
        # Try to find match
        result = redis_client.find_match(player_id, mode, difficulty)
        return jsonify(result)

    # Join queue
    redis_client.join_matchmaking(player_id, player_name, mode, difficulty)

    # Immediately try to find a match
    result = redis_client.find_match(player_id, mode, difficulty)
    return jsonify(result)


@app.route('/api/matchmaking/leave', methods=['POST'])
def leave_matchmaking():
    """Leave matchmaking queue."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    player_id = data.get('playerId')

    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    redis_client.leave_matchmaking(player_id)
    return jsonify({'success': True})


@app.route('/api/matchmaking/status', methods=['GET'])
def matchmaking_status():
    """Check matchmaking queue status."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    player_id = request.args.get('playerId')
    if not player_id:
        return jsonify({'error': 'Missing playerId'}), 400

    mode = redis_client.is_in_queue(player_id)
    if not mode:
        # Not in queue - check if already matched to a room
        room_code = redis_client.get_player_room(player_id)
        if room_code:
            # Check if this player is the host
            room = redis_client.get_room(room_code)
            is_host = room and room.get('host_id') == player_id
            return jsonify({
                'matched': True,
                'room_code': room_code,
                'inQueue': False,
                'isHost': is_host
            })
        return jsonify({'inQueue': False})

    # Try to find match
    result = redis_client.find_match(player_id, mode, 'MEDIUM')
    result['inQueue'] = True
    return jsonify(result)


# === MULTIPLAYER GAME STATE API ===

@app.route('/api/multiplayer/state', methods=['POST'])
def update_multiplayer_state():
    """Update multiplayer game state."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    room_code = data.get('roomCode')
    player_id = data.get('playerId')
    state = data.get('state')

    if not room_code or not state:
        return jsonify({'error': 'Missing roomCode or state'}), 400

    redis_client.set_multiplayer_state(room_code, state)
    return jsonify({'success': True})


@app.route('/api/multiplayer/state/<room_code>', methods=['GET'])
def get_multiplayer_state(room_code):
    """Get multiplayer game state."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    state = redis_client.get_multiplayer_state(room_code.upper())
    room = redis_client.get_room(room_code.upper())

    return jsonify({
        'room': room,
        'state': state
    })


@app.route('/api/multiplayer/end', methods=['POST'])
def end_multiplayer_game():
    """End a multiplayer game."""
    if not USE_REDIS:
        return jsonify({'error': 'Multiplayer not available'}), 503

    data = request.get_json() or {}
    room_code = data.get('roomCode')
    winner_id = data.get('winnerId')

    if not room_code:
        return jsonify({'error': 'Missing roomCode'}), 400

    redis_client.end_room_game(room_code.upper(), winner_id)
    redis_client.delete_multiplayer_state(room_code.upper())

    return jsonify({'success': True})


# Initialize scheduler when running with gunicorn
backup_scheduler = None


if __name__ == '__main__':
    # Create empty leaderboard file if it doesn't exist
    if not LEADERBOARD_FILE.exists():
        save_leaderboard_fallback([])

    print(f"Leaderboard file: {LEADERBOARD_FILE}")
    print(f"Redis enabled: {USE_REDIS}")
    print(f"PostgreSQL enabled: {USE_POSTGRES}")
    print(f"WebSocket enabled: {USE_WEBSOCKET}")

    # Start backup scheduler
    backup_scheduler = init_backup_scheduler()

    print("Starting server on http://0.0.0.0:8080")

    # Use SocketIO if available for WebSocket support
    if USE_WEBSOCKET and socketio:
        socketio.run(app, host='0.0.0.0', port=8080, debug=True)
    else:
        app.run(host='0.0.0.0', port=8080, debug=True, threaded=True)
else:
    # Running under gunicorn/eventlet - start scheduler
    backup_scheduler = init_backup_scheduler()
