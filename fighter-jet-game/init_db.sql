-- ============================================================
-- FIGHTER JET GAME - COMPREHENSIVE DATABASE SCHEMA
-- ============================================================
-- This schema provides:
-- - Unique player identities with device fingerprinting
-- - Session-based authentication
-- - Server-side score validation via event tracking
-- - Secure continue key system
-- - Rate limiting
-- - Full audit trail

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================
-- PLAYERS TABLE - Core identity with authentication
-- ============================================================
CREATE TABLE IF NOT EXISTS players (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Identity
    username VARCHAR(12) UNIQUE NOT NULL,
    display_name VARCHAR(12) NOT NULL,

    -- Authentication (nullable for anonymous players)
    email VARCHAR(255) UNIQUE,
    email_verified BOOLEAN DEFAULT FALSE,
    email_verification_token VARCHAR(64),
    email_verification_expires TIMESTAMP WITH TIME ZONE,
    password_hash VARCHAR(255),  -- bcrypt hash, nullable for email-only auth

    -- Device fingerprinting (for anonymous play)
    device_fingerprint VARCHAR(64),

    -- Stats
    total_games INTEGER DEFAULT 0,
    total_score BIGINT DEFAULT 0,
    total_playtime INTEGER DEFAULT 0,  -- seconds
    best_score INTEGER DEFAULT 0,
    best_level INTEGER DEFAULT 0,
    best_duration INTEGER,
    victories INTEGER DEFAULT 0,

    -- Tracking
    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_ip INET,

    -- Status
    is_banned BOOLEAN DEFAULT FALSE,
    ban_reason VARCHAR(255),
    ban_expires TIMESTAMP WITH TIME ZONE,

    -- Constraints
    CONSTRAINT valid_username CHECK (username ~ '^[a-zA-Z0-9_]{1,12}$'),
    CONSTRAINT valid_email CHECK (email IS NULL OR email ~* '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}$')
);

