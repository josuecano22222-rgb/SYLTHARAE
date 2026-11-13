/**
 * Unified table behaviour smoke: three-state header sort and the Load More
 * adoption, driven against the stub DOM with the shipped module.
 *
 *   node tests/js/unified_table_smoke.mjs
 */
import { FakeElement, installDom, treeFromHtml } from './_dom_stub.mjs';

const checks = [];
function check(name, ok, detail) {
    checks.push([name, ok === true]);
    if (ok !== true) console.log(`     detail: ${detail ?? ''}`);
}

const { documentRoot, document, globalThisRef } = installDom();

// The module reads `window.location` for the default server paths.
globalThisRef.location = new URL('http://localhost/files?search=rep&sort=name&order=asc&page=1');
globalThisRef.URL = URL;
globalThisRef.DOMParser = class {
    parseFromString(html) { return treeFromHtml(html); }
};
globalThisRef.fetch = () => Promise.reject(new Error('no fetch in this harness unless set'));

// The table as `record_table` renders it: wrapper config, sortable headers,
// a body with three rows, and the load-more block.
documentRoot.appendChild(treeFromHtml(`
<div data-unified-table='{"mode":"server","sortParam":"sort","orderParam":"order","pageParam":"page"}'>
  <table id="filesTable">
    <thead>
      <tr>
        <th class="ut-col ut-sortable" data-ut-sort="name" data-ut-type="text">Name</th>
        <th class="ut-col ut-sortable" data-ut-sort="size" data-ut-type="number">Size
          <button type="button" class="ut-filter-btn" data-ut-filter-btn aria-expanded="false"><i class="bi bi-funnel"></i></button>
          <div class="ut-col-filter" data-ut-filter-pop hidden
               data-ut-filter-config='{"kind":"values","row_attr":"data-type","param":"file_type","exclude_param":"exclude_file_type"}'>
            <div class="ut-filter-values">
              <label class="ut-filter-value"><input type="checkbox" class="ut-filter-check" value="pdf"><span>PDF</span></label>
              <label class="ut-filter-value"><input type="checkbox" class="ut-filter-check" value="txt"><span>TXT</span></label>
            </div>
            <div class="ut-col-filter-actions">
              <button type="button" class="btn" data-ut-filter-only>Show only</button>
              <button type="button" class="btn" data-ut-filter-hide>Hide</button>
              <button type="button" class="btn" data-ut-filter-clear>Clear</button>
            </div>
          </div>
        </th>
      </tr>
    </thead>
    <tbody id="filesTableBody">
      <tr><td data-ut-value="bravo">bravo</td><td data-type="pdf" data-ut-value="20">20</td></tr>
      <tr><td data-ut-value="alpha">alpha</td><td data-type="txt" data-ut-value="9">9</td></tr>
      <tr><td data-ut-value="charlie">charlie</td><td data-type="pdf" data-ut-value="40">40</td></tr>
    </tbody>
  </table>
  <div class="ut-toolbar"></div>
  <div class="dropdown ut-columns" data-ut-columns>
    <div class="dropdown-menu ut-columns-menu" data-ut-columns-menu>
      <label class="ut-columns-option"><input type="checkbox" class="ut-columns-check" data-ut-column-index="0" checked><span>Name</span></label>
      <label class="ut-columns-option"><input type="checkbox" class="ut-columns-check" data-ut-column-index="1" checked><span>Size</span></label>
    </div>
  </div>
  <div class="ut-load-more" data-ut-load-more
       data-page="1" data-total-pages="3" data-total="7" data-shown="3">
    <button type="button" class="btn">Load More</button>
    <span class="ut-load-more-info">Showing <span data-ut-first>1</span>&ndash;<span data-ut-last>3</span> of <span data-ut-total>7</span></span>
  </div>
</div>`));

