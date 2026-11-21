// Synthetic check-screen canvas proof against a disposable Athena instance.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const { ATHENA_QA_BASE: base, ATHENA_QA_SESSION_FILE: sessionFile, ATHENA_QA_PROJECT: project, ATHENA_QA_APP: app, ATHENA_QA_SLUG: slug } = process.env;
if (!base || !sessionFile || !project || !app || !slug) throw new Error('Set ATHENA_QA_BASE, ATHENA_QA_SESSION_FILE, ATHENA_QA_PROJECT, ATHENA_QA_APP and ATHENA_QA_SLUG.');
const session = fs.readFileSync(sessionFile, 'utf8').trim();
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [390, 1280]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
    try {
      await context.addCookies([{ name: 'sessionid', value: session, url: base }]);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(`${base}/projects/${project}/check-screen/setup?app=${app}`, { waitUntil: 'networkidle' });
      await page.waitForFunction(() => document.querySelector('#cs-wf-vals-input')?.textContent.includes('Accepts'));
      await page.locator('[data-wf-edit="input"]').click();
      assert(await page.evaluate(() => document.activeElement.closest('#cs-types') !== null));
      const webp = page.locator('#cs-types input[value="image/webp"]');
      await webp.check();
      await webp.uncheck();
      await page.waitForFunction(() => document.querySelector('#cs-wf-badge').textContent.includes('Unsaved'));
      assert.doesNotMatch(await page.locator('#cs-wf-vals-input').innerText(), /WebP/);
      await page.locator('#cs-save').click();
      await page.waitForFunction(() => document.querySelector('#cs-save-msg').textContent.includes('Saved.'), null, { timeout: 12000 });
      const response = await page.evaluate(async url => (await fetch(url)).json(), `/api/projects/${project}/inference/apps/${app}/preview`);
      assert(!response.preview.accepted_inputs.includes('image/webp'));
      await page.reload({ waitUntil: 'networkidle' });
      await page.waitForSelector('#cs-types input[value="image/webp"]');
      assert.equal(await page.locator('#cs-types input[value="image/webp"]').isChecked(), false);
      await page.goto(`${base}/check/${slug}`, { waitUntil: 'networkidle' });
      assert(!(await page.locator('input[type=file]').first().getAttribute('accept')).includes('image/webp'));
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px: canvas edit → save → server preview → reload → operator input`);
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
}
