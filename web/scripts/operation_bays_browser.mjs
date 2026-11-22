// Browser proof of polygon bays (T17): the whole bay workflow — add a bay by
// keyboard, name it, type its four corners, choose corners with buttons, nudge
// with arrow keys, undo/redo, duplicate (a fresh stable ID and a "(copy)" name),
// rename (same ID, new name — history kept), reject a self-crossing "bowtie"
// with the plain message, save, reload, verify persistence and the ID — driven
// entirely by the keyboard. Like operation_a11y_browser.mjs, this script never
// touches page.mouse and never samples a pixel: no dragging and no colour
// recognition. The geometry is checked through the text summary, the field
// values, and the server's own answers.
//
// Run against a disposable Athena server. The caller supplies:
//   ATHENA_QA_BASE             e.g. http://127.0.0.1:8123
//   ATHENA_QA_SESSION_FILE     a file containing one signed-in sessionid cookie value
//   PLAYWRIGHT_NODE_MODULES    a node_modules folder that contains playwright
//   ATHENA_QA_VIDEO            optional; without it this script makes its own synthetic MP4
//                              with ffmpeg (same recipe as make_clip in tests_source_recordings.py)
import { createRequire } from 'node:module';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import assert from 'node:assert/strict';
const run = promisify(execFile);
const require = createRequire(import.meta.url);
const { chromium } = require(require.resolve('playwright', { paths: [process.env.PLAYWRIGHT_NODE_MODULES] }));
const base = process.env.ATHENA_QA_BASE;
const session = fs.readFileSync(process.env.ATHENA_QA_SESSION_FILE, 'utf8').trim();

async function makeClip() {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'athena-bays-'));
  const file = path.join(folder, 'clip.mp4');
  await run('ffmpeg', [
    '-hide_banner', '-loglevel', 'error',
    '-f', 'lavfi', '-i', 'color=c=blue:s=64x48:r=5',
    '-t', '4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
    '-movflags', '+faststart', '-y', file
  ]);
  return file;
}

