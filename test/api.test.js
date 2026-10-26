const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { once } = require('node:events');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
let child, baseUrl, tempDir;
before(async () => {
  tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'importkit-test-'));
  const port = 20000 + Math.floor(Math.random() * 40000); baseUrl = `http://127.0.0.1:${port}`;
  child = spawn(process.execPath, ['server.js'], { cwd: path.resolve(__dirname, '..'), env: { ...process.env, PORT: String(port), DATABASE_PATH: path.join(tempDir, 'test.sqlite') }, stdio: 'ignore' });
  for (let i = 0; i < 100; i++) { if (child.exitCode !== null) throw new Error(`Server exited with code ${child.exitCode}`); try { if ((await fetch(`${baseUrl}/api/dashboard`)).ok) return; } catch {} await new Promise(resolve => setTimeout(resolve, 50)); }
  throw new Error('Test server did not become ready');
});
after(async () => { if (child && child.exitCode === null) { child.kill(); await Promise.race([once(child, 'exit'), new Promise(resolve => setTimeout(resolve, 2000))]); } if (tempDir) await fs.rm(tempDir, { recursive: true, force: true }); });
async function json(url, options) { const response = await fetch(baseUrl + url, { ...options, headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) } }); return { response, body: await response.json() }; }
test('maps custom CSV headers, commits only valid rows, and exports safe row errors', async () => {
  const csv = 'Full Name,E-mail,Organization\nAvery Stone,avery@northstar.test,Northstar\nTaylor Stone,invalid-address,=HYPERLINK';
  const preview = await json('/api/imports/preview', { method: 'POST', body: JSON.stringify({ filename: 'external-contacts.csv', csv, mapping: { name: 'Full Name', email: 'E-mail', company: 'Organization' } }) });
  assert.equal(preview.response.status, 201); assert.equal(preview.body.validRows, 1); assert.equal(preview.body.invalidRows, 1); assert.equal(preview.body.rows[0].data.company, 'Northstar');
  const details = await json(`/api/imports/${preview.body.id}`); assert.equal(details.body.mapping.email, 'e_mail');
  const committed = await json(`/api/imports/${preview.body.id}/commit`, { method: 'POST', body: '{}' }); assert.equal(committed.body.created, 1);
  const contacts = await json('/api/contacts'); assert.ok(contacts.body.some(contact => contact.name === 'Avery Stone')); assert.ok(!contacts.body.some(contact => contact.name === 'Taylor Stone'));
  const report = await fetch(`${baseUrl}/api/imports/${preview.body.id}/errors.csv`); assert.equal(report.status, 200); assert.match(report.headers.get('content-type'), /text\/csv/); assert.match(await report.text(), /"'=HYPERLINK"/);
});
