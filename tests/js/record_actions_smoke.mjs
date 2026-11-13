/**
 * The record action surface, exercised against the real runtime.
 *
 * The server prepares a record's actions, the component draws them, and this
 * module runs the part a server cannot: the question, the busy state, the
 * outcome. The harness drives the shipped code - never a copy of it - and
 * checks the boundary as well as the behaviour:
 *
 * * the page's handler is the only thing that runs the operation;
 * * a question that cannot be asked is not answered (no dialog on the page
 *   means no action, and never a browser `confirm`);
 * * a refused action is not a click away, and a disabled one says why;
 * * every action ends visibly, and a thrown handler leaks nothing.
 *
 * Usage:
 *     node tests/js/record_actions_smoke.mjs
 */

import { installDom, loadRuntime, treeFromHtml } from './_dom_stub.mjs';

const failures = [];
const notes = [];
let checks = 0;

function check(what, condition, detail = '') {
    checks += 1;
    if (!condition) {
        failures.push(`${what}${detail ? ` -- ${detail}` : ''}`);
    }
}

const { documentRoot, globalThisRef } = installDom();
globalThisRef.setTimeout = setTimeout;
globalThisRef.console = console;
let navigatedTo = null;
globalThisRef.location = {set href(value) { navigatedTo = value; }};

for (const file of ['static/js/modules/core/confirm-dialog.js',
                    'static/js/modules/core/toast.js',
                    'static/js/modules/core/record-actions.js']) {
    loadRuntime(file);
}
check('the record-action runtime is defined',
      typeof globalThisRef.RecordActions === 'object');
check('it exposes the frozen-shaped API and nothing more',
      JSON.stringify(Object.keys(globalThisRef.RecordActions).sort())
      === JSON.stringify(['bind', 'bound', 'setBusy', 'setState']),
      Object.keys(globalThisRef.RecordActions).join(','));

// ---------------------------------------------------------------------------
// The surface a server would have rendered for one record
// ---------------------------------------------------------------------------
const SURFACE = `
<div class="record-actions" id="fileRecordActions" role="toolbar"
     aria-label="Actions for this file" aria-busy="false" data-record-actions>
  <a class="btn btn-sm btn-primary" href="/api/file/7/original/content"
     target="_blank" data-record-action="files.view_original"
     data-record-action-label="View Original">
    <i class="bi bi-file-earmark-image me-1"></i>View Original</a>
  <a class="btn btn-sm btn-outline-secondary disabled" href="/x"
     data-record-action="files.download_original"
     data-record-action-label="Download Original"
     data-disabled-reason="original_missing" aria-disabled="true">Download Original</a>
  <button type="button" class="btn btn-sm btn-outline-secondary"
          data-record-action="files.export_content"
          data-record-action-label="Export Extracted Text"
          data-record-endpoint="/api/files/7/export" data-record-method="GET"
          data-record-success="The extracted text was exported."
          data-record-failure="The extracted text could not be exported.">
    <span class="spinner-border spinner-border-sm me-1 d-none"
          role="status" aria-hidden="true" data-record-action-spinner></span>
    Export Extracted Text</button>
  <button type="button" class="btn btn-sm btn-danger"
          data-record-action="files.delete" data-record-action-label="Delete"
          data-record-endpoint="/file/7/delete" data-record-method="POST"
          data-confirm-key="action.files.delete.confirm"
          data-confirm-title="Delete this file?"
          data-confirm-message="This permanently removes the record."
          data-confirm-accept-label="Delete file"
          data-confirm-cancel-label="Keep it"
          data-confirm-dangerous="true"
          data-record-success="The record was deleted."
          data-record-failure="The record could not be deleted.">
    <span class="spinner-border spinner-border-sm me-1 d-none"
          role="status" aria-hidden="true" data-record-action-spinner></span>
    Delete</button>
  <!-- The surface is the server's markup: these are the two labels the
       component emits, and the harness checks they are the page's words. -->
  <span class="visually-hidden" data-record-actions-hidden></span>
</div>`;

