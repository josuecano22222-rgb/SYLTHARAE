/**
 * The one Load More control, for a list that is not a table (the file list is
 * cards). The click goes to the page's handler, the counters follow the state
 * by id, and nothing is fetched by the engine.
 *
 *   node tests/js/load_more_handler_smoke.mjs
 */
import { installDom, treeFromHtml } from './_dom_stub.mjs';

const checks = [];
function check(name, ok, detail) {
    checks.push([name, ok === true]);
    console.log(`${ok === true ? 'ok  ' : 'FAIL'} ${name}`);
    if (ok !== true) console.log(`     detail: ${detail ?? ''}`);
}

const { documentRoot, globalThisRef } = installDom();
globalThisRef.location = new URL('http://localhost/files?page=1');
globalThisRef.URL = URL;
globalThisRef.DOMParser = class { parseFromString(html) { return treeFromHtml(html); } };
const fetched = [];
globalThisRef.fetch = (url) => { fetched.push(String(url)); return Promise.reject(new Error('no fetch expected')); };

// A card list (a div, not a table) with its one Load More under it.
documentRoot.appendChild(treeFromHtml(`
<div id="mainContent">
  <div id="fmasFilesContainer"><div class="file-card">a</div><div class="file-card">b</div></div>
  <div class="unified-pagination-container" data-current-page="1" data-total-pages="3">
    <div class="ut-load-more" data-ut-load-more data-table-id="fmasFilesContainer"
         data-page="1" data-total-pages="3" data-total="6" data-shown="2">
      <button type="button" class="btn">Show More</button>
      <span class="ut-load-more-info">Showing <span data-ut-first>1</span>&ndash;<span data-ut-last>2</span> of <span data-ut-total>6</span></span>
      <span class="ut-load-more-error" data-ut-load-error hidden>retry</span>
    </div>
  </div>
</div>`));

await import('../../static/js/modules/ui/unified-table.js');
const UT = globalThisRef.UnifiedTable;
const block = documentRoot.querySelector('[data-ut-load-more]');
const errorLine = block.querySelector('[data-ut-load-error]');

// 1. A click reaches the page's handler, and the engine fetches nothing.
let calls = 0;
UT.onLoadMore('fmasFilesContainer', () => { calls += 1; });
errorLine.hidden = false;
block.querySelector('button').dispatch('click');
await new Promise((resolve) => setTimeout(resolve, 10));
check('a click calls the page handler once', calls === 1, calls);
check('the engine fetches nothing for a handler-owned control', fetched.length === 0, fetched);
check('a click clears a previous error', errorLine.hidden === true);

// 2. The page moves the counters by id (no table element involved).
UT.syncLoadMore('fmasFilesContainer', 4, 6, 2);
check('syncLoadMore moves the shown counter by id', block.getAttribute('data-shown') === '4', block.getAttribute('data-shown'));
check('syncLoadMore keeps the control while items remain', block.hidden !== true);
check('the counter text follows', block.querySelector('[data-ut-last]').textContent === '4');

// 3. The last items: the one control hides itself.
UT.syncLoadMore('fmasFilesContainer', 6, 6, 2);
check('the control hides when everything is shown', block.hidden === true, block.hidden);

// 4. An unknown id is a no-op, not an error.
let threw = false;
try { UT.syncLoadMore('noSuchList', 1, 2, 1); } catch (e) { threw = true; }
check('syncLoadMore on an unknown id does nothing', threw === false);

const failed = checks.filter(([, ok]) => !ok).length;
console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