const table = documentRoot.querySelector('#filesTable');
const wrapper = table.closest('[data-unified-table]');
const headerRow = table.querySelector('thead').querySelector('tr');
const sortCell = (key) => Array.from(headerRow.querySelectorAll('th'))
    .find((cell) => cell.getAttribute('data-ut-sort') === key);
const bodyRows = () => table.querySelector('tbody').querySelectorAll('tr')
    .filter((row) => row.tagName === 'TR');

import path from 'node:path';
import url from 'node:url';
const ROOT = path.resolve(path.dirname(url.fileURLToPath(import.meta.url)), '..', '..');
const { loadRuntime } = { loadRuntime: (f) => import(path.join('file://', ROOT, f)) };
await import(path.join('file://', ROOT, 'static/js/modules/ui/unified-table.js'));
const UnifiedTable = globalThisRef.UnifiedTable;
check('the module is on the global', typeof UnifiedTable === 'object');
// The stub's querySelectorAll has no descendant combinator, so the module's
// document scan finds nothing here; enhance this table the way init() would.
UnifiedTable.enhance(table);

// ---------------------------------------------------------------------
// Three-state sort: the URL rewrite the default server path performs
// ---------------------------------------------------------------------
const assigned = [];
const nativeAssigns = [];
globalThisRef.location.assign = (url) => nativeAssigns.push(url);
globalThisRef.swapNavigate = (url) => {
    assigned.push(String(url));
    return Promise.resolve(true);
};

function headerClicked(key) {
    const th = sortCell(key);
    th.classList.add('ut-sortable');
    // Click the header itself, as the reader does.
    th.dispatch('click');
}

// First click on "size" (a number column): descending.
headerClicked('size');
check('first click on a number column asks descending',
      assigned.length === 1 && assigned[0].includes('sort=size&order=desc'),
      assigned[assigned.length - 1]);
check('header sorting uses the content swap, not native navigation',
      nativeAssigns.length === 0, JSON.stringify(nativeAssigns));

// Now the header is marked sorted-desc; the module reflects that in the DOM.
const sizeTh = sortCell('size');
sizeTh.classList.add('ut-sorted', 'ut-sorted-desc');
sizeTh.setAttribute('aria-sort', 'descending');

// Second click: ascending.
headerClicked('size');
check('second click flips to ascending',
      assigned.length === 2 && assigned[1].includes('order=asc'), assigned[1]);

sizeTh.classList.remove('ut-sorted-desc');
sizeTh.classList.add('ut-sorted-asc');

// Third click: the sort is cleared - the request carries no sort at all,
// and goes back to the first page.
headerClicked('size');
const clearUrl = new URL(assigned[2]);
check('third click clears the sort',
      !clearUrl.searchParams.has('sort') && !clearUrl.searchParams.has('order'),
      assigned[2]);
check('a cleared sort still resets to the first page',
      clearUrl.searchParams.get('page') === '1', assigned[2]);
check("a cleared sort keeps the reader's search",
      clearUrl.searchParams.get('search') === 'rep', assigned[2]);

// A page handler can hear the sort instead (null direction = cleared).
let heard = null;
UnifiedTable.onSort('filesTable', (key, direction) => { heard = [key, direction]; });
headerClicked('name');
check('the page handler hears the first click as asc',
      heard && heard[0] === 'name' && heard[1] === 'asc', JSON.stringify(heard));
headerClicked('name');
check('the page handler hears the flip as desc',
      heard && heard[1] === 'desc', JSON.stringify(heard));
headerClicked('name');
check('the page handler hears the clear as null',
      heard && heard[0] === 'name' && heard[1] === null, JSON.stringify(heard));
UnifiedTable.onSort('filesTable', null);
// The page the handler would have re-rendered arrives unsorted; put the
// header back the way that page's markup would have it.
UnifiedTable.setSort('filesTable', null, null);
// And this simulated view asked for name/asc on its last click; restore the
// URL the default fetch reads from.
assigned.length = 0;

