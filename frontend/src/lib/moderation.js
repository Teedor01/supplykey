function isoDate(value, label) {
  if (!value) throw new Error(`${label} is required.`);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error(`${label} must be a valid date and time.`);
  return date.toISOString();
}

/** Build the exact versioned API request for a moderator case action. */
export function buildModeratorActionRequest({ caseItem, detail, action, fields = {} }) {
  if (!caseItem?.id) throw new Error('A case ID is required before submitting a moderator action.');
  const isDispute = caseItem.status !== 'SUBMITTED_FOR_REVIEW';
  const version = detail?.version ?? detail?.decision?.version ?? detail?.dispute?.version ?? caseItem.version;
  const headers = version !== undefined && version !== null ? { 'If-Match': String(version) } : {};
  let path;
  let body;

  if (isDispute && action === 'authorize_refund') {
    path = `/mod/disputes/${caseItem.id}/authorize-refund`;
    body = {};
  } else if (isDispute && action === 'decision') {
    const outcome = fields.outcome || 'NO_FINANCIAL_ACTION';
    const affected = caseItem.affected_lots?.length ? caseItem.affected_lots : [{item_id:caseItem.item_id,lot_id:caseItem.lot_id,quantity:Number(fields.quantity || caseItem.quantity || 1)}];
    body = { outcomes: affected.map(lot => ({ item_id: lot.item_id, lot_id: lot.lot_id,
      quantity: Number(lot.quantity || fields.quantity || caseItem.quantity || 1), outcome,
      ...(outcome === 'REDISPATCH_OR_REPLACEMENT' ? {
        consent_statement_ids: detail?.consent_statement_ids || detail?.dispute?.consent_statement_ids || caseItem.consent_statement_ids,
        new_dispatch_deadline_at: isoDate(fields.newDispatchDeadlineAt, 'New dispatch deadline')
      } : {})
    })), reason: fields.reason, evidence_ids_considered: fields.evidenceIdsConsidered || [] };
    path = `/mod/disputes/${caseItem.id}/decision`;
  } else if (action === 'confirm' && !isDispute) {
    path = `/mod/decisions/${caseItem.id}/confirm`;
    body = { reason: fields.reason || 'Reviewed the evidence and confirmed the agreed acceptance conditions.',
      evidence_ids_considered: fields.evidenceIdsConsidered || [],
      ...(caseItem.supplier_photos === 'OVERDUE' ? {
        acknowledge_missing_supplier_photos: !!fields.acknowledgeMissingSupplierPhotos,
        acknowledgement_reason: fields.acknowledgementReason
      } : {}) };
  } else if (action === 'request') {
    path = isDispute ? `/mod/disputes/${caseItem.id}/request-evidence` : `/mod/decisions/${caseItem.id}/request-evidence`;
    body = { party: fields.party || 'BUYER', question: fields.question, due_at: isoDate(fields.dueAt, 'Evidence due date') };
  } else if (action === 'extend') {
    path = isDispute ? `/mod/disputes/${caseItem.id}/extend-window` : `/mod/decisions/${caseItem.id}/extend-window`;
    body = { reason: fields.reason, extra_hours: Number(fields.extraHours || 24) };
  } else if (action === 'note' && isDispute) {
    path = `/mod/disputes/${caseItem.id}/notes`;
    body = { note: fields.internalNote || fields.reason };
  } else if (action === 'assign' && isDispute) {
    path = `/mod/disputes/${caseItem.id}/assign`;
    body = { moderator_id: fields.assignee || undefined };
  } else if ((action === 'hold' || action === 'resume') && isDispute) {
    path = `/mod/disputes/${caseItem.id}/${action}`;
    body = { reason: fields.reason };
  } else if (action === 'dispute' && !isDispute) {
    path = `/mod/decisions/${caseItem.id}/open-dispute`;
    body = { reason_code: fields.reasonCode || 'OTHER', reason: fields.reason || 'Further review required.' };
  } else {
    throw new Error(`The ${action || 'selected'} action is not available for this case type.`);
  }
  return { path, body, headers };
}
