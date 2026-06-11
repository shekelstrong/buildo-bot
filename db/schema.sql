-- Buildo database schema (Supabase PostgreSQL)
-- Run this AFTER creating a new Supabase project for Buildo.
-- Idempotent: safe to re-run.

-- =====================================================================
-- TABLES
-- =====================================================================

-- Users: Telegram + Web (Buildo users)
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    tg_user_id BIGINT UNIQUE,
    tg_username TEXT,
    tg_first_name TEXT,
    tg_last_name TEXT,
    web_user_id TEXT,
    email TEXT,
    kind TEXT NOT NULL DEFAULT 'site' CHECK (kind IN ('site', 'automate', 'both')),
    language TEXT DEFAULT 'ru',
    is_banned BOOLEAN DEFAULT FALSE,
    is_admin BOOLEAN DEFAULT FALSE,
    free_sites_used INTEGER DEFAULT 0,
    free_sites_limit INTEGER DEFAULT 1,
    referred_by_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    referral_code TEXT UNIQUE,
    referral_balance_rub NUMERIC(12, 2) DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_users_tg_user_id ON users(tg_user_id);
CREATE INDEX IF NOT EXISTS idx_users_web_user_id ON users(web_user_id);
CREATE INDEX IF NOT EXISTS idx_users_kind ON users(kind) WHERE NOT is_banned;
CREATE INDEX IF NOT EXISTS idx_users_referral_code ON users(referral_code) WHERE referral_code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_users_referred_by ON users(referred_by_user_id) WHERE referred_by_user_id IS NOT NULL;

-- Projects: a site being built (one row per generation)
CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_name TEXT NOT NULL,
    framework TEXT NOT NULL DEFAULT 'vite-react',
    prompt TEXT NOT NULL,
    files_count INTEGER NOT NULL DEFAULT 0,
    size_kb NUMERIC(10, 2) DEFAULT 0,
    preview_summary TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_created_at ON projects(created_at DESC);

-- Sites: a deployed site (one row per live URL)
CREATE TABLE IF NOT EXISTS sites (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    project_name TEXT NOT NULL,
    domain TEXT,
    deploy_target TEXT NOT NULL CHECK (deploy_target IN ('layero', 'beget', 'github', 'gitverse')),
    deploy_url TEXT NOT NULL,
    deploy_id TEXT,
    status TEXT DEFAULT 'deployed' CHECK (status IN ('building', 'deployed', 'failed', 'deleted')),
    last_deploy_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sites_user_id ON sites(user_id);
CREATE INDEX IF NOT EXISTS idx_sites_status ON sites(status);
CREATE INDEX IF NOT EXISTS idx_sites_created_at ON sites(created_at DESC);

-- Payments (all 3 providers in one table)
CREATE TABLE IF NOT EXISTS payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount NUMERIC(12, 2) NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('yookassa', 'cryptobot', 'telegram_stars')),
    external_id TEXT,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'succeeded', 'failed', 'refunded')),
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at DESC);

-- Articles (SEO blog) - Phase 1.5
CREATE TABLE IF NOT EXISTS articles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    content_markdown TEXT NOT NULL,
    content_html TEXT,
    author TEXT DEFAULT 'Buildo Team',
    tags TEXT[] DEFAULT '{}',
    keywords_hf TEXT[] DEFAULT '{}',
    keywords_mf TEXT[] DEFAULT '{}',
    keywords_lf TEXT[] DEFAULT '{}',
    word_count INTEGER DEFAULT 0,
    is_published BOOLEAN DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_articles_slug ON articles(slug);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(is_published, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_tags ON articles USING GIN(tags);

-- Automate clients (Phase 2)
CREATE TABLE IF NOT EXISTS automate_clients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_name TEXT NOT NULL,
    niche TEXT,
    plan TEXT DEFAULT 'starter' CHECK (plan IN ('starter', 'pro', 'enterprise')),
    monthly_fee_rub INTEGER DEFAULT 0,
    agents_active TEXT[] DEFAULT '{}',
    integrations_active TEXT[] DEFAULT '{}',
    status TEXT DEFAULT 'active' CHECK (status IN ('active', 'paused', 'cancelled')),
    started_at TIMESTAMPTZ DEFAULT now(),
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_automate_clients_user_id ON automate_clients(user_id);
CREATE INDEX IF NOT EXISTS idx_automate_clients_status ON automate_clients(status);

-- Audit log
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target TEXT,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_actor ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at DESC);

