/**
 * The single select/deselect toggle on the files list.
 *
 * The files action bar used to carry two buttons - "Select All" and
 * "Select None". They are one standout toggle now: it selects every file
 * when nothing is selected and clears the selection otherwise, and its
 * label, icon, aria-pressed flag and count badge always describe the
 * current selection. This harness drives the shipped module against the
 * stub DOM: toggle from empty, toggle from full, a single checkbox change
 * syncing the button, and the translated labels.
 */
import { installDom, treeFromHtml } from './_dom_stub.mjs';

let passed = 0;
let failed = 0;

function check(name, ok, detail = '') {
    if (ok) {
        passed += 1;
        console.log(`PASS ${name}`);
    } else {
        failed += 1;
        console.log(`FAIL ${name}${detail ? ' -- ' + detail : ''}`);
    }
}

const MODULE = 'file://' + new URL('../../static/js/modules/file-operations/file-selection.js', import.meta.url).pathname;

function mountPage(html) {
    const dom = installDom();
    const tree = treeFromHtml(html);
    for (const child of [...tree.children]) {
        dom.documentRoot.appendChild(child);
    }
    return dom;
}

const PAGE_HTML = `
<div id="app">
    <button class="btn-action btn-select-toggle" id="selectToggleBtn" type="button"
            aria-pressed="false">
        <i class="bi bi-check-square-fill select-toggle-icon" aria-hidden="true"></i>
        <span class="select-toggle-label">Select All</span>
        <span class="select-toggle-count" id="selectToggleCount" hidden>0</span>
    </button>
    <input type="checkbox" class="file-checkbox" value="1" id="cb1">
    <input type="checkbox" class="file-checkbox" value="2" id="cb2">
    <input type="checkbox" class="file-checkbox" value="3" id="cb3">
</div>
`;

mountPage(PAGE_HTML);
// Translated phrases, the way files-list-page.js merges the page block into
// window.translations: the toggle must use these, not invent English.
globalThis.window.translations = {
    selectAll: 'Alles auswählen',
    deselectAll: 'Auswahl aufheben',
};

const { toggleAllFilesSelection, updateSelectToggle, selectAllFiles, deselectAllFiles } =
    await import(MODULE);

// The page syncs the toggle with the (empty) selection on load.
updateSelectToggle();

const btn = globalThis.document.getElementById('selectToggleBtn');
const label = btn.querySelector('.select-toggle-label');
const icon = btn.querySelector('.select-toggle-icon');
const count = globalThis.document.getElementById('selectToggleCount');
const boxes = () => globalThis.document.querySelectorAll('.file-checkbox');

// --- initial state -------------------------------------------------------
check('button starts unpressed', btn.getAttribute('aria-pressed') === 'false');
check('button starts with the select label', label.textContent === 'Alles auswählen', label.textContent);
check('count badge hidden when nothing is selected', count.hidden === true);

// --- toggle from empty: select everything --------------------------------
toggleAllFilesSelection();
check('toggle selects every file when none selected',
      [...boxes()].every(box => box.checked === true),
      `${[...boxes()].filter(box => box.checked).length}/3 checked`);
check('button is pressed after selecting', btn.getAttribute('aria-pressed') === 'true');
check('button carries the is-selected state', btn.classList.contains('is-selected') === true);
check('label switches to the deselect phrase', label.textContent === 'Auswahl aufheben', label.textContent);
check('icon switches with the state', icon.classList.contains('bi-dash-square-fill') === true, icon.className);
check('count badge shows the selection size', count.hidden === false && count.textContent === '3',
      `hidden=${count.hidden} text=${count.textContent}`);

// --- toggle from full: clear everything ----------------------------------
toggleAllFilesSelection();
check('toggle clears the selection when it is active',
      [...boxes()].every(box => box.checked === false));
check('button is unpressed again', btn.getAttribute('aria-pressed') === 'false');
check('is-selected removed after clearing', btn.classList.contains('is-selected') === false);
check('label returns to the select phrase', label.textContent === 'Alles auswählen', label.textContent);
check('icon returns to the select glyph', icon.classList.contains('bi-check-square-fill') === true, icon.className);
check('count badge hidden again', count.hidden === true && count.textContent === '0');

// --- a single checkbox change syncs the button ---------------------------
const first = globalThis.document.getElementById('cb1');
first.checked = true;
updateSelectToggle();
check('one manual checkbox presses the button', btn.getAttribute('aria-pressed') === 'true');
check('count badge shows a single selection', count.hidden === false && count.textContent === '1',
      `hidden=${count.hidden} text=${count.textContent}`);
check('label shows the deselect phrase for a partial selection',
      label.textContent === 'Auswahl aufheben', label.textContent);

// --- the module functions keep the button in sync too --------------------
first.checked = false;
selectAllFiles();
check('selectAllFiles presses the button', btn.getAttribute('aria-pressed') === 'true');
deselectAllFiles();
check('deselectAllFiles releases the button', btn.getAttribute('aria-pressed') === 'false');

// --- a page without the button does not throw ----------------------------
mountPage('<div id="app"><input type="checkbox" class="file-checkbox"></div>');
let threw = null;
try {
    updateSelectToggle();
    toggleAllFilesSelection();
} catch (error) {
    threw = error;
}
check('absent button is tolerated (no throw)', threw === null, String(threw));

console.log(`file select toggle: ${passed} checks passed, ${failed} failed`);
if (failed > 0) {
    process.exit(1);
}
