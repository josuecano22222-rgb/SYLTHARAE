/**
 * Unified pager smoke: one Load More control, owned by the numbered pager
 * and driven by the shared table engine. The control sits OUTSIDE the table
 * wrapper (under the pager), and a click must still reach the engine.
 *
 *   node tests/js/pager_load_more_smoke.mjs
 */
import { installDom, treeFromHtml } from './_dom_stub.mjs';

const checks = [];
function check(name, ok, detail) {
    checks.push([name, ok === true]);
    console.log(`${ok === true ? 'ok  ' : 'FAIL'} ${name}`);
    if (ok !== true) console.log(`     detail: ${detail ?? ''}`);
}

const { documentRoot, globalThisRef } = installDom();
globalThisRef.location = new URL('http://localhost/categories?per_page=5&page=1');
globalThisRef.URL = URL;
globalThisRef.DOMParser = class { parseFromString(html) { return treeFromHtml(html); } };
const served = [];
globalThisRef.fetch = (url) => {
    served.push(String(url));
    return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => `<div data-unified-table='{"mode":"server"}'><table id="categoriesTable">
            <tbody><tr><td>f</td></tr><tr><td>g</td></tr></tbody></table></div>`,
    });
};

// The table, then the pager under it - the way a list page renders them.
documentRoot.appendChild(treeFromHtml(`
<div id="mainContent">
  <div data-unified-table='{"mode":"server","pageParam":"page"}'>
    <table id="categoriesTable"><tbody id="categoriesBody">
      <tr><td>a</td></tr><tr><td>b</td></tr><tr><td>c</td></tr>
    </tbody></table>
  </div>
  <div class="pagination-container" id="pagination">
    <div class="unified-pagination-container" data-endpoint="categories_list"
         data-current-page="1" data-total-pages="3">
      <div class="unified-pagination-info"><span>Page 1 of 3</span></div>
      <div class="unified-pagination-controls">
        <div class="ut-load-more" data-ut-load-more data-table-id="categoriesTable"
             data-page="1" data-total-pages="3" data-total="6" data-shown="3">
          <button type="button" class="btn">Show More</button>
          <span class="ut-load-more-info">Showing <span data-ut-first>1</span>&ndash;<span data-ut-last>3</span> of <span data-ut-total>6</span></span>
          <span class="ut-load-more-error" data-ut-load-error hidden>retry</span>
        </div>
        <nav><ul><li><a class="unified-pagination-link" href="/categories?page=2">2</a></li></ul></nav>
      </div>
    </div>
  </div>
</div>`));

// The shipped engine: loading it installs the delegated click listener.
await import('../../static/js/modules/ui/unified-table.js');
const UT = globalThisRef.UnifiedTable;

const container = documentRoot.querySelector('.unified-pagination-container');
const block = documentRoot.querySelector('[data-ut-load-more]');
check('exactly one Load More control on the page',
      documentRoot.querySelectorAll('[data-ut-load-more]').length === 1);
check('the engine is loaded', !!UT && typeof UT.onLoadMore === 'function');

block.querySelector('button').dispatch('click');
await new Promise((resolve) => setTimeout(resolve, 20));

check('the batch asks for page 2 of the same view',
      served.length === 1 && new URL(served[0]).searchParams.get('page') === '2', served[0]);
check('the rows were appended to the table body',
      documentRoot.querySelectorAll('#categoriesBody tr').length === 5,
      documentRoot.querySelectorAll('#categoriesBody tr').length);
check('the shown counter moved to 5 (pager-placed control)',
      block.getAttribute('data-shown') === '5', block.getAttribute('data-shown'));
check('Show More keeps the pager on page 1', container.getAttribute('data-current-page') === '1',
      container.getAttribute('data-current-page'));
check('the pager counter text moved too',
      container.querySelector('[data-ut-last]').textContent === '5',
      container.querySelector('[data-ut-last]').textContent);

// The last batch: the control hides itself.
block.setAttribute('data-page', '2');
block.setAttribute('data-shown', '5');
block.querySelector('button').dispatch('click');
await new Promise((resolve) => setTimeout(resolve, 20));
check('reaching the end hides the one control', block.hidden === true, block.hidden);

const failed = checks.filter(([, ok]) => !ok).length;
console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
