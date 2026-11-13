/**
 * The action lifecycle components, exercised for real.
 *
 * The server renders the dialog and the toast region; this harness puts that
 * rendered markup in front of the real runtimes and checks what a reader would
 * actually meet: the answer the dialog gives, the words it uses, the typing
 * rule, the loading and error states, and - the one that matters most - that a
 * message can never become markup.
 *
 * Usage:
 *     node tests/js/action_lifecycle_smoke.mjs --dialog-html=/tmp/dialog.html
 */

import { readFileSync } from 'node:fs';

const args = Object.fromEntries(process.argv.slice(2).map((arg) => {
    const [key, ...rest] = arg.replace(/^--/, '').split('=');
    return [key, rest.join('=')];
}));

const failures = [];
const notes = [];
let checks = 0;

function check(what, condition, detail = '') {
    checks += 1;
    if (!condition) {
        failures.push(`${what}${detail ? ` -- ${detail}` : ''}`);
    }
}

// ---------------------------------------------------------------------------
// The stub DOM, and the real runtimes
// ---------------------------------------------------------------------------
import { FakeElement, installDom, loadRuntime, treeFromHtml } from './_dom_stub.mjs';

const {documentRoot, document, shown, globalThisRef} = installDom();
globalThisRef.setTimeout = setTimeout;
globalThisRef.console = console;
globalThisRef.confirm = () => { throw new Error('the browser dialog must not be used'); };

// ---------------------------------------------------------------------------
// The real runtimes, and the server's own rendered dialog
// ---------------------------------------------------------------------------
for (const file of ['static/js/modules/core/confirm-dialog.js',
                    'static/js/modules/core/toast.js']) {
    loadRuntime(file);
}
check('the dialog runtime is defined', typeof globalThisRef.ConfirmDialog === 'object');
check('the toast runtime is defined', typeof globalThisRef.Toast === 'object');

const dialogHtml = readFileSync(args['dialog-html'], 'utf8');
documentRoot.appendChild(treeFromHtml(dialogHtml));

const dialog = documentRoot.querySelector('[data-confirm-dialog]');
check('the server rendered a dialog component', dialog !== null);
check('the dialog declares the action it is asking about',
      dialog.getAttribute('data-confirm-action') === 'files.delete_selected',
      dialog.getAttribute('data-confirm-action'));
check('the dialog is a modal dialog with a name and a description',
      dialog.getAttribute('role') === 'dialog'
      && dialog.getAttribute('aria-modal') === 'true'
      && dialog.getAttribute('aria-labelledby') !== null
      && dialog.getAttribute('aria-describedby') !== null);
check('the dialog carries the Action Registry confirmation key',
      dialog.getAttribute('data-confirm-key') === 'action.files.delete_selected.confirm');

// ---------------------------------------------------------------------------
// Asking
// ---------------------------------------------------------------------------
async function answer(how, spec) {
    const promise = globalThisRef.ConfirmDialog.request(spec);
    if (how === 'accept') {
        dialog.querySelector('[data-confirm-accept]')
            .closest('[data-confirm-dialog]') ?? null;
        dialog.dispatch('click', {target: dialog.querySelector('[data-confirm-accept]')});
    } else if (how === 'cancel') {
        dialog.dispatch('click', {target: dialog.querySelector('[data-confirm-cancel]')});
    } else {
        dialog.dispatch('hidden.bs.modal');
    }
    return promise;
}

const accepted = await answer('accept', {
    action: 'files.delete_selected',
    title: 'Delete 27 files?',
    message: 'This permanently removes the selected records.',
    scope: '27 files',
    confirmLabel: 'Delete 27 Files',
    cancelLabel: 'Keep them',
    dangerous: true,
});
check('accepting returns true', accepted === true);
check('the scope the page passed is shown',
      dialog.querySelector('[data-confirm-scope]').textContent.includes('27 files'));
