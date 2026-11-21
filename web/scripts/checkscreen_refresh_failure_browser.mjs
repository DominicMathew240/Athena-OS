// Run only against a disposable seeded Athena instance; creates one synthetic screen.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const { ATHENA_QA_BASE: base, ATHENA_QA_SESSION_FILE: file, ATHENA_QA_PROJECT: project } = process.env;
if (!base || !file || !project) throw new Error('Provide disposable ATHENA_QA_BASE, ATHENA_QA_SESSION_FILE and ATHENA_QA_PROJECT.');
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext();
try {
  await context.addCookies([{ name: 'sessionid', value: fs.readFileSync(file, 'utf8').trim(), url: base }]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const list = `${base}/api/projects/${project}/inference/apps`;
  let failRefresh = false;
  const methods = [];
  await page.route(`${list}**`, async route => {
    const request = route.request();
    if (request.method() === 'POST' || request.method() === 'PATCH') methods.push(request.method());
    if (failRefresh && request.url() === list && request.method() === 'GET') {
      return route.fulfill({ status: 503, contentType: 'application/json', body: '{"error":"synthetic unavailable"}' });
    }
    return route.continue();
  });
  await page.goto(`${base}/projects/${project}/check-screen/setup`, { waitUntil: 'networkidle' });
  await page.locator('#cs-app-select').selectOption('new');
  await page.locator('#cs-name').fill(`Synthetic refresh ${Date.now()}`);
  failRefresh = true;
  const create = page.waitForResponse(response => response.url() === list && response.request().method() === 'POST');
  await page.locator('#cs-save').click();
  const created = await create;
  if (created.status() !== 201) throw new Error(`Create returned ${created.status()}: ${await created.text()}`);
  const id = (await created.json()).app.id;
  await page.waitForFunction(() => document.querySelector('#cs-save-msg').textContent.includes('list could not be refreshed'));
  assert.equal(await page.locator('#cs-page-alert').isVisible(), true);
  assert.equal(await page.locator('#cs-app-select').inputValue(), id);
  assert.equal(await page.locator('#cs-save').innerText(), 'Save changes');
  await page.locator('#cs-name').fill('Synthetic refresh updated');
  const patch = page.waitForResponse(response => response.url() === `${list}/${id}` && response.request().method() === 'PATCH');
  await page.locator('#cs-save').click();
  assert.equal((await patch).status(), 200);
  await page.waitForFunction(() => document.querySelector('#cs-save-msg').textContent.includes('list could not be refreshed'));
  assert.deepEqual(methods, ['POST', 'PATCH']);
  const stored = await (await page.request.get(`${list}/${id}`)).json();
  assert.equal(stored.app.name, 'Synthetic refresh updated');
  assert.deepEqual(errors, []);
  console.log('PASS real POST→synthetic list 503→same selection→real PATCH→read-back; no duplicate POST');
} finally {
  await context.close();
  await browser.close();
}