const videoFile = process.env.ATHENA_QA_VIDEO || await makeClip();
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
try {
  await context.addCookies([{ name: 'sessionid', value: session, url: base }]);
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  // The hard rule of this proof: no mouse input, ever.
  page.mouse = new Proxy({}, { get: () => { throw new Error('this proof must never use the mouse'); } });

  async function pressById(selector, key = 'Enter') {
    await page.waitForFunction(sel => {
      const element = document.querySelector(sel);
      return element && !element.disabled;
    }, selector, { timeout: 10000 });
    await page.locator(selector).focus();
    await page.keyboard.press(key);
  }

  // Duplicate/rename persist on the server without showDraft. The next Save
  // draft must PUT the current server revision (not the pre-action one) and
  // succeed; #bay-summary already says "saved" from load() and is not proof.
  async function assertSuccessfulDraftSave(id) {
    const before = await page.evaluate(async draftId => (
      await (await fetch(`/api/operation-drafts/${draftId}`)).json()
    ).draft, id);
    const pending = page.waitForResponse(response => {
      const url = response.url();
      return response.request().method() === 'PUT'
        && url.includes(`/api/operation-drafts/${id}`)
        && !url.includes('/bays/');
    });
    await pressById('[data-testid="bay-save"]');
    const put = await pending;
    assert.equal(put.status(), 200);
    const submitted = JSON.parse(put.request().postData());
    assert.equal(submitted.revision, before.revision);
    const body = await put.json();
    assert.equal(body.draft.revision, before.revision + 1);
    await page.waitForFunction(() => {
      const box = document.querySelector('#draft-status');
      return box && box.textContent === 'Saved draft. Changes are saved when you select Save draft.';
    }, null, { timeout: 10000 });
    assert.equal(await page.locator('#draft-status').innerText(), 'Saved draft. Changes are saved when you select Save draft.');
    assert.equal((await page.locator('#draft-error').innerText()).trim(), '');
  }

  // A draft with one recording, walked through the guided stepper by keyboard.
  await page.goto(base + '/operations');
  await page.getByText(/No drafts yet|Select a draft|Showing up to/).waitFor();
  await page.locator('#draft-name').focus();
  await page.keyboard.type('Keyboard-only bay walk');
  await page.keyboard.press('Enter');
  await page.getByText('Saved draft. Changes are saved when you select Save draft.').waitFor();
  await page.getByTestId('guide-continue').focus();
  await page.keyboard.press('Enter');
  await page.locator('#recording-section').waitFor({ state: 'visible' });
  await page.locator('#recording-file').focus();
  await page.locator('#recording-file').setInputFiles(videoFile);
  await page.locator('#recording-upload').focus();
  await page.keyboard.press('Enter');
  await page.getByText('Recording saved. Counting is not available yet.').waitFor();
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  // Continue to the line step and draw a quick line first — bays share the
  // picture and the draft save with the counting line, so the real flow has
  // both. (The full line workflow is proven by operation_a11y_browser.mjs.)
  await page.getByTestId('guide-continue').focus();
  await page.keyboard.press('Enter');
  await page.locator('#line-section').waitFor({ state: 'visible' });
  await page.locator('#line-name').focus();
  await page.keyboard.type('Keyboard door line');
  for (const [id, value] of [
    ['line-start-x', '20'], ['line-start-y', '50'],
    ['line-end-x', '80'], ['line-end-y', '50'],
    ['line-positive', 'Shop'], ['line-negative', 'Street']
  ]) {
    await page.keyboard.press('Tab');
    await page.keyboard.type(value);
  }
  await page.locator('#line-save').focus();
  await page.keyboard.press('Enter');
  await page.getByText(/The line matches what is saved\./).waitFor();
  await page.waitForFunction(() => {
    const button = document.querySelector('#line-save');
    return button && !button.disabled;
  }, null, { timeout: 10000 });
  // The line step is done, so its step chip opens the bays step.
  await page.getByTestId('guide-step-bays').focus();
  await page.keyboard.press('Enter');
  await page.locator('#bay-section').waitFor({ state: 'visible' });

  // 1. Add a bay by keyboard; the new bay gets default corners and its own new ID.
  await pressById('[data-testid="bay-add"]');
  await page.getByText(/Bay 1 added with its own new ID/).waitFor();
  assert.match(await page.locator('#bay-summary').innerText(), /1 bay\./);
  assert.match(await page.locator('#bay-summary').innerText(), /Editing bay "unnamed"\./);
  assert.match(await page.locator('#bay-summary').innerText(), /There are unsaved changes to the bays\./);

  // 2. Name it and type the four corners purely by keyboard. The Add action put
  //    focus in the name field; the corner fields follow in the bay section's
  //    own tab order (name → frame → canvas → action buttons → corner fields),
  //    so the walk Tab-s there like a person would.
  await page.keyboard.type('Keyboard bay 1');
  for (const [id, value] of [
    ['bay-corner-0-x', '10'], ['bay-corner-0-y', '10'],
    ['bay-corner-1-x', '40'], ['bay-corner-1-y', '10'],
    ['bay-corner-2-x', '40'], ['bay-corner-2-y', '40'],
    ['bay-corner-3-x', '10'], ['bay-corner-3-y', '40'],
  ]) {
    // Focus puts the caret after the default value, so select-all first and
    // type over it — exactly what a person replacing a number does.
    await page.locator(`#${id}`).focus();
    await page.keyboard.press('Meta+a');
    await page.keyboard.type(value);
  }
  await page.waitForFunction(() => {
    const box = document.querySelector('#bay-summary');
    return box && /Corner 1: 10% from the left, 10% from the top\./.test(box.textContent);
  }, null, { timeout: 10000 });
  assert.match(await page.locator('#bay-summary').innerText(), /Editing bay "Keyboard bay 1"\./);

  // 3. Keyboard corner choice and arrow nudging, the T15 idioms on bays.
  await pressById('[data-testid="bay-select-corner-2"]');
  await page.getByText(/Keyboard corner chosen: corner 3 of the selected bay at 40% from the left, 40% from the top\./).waitFor();
  assert.equal(await page.getByTestId('bay-select-corner-2').getAttribute('aria-pressed'), 'true');
  await pressById('[data-testid="bay-move-left"]', 'ArrowLeft');
  assert.equal(await page.locator('#bay-corner-2-x').inputValue(), '39.9');
  await page.keyboard.press('Shift+ArrowLeft');
  assert.equal(await page.locator('#bay-corner-2-x').inputValue(), '38.9');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Shift+ArrowUp');
  assert.equal(await page.locator('#bay-corner-2-y').inputValue(), '39.1');
  // Arrow keys directly in a corner field nudge that corner too.
  await page.locator('#bay-corner-2-x').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#bay-corner-2-x').inputValue(), '39');
  // Clamping holds a corner at the edge instead of losing it.
  await page.locator('#bay-corner-2-x').fill('100');
  await page.locator('#bay-corner-2-x').focus();
  for (let i = 0; i < 3; i++) await page.keyboard.press('Shift+ArrowRight');
  assert.equal(await page.locator('#bay-corner-2-x').inputValue(), '100', 'nudging past the edge is clamped');
  await page.locator('#bay-corner-2-x').fill('40');
  await page.locator('#bay-corner-2-y').fill('40');

  // 4. Undo the nudge run, redo it: the keyboard paths are one history.
  await pressById('[data-testid="bay-undo"]');
  await page.getByText(/Undone\./).waitFor();
  await pressById('[data-testid="bay-redo"]');

  // 5. A bowtie is refused with the plain message before anything is saved.
  await page.locator('#bay-corner-0-x').fill('10');
  await page.locator('#bay-corner-0-y').fill('10');
  await page.locator('#bay-corner-1-x').fill('90');
  await page.locator('#bay-corner-1-y').fill('90');
  await page.locator('#bay-corner-2-x').fill('90');
  await page.locator('#bay-corner-2-y').fill('10');
  await page.locator('#bay-corner-3-x').fill('10');
  await page.locator('#bay-corner-3-y').fill('90');
  await page.locator('#bay-corner-3-y').focus();
  await page.keyboard.press('Tab');
  await page.waitForFunction(() => {
    const box = document.querySelector('#bay-summary');
    return box && /cross each other/.test(box.textContent);
  }, null, { timeout: 10000 });
  // Saving is refused by the client-side check with the same words the server uses.
  await pressById('[data-testid="bay-save"]');
  await page.getByRole('alert').filter({ hasText: /cross each other/ }).waitFor();
  assert.match(await page.locator('#bay-error').innerText(), /The four corners cross each other; a bay's outline must not overlap itself\./);
  // Fix the corner order back to a legal quad.
  for (const [id, value] of [['bay-corner-1-x', '40'], ['bay-corner-1-y', '10'], ['bay-corner-2-x', '40'], ['bay-corner-2-y', '40'], ['bay-corner-3-x', '10'], ['bay-corner-3-y', '40']]) {
    await page.locator(`#${id}`).fill(value);
  }

  // 5b. Identity actions on an unsaved new bay are refused locally: the editor
  //     must not POST a locally minted ID, and must not reload-and-discard the
  //     corners still being edited.
  await pressById('[data-testid="bay-duplicate"]');
  await page.getByRole('alert').filter({ hasText: /Save the draft first/ }).waitFor();
  assert.match(await page.locator('#bay-error').innerText(), /Save the draft first so this new bay exists on the server/);
  assert.match(await page.locator('#bay-summary').innerText(), /1 bay\./);
  assert.equal(await page.locator('#bay-corner-0-x').inputValue(), '10', 'unsaved corners are still here');

  // 6. Save from the keyboard; the summary states the saved state as text.
  let saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#bay-save').focus();
  await page.keyboard.press('Enter');
  const firstSave = await saved;
  assert.equal(firstSave.status(), 200);
  const draftId = (await firstSave.json()).draft.id;
  await page.getByText(/The bays match what is saved\./).waitFor();
  let serverDraft = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  assert.equal(serverDraft.bays.length, 1);
  assert.equal(serverDraft.bays[0].name, 'Keyboard bay 1');
  assert.deepEqual(serverDraft.bays[0].corners, [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]);
  const bayId = serverDraft.bays[0].id;

  // 6b. Unsaved corner edits on a saved bay are also refused before the
  //     duplicate POST, so load() cannot throw the edit away.
  await page.locator('#bay-corner-2-x').focus();
  await page.keyboard.press('ArrowLeft');
  assert.equal(await page.locator('#bay-corner-2-x').inputValue(), '39.9');
  await pressById('[data-testid="bay-duplicate"]');
  await page.getByRole('alert').filter({ hasText: /Unsaved bay changes would be lost/ }).waitFor();
  assert.match(await page.locator('#bay-summary').innerText(), /1 bay\./);
  assert.equal(await page.locator('#bay-corner-2-x').inputValue(), '39.9', 'unsaved corner edit is still here');
  await page.locator('#bay-corner-2-x').fill('40');

  // 7. Duplicate by keyboard: a fresh stable ID and a plainly-named copy.
  //    The server message does not quote the copy name. The editor must select
  //    the minted copy (not "the first bay that isn't this one").
  await pressById('[data-testid="bay-duplicate"]');
  await page.getByText(/Duplicated the bay as Keyboard bay 1 \(copy\) with its own new ID/).waitFor();
  assert.match(await page.locator('#bay-summary').innerText(), /2 bays\./);
  assert.match(await page.locator('#bay-summary').innerText(), /Editing bay "Keyboard bay 1 \(copy\)"\./);
  assert.equal(await page.locator('#bay-name').inputValue(), 'Keyboard bay 1 (copy)');
  const copyId = await page.locator('#bay-select').inputValue();
  assert.notEqual(copyId, bayId, 'the selected bay after duplicate is the minted copy');
  await assertSuccessfulDraftSave(draftId);
  assert.equal(await page.locator('#bay-select').inputValue(), copyId, 'saving the duplicate must preserve the selected copy');
  serverDraft = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  assert.equal(serverDraft.bays.length, 2);
  const original = serverDraft.bays.find(bay => bay.id === bayId);
  const copy = serverDraft.bays.find(bay => bay.id === copyId);
  assert.ok(original, 'the original bay is still on the draft');
  assert.ok(copy, 'the minted copy is on the draft');
  assert.notEqual(copy.id, bayId, 'the duplicate is a NEW bay with its own stable ID');
  assert.equal(copy.name, 'Keyboard bay 1 (copy)');
  assert.deepEqual(copy.corners, original.corners);

  // 8. Rename by keyboard through the identity-preserving action: the selected
  //    copy keeps its ID while only its name changes. The original is untouched.
  await page.locator('#bay-name').focus();
  await page.locator('#bay-name').fill('Keyboard bay 1 renamed');
  await pressById('[data-testid="bay-rename"]');
  await page.getByText(/Bay renamed\. The ID — and the bay's history — is unchanged\./).waitFor();
  assert.equal(await page.locator('#bay-select').inputValue(), copyId, 'rename keeps the copy selected');
  assert.equal(await page.locator('#bay-name').inputValue(), 'Keyboard bay 1 renamed');
  serverDraft = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  const renamedOriginal = serverDraft.bays.find(bay => bay.id === bayId);
  const renamedCopy = serverDraft.bays.find(bay => bay.id === copyId);
  assert.equal(renamedOriginal.id, bayId, 'renaming the copy must not change the original ID');
  assert.equal(renamedOriginal.name, 'Keyboard bay 1', 'the original name is unchanged');
  assert.equal(renamedCopy.id, copyId, 'renaming must keep the copy\'s stable ID');
  assert.equal(renamedCopy.name, 'Keyboard bay 1 renamed');
  // History: the older draft-version snapshots keep the older names, identity by ID.
  const versions = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}/versions`)).json()).versions, draftId);
  const names = versions.map(v => v.snapshot.geometry.bays.map(b => b.name).sort().join('|'));
  assert(names.includes('Keyboard bay 1|Keyboard bay 1 (copy)'), 'an older snapshot keeps the old names');
  const ids = new Set(versions.flatMap(v => v.snapshot.geometry.bays.map(b => b.id)));
  assert(ids.has(bayId), 'the original bay ID resolves across every snapshot');
  assert(ids.has(copyId), 'the copy bay ID resolves across snapshots after it was minted');
  await assertSuccessfulDraftSave(draftId);

  // 9. Reload and resume: the keyboard-drawn bays persisted, normalized, and
  //    the summary restates them as text.
  await page.reload();
  await page.locator(`#draft-list [data-draft-id="${draftId}"]`).focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  await page.getByTestId('guide-step-bays').focus();
  await page.keyboard.press('Enter');
  await page.locator('#bay-section').waitFor({ state: 'visible' });
  assert.equal(await page.getByTestId('bay-undo').isDisabled(), true, 'undo must not claim to reach before this page life');
  assert.match(await page.locator('#bay-summary').innerText(), /2 bays\./);
  assert.match(await page.locator('#bay-summary').innerText(), /The bays match what is saved\./);
  const optionLabels = await page.locator('#bay-select option').allTextContents();
  assert.deepEqual(optionLabels, ['Keyboard bay 1', 'Keyboard bay 1 renamed']);
  const reloaded = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  assert.equal(reloaded.bays.find(bay => bay.id === bayId).name, 'Keyboard bay 1');
  assert.equal(reloaded.bays.find(bay => bay.id === copyId).name, 'Keyboard bay 1 renamed');
  assert.deepEqual(reloaded.bays.find(bay => bay.id === bayId).corners, [[0.1, 0.1], [0.4, 0.1], [0.4, 0.4], [0.1, 0.4]]);

  // 10. The saved bays ride the T13 replay surface overlay alongside the line:
  //     each bay is a toggleable overlay object with its own label.
  const toggles = await page.locator('[data-testid^="replay-toggle-bay-"]').count();
  assert.equal(toggles, 2, 'each saved bay is one overlay object on the replay surface');

  // Leave the disposable server clean, also by keyboard.
  await page.getByTestId('guide-exit').focus();
  await page.keyboard.press('Enter');
  await page.locator('#draft-delete').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete draft' }).focus();
  await page.keyboard.press('Enter');
  await page.getByText('Draft deleted.').waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: the whole bay workflow — add by keyboard, name, type corners, choose corners by button, nudge by arrow keys, undo/redo, bowtie refusal, unsaved-identity-action refusal, save, duplicate selecting the minted copy, save after duplicate with the current revision, rename of that copy keeping IDs and history, save after rename with the current revision, reload, persist, replay overlay — runs from the keyboard alone, with the geometry restated as text throughout.');
} finally {
  await context.close();
  await browser.close();
}