// ---------------------------------------------------------------------
// Load More: the next batch is adopted, the counters move, the control
// hides on the last batch - and the page never navigates.
// ---------------------------------------------------------------------
const servedUrls = [];
globalThisRef.fetch = (url) => {
    servedUrls.push(String(url));
    return Promise.resolve({
        ok: true,
        status: 200,
        text: async () => `
            <div data-unified-table='{"mode":"server"}'>
              <table id="filesTable">
                <tbody id="filesTableBody">
                  <tr><td data-ut-value="delta">delta</td><td data-ut-value="3">3</td></tr>
                  <tr><td data-ut-value="echo">echo</td><td data-ut-value="5">5</td></tr>
                </tbody>
                <!-- second serve swaps the rows; the harness reads tbody each time -->
              </table>
            </div>`,
    });
};

const loadMore = wrapper.querySelector('[data-ut-load-more]');
const loadButton = loadMore.querySelector('button');
loadButton.dispatch('click');

await new Promise((resolve) => setTimeout(resolve, 20));

// This view opened sorted by name/asc; the batch is the same view, one page
// on - every choice in the query string, only the page number moved.
check('the batch request is this view one page on',
      servedUrls.length === 1
      && new URL(servedUrls[0]).searchParams.get('page') === '2'
      && new URL(servedUrls[0]).searchParams.get('search') === 'rep'
      && new URL(servedUrls[0]).searchParams.get('sort') === 'name'
      && new URL(servedUrls[0]).searchParams.get('order') === 'asc',
      servedUrls[0]);
check('Load More does not start a page navigation', assigned.length === 0 && nativeAssigns.length === 0,
      'swap calls: ' + assigned.length + ', native navigations: ' + nativeAssigns.length);
check('the new rows are in the table body', bodyRows().length === 5,
      String(bodyRows().length));
check('the counter moved to five shown',
      loadMore.getAttribute('data-shown') === '5'
      && loadMore.querySelector('[data-ut-last]').textContent === '5',
      loadMore.getAttribute('data-shown'));
check('Show More keeps the control on page 1 (it reveals rows, it does not navigate)', loadMore.getAttribute('data-page') === '1',
      loadMore.getAttribute('data-page'));

// The last batch: the control hides itself.
loadButton.dispatch('click');
await new Promise((resolve) => setTimeout(resolve, 20));
check('reaching the end hides the control', loadMore.hidden === true
      && bodyRows().length === 7, 'rows: ' + bodyRows().length);

// A page that loads its own batches hands rows over through appendRows.
loadMore.hidden = false;
loadMore.setAttribute('data-page', '3');
UnifiedTable.appendRows('filesTable', { html: ['<tr><td>fox</td><td>1</td></tr>'], total: 8 });
check('appendRows adopts the page-own batch',
      bodyRows().length === 8 && loadMore.getAttribute('data-shown') === '8',
      loadMore.getAttribute('data-shown'));

// ---------------------------------------------------------------------
// Column visibility: the reader chooses which columns stand.
// ---------------------------------------------------------------------
const nameHeader = sortCell('name');
const sizeHeader = sortCell('size');
const columnsMenu = wrapper.querySelector('[data-ut-columns-menu]');
const sizeColumnCheck = columnsMenu.querySelector('[data-ut-column-index="1"]');
sizeColumnCheck.checked = false;
sizeColumnCheck.dispatch('change');
check('hiding a column hides its header cell',
      sizeHeader.hasAttribute('hidden'), String(sizeHeader.attributes.hidden));
const bodyRowCells = wrapper.querySelectorAll('tbody tr td');
check('hiding a column hides its cells in every body row',
      Array.from(wrapper.querySelectorAll('tbody tr')).every(
          (row) => row.querySelectorAll('td')[1].hasAttribute('hidden')),
      'some cell still stands');
sizeColumnCheck.checked = true;
sizeColumnCheck.dispatch('change');
check('showing the column again restores every cell',
      !sizeHeader.hasAttribute('hidden')
      && Array.from(wrapper.querySelectorAll('tbody tr')).every(
          (row) => !row.querySelectorAll('td')[1].hasAttribute('hidden')),
      'a cell stayed hidden');

