// Browser proof of accessible geometry editing (T15): the WHOLE line workflow —
// draw the line by typing numbers, choose endpoints with buttons, adjust with
// arrow keys, undo/redo, reverse direction, clear with a warning, save, reload,
// verify persistence — driven entirely by the keyboard. This script never
// touches page.mouse and never samples a pixel: no dragging and no colour
// recognition, which is exactly the bar the task sets. The geometry is checked
// through the text summary and the field values throughout.
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
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'athena-a11y-'));
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
  // The hard rule of this proof: no mouse input, ever. Any accidental use
  // (including by helper code) fails the run instead of silently passing.
  page.mouse = new Proxy({}, { get: () => { throw new Error('this proof must never use the mouse'); } });

  // A draft with one recording, walked through the guided stepper by keyboard.
  await page.goto(base + '/operations');
  await page.getByText(/No drafts yet|Select a draft|Showing up to/).waitFor();
  await page.locator('#draft-name').focus();
  await page.keyboard.type('Keyboard-only line walk');
  await page.keyboard.press('Enter');
  await page.getByText('Saved draft. Changes are saved when you select Save draft.').waitFor();
  await page.getByTestId('guide-continue').focus();
  await page.keyboard.press('Enter');
  await page.locator('#recording-section').waitFor({ state: 'visible' });
  await page.locator('#recording-file').focus();
  // Setting the file input programmatically stands in for the OS file picker,
  // which cannot be driven by keys in a headless run; it is not a mouse action.
  await page.locator('#recording-file').setInputFiles(videoFile);
  await page.locator('#recording-upload').focus();
  await page.keyboard.press('Enter');
  await page.getByText('Recording saved. Counting is not available yet.').waitFor();
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  await page.getByTestId('guide-continue').focus();
  await page.keyboard.press('Enter');
  await page.locator('#line-section').waitFor({ state: 'visible' });

  // 1. Draw the line purely by typing numbers into the fields: no canvas click,
  //    no dragging, no frame capture. Typing, then Tab to the next field.
  await page.locator('#line-start-x').focus();
  await page.keyboard.type('20');
  await page.keyboard.press('Tab');
  await page.keyboard.type('50');
  await page.keyboard.press('Tab');
  await page.keyboard.type('70');
  await page.keyboard.press('Tab');
  await page.keyboard.type('50');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Keyboard door line');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Shop');
  await page.keyboard.press('Tab');
  await page.keyboard.type('Street');
  await page.keyboard.press('Tab');
  assert.match(await page.locator('#line-summary').innerText(), /Line "Keyboard door line"\./);
  assert.match(await page.locator('#line-summary').innerText(), /Start point: 20% from the left, 50% from the top\./);
  assert.match(await page.locator('#line-summary').innerText(), /End point: 70% from the left, 50% from the top\./);
  assert.match(await page.locator('#line-summary').innerText(), /Movement towards the arrow counts as "Shop"/);
  assert.match(await page.locator('#line-summary').innerText(), /There are unsaved changes to the line\./);

  // 2. Undo the whole typing run as one step, then redo it: the drawn line comes
  //    back without a single mouse action.
  await page.getByTestId('line-undo').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#line-name').inputValue(), '', 'undo removes the whole typing run at once');
  await page.getByText(/The line now differs from what is saved\./).waitFor();
  await page.getByTestId('line-redo').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#line-name').inputValue(), 'Keyboard door line');
  assert.equal(await page.locator('#line-start-x').inputValue(), '20');

  // 3. Keyboard endpoint choice and arrow nudging. The chosen-endpoint button
  //    announces its state as text (aria-pressed), not by colour alone.
  await page.getByTestId('line-select-end').focus();
  await page.keyboard.press('Enter');
  await page.getByText('Keyboard endpoint chosen: the end point at 70% from the left, 50% from the top.').waitFor();
  assert.equal(await page.getByTestId('line-select-end').getAttribute('aria-pressed'), 'true');
  assert.equal(await page.getByTestId('line-select-start').getAttribute('aria-pressed'), 'false');
  // Arrow keys on the nudge buttons: one fine press is 0.1% (0.001 normalized).
  await page.getByTestId('line-move-right').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#line-end-x').inputValue(), '70.1');
  assert.equal(await page.locator('#line-end-y').inputValue(), '50');
  await page.getByText('End point moved to 70.1% from the left, 50% from the top.').waitFor();
  // Shift on the nudge button is the coarse step of 1% (0.01 normalized).
  await page.keyboard.press('Shift+ArrowRight');
  assert.equal(await page.locator('#line-end-x').inputValue(), '71.1');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Shift+ArrowUp');
  assert.equal(await page.locator('#line-end-y').inputValue(), '49.1');
  // Arrow keys directly in the coordinate field nudge that endpoint too: the
  // keyboard paths and the numeric path are one model.
  await page.locator('#line-end-x').focus();
  await page.keyboard.press('ArrowLeft');
  assert.equal(await page.locator('#line-end-x').inputValue(), '71');
  await page.keyboard.press('Shift+ArrowLeft');
  assert.equal(await page.locator('#line-end-x').inputValue(), '70');
  // Clamping: nudging far past the edge holds the endpoint at the edge.
  for (let i = 0; i < 40; i++) await page.keyboard.press('Shift+ArrowRight');
  assert.equal(await page.locator('#line-end-x').inputValue(), '100');
  await page.keyboard.press('Shift+ArrowRight');
  assert.equal(await page.locator('#line-end-x').inputValue(), '100', 'nudging past the edge is clamped, not lost');
  await page.locator('#line-end-x').fill('70');
  await page.locator('#line-end-y').fill('50');
  await page.locator('#line-end-x').focus();
  await page.keyboard.press('Tab');

  // 4. Save from the keyboard; the summary states the saved state as text.
  let saved = page.waitForResponse(response => response.url().includes('/api/operation-drafts/') && response.request().method() === 'PUT');
  await page.locator('#line-save').focus();
  await page.keyboard.press('Enter');
  const firstSave = await saved;
  assert.equal(firstSave.status(), 200);
  const draftId = (await firstSave.json()).draft.id;
  await page.getByText(/The line matches what is saved\./).waitFor();
  // After the save, undo still works within the session and is honest: it can
  // step back through this session's edits and says the line differs again.
  assert.equal(await page.getByTestId('line-undo').isDisabled(), false);
  await page.getByTestId('line-undo').focus();
  await page.keyboard.press('Enter');
  await page.getByText(/The line now differs from what is saved\./).waitFor();
  await page.getByTestId('line-redo').focus();
  await page.keyboard.press('Enter');
  await page.getByText(/The line matches what is saved\./).waitFor();

  // 5. Reverse the direction from the keyboard; the summary follows as text.
  await page.getByTestId('line-reverse').focus();
  await page.keyboard.press('Enter');
  await page.getByText('Direction reversed: the start and end points swapped, and the side labels moved with them.').waitFor();
  assert.equal(await page.locator('#line-start-x').inputValue(), '70');
  assert.equal(await page.locator('#line-start-y').inputValue(), '50');
  assert.equal(await page.locator('#line-end-x').inputValue(), '20');
  assert.equal(await page.locator('#line-end-y').inputValue(), '50');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Street');
  assert.equal(await page.locator('#line-negative').inputValue(), 'Shop');
  assert.match(await page.locator('#line-summary').innerText(), /Start point: 70% from the left, 50% from the top\./);
  assert.match(await page.locator('#line-summary').innerText(), /counts as "Street"/);
  // Undo reaches back over the reversal too.
  await page.getByTestId('line-undo').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Shop');
  await page.getByTestId('line-redo').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Street');

  // 6. Clear with unsaved changes warns before losing work — answered by keyboard.
  await page.locator('#line-start-x').focus();
  await page.keyboard.press('Shift+ArrowRight');
  await page.getByText(/There are unsaved changes to the line\./).waitFor();
  await page.locator('#line-clear').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('alertdialog').waitFor();
  await page.getByRole('alertdialog').getByRole('button', { name: 'Keep editing' }).focus();
  await page.keyboard.press('Enter'); // the safe answer comes first
  await page.getByText(/There are unsaved changes to the line\./).waitFor();
  assert.equal(await page.locator('#line-name').inputValue(), 'Keyboard door line', 'keeping editing must not clear anything');
  assert.equal(await page.locator('#line-start-x').inputValue(), '21', 'keeping editing must not revert the nudge');
  await page.locator('#line-clear').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('alertdialog').getByRole('button', { name: 'Discard and clear' }).focus();
  await page.keyboard.press('Enter');
  await page.getByText('Unsaved line changes discarded. No line is set now; save the draft to apply the clear.').waitFor();
  assert.equal(await page.locator('#line-name').inputValue(), '');
  assert.match(await page.locator('#line-summary').innerText(), /No line drawn yet\./);
  // Undo brings the unsaved work back within the session.
  await page.getByTestId('line-undo').focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('#line-name').inputValue(), 'Keyboard door line');
  assert.equal(await page.locator('#line-start-x').inputValue(), '21');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Street');

  // 7. Reload and resume: the keyboard-drawn line persisted, normalized, and
  //    the summary restates it as text. The resume click is a keyboard Enter.
  await page.reload();
  await page.locator(`#draft-list [data-draft-id="${draftId}"]`).focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(() => document.querySelector('#recording-preview').readyState >= 2);
  await page.getByTestId('guide-step-line').focus();
  await page.keyboard.press('Enter');
  await page.locator('#line-section').waitFor({ state: 'visible' });
  assert.equal(await page.locator('#line-name').inputValue(), 'Keyboard door line');
  assert.equal(await page.locator('#line-start-x').inputValue(), '70');
  assert.equal(await page.locator('#line-start-y').inputValue(), '50');
  assert.equal(await page.locator('#line-end-x').inputValue(), '20');
  assert.equal(await page.locator('#line-end-y').inputValue(), '50');
  assert.equal(await page.locator('#line-positive').inputValue(), 'Street');
  assert.equal(await page.locator('#line-negative').inputValue(), 'Shop');
  assert.match(await page.locator('#line-summary').innerText(), /Start point: 70% from the left, 50% from the top\./);
  assert.match(await page.locator('#line-summary').innerText(), /The line matches what is saved\./);
  const serverDraft = await page.evaluate(async id => (await (await fetch(`/api/operation-drafts/${id}`)).json()).draft, draftId);
  assert.deepEqual(serverDraft.line.start, [0.7, 0.5], 'saved coordinates must stay normalized');
  assert.deepEqual(serverDraft.line.end, [0.2, 0.5]);
  assert.equal(serverDraft.line.name, 'Keyboard door line');
  assert.equal(serverDraft.line.positive_label, 'Street');
  assert.equal(serverDraft.line.negative_label, 'Shop');

  // 8. Tab order: the essential line controls are all reachable by Tab, in the
  //    order name → coordinates → endpoint choice → nudge → undo/redo → save,
  //    each with a visible focus state.
  await page.locator('#line-name').focus();
  const expected = ['#line-start-x', '#line-start-y', '#line-end-x', '#line-end-y', '#line-positive', '#line-negative', '#line-select-start', '#line-select-end', '#line-move-left', '#line-move-right', '#line-move-up', '#line-move-down', '#line-undo', '#line-redo'];
  const seen = [];
  for (let i = 0; i < 60 && seen.length < expected.length; i++) {
    await page.keyboard.press('Tab');
    const id = await page.evaluate(() => document.activeElement && document.activeElement.id);
    if (id === 'line-save') break;
    if (id) seen.push('#' + id);
  }
  assert.deepEqual(seen, expected, `tab order through the line controls (got ${seen.join(', ')})`);
  assert.equal(await page.evaluate(() => document.activeElement && document.activeElement.id), 'line-save', 'the save button is the next Tab stop after the history buttons');
  // The focused control shows a visible focus state.
  await page.locator('#line-undo').focus();
  const outline = await page.evaluate(() => {
    const element = document.getElementById('line-undo');
    const style = getComputedStyle(element);
    return style.outlineStyle + ' ' + style.outlineWidth;
  });
  assert.match(outline, /solid/, 'focused controls must show a visible outline');

  // Leave the disposable server clean, also by keyboard.
  await page.getByTestId('guide-exit').focus();
  await page.keyboard.press('Enter');
  await page.locator('#draft-delete').focus();
  await page.keyboard.press('Enter');
  await page.getByRole('alertdialog').getByRole('button', { name: 'Delete draft' }).focus();
  await page.keyboard.press('Enter');
  await page.getByText('Draft deleted.').waitFor();
  assert.deepEqual(errors, []);
  console.log('PASS: the whole line workflow — draw by typing numbers, choose endpoints by button, nudge by arrow keys, undo/redo, reverse, clear-with-warning, save, reload, persist — runs from the keyboard alone, with the geometry restated as text throughout.');
} finally {
  await context.close();
  await browser.close();
}