const DIALOG = `
<div class="modal fade" id="fileDeleteDialog" role="dialog" aria-modal="true"
     data-confirm-dialog data-confirm-action="files.delete"
     data-confirm-key="action.files.delete.confirm"
     data-confirm-dangerous="true">
  <div class="modal-dialog"><div class="modal-content">
    <div class="modal-header"><h5 class="modal-title">Delete this file?</h5>
      <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
    <div class="modal-body">
      <p data-confirm-message>This permanently removes the record.</p>
      <p class="d-none" data-confirm-scope-line><strong data-confirm-scope></strong></p>
      <div class="alert alert-danger d-none" data-confirm-error></div>
    </div>
    <div class="modal-footer">
      <button type="button" data-bs-dismiss="modal" data-confirm-cancel>Keep it</button>
      <button type="button" data-confirm-accept>
        <span class="spinner-border d-none" data-confirm-spinner></span>
        <span data-confirm-accept-label>Delete file</span></button>
    </div>
  </div></div>
</div>
<div class="toast-container" id="toastRegion" role="status" aria-live="polite"
     aria-atomic="false" data-toast-region></div>`;

documentRoot.appendChild(treeFromHtml(SURFACE));
documentRoot.appendChild(treeFromHtml(DIALOG));

const surface = documentRoot.querySelector('[id="fileRecordActions"]');
const dialog = documentRoot.querySelector('[data-confirm-dialog]');
const region = documentRoot.querySelector('[id="toastRegion"]');
const deleteButton = surface.querySelector('[data-record-action="files.delete"]');
const exportButton = surface.querySelector('[data-record-action="files.export_content"]');
const downloadLink = surface.querySelector('[data-record-action="files.download_original"]');

function click(node) {
    node.dispatch('click', {target: node});
}

/**
 * Let the promises the runtimes chain settle.
 *
 * A confirmation resolves through the dialog's own promise, then the page's
 * handler, then the toast: `await Promise.resolve()` is not a fixed number of
 * turns of that chain, so the harness waits on the timer queue instead.
 */
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function toasts() {
    return region.querySelectorAll('[data-toast]');
}

// ---------------------------------------------------------------------------
// Binding: the page supplies the operation, the runtime supplies the ceremony
// ---------------------------------------------------------------------------
const calls = [];
const handler = async (actionId, context) => {
    calls.push({actionId, tag: context.element.tagName});
    if (actionId === 'files.delete') {
        return {ok: true, handled: true};
    }
    return {ok: true, handled: true};
};

const unbind = globalThisRef.RecordActions.bind(surface, handler);
check('binding marks the surface as bound',
      globalThisRef.RecordActions.bound(surface) === true);

let secondBindRefused = false;
try {
    globalThisRef.RecordActions.bind(surface, handler);
} catch (error) {
    secondBindRefused = true;
}
check('one surface carries one handler', secondBindRefused);

// A link with no question: the runtime calls the page's handler and nothing else.
click(exportButton);
await settle();
check('the page handler is what runs the action',
      calls.length === 1 && calls[0].actionId === 'files.export_content',
      JSON.stringify(calls));
check('the runtime is told which element was used', calls[0].tag === 'BUTTON');
check('an action that worked ends in a toast',
      toasts().length === 1
      && toasts()[0].textContent.includes('The extracted text was exported.'));

