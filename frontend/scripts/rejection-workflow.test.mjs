import test from 'node:test';
import assert from 'node:assert/strict';
import { api } from '../src/lib/api.js';

const file = (name='evidence.jpg') => new File([new Uint8Array([1,2,3,4])], name, {type:'image/jpeg'});

test('local demo follows role-specific rejection, return receipt and single-moderator refund authorization without false settlement', async () => {
  api.reset();
  api.switchRole('BUYER');
  const txid='tx_b7c1';
  const buyerTx=await api.transaction(txid);
  const delivery=await api.upload(file('delivery.jpg'),'DELIVERY_PHOTO',txid);
  await api.action(`/transactions/${txid}/delivery/evidence`,{requirement_id:'dpr_2',evidence_ids:[delivery.id]});
  await api.action(`/transactions/${txid}/delivery/confirm-received`,{lines:[{item_id:'it_1',quantity:5}]});
  const rejectionProof=await api.upload(file('rejection.jpg'),'REJECTION_PROOF',txid);
  const submitted=await api.action(`/transactions/${txid}/decisions`,{lines:[{
    item_id:'it_1',quantity_submitted:1,quantity_accepted:0,quantity_rejected:1,
    reject:{reason_text:'The delivered phone is not factory sealed as required.',agreed_condition_ids:['c_1'],evidence_ids:[rejectionProof.id]}
  }]});
  const did=submitted.data.dispute.id;
  assert.equal(submitted.data.dispute.status,'EVIDENCE_WINDOW_OPEN');
  assert.equal((await api.transaction(txid)).status,'DISPUTED');
  api.switchRole('SUPPLIER');
  const supplierTx=await api.transaction(txid);
  assert.equal(supplierTx.dispute.id,did);
  assert.equal(supplierTx.allowed_actions.includes('confirm_return_receipt'),false); // not available before a return is handed over
  api.switchRole('MODERATOR');
  const queue=await api.moderatorQueue({});
  assert.ok(queue.items.some(x=>x.id===did));
  const caseDetail=await api.caseDetail(did);
  const lot=caseDetail.transaction.lots.find(x=>x.is_frozen);
  api.switchRole('BUYER');
  await api.action(`/disputes/${did}/waive-window`,{});
  api.switchRole('SUPPLIER');
  await api.action(`/disputes/${did}/waive-window`,{});
  api.switchRole('MODERATOR');
  const decision=await api.action(`/mod/disputes/${did}/decision`,{
    reason:'The evidence supports the buyer rejection and the goods must be returned before the refund.',
    evidence_ids_considered:[rejectionProof.id],
    outcomes:[{item_id:'it_1',lot_id:lot.id,quantity:1,outcome:'RETURN_AND_REFUND'}]
  });
  assert.equal(decision.return_required,true);
  api.switchRole('BUYER');
  const opened=await api.action(`/transactions/${txid}/returns`,{lines:[{lot_id:lot.id,item_id:'it_1',quantity:1}]});
  const rid=opened.data.id;
  const handover=await api.upload(file('handover.jpg'),'RETURN_HANDOVER',txid);
  await api.action(`/returns/${rid}/handover-evidence`,{evidence_ids:[handover.id],courier_name:'Demo Courier',rider_name:'Demo Rider',rider_phone:'+2348000000000'});
  api.switchRole('SUPPLIER');
  const receiptEvidence=await api.upload(file('receipt.jpg'),'RETURN_RECEIPT',txid);
  const receipt=await api.action(`/returns/${rid}/confirm-receipt`,{
    lines:[{lot_id:lot.id,item_id:'it_1',quantity_received:1}],evidence_ids:[receiptEvidence.id]
  });
  assert.equal(receipt.refund.status,'PENDING_APPROVAL');
  const refunds=await api.financialInstructions(txid,'refunds');
  assert.equal(refunds.length,1);
  assert.equal(refunds[0].status,'PENDING_APPROVAL');
  assert.notEqual((await api.transaction(txid)).status,'COMPLETED');
  api.switchRole('MODERATOR');
  const approval=await api.action(`/mod/disputes/${did}/authorize-refund`,{});
  assert.equal(approval.status,'QUEUED');
  assert.equal((await api.financialInstructions(txid,'refunds'))[0].status,'QUEUED');
  assert.notEqual((await api.transaction(txid)).status,'COMPLETED');
});
