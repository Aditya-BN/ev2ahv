// Screenshot + click-through script. Run after `npm run build` with the mock server on 127.0.0.1:8443:
//   node tools/shots.mjs [light|dark]   (default light; writes docs/ui-shots/<scheme>/)
// Creates a plan named uitest-<time>, drives it through Test and Cutover, then deletes every uitest-* plan via the API.
import { createRequire } from 'node:module';
import path from 'node:path';
import os from 'node:os';
const require = createRequire('/home/adit@ntnx.hin/work/shots-tool/');
const { chromium } = require('playwright-core');

const BASE = 'https://127.0.0.1:8443';
const SCHEME = process.argv[2] === 'dark' ? 'dark' : 'light';
const OUT = path.join(os.homedir(), 'ev2ahv/docs/ui-shots', SCHEME);
import fs from 'node:fs'; fs.mkdirSync(OUT, { recursive: true });
const PLAN = 'uitest-' + Date.now().toString(36);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, colorScheme: SCHEME, viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();
const shot = async (n, opts = {}) => { await sleep(350); await page.screenshot({ path: path.join(OUT, n + '.png'), fullPage: true, ...opts }); console.log('shot', n); };
const poll = async (fn, what, tries = 90, ms = 1000) => {
  for (let i = 0; i < tries; i++) { if (await page.evaluate(fn).catch(() => false)) return; await sleep(ms); }
  throw new Error('timeout: ' + what);
};
const go = async (p) => { await page.goto(BASE + p); await sleep(900); };
const text = (t) => page.getByText(t, { exact: true });