// ---------------------------------------------------------------------
// Column filters: the header carries show-only / hide / clear, and the
// request it builds keeps the reader's view but the filter.
// ---------------------------------------------------------------------
const filterBtn = sizeHeader.querySelector('[data-ut-filter-btn]');
const filterPop = sizeHeader.querySelector('[data-ut-filter-pop]');
filterBtn.dispatch('click');
check('clicking the funnel opens the column filter', !filterPop.hidden,
      String(filterPop.hidden));

const pdfCheck = filterPop.querySelector('.ut-filter-check[value="pdf"]');
pdfCheck.checked = true;
filterPop.querySelector('[data-ut-filter-only]').dispatch('click');
const onlyUrl = new URL(assigned[assigned.length - 1]);
check('"show only" filters this view to the chosen formats',
      onlyUrl.searchParams.get('file_type') === 'pdf', assigned[assigned.length - 1]);
check("the column filter keeps the reader's search and sort",
      onlyUrl.searchParams.get('search') === 'rep'
      && onlyUrl.searchParams.get('sort') === 'name'
      && onlyUrl.searchParams.get('order') === 'asc', assigned[assigned.length - 1]);
check('the column filter lands on the first page',
      onlyUrl.searchParams.get('page') === '1', assigned[assigned.length - 1]);
check('applying a filter closes the popover', filterPop.hidden);

filterBtn.dispatch('click');
pdfCheck.checked = true;
filterPop.querySelector('[data-ut-filter-hide]').dispatch('click');
const hideUrl = new URL(assigned[assigned.length - 1]);
check('"hide" excludes the chosen formats',
      hideUrl.searchParams.get('exclude_file_type') === 'pdf'
      && !hideUrl.searchParams.has('file_type'), assigned[assigned.length - 1]);

// ---------------------------------------------------------------------
// The column's own search field (kind 'text'): a dedicated search field
// per column. Client tables filter in place; several active filters
// (text and values, either order) combine with AND. Enter applies.
// ---------------------------------------------------------------------
documentRoot.appendChild(treeFromHtml(`
<div data-unified-table='{"mode":"client"}'>
  <table id="clientTable">
    <thead><tr>
      <th class="ut-col ut-sortable" data-ut-sort="name" data-ut-type="text">Name
        <button type="button" class="ut-filter-btn" data-ut-filter-btn aria-expanded="false"></button>
        <div class="ut-col-filter" data-ut-filter-pop hidden
             data-ut-filter-config='{"kind":"text","row_attr":"data-ut-value","param":"name_contains"}'>
          <input type="search" data-ut-filter-text value="">
          <div class="ut-col-filter-actions">
            <button type="button" class="btn" data-ut-filter-apply>Apply</button>
            <button type="button" class="btn" data-ut-filter-clear>Clear</button>
          </div>
        </div>
      </th>
      <th class="ut-col" data-ut-type="text">Kind
        <button type="button" class="ut-filter-btn" data-ut-filter-btn aria-expanded="false"></button>
        <div class="ut-col-filter" data-ut-filter-pop hidden
             data-ut-filter-config='{"kind":"values","row_attr":"data-kind","param":"kind"}'>
          <label class="ut-filter-value"><input type="checkbox" class="ut-filter-check" value="memo"><span>Memo</span></label>
          <label class="ut-filter-value"><input type="checkbox" class="ut-filter-check" value="log"><span>Log</span></label>
          <div class="ut-col-filter-actions">
            <button type="button" class="btn" data-ut-filter-only>Show only</button>
            <button type="button" class="btn" data-ut-filter-clear>Clear</button>
          </div>
        </div>
      </th>
    </tr></thead>
    <tbody>
      <tr><td data-ut-value="annual report">annual report</td><td data-kind="memo" data-ut-value="memo">memo</td></tr>
      <tr><td data-ut-value="bananas ledger">bananas ledger</td><td data-kind="log" data-ut-value="log">log</td></tr>
      <tr><td data-ut-value="annual budget">annual budget</td><td data-kind="log" data-ut-value="log">log</td></tr>
      <tr><td data-ut-value="minutes draft">minutes draft</td><td data-kind="memo" data-ut-value="memo">memo</td></tr>
    </tbody>
  </table>
  <div class="ut-toolbar"></div>
</div>`));
const clientTable = documentRoot.querySelector('#clientTable');
UnifiedTable.enhance(clientTable);
const clientRows = () => Array.from(clientTable.querySelectorAll('tbody tr'));
const visibleTexts = () => clientRows()
    .filter((row) => !row.classList.contains('ut-row-filtered'))
    .map((row) => row.querySelectorAll('td')[0].getAttribute('data-ut-value'));

