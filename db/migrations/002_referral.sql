-- Migration 002: referral system
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_code TEXT UNIQUE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_balance_rub NUMERIC(12, 2) DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by_user_id) WHERE referred_by_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS referral_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('signup', 'payment')),
    source_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referrer_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    level SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 3),
    payment_id UUID REFERENCES payments(id) ON DELETE SET NULL,
    commission_rub NUMERIC(12, 2) DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_referral_events_source ON referral_events(source_user_id);
CREATE INDEX IF NOT EXISTS idx_referral_events_referrer ON referral_events(referrer_user_id, level);
CREATE INDEX IF NOT EXISTS idx_referral_events_type ON referral_events(event_type, created_at DESC);

CREATE TABLE IF NOT EXISTS referral_payouts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount_rub NUMERIC(12, 2) NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'cancelled')),
    payment_method TEXT,
    external_id TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_referral_payouts_user ON referral_payouts(user_id, created_at DESC);
