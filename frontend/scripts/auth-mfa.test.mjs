import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const app = await readFile(new URL('../src/App.jsx', import.meta.url), 'utf8');
const api = await readFile(new URL('../src/lib/api.js', import.meta.url), 'utf8');

test('staff login switches to an inline MFA screen without storing an access token', () => {
  assert.match(app, /if\(r\.mfa_required\)\{sessionStorage\.removeItem\('sk_access'\)/);
  assert.match(app, /setAuthMode\('mfa'\)/);
  assert.match(app, /authMode==='mfa'\?/);
  assert.match(app, /6-digit authenticator code/);
  assert.ok(app.includes("disabled={busy||!/^\\d{6}$/.test(form.mfaCode||'')}"));
});

test('MFA verification submits the backend code payload and stores the token only after success', () => {
  assert.match(app, /api\.action\('\/auth\/mfa\/verify',\{code\}\)/);
  assert.match(app, /if\(!r\.access_token\)throw new Error/);
  assert.match(app, /sessionStorage\.setItem\('sk_access',r\.access_token\)/);
  assert.match(app, /MFA_CHALLENGE_EXPIRED/);
  assert.match(app, /MFA_INVALID/);
});

test('MFA challenge request uses cookie credentials and mock rejects invalid codes', () => {
  assert.match(api, /credentials:'include'/);
  assert.match(api, /state\.pending_mfa/);
  assert.match(api, /MFA_INVALID/);
});
