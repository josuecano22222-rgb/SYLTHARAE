// A soft navigation into the archives page can evaluate file-management-system.js
// a second time (new ?swap= URL, so a new module instance). The setup it calls
// must not stack a second listener onto the same DOM node or document. This
// imports the real sidebar and file-selection modules, runs their setup twice,
// and also re-imports file-selection under a new URL, as a re-evaluation does.
const listeners = { sidebar: [], document: [] };
const sidebarEl = { dataset: {}, addEventListener(type, fn) { listeners.sidebar.push([type, fn]); },
  getAttribute() { return null; } };
const docEl = { dataset: {} };
globalThis.document = {
  documentElement: docEl,
  readyState: 'complete',
  getElementById: (id) => (id === 'fileManagerSidebar' ? sidebarEl : null),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener(type, fn) { listeners.document.push([type, fn]); },
  removeEventListener() {},
};
globalThis.window = { addEventListener() {}, removeEventListener() {} };

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}

const sidebar = await import('../../static/js/modules/ui/sidebar.js');
sidebar.setupSidebarKeyboardNavigation();
sidebar.setupSidebarKeyboardNavigation();
check('sidebar keyboard handler is attached once', listeners.sidebar.filter(([t]) => t === 'keydown').length === 1,
      listeners.sidebar.length);

const sel = await import('../../static/js/modules/file-operations/file-selection.js');
sel.initializeFileSelection();
sel.initializeFileSelection();
const changeCount = () => listeners.document.filter(([t]) => t === 'change').length;
check('file selection change listener is attached once', changeCount() === 1, changeCount());

// A re-evaluated module instance (fresh module state) must not wire again.
const selAgain = await import('../../static/js/modules/file-operations/file-selection.js?swap=2');
selAgain.initializeFileSelection();
check('re-evaluated file selection does not add a listener', changeCount() === 1, changeCount());

if (failures) { console.log(`${failures} failure(s)`); process.exit(1); }