CREATE INDEX IF NOT EXISTS idx_players_username ON players(username);
CREATE INDEX IF NOT EXISTS idx_players_email ON players(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_players_device ON players(device_fingerprint) WHERE device_fingerprint IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_players_last_seen ON players(last_seen);

-- ============================================================
-- PLAYER_SESSIONS TABLE - Authentication sessions
-- ============================================================
CREATE TABLE IF NOT EXISTS player_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,

    -- Session token (stored hashed)
    token_hash VARCHAR(64) NOT NULL,

    -- Session metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_used TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Device/Network tracking
    ip_address INET NOT NULL,
    user_agent VARCHAR(512),
    device_fingerprint VARCHAR(64),

    -- Session state
    is_active BOOLEAN DEFAULT TRUE,
    revoked_at TIMESTAMP WITH TIME ZONE,
    revoke_reason VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_sessions_token ON player_sessions(token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_player ON player_sessions(player_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON player_sessions(expires_at) WHERE is_active = TRUE;

-- ============================================================
-- EMAIL_LOGIN_TOKENS TABLE - One-time login links
-- ============================================================
CREATE TABLE IF NOT EXISTS email_login_tokens (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,

    -- Token (stored hashed for security)
    token_hash VARCHAR(64) NOT NULL,

    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    used_at TIMESTAMP WITH TIME ZONE,

    -- Security
    created_from_ip INET,
    used_from_ip INET,

    -- Status
    is_used BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_email_login_token ON email_login_tokens(token_hash) WHERE is_used = FALSE;
CREATE INDEX IF NOT EXISTS idx_email_login_player ON email_login_tokens(player_id);

-- ============================================================
-- GAME_SESSIONS TABLE - Every game played
-- ============================================================
CREATE TABLE IF NOT EXISTS game_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    player_session_id UUID REFERENCES player_sessions(id),

    -- Game configuration
    difficulty VARCHAR(10) NOT NULL CHECK (difficulty IN ('EASY', 'MEDIUM', 'HARD', 'EXPERT')),
    game_mode VARCHAR(20) DEFAULT 'single' CHECK (game_mode IN ('single', 'coop', 'versus')),
    room_code VARCHAR(6),  -- For multiplayer

    -- Timing
    started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    ended_at TIMESTAMP WITH TIME ZONE,
    duration INTEGER,  -- seconds

    -- Results
    final_score INTEGER DEFAULT 0,
    final_level INTEGER DEFAULT 1,
    bosses_defeated INTEGER DEFAULT 0,
    enemies_killed INTEGER DEFAULT 0,
    death_reason VARCHAR(50),
    is_victory BOOLEAN DEFAULT FALSE,

    -- Validation
    score_validated BOOLEAN DEFAULT FALSE,
    validation_score INTEGER,  -- Server-calculated score
    validation_discrepancy INTEGER,  -- Difference from claimed score

    -- Network tracking
    client_ip INET,

    -- Anti-cheat
    suspicious_flags JSONB DEFAULT '[]'::jsonb,
    is_flagged BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_game_sessions_player ON game_sessions(player_id);
CREATE INDEX IF NOT EXISTS idx_game_sessions_score ON game_sessions(final_score DESC) WHERE ended_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_game_sessions_started ON game_sessions(started_at);
CREATE INDEX IF NOT EXISTS idx_game_sessions_difficulty ON game_sessions(difficulty);

-- ============================================================
-- GAME_EVENTS TABLE - Detailed action log for score validation
-- ============================================================
CREATE TABLE IF NOT EXISTS game_events (
    id BIGSERIAL PRIMARY KEY,
    game_session_id UUID NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,

    -- Event details
    event_type VARCHAR(30) NOT NULL,
    event_subtype VARCHAR(30),

    -- Timing
    game_timestamp INTEGER NOT NULL,  -- Milliseconds since game start
    server_timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Scoring
    score_delta INTEGER DEFAULT 0,  -- Points gained/lost from this event
    running_score INTEGER,  -- Score at this moment

    -- Game state
    level INTEGER,
    position_x INTEGER,
    position_y INTEGER,

    -- Extra data
    details JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_game_events_session ON game_events(game_session_id);
CREATE INDEX IF NOT EXISTS idx_game_events_type ON game_events(event_type);
CREATE INDEX IF NOT EXISTS idx_game_events_timestamp ON game_events(game_session_id, game_timestamp);

-- ============================================================
-- CONTINUE_KEYS TABLE - Respawn system with security
-- ============================================================
CREATE TABLE IF NOT EXISTS continue_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,

    -- Key details (stored hashed)
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    key_suffix CHAR(6) NOT NULL,  -- Last 6 chars for support reference

    -- Progression state at key creation
    saved_level INTEGER NOT NULL,
    saved_score INTEGER NOT NULL,
    saved_difficulty VARCHAR(10) NOT NULL,

    -- Usage limits
    total_respawns INTEGER DEFAULT 3,
    respawns_used INTEGER DEFAULT 0,

    -- Timing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT (NOW() + INTERVAL '7 days'),
    last_used_at TIMESTAMP WITH TIME ZONE,
    exhausted_at TIMESTAMP WITH TIME ZONE,

    -- Security
    created_from_ip INET,
    failed_attempts INTEGER DEFAULT 0,
    locked_until TIMESTAMP WITH TIME ZONE,

    CONSTRAINT respawns_positive CHECK (respawns_used <= total_respawns)
);

CREATE INDEX IF NOT EXISTS idx_continue_keys_player ON continue_keys(player_id);
CREATE INDEX IF NOT EXISTS idx_continue_keys_hash ON continue_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_continue_keys_expires ON continue_keys(expires_at) WHERE respawns_used < total_respawns;

-- ============================================================
-- CONTINUE_KEY_USES TABLE - Track each use of a continue key
-- ============================================================
CREATE TABLE IF NOT EXISTS continue_key_uses (
    id BIGSERIAL PRIMARY KEY,
    continue_key_id UUID NOT NULL REFERENCES continue_keys(id) ON DELETE CASCADE,
    game_session_id UUID REFERENCES game_sessions(id),

    used_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    used_from_ip INET,

    -- State at use time
    level_at_use INTEGER,
    score_at_use INTEGER
);

CREATE INDEX IF NOT EXISTS idx_key_uses_key ON continue_key_uses(continue_key_id);

-- ============================================================
-- LEADERBOARD_ENTRIES TABLE - Validated high scores
-- ============================================================
CREATE TABLE IF NOT EXISTS leaderboard_entries (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_session_id UUID UNIQUE NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,

    -- Score data
    score INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    level INTEGER NOT NULL,
    difficulty VARCHAR(10) NOT NULL,
    bosses_defeated INTEGER DEFAULT 0,

    -- Validation
    is_validated BOOLEAN DEFAULT FALSE,
    validated_at TIMESTAMP WITH TIME ZONE,
    validation_method VARCHAR(20),

    -- Timing
    achieved_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_leaderboard_difficulty ON leaderboard_entries(difficulty, score DESC, duration ASC);
CREATE INDEX IF NOT EXISTS idx_leaderboard_player ON leaderboard_entries(player_id);
CREATE INDEX IF NOT EXISTS idx_leaderboard_validated ON leaderboard_entries(is_validated, difficulty);

-- ============================================================
-- VICTORY_RECORDS TABLE - Players who beat the game
-- ============================================================
CREATE TABLE IF NOT EXISTS victory_records (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    game_session_id UUID UNIQUE NOT NULL REFERENCES game_sessions(id) ON DELETE CASCADE,

    -- Victory details
    final_score INTEGER NOT NULL,
    enemies_killed INTEGER NOT NULL,
    duration INTEGER NOT NULL,
    difficulty VARCHAR(10) NOT NULL,

    -- Contact (encrypted with pgcrypto)
    encrypted_email BYTEA,

    achieved_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_victories_player ON victory_records(player_id);
CREATE INDEX IF NOT EXISTS idx_victories_date ON victory_records(achieved_at);

-- ============================================================
-- MULTIPLAYER_ROOMS TABLE - Room history
-- ============================================================
CREATE TABLE IF NOT EXISTS multiplayer_rooms (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    room_code VARCHAR(6) NOT NULL,

    -- Configuration
    mode VARCHAR(10) NOT NULL CHECK (mode IN ('coop', 'versus')),
    difficulty VARCHAR(10) NOT NULL,

    -- Host
    host_player_id UUID REFERENCES players(id),

    -- Timing
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    ended_at TIMESTAMP WITH TIME ZONE,

    -- Results
    status VARCHAR(20) DEFAULT 'created' CHECK (status IN ('created', 'waiting', 'playing', 'finished', 'abandoned')),
    winner_player_id UUID REFERENCES players(id),
    final_score INTEGER
);

CREATE INDEX IF NOT EXISTS idx_rooms_code ON multiplayer_rooms(room_code);
CREATE INDEX IF NOT EXISTS idx_rooms_host ON multiplayer_rooms(host_player_id);
CREATE INDEX IF NOT EXISTS idx_rooms_created ON multiplayer_rooms(created_at);

-- ============================================================
-- MULTIPLAYER_PARTICIPANTS TABLE - Players in rooms
-- ============================================================
CREATE TABLE IF NOT EXISTS multiplayer_participants (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    room_id UUID NOT NULL REFERENCES multiplayer_rooms(id) ON DELETE CASCADE,
    player_id UUID NOT NULL REFERENCES players(id) ON DELETE CASCADE,

    slot_number INTEGER NOT NULL CHECK (slot_number IN (1, 2)),

    joined_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    left_at TIMESTAMP WITH TIME ZONE,

    -- Performance in this room
    final_score INTEGER,
    final_lives INTEGER,
    respawns_used INTEGER DEFAULT 0,

    UNIQUE(room_id, slot_number),
    UNIQUE(room_id, player_id)
);

CREATE INDEX IF NOT EXISTS idx_participants_room ON multiplayer_participants(room_id);
CREATE INDEX IF NOT EXISTS idx_participants_player ON multiplayer_participants(player_id);

-- ============================================================
-- RATE_LIMITS TABLE - Rate limiting tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS rate_limits (
    id BIGSERIAL PRIMARY KEY,

    -- Identifier (IP or player_id or fingerprint)
    identifier_type VARCHAR(20) NOT NULL CHECK (identifier_type IN ('ip', 'player', 'fingerprint', 'session')),
    identifier_value VARCHAR(100) NOT NULL,

    -- Action being rate limited
    action_type VARCHAR(50) NOT NULL,

    -- Counts
    request_count INTEGER DEFAULT 1,
    window_start TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(identifier_type, identifier_value, action_type)
);

CREATE INDEX IF NOT EXISTS idx_rate_limits_lookup ON rate_limits(identifier_type, identifier_value, action_type);
CREATE INDEX IF NOT EXISTS idx_rate_limits_window ON rate_limits(window_start);

-- ============================================================
-- AUDIT_LOG TABLE - Security audit trail
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,

    -- Who
    player_id UUID REFERENCES players(id),
    session_id UUID REFERENCES player_sessions(id),
    ip_address INET,

    -- What
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(30),
    resource_id VARCHAR(100),

    -- Details
    old_value JSONB,
    new_value JSONB,
    metadata JSONB DEFAULT '{}'::jsonb,

    -- When
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Outcome
    success BOOLEAN DEFAULT TRUE,
    error_message VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_audit_player ON audit_log(player_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id);

-- ============================================================
-- IP_ADDRESSES TABLE - IP reputation tracking
-- ============================================================
CREATE TABLE IF NOT EXISTS ip_addresses (
    ip INET PRIMARY KEY,

    -- Reputation
    trust_score INTEGER DEFAULT 100 CHECK (trust_score BETWEEN 0 AND 100),
    failed_auth_attempts INTEGER DEFAULT 0,
    last_failed_auth TIMESTAMP WITH TIME ZONE,

    -- Bans
    is_banned BOOLEAN DEFAULT FALSE,
    ban_expires TIMESTAMP WITH TIME ZONE,
    ban_reason VARCHAR(255),

    -- Stats
    total_players INTEGER DEFAULT 0,
    total_games INTEGER DEFAULT 0,

    first_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ip_banned ON ip_addresses(ip) WHERE is_banned = TRUE;
CREATE INDEX IF NOT EXISTS idx_ip_trust ON ip_addresses(trust_score);

-- ============================================================
-- VIEWS
-- ============================================================

-- Leaderboard view per difficulty (top 10 each)
CREATE OR REPLACE VIEW leaderboard_view AS
SELECT
    p.username,
    p.display_name,
    le.score,
    le.duration,
    le.level,
    le.difficulty,
    le.bosses_defeated,
    le.achieved_at as date,
    le.is_validated,
    ROW_NUMBER() OVER (
        PARTITION BY le.difficulty
        ORDER BY le.score DESC, le.duration ASC
    ) as rank
FROM leaderboard_entries le
JOIN players p ON le.player_id = p.id
WHERE le.is_validated = TRUE
  AND p.is_banned = FALSE;

-- Player stats summary
CREATE OR REPLACE VIEW player_stats_view AS
SELECT
    p.id,
    p.username,
    p.display_name,
    p.total_games,
    p.total_score,
    p.total_playtime,
    p.best_score,
    p.best_level,
    p.victories,
    p.first_seen,
    p.last_seen,
    COUNT(DISTINCT gs.id) as recent_games_30d,
    COALESCE(SUM(gs.final_score) FILTER (WHERE gs.started_at > NOW() - INTERVAL '30 days'), 0) as score_30d
FROM players p
LEFT JOIN game_sessions gs ON p.id = gs.player_id
GROUP BY p.id;

-- ============================================================
-- FUNCTIONS
-- ============================================================

-- Function to clean up expired sessions
CREATE OR REPLACE FUNCTION cleanup_expired_sessions() RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    UPDATE player_sessions
    SET is_active = FALSE, revoked_at = NOW(), revoke_reason = 'expired'
    WHERE expires_at < NOW() AND is_active = TRUE;

    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function for rate limiting check
CREATE OR REPLACE FUNCTION check_rate_limit(
    p_identifier_type VARCHAR(20),
    p_identifier_value VARCHAR(100),
    p_action VARCHAR(50),
    p_max_requests INTEGER,
    p_window_seconds INTEGER
) RETURNS BOOLEAN AS $$
DECLARE
    v_count INTEGER;
    v_window_start TIMESTAMP WITH TIME ZONE;
BEGIN
    v_window_start := NOW() - (p_window_seconds || ' seconds')::INTERVAL;

    -- Clean old entries and get/increment count
    DELETE FROM rate_limits
    WHERE identifier_type = p_identifier_type
      AND identifier_value = p_identifier_value
      AND action_type = p_action
      AND window_start < v_window_start;

    -- Upsert and get count
    INSERT INTO rate_limits (identifier_type, identifier_value, action_type, request_count, window_start)
    VALUES (p_identifier_type, p_identifier_value, p_action, 1, NOW())
    ON CONFLICT (identifier_type, identifier_value, action_type)
    DO UPDATE SET request_count = rate_limits.request_count + 1
    RETURNING request_count INTO v_count;

    RETURN v_count <= p_max_requests;
END;
$$ LANGUAGE plpgsql;

-- Function to calculate score from game events
CREATE OR REPLACE FUNCTION calculate_game_score(p_game_session_id UUID) RETURNS INTEGER AS $$
DECLARE
    v_total INTEGER;
BEGIN
    SELECT COALESCE(SUM(score_delta), 0)
    INTO v_total
    FROM game_events
    WHERE game_session_id = p_game_session_id;

    RETURN v_total;
END;
$$ LANGUAGE plpgsql;

-- Function to validate and insert leaderboard entry
CREATE OR REPLACE FUNCTION insert_validated_score(
    p_game_session_id UUID,
    p_validation_method VARCHAR(20) DEFAULT 'server_calculated'
) RETURNS UUID AS $$
DECLARE
    v_entry_id UUID;
    v_player_id UUID;
    v_score INTEGER;
    v_duration INTEGER;
    v_level INTEGER;
    v_difficulty VARCHAR(10);
    v_bosses INTEGER;
BEGIN
    -- Get game session data
    SELECT player_id, final_score, duration, final_level, difficulty, bosses_defeated
    INTO v_player_id, v_score, v_duration, v_level, v_difficulty, v_bosses
    FROM game_sessions
    WHERE id = p_game_session_id AND score_validated = TRUE;

    IF v_player_id IS NULL THEN
        RAISE EXCEPTION 'Game session not found or not validated';
    END IF;

    -- Insert leaderboard entry
    INSERT INTO leaderboard_entries (
        player_id, game_session_id, score, duration, level,
        difficulty, bosses_defeated, is_validated, validated_at, validation_method
    ) VALUES (
        v_player_id, p_game_session_id, v_score, v_duration, v_level,
        v_difficulty, v_bosses, TRUE, NOW(), p_validation_method
    )
    RETURNING id INTO v_entry_id;

    -- Update player best scores
    UPDATE players SET
        best_score = GREATEST(best_score, v_score),
        best_level = GREATEST(best_level, v_level),
        last_seen = NOW()
    WHERE id = v_player_id;

    RETURN v_entry_id;
END;
$$ LANGUAGE plpgsql;

-- Function to clean up old rate limit entries (run periodically)
CREATE OR REPLACE FUNCTION cleanup_rate_limits() RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM rate_limits WHERE window_start < NOW() - INTERVAL '1 hour';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Function to clean up old audit log entries (keep 30 days)
CREATE OR REPLACE FUNCTION cleanup_audit_log() RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER;
BEGIN
    DELETE FROM audit_log WHERE created_at < NOW() - INTERVAL '30 days';
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;
