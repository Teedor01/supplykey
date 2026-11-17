-- SupplyKey runtime schema, PostgreSQL 15+. IDs are UUID strings in the API; UUID v7 is recommended.
CREATE TABLE IF NOT EXISTS schema_migrations (version varchar(64) PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE EXTENSION IF NOT EXISTS citext;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE TABLE IF NOT EXISTS users (
 id varchar(36) PRIMARY KEY, email citext UNIQUE, phone varchar(32) UNIQUE,
 password_hash varchar(256) NOT NULL, display_name varchar(160) NOT NULL,
 role varchar(32) NOT NULL DEFAULT 'USER' CHECK(role IN ('USER','MODERATOR','SENIOR_MODERATOR','ADMIN')),
 verified boolean NOT NULL DEFAULT false, mfa_secret varchar(64), profile jsonb NOT NULL DEFAULT '{}'::jsonb,
 payout_account jsonb NOT NULL DEFAULT '{}'::jsonb, created_at timestamptz NOT NULL DEFAULT now(),
 CHECK(email IS NOT NULL OR phone IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS sessions (
 id varchar(36) PRIMARY KEY, user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 token_hash char(64) NOT NULL UNIQUE, expires_at timestamptz NOT NULL,
 revoked boolean NOT NULL DEFAULT false, mfa_pending boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_sessions_user_id ON sessions(user_id);
CREATE TABLE IF NOT EXISTS transactions (
 id varchar(36) PRIMARY KEY, reference varchar(32) NOT NULL UNIQUE,
 supplier_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 buyer_id varchar(36) REFERENCES users(id) ON DELETE RESTRICT,
 status varchar(40) NOT NULL CHECK(status IN ('DRAFT','AWAITING_BUYER_AGREEMENT','AWAITING_PAYMENT','FUNDED','COMPLETED','CANCELLED','EXPIRED')),
 version integer NOT NULL DEFAULT 1, data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK(buyer_id IS NULL OR buyer_id <> supplier_id)
);
CREATE INDEX IF NOT EXISTS ix_transactions_supplier_status ON transactions(supplier_id,status);
CREATE INDEX IF NOT EXISTS ix_transactions_buyer_status ON transactions(buyer_id,status);
CREATE TABLE IF NOT EXISTS payments (
 id varchar(36) PRIMARY KEY, transaction_id varchar(36) NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
 amount_kobo bigint NOT NULL CHECK(amount_kobo>0), status varchar(32) NOT NULL CHECK(status IN ('PENDING','PAYMENT_RECEIVED','FUNDS_SECURED','FAILED','EXPIRED','UNKNOWN')),
 provider_ref varchar(80) NOT NULL UNIQUE, mode varchar(20) NOT NULL DEFAULT 'SIMULATED', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_payments_transaction_id ON payments(transaction_id);
CREATE TABLE IF NOT EXISTS idempotency_keys (
 id varchar(36) PRIMARY KEY,user_id varchar(36) NOT NULL,endpoint varchar(255) NOT NULL,key varchar(128) NOT NULL,
 request_hash char(64) NOT NULL,response jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(user_id,endpoint,key)
);
CREATE TABLE IF NOT EXISTS audit_log (
 id varchar(36) PRIMARY KEY,actor_id varchar(36),action varchar(100) NOT NULL,entity_type varchar(60) NOT NULL,
 entity_id varchar(36) NOT NULL,details jsonb NOT NULL DEFAULT '{}'::jsonb,previous_hash char(64),record_hash char(64) NOT NULL DEFAULT '',created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_audit_actor ON audit_log(actor_id);
CREATE TABLE IF NOT EXISTS evidence_files (
 id varchar(36) PRIMARY KEY,transaction_id varchar(36) NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
 uploader_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,purpose varchar(40) NOT NULL,
 filename varchar(255) NOT NULL,mime_type varchar(100) NOT NULL,size_bytes bigint NOT NULL CHECK(size_bytes>0),
 sha256 char(64) NOT NULL,status varchar(24) NOT NULL DEFAULT 'PENDING',object_path text NOT NULL,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_evidence_sha256 ON evidence_files(sha256);
CREATE TABLE IF NOT EXISTS financial_instructions (
 id varchar(36) PRIMARY KEY,transaction_id varchar(36) NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
 instruction_type varchar(12) NOT NULL CHECK(instruction_type IN ('PAYOUT','REFUND')),amount_kobo bigint NOT NULL CHECK(amount_kobo>0),
 status varchar(32) NOT NULL CHECK(status IN ('PENDING_APPROVAL','QUEUED','SUBMITTED','SUCCEEDED','FAILED','UNKNOWN','CANCELLED')),
 origin_id varchar(36) NOT NULL,deciding_moderator_id varchar(36),idempotency_key varchar(128) NOT NULL UNIQUE,
 provider_reference varchar(50),provider_stage varchar(32),provider_response jsonb,resolved boolean NOT NULL DEFAULT false,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_instruction_status ON financial_instructions(status);
CREATE INDEX IF NOT EXISTS ix_instruction_provider_reference ON financial_instructions(provider_reference);
CREATE TABLE IF NOT EXISTS disputes (
 id varchar(36) PRIMARY KEY,transaction_id varchar(36) NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
 reason_code varchar(64) NOT NULL,status varchar(40) NOT NULL DEFAULT 'EVIDENCE_WINDOW_OPEN',data jsonb NOT NULL DEFAULT '{}'::jsonb,created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS notifications (
 id varchar(36) PRIMARY KEY,user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
 type varchar(80) NOT NULL,entity_id varchar(36) NOT NULL,payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 status varchar(16) NOT NULL DEFAULT 'QUEUED',created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ledger_journals (
 id varchar(36) PRIMARY KEY,transaction_id varchar(36) NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,
 entry_type varchar(48) NOT NULL,reference_id varchar(36) NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(entry_type,reference_id)
);
CREATE TABLE IF NOT EXISTS ledger_entries (
 id varchar(36) PRIMARY KEY,journal_id varchar(36) NOT NULL REFERENCES ledger_journals(id) ON DELETE RESTRICT,
 transaction_id varchar(36) NOT NULL REFERENCES transactions(id) ON DELETE RESTRICT,account_code varchar(48) NOT NULL,
 direction varchar(8) NOT NULL CHECK(direction IN ('DEBIT','CREDIT')),amount_kobo bigint NOT NULL CHECK(amount_kobo>0),created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ledger_tx ON ledger_entries(transaction_id);
CREATE TABLE IF NOT EXISTS provider_events (
 id varchar(36) PRIMARY KEY,provider_name varchar(48) NOT NULL,provider_event_id varchar(128) NOT NULL,
 event_type varchar(64) NOT NULL,signature_valid boolean NOT NULL,raw_body jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now(),UNIQUE(provider_name,provider_event_id)
);
CREATE TABLE IF NOT EXISTS paga_subsidiary_accounts (
 id varchar(36) PRIMARY KEY, transaction_id varchar(36) NOT NULL UNIQUE REFERENCES transactions(id) ON DELETE RESTRICT,
 payment_id varchar(36) NOT NULL UNIQUE REFERENCES payments(id) ON DELETE RESTRICT,
 account_reference varchar(30) NOT NULL UNIQUE, account_number varchar(10) UNIQUE,
 provider_reference varchar(50) NOT NULL UNIQUE, status varchar(24) NOT NULL DEFAULT 'ACTIVE', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS provider_event_processing (
 id varchar(36) PRIMARY KEY, provider_event_id varchar(36) NOT NULL UNIQUE REFERENCES provider_events(id) ON DELETE RESTRICT,
 status varchar(16) NOT NULL DEFAULT 'PENDING', processing_error text, updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_provider_event_processing_event ON provider_event_processing(provider_event_id);
CREATE INDEX IF NOT EXISTS ix_paga_accounts_transaction ON paga_subsidiary_accounts(transaction_id);
CREATE INDEX IF NOT EXISTS ix_paga_accounts_payment ON paga_subsidiary_accounts(payment_id);
CREATE OR REPLACE FUNCTION supplykey_reject_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'append-only financial/audit records cannot be updated or deleted'; END; $$;
DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries;
CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON ledger_entries FOR EACH ROW EXECUTE FUNCTION supplykey_reject_ledger_mutation();
DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;
CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION supplykey_reject_ledger_mutation();
DROP TRIGGER IF EXISTS provider_events_immutable ON provider_events;
CREATE TRIGGER provider_events_immutable BEFORE UPDATE OR DELETE ON provider_events FOR EACH ROW EXECUTE FUNCTION supplykey_reject_ledger_mutation();
CREATE TABLE IF NOT EXISTS refresh_sessions (
 id varchar(36) PRIMARY KEY,user_id varchar(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 family_id varchar(36) NOT NULL,token_hash char(64) NOT NULL UNIQUE,expires_at timestamptz NOT NULL,revoked boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS ix_refresh_user ON refresh_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_refresh_family ON refresh_sessions(family_id);
CREATE OR REPLACE FUNCTION supplykey_check_journal_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE deb bigint; cred bigint;
BEGIN
 SELECT COALESCE(sum(amount_kobo) FILTER (WHERE direction='DEBIT'),0),COALESCE(sum(amount_kobo) FILTER (WHERE direction='CREDIT'),0)
 INTO deb,cred FROM ledger_entries WHERE journal_id=NEW.journal_id;
 IF deb<>cred THEN RAISE EXCEPTION 'journal % is unbalanced: debit %, credit %',NEW.journal_id,deb,cred; END IF;
 RETURN NULL;
END; $$;
DROP TRIGGER IF EXISTS ledger_journal_balance ON ledger_entries;
CREATE CONSTRAINT TRIGGER ledger_journal_balance AFTER INSERT ON ledger_entries DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION supplykey_check_journal_balance();
DROP TRIGGER IF EXISTS ledger_journals_immutable ON ledger_journals;
CREATE TRIGGER ledger_journals_immutable BEFORE UPDATE OR DELETE ON ledger_journals FOR EACH ROW EXECUTE FUNCTION supplykey_reject_ledger_mutation();

INSERT INTO schema_migrations(version) VALUES ('001_initial') ON CONFLICT (version) DO NOTHING;
