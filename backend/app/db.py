from . import config as _config  # Load backend/.env before creating the database engine.
import os
import json
from datetime import datetime, timezone
from sqlalchemy import create_engine, String, Integer, BigInteger, DateTime, JSON, Text, ForeignKey, UniqueConstraint, Index, CheckConstraint, text, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///./supplykey.db')
connect_args = {'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
class Base(DeclarativeBase): pass

def now(): return datetime.now(timezone.utc)
class User(Base):
    __tablename__='users'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    email: Mapped[str|None]=mapped_column(String(320), unique=True, index=True)
    phone: Mapped[str|None]=mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str]=mapped_column(String(256))
    display_name: Mapped[str]=mapped_column(String(160))
    role: Mapped[str]=mapped_column(String(32), default='USER')
    verified: Mapped[bool]=mapped_column(default=False)
    mfa_secret: Mapped[str|None]=mapped_column(String(64),nullable=True)
    profile: Mapped[dict]=mapped_column(JSON, default=dict)
    payout_account: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class SessionToken(Base):
    __tablename__='sessions'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str]=mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool]=mapped_column(default=False)
    mfa_pending: Mapped[bool]=mapped_column(default=False)
class Aggregate(Base):
    __tablename__='transactions'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    reference: Mapped[str]=mapped_column(String(32), unique=True, index=True)
    supplier_id: Mapped[str]=mapped_column(ForeignKey('users.id'))
    buyer_id: Mapped[str|None]=mapped_column(ForeignKey('users.id'), nullable=True, index=True)
    status: Mapped[str]=mapped_column(String(40), index=True)
    version: Mapped[int]=mapped_column(Integer, default=1)
    data: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now, onupdate=now)
