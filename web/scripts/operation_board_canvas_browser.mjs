// Disposable synthetic operation-board preview: no model, live data, or publication.
import { createRequire } from 'node:module';
import fs from 'node:fs';
import assert from 'node:assert/strict';

const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const base = process.env.ATHENA_QA_BASE;
const session = fs.readFileSync(process.env.ATHENA_QA_SESSION_FILE, 'utf8').trim();
const operation = process.env.ATHENA_QA_OPERATION;
const draftName = process.env.ATHENA_QA_DRAFT_NAME;
if (!base || !operation || !draftName) throw new Error('Set ATHENA_QA_BASE, ATHENA_QA_OPERATION, and ATHENA_QA_DRAFT_NAME.');
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [390, 1280]) {
    const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
    try {
      await context.addCookies([{ name: 'sessionid', value: session, url: base }]);
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base + '/operations', { waitUntil: 'networkidle' });
      await page.locator('#draft-list').getByRole('button', { name: draftName, exact: true }).click();
      await page.waitForSelector('#create-screen', { state: 'attached' });
      const exit = page.getByRole('button', { name: /exit setup/i });
      if (await exit.isVisible()) await exit.click();
      await page.waitForSelector('#screen-title', { state: 'visible' });
      await page.locator('#screen-title').fill('My entrance');
      await page.getByTestId('move-earlier-exits').click();
      assert.deepEqual(await page.locator('[data-testid="screen-preview-tiles"] [data-widget]').evaluateAll(items => items.map(item => item.dataset.widget)), ['exits', 'entries']);
      assert.match(await page.evaluate(() => document.activeElement.getAttribute('aria-label')), /Move Exits/);
      await page.locator('#screen-entries').uncheck();
      assert.equal(await page.getByTestId('screen-preview-value-exits').innerText(), '—');
      assert.match(await page.getByTestId('screen-preview-notice').innerText(), /not live or published/i);
      const response = page.waitForResponse(result => result.url().includes('/screen-versions') && result.request().method() === 'POST');
      await page.getByTestId('create-screen').click();
      assert.equal((await response).status(), 201);
      const versions = await page.evaluate(async id => (await fetch(`/api/operations/${id}/screen-versions`)).json(), operation);
      assert.deepEqual(versions.screen_versions[0].configuration, { title: 'My entrance', show_entries: false, show_exits: true, widget_order: ['exits', 'entries'] });
      const saved = versions.screen_versions[0];
      await page.reload({ waitUntil: 'networkidle' });
      await page.locator('#draft-list').getByRole('button', { name: draftName, exact: true }).click();
      if (await page.getByRole('button', { name: /exit setup/i }).isVisible()) await page.getByRole('button', { name: /exit setup/i }).click();
      await page.waitForFunction(id => [...(document.querySelector('#screen-version-select')?.options || [])].some(o => o.value === id), saved.id);
      await page.locator('#screen-version-select').selectOption(saved.id);
      assert.equal(await page.locator('#screen-title').inputValue(), 'My entrance');
      assert.equal(await page.locator('#screen-entries').isChecked(), false);
      assert.deepEqual(await page.locator('[data-testid="screen-order-list"] [data-widget]').evaluateAll(items => items.map(item => item.dataset.widget)), ['exits', 'entries']);
      await page.locator('#screen-title').fill('Edited board');
      await page.locator('#screen-version-select').selectOption('');
      await page.getByRole('button', { name: 'Keep editing' }).click();
      await page.waitForFunction(id => document.querySelector('#screen-version-select').value === id, saved.id);
      assert.equal(await page.locator('#screen-title').inputValue(), 'Edited board');
      const previousTitle = await page.locator('#screen-title').elementHandle();
      const recipeResponse = page.waitForResponse(result => result.url().includes('/recipe-versions') && result.request().method() === 'POST');
      await page.getByTestId('create-recipe').click();
      assert.equal((await recipeResponse).status(), 201);
      await page.waitForFunction(el => !el.isConnected, previousTitle);
      await page.waitForFunction(() => document.querySelector('#screen-title')?.value === 'Edited board');
      assert.equal(await page.locator('#screen-version-select').inputValue(), saved.id);
      const copyResponse = page.waitForResponse(result => result.url().includes('/screen-versions') && result.request().method() === 'POST');
      await page.getByTestId('create-screen').click();
      assert.equal((await copyResponse).status(), 201);
      const afterCopy = await page.evaluate(async id => (await fetch(`/api/operations/${id}/screen-versions`)).json(), operation);
      assert.equal(afterCopy.screen_versions[0].configuration.title, 'Edited board');
      assert.deepEqual(afterCopy.screen_versions.find(s => s.id === saved.id).configuration, saved.configuration);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth), 0);
      assert.deepEqual(errors, []);
      console.log(`PASS ${width}px: customize, keyboard-focus retention, save and API read-back, no page errors or horizontal overflow`);
    } finally {
      await context.close();
    }
  }
} finally {
  await browser.close();
}
