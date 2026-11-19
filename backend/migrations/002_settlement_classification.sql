-- Separate excess-payment refunds from rejection/dispute refunds and add an
-- explicit overall settlement-pending transaction state.
ALTER TABLE financial_instructions
  ADD COLUMN IF NOT EXISTS refund_classification varchar(32);
ALTER TABLE financial_instructions
  ADD COLUMN IF NOT EXISTS retry_of_id varchar(36);

UPDATE financial_instructions
SET refund_classification='EXCESS_PAYMENT'
WHERE instruction_type='REFUND'
  AND refund_classification IS NULL
  AND COALESCE(provider_response->>'origin_type','')='PAGA_OVERPAYMENT';

UPDATE financial_instructions
SET refund_classification='DISPUTE_RETURN'
WHERE instruction_type='REFUND' AND refund_classification IS NULL;

CREATE INDEX IF NOT EXISTS ix_financial_instructions_refund_classification
  ON financial_instructions(refund_classification);
CREATE UNIQUE INDEX IF NOT EXISTS ix_financial_instructions_retry_of_id
  ON financial_instructions(retry_of_id) WHERE retry_of_id IS NOT NULL;

ALTER TABLE financial_instructions
  DROP CONSTRAINT IF EXISTS financial_instructions_refund_classification_check;
ALTER TABLE financial_instructions
  ADD CONSTRAINT financial_instructions_refund_classification_check
  CHECK ((instruction_type <> 'REFUND' AND refund_classification IS NULL) OR (instruction_type = 'REFUND' AND refund_classification IN ('EXCESS_PAYMENT','DISPUTE_RETURN')));

ALTER TABLE transactions DROP CONSTRAINT IF EXISTS transactions_status_check;
ALTER TABLE transactions ADD CONSTRAINT transactions_status_check
  CHECK(status IN ('DRAFT','AWAITING_BUYER_AGREEMENT','AWAITING_PAYMENT','FUNDED','SETTLEMENT_PENDING','COMPLETED','CANCELLED','EXPIRED'));

INSERT INTO schema_migrations(version) VALUES ('002_settlement_classification') ON CONFLICT (version) DO NOTHING;