// The surface never talks to the network itself.
check('the runtime contains no endpoint of its own',
      !/fetch\(|XMLHttpRequest|axios|\.post\(/.test(
          String(globalThisRef.RecordActions.bind)
          + String(globalThisRef.RecordActions.setBusy)),
      'the operation belongs to the page');

// ---------------------------------------------------------------------------
// A disabled action is not a click away, and its reason is on the element
// ---------------------------------------------------------------------------
const before = calls.length;
click(downloadLink);
await settle();
check('a disabled action runs nothing', calls.length === before);
check('its reason is exposed to assistive technology',
      downloadLink.getAttribute('data-disabled-reason') === 'original_missing'
      && downloadLink.getAttribute('aria-disabled') === 'true');

// ---------------------------------------------------------------------------
// Confirmation: the question is asked, and the answer is the reader's
// ---------------------------------------------------------------------------
click(deleteButton);
await settle();
check('the dialog is the component the page rendered',
      globalThisRef.ConfirmDialog !== undefined
      && dialog.getAttribute('data-confirm-action') === 'files.delete',
      "the question is asked through the shared dialog, not window.confirm");

// Cancel: dismissing is never consent.
dialog.dispatch('click', {target: dialog.querySelector('[data-confirm-cancel]')});
await settle();
check('cancelling performs nothing',
      calls.filter((call) => call.actionId === 'files.delete').length === 0,
      JSON.stringify(calls));

// Accept: the page's handler runs, once, and the action ends visibly. The
// question is asked again, because a cancelled question is over.
click(deleteButton);
await settle();
const accept = dialog.querySelector('[data-confirm-accept]');
dialog.dispatch('click', {target: accept});
await settle();
check('accepting runs the page handler exactly once',
      calls.filter((call) => call.actionId === 'files.delete').length === 1,
      JSON.stringify(calls));
check('and the action ends in a toast',
      toasts().some((toast) => toast.textContent.includes('The record was deleted.')));
notes.push(`toasts in the region: ${toasts().length}`);

// ---------------------------------------------------------------------------
// A page with no dialog: refuse, never fall back to window.confirm
// ---------------------------------------------------------------------------
let browserDialogUsed = false;
globalThisRef.confirm = () => { browserDialogUsed = true; return true; };
dialog.remove();
const orphanCallsBefore = calls.length;
click(deleteButton);
await settle();
check('without the dialog the action is refused, not run unconfirmed',
      calls.length === orphanCallsBefore);
check('and the browser dialog is still not used', browserDialogUsed === false);
documentRoot.appendChild(treeFromHtml(DIALOG));

// ---------------------------------------------------------------------------
// Busy state, failure, and a handler that throws
// ---------------------------------------------------------------------------
const spinner = exportButton.querySelector('[data-record-action-spinner]');
globalThisRef.RecordActions.setBusy(surface, 'files.export_content', true);
check('a busy action keeps its label and shows the spinner',
      spinner.classList.contains('d-none') === false
      && exportButton.getAttribute('aria-busy') === 'true');
globalThisRef.RecordActions.setBusy(surface, 'files.export_content', false);
check('and the spinner goes away when it is done',
      spinner.classList.contains('d-none') === true
      && exportButton.getAttribute('aria-busy') === 'false');

const failing = async () => ({ok: false});
const fresh = treeFromHtml(SURFACE);
documentRoot.appendChild(fresh);
const freshSurface = fresh;
globalThisRef.RecordActions.bind(freshSurface, failing);
const beforeFailure = toasts().length;
click(freshSurface.querySelector('[data-record-action="files.export_content"]'));
await settle();
const failure = toasts()[toasts().length - 1];
check('a failed action says so, with the page\'s own words',
      toasts().length === beforeFailure + 1
      && failure.textContent.includes('could not be exported'));
check('a failure is announced assertively', failure.getAttribute('role') === 'alert');
check('and carries nothing technical', !/Traceback|psycopg2|SELECT |C:\\\\/.test(
    failure.textContent));

const throwing = treeFromHtml(SURFACE);
documentRoot.appendChild(throwing);
globalThisRef.RecordActions.bind(throwing, async () => { throw new Error('boom'); });
const beforeThrow = toasts().length;
click(throwing.querySelector('[data-record-action="files.export_content"]'));
await settle();
check('a thrown handler still ends visibly, without the exception text',
      toasts().length === beforeThrow + 1
      && !/boom/.test(toasts()[toasts().length - 1].textContent));

// ---------------------------------------------------------------------------
// State the page decides: the endpoint still decides, the button just says so
// ---------------------------------------------------------------------------
globalThisRef.RecordActions.setState(surface, 'files.delete',
                                     {enabled: false, reason: 'record_archived'});
check('a page can say an action is no longer available',
      deleteButton.getAttribute('aria-disabled') === 'true'
      && deleteButton.getAttribute('data-disabled-reason') === 'record_archived');
globalThisRef.RecordActions.setState(surface, 'files.delete', {enabled: true});
check('and can offer it again',
      deleteButton.getAttribute('aria-disabled') === 'false');

unbind();
check('unbinding is what the binding returns',
      globalThisRef.RecordActions.bound(surface) === false);
const afterUnbind = calls.length;
click(exportButton);
await settle();
check('nothing runs once the surface is unbound', calls.length === afterUnbind);

// ---------------------------------------------------------------------------
// Report
// ---------------------------------------------------------------------------
for (const note of notes) {
    console.log(`note: ${note}`);
}
if (failures.length) {
    failures.forEach((failure) => console.log(`FAIL ${failure}`));
    console.log(`${checks - failures.length}/${checks} checks passed`);
    process.exit(1);
}
console.log(`${checks}/${checks} checks passed`);
