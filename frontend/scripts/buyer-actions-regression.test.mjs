import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../src/App.jsx', import.meta.url), 'utf8');

test('receipt confirmation builds item choices from dispatched transaction lots', () => {
  assert.match(source, /const eligibleIds=\[\.\.\.new Set\(dispatched\.map\(l=>l\.item_id\)\.filter\(Boolean\)\)\]/);
  assert.match(source, /item_id:itemId,quantity:Number\(quantity\)/);
  assert.match(source, /There are no dispatched quantities awaiting receipt confirmation yet/);
});

test('buyer delivery photographs are uploaded, verified, then submitted automatically', () => {
  assert.match(source, /api\.upload\(file,'DELIVERY_PHOTO',txId\)/);
  assert.match(source, /\/transactions\/\$\{txId\}\/delivery\/evidence/);
  assert.match(source, /Photographs submitted successfully/);
});

test('buyer acceptance and rejection actions use a valid item ID and show API errors', () => {
  assert.match(source, /item_id:itemId,quantity_submitted/);
  assert.match(source, /submitError&&<div className="alert error"/);
  assert.match(source, /No received quantities are available for review/);
});

test('buyer receipt choices never invent a label from an item ID and detail refreshes while open', () => {
  assert.match(source, /const eligibleItems=eligibleIds\.map\(id=>items\.find\(i=>i\.id===id\)\)\.filter\(i=>i&&String\(i\.description\|\|i\.name\|\|'\'\)\.trim\(\)\)/);
  assert.doesNotMatch(source, /Item \$\{i\.id\}/);
  assert.match(source, /setInterval\(sync,8000\)/);
  assert.match(source, /api\.transaction\(selected\.id\)/);
});
