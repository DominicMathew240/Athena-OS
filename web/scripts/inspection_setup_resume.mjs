// Browser regression for an unfinished inspection during AI setup. The AI status is STUBBED.
// Run against a disposable Athena server: ATHENA_QA_BASE, ATHENA_QA_SESSION_FILE,
// ATHENA_QA_IMAGE and PLAYWRIGHT_NODE_MODULES must be supplied by the caller.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const base = process.env.ATHENA_QA_BASE;
const session = fs.readFileSync(process.env.ATHENA_QA_SESSION_FILE, 'utf8').trim();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
try {
  await context.addCookies([{ name: 'sessionid', value: session, url: base }]);
  let configured = false;
  let statusFails = false;
  await context.route('**/api/vision/status', route => statusFails ? route.fulfill({ status: 503, json: { error: 'QA status unavailable' } }) : route.fulfill({ json: {
    active: { configured, label: 'QA model', model: 'example' }, reachable: configured,
  }}));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(`${base}/inspect/new?template=corrosion`, { waitUntil: 'networkidle' });
  await page.getByTestId('connect-ai-first-button').waitFor({ state: 'visible' });
  await page.locator('#instructions').fill('Look closely at the pipe.');
  await page.locator('#file-input').setInputFiles(process.env.ATHENA_QA_IMAGE);
  await page.waitForFunction(() => document.querySelector('#mark-img').naturalWidth > 8);
  await page.locator('#mark-stage').scrollIntoViewIfNeeded();
  const image = await page.locator('#mark-img').boundingBox();
  await page.mouse.move(image.x + image.width * .3, image.y + image.height * .65);
  await page.mouse.down();
  await page.mouse.move(image.x + image.width * .6, image.y + image.height * .95, { steps: 8 });
  await page.mouse.up();
  assert.equal(await page.locator('#mark-list li').count(), 1);
  assert.equal(await page.getByTestId('nav-ai-settings-link').getAttribute('target'), '_blank');
  const popupPromise = context.waitForEvent('page', { timeout: 4000 });
  await page.getByTestId('connect-ai-first-button').click();
  const settings = await popupPromise;
  await settings.waitForURL('**/settings/ai');
  configured = true; // synthetic readiness; no actual model is connected
  await page.bringToFront();
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.waitForFunction(() => document.getElementById('sum-ai-text').textContent.includes('QA model'));
  assert.equal(await page.locator('#instructions').inputValue(), 'Look closely at the pipe.');
  assert.equal(await page.locator('#mark-list li').count(), 1);
  assert.equal(await page.locator('#thumbs img').count(), 1);
  assert.equal(await page.locator('#run').isEnabled(), true);
  assert.equal(await page.locator('#connect-first').isHidden(), true);
  assert.equal(await page.locator('[data-ai-dot]').getAttribute('data-state'), 'ok', 'sidebar must agree with the refreshed summary');
  assert.deepEqual(errors, []);
  if (process.env.ATHENA_QA_SCREENSHOT) await page.screenshot({ path: process.env.ATHENA_QA_SCREENSHOT, fullPage: true });
  statusFails = true;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.waitForFunction(() => document.getElementById('sum-ai-text').textContent === 'AI model status unknown');
  assert.equal(await page.locator('#sum-ai').getAttribute('data-ok'), 'false');
  assert.equal(await page.locator('#mark-list li').count(), 1);
  statusFails = false;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await page.waitForFunction(() => document.getElementById('sum-ai-text').textContent.includes('QA model'));
  await page.locator('#file-input').setInputFiles(process.env.ATHENA_QA_IMAGE);
  assert.equal(await page.locator('#thumbs img').count(), 2);
  await page.getByTestId('remove-picture-2').click();
  assert.equal(await page.locator('#mark-list li').count(), 1, 'removing a different picture must keep the first picture marks');
  await page.locator('#file-input').setInputFiles(process.env.ATHENA_QA_IMAGE);
  await page.getByTestId('remove-picture-1').click();
  assert.equal(await page.locator('#mark-list li').count(), 0, 'marks for the removed first image must not apply to its replacement');
  console.log('PASS: image, marked area and instructions survive AI setup in another tab; status refresh is stubbed.');
} finally {
  await context.close();
  await browser.close();
}