-- Referral events: who-referred-whom + commission payouts
-- Level 1 = direct referral, Level 2 = referral of referral, Level 3 = 3rd level
CREATE TABLE IF NOT EXISTS referral_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('signup', 'payment')),
    -- The user who triggered the event (joined or paid)
    source_user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- The user who earned the commission (level 1/2/3 referrer)
    referrer_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    level SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 3),
    -- For payment events: the payment that triggered commission
    payment_id UUID REFERENCES payments(id) ON DELETE SET NULL,
    -- Commission amount earned (for signup = 0, for payment = 30%/10%/5% of amount)
    commission_rub NUMERIC(12, 2) DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_referral_events_source ON referral_events(source_user_id);
CREATE INDEX IF NOT EXISTS idx_referral_events_referrer ON referral_events(referrer_user_id, level);
CREATE INDEX IF NOT EXISTS idx_referral_events_type ON referral_events(event_type, created_at DESC);

-- Referral commissions: payout ledger
-- When referrer balance reaches threshold, can be withdrawn (Phase 1.5)
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

-- =====================================================================
-- VIEWS
-- =====================================================================

CREATE OR REPLACE VIEW v_platform_stats AS
SELECT
    (SELECT COUNT(*) FROM users WHERE NOT is_banned) AS users_total,
    (SELECT COUNT(*) FROM users WHERE NOT is_banned AND created_at > now() - INTERVAL '24 hours') AS users_24h,
    (SELECT COUNT(*) FROM projects) AS projects_total,
    (SELECT COUNT(*) FROM sites WHERE status = 'deployed') AS sites_deployed,
    (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'succeeded' AND currency = 'RUB') AS revenue_rub,
    (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'succeeded' AND provider = 'telegram_stars') AS revenue_stars,
    (SELECT COALESCE(SUM(amount), 0) FROM payments WHERE status = 'succeeded' AND provider = 'cryptobot' AND currency LIKE 'USD%') AS revenue_crypto_usd;

CREATE OR REPLACE VIEW v_recent_users AS
SELECT id, tg_user_id, tg_username, tg_first_name, kind, is_admin, created_at
FROM users
WHERE NOT is_banned
ORDER BY created_at DESC
LIMIT 50;

CREATE OR REPLACE VIEW v_recent_payments AS
SELECT
    p.id, p.user_id, p.amount, p.currency, p.provider, p.status, p.created_at,
    u.tg_user_id, u.tg_username
FROM payments p
LEFT JOIN users u ON u.id = p.user_id
ORDER BY p.created_at DESC
LIMIT 50;

-- =====================================================================
-- FUNCTIONS & TRIGGERS
-- =====================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_users_updated ON users;
CREATE TRIGGER trg_users_updated BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_articles_updated ON articles;
CREATE TRIGGER trg_articles_updated BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Free tier enforcement
CREATE OR REPLACE FUNCTION check_free_tier()
RETURNS TRIGGER AS $$
DECLARE
    deployed_count INTEGER;
    free_limit INTEGER;
BEGIN
    SELECT free_sites_limit INTO free_limit FROM users WHERE id = NEW.user_id;
    SELECT COUNT(*) INTO deployed_count FROM sites
    WHERE user_id = NEW.user_id AND status = 'deployed';
    IF deployed_count >= free_limit AND free_limit > 0 THEN
        RAISE EXCEPTION 'Free tier limit reached (%) sites. Upgrade to deploy more.', free_limit;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sites_free_check ON sites;
CREATE TRIGGER trg_sites_free_check BEFORE INSERT ON sites
    FOR EACH ROW EXECUTE FUNCTION check_free_tier();

-- =====================================================================
-- SEED: admin user
-- =====================================================================

INSERT INTO users (tg_user_id, tg_username, tg_first_name, is_admin, kind)
VALUES (6318513424, 'shekelstrong', 'Admin', TRUE, 'both')
ON CONFLICT (tg_user_id) DO UPDATE SET is_admin = TRUE, kind = 'both';

COMMENT ON TABLE users IS 'Buildo users: TG + Web';
COMMENT ON TABLE projects IS 'LLM-generated site projects';
COMMENT ON TABLE sites IS 'Deployed sites (live URLs)';
COMMENT ON TABLE payments IS 'All payment providers';
COMMENT ON TABLE articles IS 'SEO/GEO/AEO blog posts';
COMMENT ON TABLE automate_clients IS 'Buildo Automate clients (Phase 2)';
COMMENT ON TABLE audit_log IS 'Admin actions audit trail';
