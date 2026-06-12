-- Migration 004: GitHub user-token + encryption support
-- Добавляет поля для подключения GitHub юзера к боту

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS github_token_encrypted TEXT,
    ADD COLUMN IF NOT EXISTS github_username TEXT,
    ADD COLUMN IF NOT EXISTS github_connected_at TIMESTAMPTZ;

COMMENT ON COLUMN users.github_token_encrypted IS 'Fernet-encrypted GitHub PAT (scope: repo)';
COMMENT ON COLUMN users.github_username IS 'GitHub username, validated at /admin → GitHub';

CREATE INDEX IF NOT EXISTS idx_users_github_username
    ON users(github_username) WHERE github_username IS NOT NULL;
