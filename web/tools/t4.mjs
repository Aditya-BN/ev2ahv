// T3/T4: drive the real UI of an appliance with Playwright (no API calls except read-only verification).
//   node tools/t4.mjs <stage> [args]     stages: env | wizard | watch <minutes> | cutover | after
// Reads the UI password from ~/.config/ev2ahv/appliance3-ui.pw and the everRun portal and Windows guest passwords from
// ~/everrun-lab/.admin-pass and ~/.config/ev2ahv/win.env. Never prints them. Screenshots (deviceScaleFactor 2) go to docs/appliance-shots/t4/.
import { createRequire } from 'node:module';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
const require = createRequire('/home/adit@ntnx.hin/work/shots-tool/');
const { chromium } = require('playwright-core');

const BASE = process.env.T4_BASE || 'https://10.12.135.160';
const OUT = path.join(os.homedir(), 'ev2ahv/docs/appliance-shots/t4'); fs.mkdirSync(OUT, { recursive: true });
const home = os.homedir();
const rd = (p) => fs.readFileSync(p, 'utf8').trim();
const UIPW = rd(`${home}/.config/ev2ahv/appliance3-ui.pw`);
const ERPW = rd(`${home}/everrun-lab/.admin-pass`);
const WINPW = rd(`${home}/.config/ev2ahv/win.env`).split('\n').map((l) => l.trim()).filter(Boolean).map((l) => l.replace(/^EV2AHV_WIN_PASS=/, ''))[0].replace(/^["']|["']$/g, '');
const PLAN = 'win22-ui-t4';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let n = Number(fs.readdirSync(OUT).filter((f) => /^\d\d-/.test(f)).length);

const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
const shot = async (name, opts = {}) => { await sleep(400); n++; const f = `${String(n).padStart(2, '0')}-${name}.png`; await page.screenshot({ path: path.join(OUT, f), fullPage: true, ...opts }); console.log('shot', f); };
const poll = async (fn, what, tries = 90, ms = 1000) => { for (let i = 0; i < tries; i++) { if (await page.evaluate(fn).catch(() => false)) return; await sleep(ms); } throw new Error('timeout: ' + what); };
const go = async (p) => { await page.goto(BASE + p); await sleep(1000); };
const login = async () => {
  await go('/');
  await page.getByPlaceholder('Enter user name').fill('nutanix');
  await page.getByPlaceholder('Enter password').fill(UIPW);
  await page.getByRole('button', { name: 'Log In' }).click();
  await poll(() => document.body.innerText.includes('Viewing'), 'dashboard'); await sleep(1500);
};
const planId = async () => { const r = await (await ctx.request.get(BASE + '/api/v2/plans')).json(); return r.entities.find((p) => p.name === PLAN)?.id; };
const vmRow = () => page.locator('tr[data-testid=vm-row]').first();
const rowText = () => vmRow().innerText().catch(() => '');
const stage = process.argv[2];

try {
  if (stage === 'env') {
    await go('/'); if (n === 0) await shot('login');
    await login(); await shot('dashboard-before');
    await page.locator('#add-env').click(); await page.locator('#env-type').selectOption('everrun'); await shot('add-environment-type');
    await page.getByPlaceholder('Enter a friendly display name').fill('everrun-lab');
    await page.getByPlaceholder('Enter IP Address or FQDN').fill('10.12.136.153');
    await page.getByPlaceholder('Enter user name').fill('admin');
    await page.locator('.modal input[type=password]').fill(ERPW);
    await page.getByPlaceholder('e.g. 10.0.0.11, 10.0.0.12').fill('10.12.136.151, 10.12.136.152');
    await shot('add-environment-filled');
    await page.getByRole('button', { name: 'Add', exact: true }).click();
    await poll(() => document.body.innerText.includes('everrun-lab'), 'env card', 60); await sleep(4000); await shot('dashboard-env-added');
    const card = page.locator('[data-testid=env-card]', { hasText: 'everrun-lab' });
    await card.locator('.icon-btn').click(); await shot('env-card-menu');
    await page.getByRole('menuitem', { name: 'Refresh' }).or(page.getByText('Refresh', { exact: true })).first().click();
    await sleep(8000); await shot('dashboard-env-refreshed');
    await card.locator('.icon-btn').click(); await page.getByText('View Details', { exact: true }).click(); await sleep(2500); await shot('env-details-everrun');
  }
  if (stage === 'wizard') {
    await login();
    await page.locator('#new-plan').click(); await sleep(600); await shot('wizard-name-gate');
    await page.locator('#plan-name').fill(PLAN); await page.locator('#gate-proceed').click(); await sleep(600);
    await page.locator('#sel-source').selectOption({ label: /everrun/.test('') ? '' : (await page.locator('#sel-source option').allInnerTexts()).find((t) => t.startsWith('everrun-lab')) });
    const tgts = await page.locator('#sel-target option').allInnerTexts();
    await page.locator('#sel-target').selectOption({ label: tgts.find((t) => /AOS/.test(t)) });
    await sleep(500);
    await page.locator('#sel-cluster').selectOption({ index: 1 }); await sleep(300);
    const conts = await page.locator('#sel-container option').allInnerTexts();
    await page.locator('#sel-container').selectOption({ label: conts.find((c) => c === 'VM_Container') || conts[1] });
    await shot('wizard-1-source-target');
    await page.locator('#wiz-next').click(); await sleep(1500);
    await shot('wizard-2-select-vms');
    await page.getByRole('button', { name: 'Vm ev2ahv-src-win22 add' }).click(); await shot('wizard-2-vm-added');
    await page.locator('#wiz-next').click(); await sleep(800); await shot('wizard-3-network');
    await page.locator('table.net select').first().selectOption('VLAN136'); await shot('wizard-3-network-mapped');
    await page.locator('#wiz-next').click(); await sleep(800); await shot('wizard-4-prep-initial');
    await page.locator('.cred-box input[type=password]').first().fill(WINPW);
    await shot('wizard-4-prep-credentials');
    await page.locator('#wiz-next').click(); await sleep(800);
    const row = page.locator('tr[data-testid=vmset-row]').first();
    await shot('wizard-5-vm-settings-default');
    await row.locator('input[aria-label^="Target name"]').fill('ev2ahv-win22-app3');
    await row.locator('select[aria-label="Disk bus"]').selectOption('pci');
    await row.locator('input[aria-label="Guest IP"]').fill('10.12.136.161');
    await shot('wizard-5-vm-settings');
    await page.locator('#wiz-next').click(); await sleep(800); await shot('wizard-6-summary');
    await page.locator('#wiz-save-start').click();
    await poll(() => location.pathname.startsWith('/migrationprogress/'), 'progress page'); await sleep(3000); await shot('progress-started');
  }
  if (stage === 'watch') {
    const mins = Number(process.argv[3] || 10); await login(); const id = await planId(); await go('/migrationprogress/' + id);
    const end = Date.now() + mins * 60000; let last = '';
    while (Date.now() < end) {
      const t = await rowText(); const key = (t.match(/Preparing|Seeding|Ready to Cutover|Failed|Completed|Cutover in Progress|[Rr]esync\w*/g) || []).join(',') ;
      console.log(new Date().toISOString(), t.replace(/\s+/g, ' ').slice(0, 220));
      if (key !== last) { last = key; await shot('progress-' + key.replace(/[^\w]+/g, '-').slice(0, 30)); }
      if (/Ready to Cutover|Failed/.test(t) && process.argv[4] === 'stop') break;
      await sleep(20000); await page.reload(); await sleep(1500);
    }
  }
  if (stage === 'checks') {
    await login(); const id = await planId(); await go('/migrationprogress/' + id);
    await vmRow().locator('.chk-badge').first().click(); await sleep(500); await shot('prep-check-popover');
    await page.keyboard.press('Escape');
    await vmRow().getByRole('button', { name: 'VM Configurations' }).click(); await sleep(500); await shot('vm-configurations'); await page.getByRole('button', { name: 'OK' }).click();
    await vmRow().getByRole('button', { name: 'View Log' }).click(); await sleep(800); await shot('vm-log'); await page.getByRole('button', { name: 'Close', exact: true }).last().click();
    await go('/plan-details/' + id); await shot('plan-summary');
  }
  if (stage === 'edit') {
    await login(); const id = await planId(); await go('/plan-details/' + id); await shot('plan-summary-before-edit');
    await page.locator('#edit-plan').click(); await sleep(1500); await shot('edit-wizard-step1');
    for (let i = 0; i < 3; i++) { await page.locator('#wiz-next').click(); await sleep(500); }
    await shot('edit-wizard-step4');
    await page.locator('.cred-box input[type=password]').first().fill(WINPW);
    for (let i = 0; i < 2; i++) { await page.locator('#wiz-next').click(); await sleep(500); }
    await page.locator('select').filter({ hasText: '15 minutes' }).last().selectOption('15'); await shot('edit-wizard-resync-15');
    await page.locator('#wiz-save').click(); await sleep(2500); await shot('edit-saved');
  }
  if (stage === 'cutover') {
    await login(); const id = await planId(); await go('/migrationprogress/' + id);
    await vmRow().locator('input[type=checkbox]').check(); await shot('cutover-selected');
    await page.locator('#act-cutover').click(); await sleep(600); await shot('cutover-dialog');
    await page.locator('#cutover-confirm').click(); await sleep(3000); await shot('cutover-started');
  }
  if (stage === 'after') {
    await login(); const id = await planId(); await go('/migrationprogress/' + id); await shot('progress-final');
    await go('/plan-details/' + id); await shot('plan-summary-final');
    await go('/events'); await shot('events');
    await go('/dashboard'); await shot('dashboard-final');
  }
} catch (e) { console.error('FAILED:', e.message); await shot('error').catch(() => {}); process.exitCode = 1; }
await browser.close();
