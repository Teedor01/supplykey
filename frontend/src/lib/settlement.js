/** Role-scoped settlement rows. Backend response filtering remains authoritative. */
export function settlementRows(settlement, role) {
  if (!settlement) return [];
  const fields = role === 'BUYER'
    ? [
        ['Payment amount received', settlement.payment_amount_kobo],
        ['Agreed payment amount', settlement.expected_payment_amount_kobo],
        ['Supplier payout status', settlement.supplier_payout_status],
        ['Excess payment refund amount', settlement.excess_payment_refund_amount_kobo],
        ['Excess payment refund status', settlement.excess_payment_refund_status],
        ['Dispute refund amount', settlement.dispute_refund_amount_kobo],
        ['Dispute refund status', settlement.dispute_refund_status],
      ]
    : role === 'SUPPLIER'
      ? [
          ['Agreed transaction amount', settlement.agreed_amount_kobo],
          ['Your payout amount', settlement.supplier_payout_amount_kobo],
          ['Your payout status', settlement.supplier_payout_status],
          ['Dispute refund amount', settlement.dispute_refund_amount_kobo],
          ['Dispute refund status', settlement.dispute_refund_status],
          ['Return status', settlement.return_status],
        ]
      : ['MODERATOR', 'SENIOR_MODERATOR'].includes(role)
        ? [
            ['Agreed amount', settlement.agreed_amount_kobo],
            ['Original payment received', settlement.original_payment_amount_kobo],
            ['Expected payment amount', settlement.expected_payment_amount_kobo],
            ['Payment attempts', (settlement.payment_statuses || []).map(x => `${x.status} · ${x.amount_kobo} kobo · ${x.mode}`).join('; ') || 'NOT_REQUIRED'],
            ['Excess payment refund', settlement.excess_payment_refund_amount_kobo],
            ['Excess refund status', settlement.excess_payment_refund_status],
            ['Supplier entitlement', settlement.supplier_entitlement_kobo],
            ['Supplier payout status', settlement.supplier_payout_status],
            ['Dispute refund status', settlement.dispute_refund_status],
            ['Return status', settlement.return_status],
            ['Moderator decision status', settlement.moderator_decision_status],
            ['Refund instructions', (settlement.refunds || []).map(x => `${x.id} · ${x.refund_classification} · ${x.amount_kobo} kobo · ${x.status}`).join('; ') || 'NOT_REQUIRED'],
            ['Payout instructions', (settlement.payouts || []).map(x => `${x.id} · ${x.amount_kobo} kobo · ${x.status}`).join('; ') || 'NOT_REQUIRED'],
          ]
        : [];
  return fields.filter(([, value]) => value !== undefined && value !== null)
    .map(([label, value]) => ({ label, value }));
}
