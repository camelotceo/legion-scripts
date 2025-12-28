-- Migration: Add authentication and token system columns
-- Run this on production to add new columns to existing players table

-- Add 6-digit verification code columns (if not exists)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'email_verification_code') THEN
        ALTER TABLE players ADD COLUMN email_verification_code VARCHAR(6);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'verification_code_expires') THEN
        ALTER TABLE players ADD COLUMN verification_code_expires TIMESTAMP WITH TIME ZONE;
    END IF;
END $$;

-- Add token economy column
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'tokens') THEN
        ALTER TABLE players ADD COLUMN tokens INTEGER DEFAULT 100;
    END IF;
END $$;

-- Add game progress columns
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'continues_this_level') THEN
        ALTER TABLE players ADD COLUMN continues_this_level INTEGER DEFAULT 0;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'saved_level') THEN
        ALTER TABLE players ADD COLUMN saved_level INTEGER DEFAULT 1;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'saved_score') THEN
        ALTER TABLE players ADD COLUMN saved_score INTEGER DEFAULT 0;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'players' AND column_name = 'saved_difficulty') THEN
        ALTER TABLE players ADD COLUMN saved_difficulty VARCHAR(10) DEFAULT 'EASY';
    END IF;
END $$;

-- Give existing players their starting tokens (only for those who don't have any)
UPDATE players SET tokens = 100 WHERE tokens IS NULL;

-- Set defaults for progress columns
UPDATE players SET continues_this_level = 0 WHERE continues_this_level IS NULL;
UPDATE players SET saved_level = 1 WHERE saved_level IS NULL;
UPDATE players SET saved_score = 0 WHERE saved_score IS NULL;
UPDATE players SET saved_difficulty = 'EASY' WHERE saved_difficulty IS NULL;

-- Verify migration
SELECT 'Migration complete. Column check:' as status;
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'players'
AND column_name IN ('email_verification_code', 'verification_code_expires', 'tokens',
                    'continues_this_level', 'saved_level', 'saved_score', 'saved_difficulty');
