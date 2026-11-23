
-- SupplyKey email verification records

CREATE TABLE IF NOT EXISTS email_verifications (
    id varchar(36) PRIMARY KEY,

    user_id varchar(36) NOT NULL UNIQUE
        REFERENCES users(id) ON DELETE CASCADE,

    otp_hash char(64) NOT NULL,

    expires_at timestamptz NOT NULL,

    attempts integer NOT NULL DEFAULT 0
        CHECK (attempts >= 0),

    resend_count integer NOT NULL DEFAULT 0
        CHECK (resend_count >= 0),

    last_sent_at timestamptz NOT NULL,

    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version)
VALUES ('004_email_verifications')
ON CONFLICT (version) DO NOTHING;