class Payment(Base):
    __tablename__='payments'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id'), index=True)
    amount_kobo: Mapped[int]=mapped_column(BigInteger)
    status: Mapped[str]=mapped_column(String(32), index=True)
    provider_ref: Mapped[str]=mapped_column(String(80), unique=True)
    mode: Mapped[str]=mapped_column(String(20), default='SIMULATED')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class Idempotency(Base):
    __tablename__='idempotency_keys'
    __table_args__=(UniqueConstraint('user_id','endpoint','key',name='uq_idempotency_scope'),)
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    user_id: Mapped[str]=mapped_column(String(36), index=True)
    endpoint: Mapped[str]=mapped_column(String(255))
    key: Mapped[str]=mapped_column(String(128))
    request_hash: Mapped[str]=mapped_column(String(64))
    response: Mapped[dict]=mapped_column(JSON)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class Audit(Base):
    __tablename__='audit_log'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str|None]=mapped_column(String(36), nullable=True, index=True)
    action: Mapped[str]=mapped_column(String(100))
    entity_type: Mapped[str]=mapped_column(String(60))
    entity_id: Mapped[str]=mapped_column(String(36))
    details: Mapped[dict]=mapped_column(JSON, default=dict)
    previous_hash: Mapped[str|None]=mapped_column(String(64),nullable=True)
    record_hash: Mapped[str]=mapped_column(String(64),default='')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class Evidence(Base):
    __tablename__='evidence_files'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id'), index=True)
    uploader_id: Mapped[str]=mapped_column(ForeignKey('users.id'))
    purpose: Mapped[str]=mapped_column(String(40))
    filename: Mapped[str]=mapped_column(String(255))
    mime_type: Mapped[str]=mapped_column(String(100))
    size_bytes: Mapped[int]=mapped_column(BigInteger)
    sha256: Mapped[str]=mapped_column(String(64), index=True)
    status: Mapped[str]=mapped_column(String(24), default='PENDING')
    object_path: Mapped[str]=mapped_column(Text)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class Instruction(Base):
    __tablename__='financial_instructions'
    __table_args__=(CheckConstraint("(instruction_type <> 'REFUND' AND refund_classification IS NULL) OR (instruction_type = 'REFUND' AND refund_classification IN ('EXCESS_PAYMENT','DISPUTE_RETURN'))",name='financial_instructions_refund_classification_check'),)
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id'), index=True)
    instruction_type: Mapped[str]=mapped_column(String(12))
    refund_classification: Mapped[str|None]=mapped_column(String(32),nullable=True,index=True)
    retry_of_id: Mapped[str|None]=mapped_column(String(36),nullable=True,unique=True,index=True)
    amount_kobo: Mapped[int]=mapped_column(BigInteger)
    status: Mapped[str]=mapped_column(String(32), index=True)
    origin_id: Mapped[str]=mapped_column(String(36))
    deciding_moderator_id: Mapped[str|None]=mapped_column(String(36))
    idempotency_key: Mapped[str]=mapped_column(String(128), unique=True)
    provider_reference: Mapped[str|None]=mapped_column(String(50),nullable=True,index=True)
    provider_stage: Mapped[str|None]=mapped_column(String(32),nullable=True)
    provider_response: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    destination_snapshot: Mapped[dict|None]=mapped_column(JSON,nullable=True)
    resolved: Mapped[bool]=mapped_column(default=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class Dispute(Base):
    __tablename__='disputes'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id'), index=True)
    reason_code: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(40), default='EVIDENCE_WINDOW_OPEN')
    data: Mapped[dict]=mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class Notification(Base):
    __tablename__='notifications'
    id: Mapped[str]=mapped_column(String(36), primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'), index=True)
    type: Mapped[str]=mapped_column(String(80))
    entity_id: Mapped[str]=mapped_column(String(36))
    payload: Mapped[dict]=mapped_column(JSON, default=dict)
    status: Mapped[str]=mapped_column(String(16), default='QUEUED')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
Index('ix_tx_supplier_status','transactions.supplier_id','transactions.status')
Index('ix_tx_buyer_status','transactions.buyer_id','transactions.status')
def init_db():
    if DATABASE_URL.startswith('postgresql'):
        # PostgreSQL must use the checked-in SQL migration so immutable-ledger,
        # audit and deferred journal-balance triggers are never silently skipped.
        with engine.connect() as connection:
            try:
                applied=connection.execute(text("SELECT 1 FROM schema_migrations WHERE version='001_initial' LIMIT 1")).first()
            except Exception as exc:
                raise RuntimeError('PostgreSQL schema migration 001 is missing. Run `python -m app.migrate` before starting the API or worker.') from exc
            if not applied:
                raise RuntimeError('PostgreSQL schema migration 001 is not recorded. Run `python -m app.migrate` before starting the API or worker.')
        return
    if DATABASE_URL.startswith('sqlite'):
        Base.metadata.create_all(bind=engine)
        # Small additive SQLite migrations keep existing local development databases
        # usable when new provider/reconciliation columns are introduced. PostgreSQL
        # always uses the checked-in SQL migration above.
        inspector=inspect(engine)
        if inspector.has_table('financial_instructions'):
            existing={column['name'] for column in inspector.get_columns('financial_instructions')}
            additions={
                'provider_reference':'VARCHAR(50)',
                'provider_stage':'VARCHAR(32)',
                'provider_response':'JSON',
                'resolved':'BOOLEAN NOT NULL DEFAULT 0',
                'refund_classification':'VARCHAR(32)',
                'retry_of_id':'VARCHAR(36)',
                'destination_snapshot':'JSON',
            }
            with engine.begin() as connection:
                for name,definition in additions.items():
                    if name not in existing:
                        connection.exec_driver_sql(f'ALTER TABLE financial_instructions ADD COLUMN {name} {definition}')
                connection.exec_driver_sql("UPDATE financial_instructions SET status='PENDING_APPROVAL' WHERE status='QUEUED' AND destination_snapshot IS NULL AND transaction_id IN (SELECT transaction_id FROM payments WHERE mode LIKE 'PAGA%')")
                legacy=connection.exec_driver_sql("SELECT id, provider_response FROM financial_instructions WHERE instruction_type='REFUND' AND refund_classification IS NULL").fetchall()
                for instruction_id, provider_response in legacy:
                    try:
                        response=provider_response if isinstance(provider_response,dict) else json.loads(provider_response or '{}')
                    except (TypeError,ValueError):
                        response={}
                    classification='EXCESS_PAYMENT' if response.get('origin_type')=='PAGA_OVERPAYMENT' else 'DISPUTE_RETURN'
                    connection.exec_driver_sql('UPDATE financial_instructions SET refund_classification=? WHERE id=?',(classification,instruction_id))
                connection.exec_driver_sql('CREATE INDEX IF NOT EXISTS ix_financial_instructions_refund_classification ON financial_instructions(refund_classification)')
                connection.exec_driver_sql('CREATE UNIQUE INDEX IF NOT EXISTS ix_financial_instructions_retry_of_id ON financial_instructions(retry_of_id) WHERE retry_of_id IS NOT NULL')
        return
    raise RuntimeError('Unsupported DATABASE_URL. Use PostgreSQL or SQLite development mode.')
class Journal(Base):
    __tablename__='ledger_journals'
    __table_args__=(UniqueConstraint('entry_type','reference_id',name='uq_ledger_journal_origin'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id'),index=True)
    entry_type: Mapped[str]=mapped_column(String(48))
    reference_id: Mapped[str]=mapped_column(String(36),index=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class LedgerEntry(Base):
    __tablename__='ledger_entries'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    journal_id: Mapped[str]=mapped_column(ForeignKey('ledger_journals.id'))
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id'),index=True)
    account_code: Mapped[str]=mapped_column(String(48))
    direction: Mapped[str]=mapped_column(String(8))
    amount_kobo: Mapped[int]=mapped_column(BigInteger)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class ProviderEvent(Base):
    __tablename__='provider_events'
    __table_args__=(UniqueConstraint('provider_name','provider_event_id',name='uq_provider_event'),)
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    provider_name: Mapped[str]=mapped_column(String(48))
    provider_event_id: Mapped[str]=mapped_column(String(128))
    event_type: Mapped[str]=mapped_column(String(64))
    signature_valid: Mapped[bool]=mapped_column(default=False)
    raw_body: Mapped[dict]=mapped_column(JSON)
    received_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
class RefreshSession(Base):
    __tablename__='refresh_sessions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id',ondelete='CASCADE'),index=True)
    family_id: Mapped[str]=mapped_column(String(36),index=True)
    token_hash: Mapped[str]=mapped_column(String(64),unique=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool]=mapped_column(default=False)

class PagaSubsidiaryAccount(Base):
    __tablename__='paga_subsidiary_accounts'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    transaction_id: Mapped[str]=mapped_column(ForeignKey('transactions.id',ondelete='RESTRICT'),unique=True,index=True)
    payment_id: Mapped[str]=mapped_column(ForeignKey('payments.id',ondelete='RESTRICT'),unique=True,index=True)
    account_reference: Mapped[str]=mapped_column(String(30),unique=True)
    account_number: Mapped[str|None]=mapped_column(String(10),unique=True,nullable=True)
    provider_reference: Mapped[str]=mapped_column(String(50),unique=True)
    status: Mapped[str]=mapped_column(String(24),default='ACTIVE')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)



class ProviderEventProcessing(Base):
    __tablename__='provider_event_processing'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    provider_event_id: Mapped[str]=mapped_column(ForeignKey('provider_events.id',ondelete='RESTRICT'),unique=True,index=True)
    status: Mapped[str]=mapped_column(String(16),default='PENDING')
    processing_error: Mapped[str|None]=mapped_column(Text,nullable=True)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now,onupdate=now)