try {
  // login
  await go('/');
  await shot('ui-login');
  await page.getByPlaceholder('Enter user name').fill('nutanix');
  await page.getByPlaceholder('Enter password').fill('nutanix/4u');
  await page.getByRole('button', { name: 'Log In' }).click();
  await poll(() => document.body.innerText.includes('Viewing'), 'dashboard');
  await sleep(1200);
  await shot('ui-01-dashboard');

  // dashboard dialogs and menus
  await page.locator('#add-env').click();
  await page.locator('#env-type').selectOption('everrun');
  await shot('ui-28-add-environment');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page.locator('.env-card .icon-btn').first().click();
  await shot('ui-26-environment-card-menu');
  await page.keyboard.press('Escape'); await page.locator('.page-title').first().click();
  const notStarted = page.locator('tr[data-testid=plan-row]', { hasText: 'Not Started' }).first();
  await notStarted.getByRole('button', { name: /More actions/ }).click();
  await shot('ui-30-plan-row-actions');
  await page.locator('.page-title').first().click();
  await page.locator('select.filter-select').selectOption('Completed');
  await shot('ui-33-plans-filter');
  await page.locator('select.filter-select').selectOption('All Plans');
  await page.getByRole('button', { name: 'Change password' }).click();
  await page.locator('input[type=password]').first().fill('x');
  await shot('ui-25-change-password');
  await page.getByRole('button', { name: 'Cancel' }).click();
  await page.getByRole('button', { name: 'About ev2ahv' }).click();
  await shot('ui-24-about');
  await page.getByRole('button', { name: 'OK' }).click();

  await go('/events'); await shot('ui-05-events');
  await go('/bandwidth-configuration'); await shot('ui-09-bandwidth-empty');
  await page.locator('#create-policy').click();
  await page.getByPlaceholder('Enter Policy Name').fill('uitest-limit');
  await page.locator('.modal select').nth(0).selectOption({ index: 1 });
  await page.locator('.modal select').nth(1).selectOption({ index: 1 });
  await page.locator('.modal input[type=number]').fill('200');
  await shot('ui-09-bandwidth');
  await page.getByRole('button', { name: 'Create', exact: true }).click(); await sleep(800);
  await shot('ui-09-bandwidth-list');
  // remove the uitest policy again
  await page.getByRole('button', { name: 'Delete' }).first().click(); await page.locator('.modal .btn.danger').click(); await sleep(600);
  await go('/realtime-logs'); await shot('ui-12-realtime-logs');
  await go('/appliance-settings'); await shot('ui-08-appliance-settings');
  await go('/health'); await shot('ui-13-health');

  // existing plans
  const plans = await (await ctx.request.get(BASE + '/api/v2/plans')).json();
  const byName = (n) => plans.entities.find((p) => p.name === n);
  await go('/plan-details/' + byName('wave2-erp').id); await shot('ui-39-plan-summary'); await page.evaluate(() => window.scrollTo(0, 99999)); await page.locator('.page').evaluate((e) => e.scrollIntoView()); await page.evaluate(() => document.querySelector('[data-testid=prep-scripts]')?.scrollIntoView({ block: 'center' })); await shot('ui-39b-plan-summary-prep-scripts', { fullPage: false });
  await go('/migrationprogress/' + byName('wave2-erp').id); await shot('ui-40-plan-details');
  await page.locator('tr[data-testid=vm-row]').first().locator('input[type=checkbox]').check();
  await shot('ui-40b-selection');
  await go('/migrationprogress/' + byName('wave1-rocky').id); await shot('ui-44-plan-completed');
  await go('/all-plan-vms/completed'); await shot('ui-45-completed-vms');
  await go('/all-plan-vms/failed'); await shot('ui-46-failed-vms');

  // wizard
  await go('/dashboard');
  await page.locator('#new-plan').click(); await sleep(600);
  await shot('ui-82-wizard-name-gate');
  await page.locator('#plan-name').fill(PLAN);
  await page.locator('#gate-proceed').click(); await sleep(500);
  await shot('ui-84-step1-initial');
  await page.locator('#sel-source').selectOption({ index: 1 }); await shot('ui-86-step1-source');
  await page.locator('#sel-target').selectOption({ index: 1 }); await shot('ui-88-step1-target');
  await page.locator('#sel-cluster').selectOption({ index: 1 }); await shot('ui-90-step1-cluster');
  await page.locator('#sel-container').selectOption({ index: 1 }); await shot('ui-93-step1-container');
  await page.locator('#wiz-next').click(); await sleep(900);
  await shot('ui-94-step2-select-vms');
  await page.getByRole('button', { name: 'Vm ev2ahv-src-rocky9 add' }).click();
  await page.getByRole('button', { name: 'Vm ev2ahv-src-win22 add' }).click();
  await page.getByRole('button', { name: 'Vm legacy-hmi add' }).click();
  await shot('ui-100-step2-vms-added');
  await page.locator('.added-panel .tabs button', { hasText: 'Warnings' }).click();
  await page.locator('.added-panel .warn-ic').first().hover();
  await shot('ui-101-step2-warnings');
  await page.locator('.added-panel .tabs button', { hasText: 'All' }).click();
  await page.locator('.rm-btn[aria-label="Vm legacy-hmi remove"]').click();
  await page.locator('#wiz-next').click(); await sleep(600);
  await shot('ui-104-step3-network');
  await page.locator('table.net select').first().selectOption({ index: 1 });
  await page.locator('#test-net').check();
  await page.locator('table.net select').nth(1).selectOption('ev2ahv-test-isolated');
  await shot('ui-114-step3-mapped');
  await page.locator('#wiz-next').click(); await sleep(600);
  await shot('ui-115-step4-prep-initial');
  await page.getByText('Manual', { exact: true }).click(); await shot('ui-116-step4-prep-manual');
  await page.getByText('Automatic', { exact: true }).click();
  await page.getByLabel('Windows password').or(page.locator('.cred-box input[type=password]').first()).first().fill('uitest-pass');
  await shot('ui-115-step4-prep');
  await page.locator('#wiz-next').click(); await sleep(600);
  // force a prep-check FAIL on win22: SCSI boot bus (needs bound vioscsi driver)
  await page.locator('tr[data-testid=vmset-row]', { hasText: 'ev2ahv-src-win22' }).locator('select[aria-label="Disk bus"]').selectOption('scsi');
  await shot('ui-step5-vm-settings');
  await page.locator('#wiz-next').click(); await sleep(600);
  await shot('ui-step6-summary');
  await page.getByText('Schedule data seeding for a date and time').click();
  await shot('ui-step6-summary-schedule');
  await page.getByText('Start data seeding now (when you click Save and Start)').click();
  await page.locator('#wiz-save-start').click();
  await poll(() => location.pathname.startsWith('/migrationprogress/'), 'progress page');
  await sleep(2500); await shot('ui-progress-start');
  await sleep(8000); await shot('ui-progress-seeding');
  // leave-wizard confirm (separate wizard instance)
  const planId = page.url().split('/').pop();

  // wait for rocky9 to be Ready to Cutover
  await poll(() => { const r = [...document.querySelectorAll('tr[data-testid=vm-row]')].find((x) => x.innerText.includes('rocky9')); return !!r && r.innerText.includes('Ready to Cutover'); }, 'rocky9 ready', 150);
  await shot('ui-progress-ready');
  const row = (n) => page.locator('tr[data-testid=vm-row]', { hasText: n });
  // Test
  await row('rocky9').locator('input[type=checkbox]').check();
  await page.locator('#act-test').click(); await shot('ui-test-dialog');
  await page.locator('#test-confirm').click();
  await poll(() => { const r = [...document.querySelectorAll('tr[data-testid=vm-row]')].find((x) => x.innerText.includes('rocky9')); return !!r && /Tested/.test(r.innerText); }, 'tested', 60);
  await shot('ui-tested');
  // Cutover
  await page.locator('#act-cutover').click(); await shot('ui-cutover-dialog');
  await page.locator('#cutover-confirm').click();
  await poll(() => { const r = [...document.querySelectorAll('tr[data-testid=vm-row]')].find((x) => x.innerText.includes('rocky9')); return !!r && /Completed/.test(r.innerText); }, 'cutover complete', 90);
  await shot('ui-cutover-completed');
  await row('rocky9').locator('input[type=checkbox]').uncheck().catch(() => {});
  // Rollback dialog (not confirmed)
  await row('rocky9').locator('input[type=checkbox]').check();
  await page.locator('#act-rollback').click(); await shot('ui-rollback-dialog');
  await page.getByRole('button', { name: 'Cancel' }).last().click();
  await row('rocky9').locator('input[type=checkbox]').uncheck();
  // win22: cutover blocked by prep-check FAIL (not forced)
  await poll(() => { const r = [...document.querySelectorAll('tr[data-testid=vm-row]')].find((x) => x.innerText.includes('win22')); return !!r && r.innerText.includes('Ready to Cutover'); }, 'win22 ready', 150);
  await row('win22').locator('input[type=checkbox]').check();
  await page.locator('#act-cutover').click();
  await page.locator('#cutover-confirm').click();
  await poll(() => document.body.innerText.includes('Cutover anyway'), 'blocked dialog', 20);
  await shot('ui-cutover-blocked');
  await page.getByRole('button', { name: 'Cancel' }).last().click();
  await row('win22').locator('.chk-badge').first().click();
  await shot('ui-checks-popover');
  await page.mouse.click(1000, 800);
  await row('win22').getByRole('button', { name: 'View Log' }).click(); await sleep(800);
  await shot('ui-vm-log');
  await page.locator('.modal-foot .btn.primary').click();

  // plan summary + events tab of the new plan, then leave-wizard confirm in edit mode
  await go('/plan-details/' + planId); await shot('ui-plan-summary-new');
  await page.getByRole('button', { name: 'Events' }).click(); await shot('ui-plan-events');
  await go('/dashboard'); await shot('ui-dashboard-after');
  await page.locator('#new-plan').click(); await page.locator('#plan-name').fill('uitest-leave');
  await page.locator('#gate-proceed').click(); await sleep(300);
  await page.getByRole('button', { name: 'Cancel' }).last().click(); await shot('ui-119-wizard-leave-confirm');
  await page.getByRole('button', { name: 'Discard' }).click();
} finally {
  // cleanup: delete every uitest-* plan via the API
  const r = await (await ctx.request.get(BASE + '/api/v2/plans')).json().catch(() => ({ entities: [] }));
  for (const p of r.entities.filter((p) => p.name.startsWith('uitest-'))) {
    const d = await ctx.request.delete(BASE + '/api/v2/plans/' + p.id);
    console.log('delete', p.name, d.status());
  }
  const s = await (await ctx.request.get(BASE + '/api/v2/settings')).json().catch(() => null);
  if (s && (s.bandwidth_policies || []).some((b) => b.name.startsWith('uitest-')))
    await ctx.request.put(BASE + '/api/v2/settings', { data: { bandwidth_policies: s.bandwidth_policies.filter((b) => !b.name.startsWith('uitest-')) } });
  await browser.close();
}
