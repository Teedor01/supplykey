-- Bind each approved transfer to the exact encrypted beneficiary destination.
ALTER TABLE financial_instructions ADD COLUMN IF NOT EXISTS destination_snapshot jsonb;
-- Legacy queued Paga instructions have no immutable destination binding. Return
-- them to senior review rather than allowing the current profile to redirect them.
UPDATE financial_instructions fi SET status='PENDING_APPROVAL'
WHERE fi.status='QUEUED' AND fi.destination_snapshot IS NULL
  AND EXISTS (SELECT 1 FROM payments p WHERE p.transaction_id=fi.transaction_id AND p.mode LIKE 'PAGA%');
INSERT INTO schema_migrations(version) VALUES ('003_verified_beneficiary_binding') ON CONFLICT (version) DO NOTHING;
