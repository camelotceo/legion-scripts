-- Fighter Jet Game Database Schema

-- Players (permanent profiles)
CREATE TABLE IF NOT EXISTS players (
    id SERIAL PRIMARY KEY,
    name VARCHAR(12) UNIQUE NOT NULL,
    first_seen TIMESTAMP DEFAULT NOW(),
    total_games INTEGER DEFAULT 0,
    total_score BIGINT DEFAULT 0,
    total_playtime INTEGER DEFAULT 0,
    best_score INTEGER DEFAULT 0,
    best_level INTEGER DEFAULT 0,
    best_duration INTEGER DEFAULT NULL
);

-- Game Sessions (every game played)
CREATE TABLE IF NOT EXISTS game_sessions (
    id SERIAL PRIMARY KEY,
    player_id INTEGER REFERENCES players(id),
    started_at TIMESTAMP DEFAULT NOW(),
    ended_at TIMESTAMP,
    duration INTEGER,
    score INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    difficulty VARCHAR(10),
    death_reason VARCHAR(50),
    bosses_defeated INTEGER DEFAULT 0
);

-- Game Events (detailed action log)
CREATE TABLE IF NOT EXISTS game_events (
    id SERIAL PRIMARY KEY,
    session_id INTEGER REFERENCES game_sessions(id),
    created_at TIMESTAMP DEFAULT NOW(),
    event_type VARCHAR(30),
    emoji VARCHAR(10),
    details JSONB
);

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_sessions_player ON game_sessions(player_id);
CREATE INDEX IF NOT EXISTS idx_sessions_score ON game_sessions(score DESC);
CREATE INDEX IF NOT EXISTS idx_events_session ON game_events(session_id);
CREATE INDEX IF NOT EXISTS idx_players_name ON players(name);

-- Leaderboard view (top scores with fastest times)
CREATE OR REPLACE VIEW leaderboard_view AS
SELECT
    p.name,
    gs.score,
    gs.duration,
    gs.level,
    gs.difficulty,
    gs.bosses_defeated,
    gs.death_reason,
    gs.ended_at as date
FROM game_sessions gs
JOIN players p ON gs.player_id = p.id
WHERE gs.score > 0 AND gs.ended_at IS NOT NULL
ORDER BY gs.score DESC, gs.duration ASC
LIMIT 100;