const clientPops = clientTable.querySelectorAll('[data-ut-filter-pop]');
const namePop = clientPops[0];      // the Name column's text filter
const kindPop = clientPops[1];      // the Kind column's values filter
const nameInput = namePop.querySelector('[data-ut-filter-text]');

// A standalone empty search field, entered directly: the input fires the
// field's dedicated search on Enter.
nameInput.value = 'annual';
nameInput.dispatch('keydown', { key: 'Enter' });
check('Enter in the column search field applies it',
      JSON.stringify(visibleTexts()) === JSON.stringify(['annual report', 'annual budget']),
      JSON.stringify(visibleTexts()));

// The same view, narrowed further by the Kind column: both filters hold.
const memoCheck = kindPop.querySelector('.ut-filter-check[value="memo"]');
memoCheck.checked = true;
kindPop.querySelector('[data-ut-filter-only]').dispatch('click');
check('a values filter combines with the text filter (AND)',
      JSON.stringify(visibleTexts()) === JSON.stringify(['annual report']),
      JSON.stringify(visibleTexts()));

// Clearing the text field lifts only the text: the values filter stands.
namePop.querySelector('[data-ut-filter-clear]').dispatch('click');
check('clearing the text filter keeps the values filter',
      JSON.stringify(visibleTexts()) === JSON.stringify(['annual report', 'minutes draft']),
      JSON.stringify(visibleTexts()));

// Clearing the values filter restores every row.
kindPop.querySelector('[data-ut-filter-clear]').dispatch('click');
check('clearing the last filter restores every row',
      visibleTexts().length === 4, JSON.stringify(visibleTexts()));

// A server-mode column search re-asks with the column's parameter.
const searchPop = sizeHeader.querySelector('[data-ut-filter-pop]');
searchPop.setAttribute('data-ut-filter-config',
    '{"kind":"text","row_attr":"data-ut-value","param":"name_contains"}');
searchPop.appendChild(treeFromHtml(
    '<div class="ut-col-filter-actions"><input type="search" data-ut-filter-text value="">'
    + '<button type="button" class="btn" data-ut-filter-apply>Apply</button>'
    + '<button type="button" class="btn" data-ut-filter-clear>Clear</button></div>'));
assigned.length = 0;
filterBtn.dispatch('click');
const textInput = searchPop.querySelector('[data-ut-filter-text]');
textInput.value = 'al';
searchPop.querySelector('[data-ut-filter-apply]').dispatch('click');
const textUrl = new URL(assigned[assigned.length - 1]);
check('a server table re-asks with the column search parameter',
      textUrl.searchParams.get('name_contains') === 'al'
      && textUrl.searchParams.get('page') === '1'
      && textUrl.searchParams.get('sort') === 'name',
      assigned[assigned.length - 1]);
check('an applied column search closes the popover', searchPop.hidden);
check('column sorting/filtering never calls native page navigation',
      nativeAssigns.length === 0, JSON.stringify(nativeAssigns));

// Report
let failed = 0;
for (const [name, ok] of checks) {
    if (!ok) failed += 1;
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}`);
}
console.log(`${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