check('the accepting button carries the page wording',
      dialog.querySelector('[data-confirm-accept]').textContent.includes('Delete 27 Files'));
check('the cancelling button carries the page wording',
      dialog.querySelector('[data-confirm-cancel]').textContent.includes('Keep them'));
check('a dangerous action is drawn as one',
      dialog.getAttribute('data-confirm-dangerous') === 'true'
      && dialog.querySelector('[data-confirm-accept]').classList.contains('btn-danger'));

const refused = await answer('cancel', {action: 'files.delete_selected'});
check('cancelling returns false', refused === false);
const dismissed = await answer('dismiss', {action: 'files.delete_selected'});
check('dismissing the dialog is not consent', dismissed === false);

// Loading and error are states of the answer, not new dialogs.
globalThisRef.ConfirmDialog.setLoading(dialog, true);
check('loading disables the accepting button',
      dialog.querySelector('[data-confirm-accept]').getAttribute('aria-disabled') === 'true');
globalThisRef.ConfirmDialog.setLoading(dialog, false);
globalThisRef.ConfirmDialog.setError(dialog, 'The export failed.');
check('an error is shown in the dialog, not in the console',
      dialog.querySelector('[data-confirm-error]').textContent.includes('The export failed.'));
globalThisRef.ConfirmDialog.setError(dialog, '');

// The typed-confirmation rule. The question stays open while it is checked,
// and is dismissed at the end - dismissing is never consent.
const typed = globalThisRef.ConfirmDialog.request({
    action: 'files.delete_selected', typed: 'DELETE', dangerous: true,
});
const acceptButton = dialog.querySelector('[data-confirm-accept]');
const field = dialog.querySelector('[data-confirm-typed-input]');
check('a typed confirmation starts locked', acceptButton.disabled === true);
field.value = 'delete';
if (field.oninput) field.oninput();
check('the wrong word keeps it locked', acceptButton.disabled === true);
field.value = 'DELETE';
if (field.oninput) field.oninput();
check('the right word unlocks it', acceptButton.disabled === false);
dialog.dispatch('hidden.bs.modal');
check('an unanswered typed confirmation resolves as a refusal',
      (await typed) === false);

// A page with no dialog must not invent one.
const orphan = documentRoot.querySelector('[data-confirm-dialog]');
orphan.remove();
const noDialog = await globalThisRef.ConfirmDialog.request({action: 'x.y'});
check('without the component the answer is a refusal, not a browser dialog',
      noDialog === false);
documentRoot.appendChild(treeFromHtml(dialogHtml));

// ---------------------------------------------------------------------------
// Telling the reader what happened
// ---------------------------------------------------------------------------
const region = documentRoot.appendChild(new FakeElement('div', {'data-toast-region': ''}));
globalThisRef.Toast.success('27 files categorised.');
const success = region.querySelector('[data-toast]');
check('a success toast appears in the shared region', success !== null);
check('a success is announced politely',
      success.getAttribute('role') === 'status'
      && success.getAttribute('aria-live') === 'polite');
check('a success says what happened',
      success.textContent.includes('27 files categorised.'));

globalThisRef.Toast.error('Export failed.', {correlationId: 'ERR-20260921-0042'});
const failure = region.querySelectorAll('[data-toast]').pop();
check('a failure is announced assertively', failure.getAttribute('role') === 'alert');
check('a failure carries the correlation id and nothing technical',
      failure.textContent.includes('ERR-20260921-0042')
      && !/Traceback|psycopg2|SELECT |C:\\\\/.test(failure.textContent));

const before = region.querySelectorAll('[data-toast]').length;
globalThisRef.Toast.info('<b>not markup</b>');
const message = region.querySelectorAll('[data-toast-message]').pop();
check('a message is text, never markup',
      message.textContent === '<b>not markup</b>'
      && message.children.length === 0);
notes.push(`toasts in the region: ${before + 1}`);

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
