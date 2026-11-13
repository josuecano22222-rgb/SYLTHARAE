/**
 * One state, every control: Show More, the registration range, the numbered
 * pager, Prev/Next/First/Last and the jump input all follow one state record.
 *
 * The view starts on page 2 of 5 (5 rows a page, 23 rows in all): rows 6-10.
 * Show More walks it to the end, one batch a page, and every control must
 * agree after each batch. A batch that brings nothing must not move the page.
 *
 *   node tests/js/unified_state_sync_smoke.mjs
 */
import { installDom, treeFromHtml } from './_dom_stub.mjs';

const checks = [];
function check(name, ok, detail) {
    checks.push([name, ok === true]);
    console.log(`${ok === true ? 'ok  ' : 'FAIL'} ${name}`);
    if (ok !== true) console.log(`     detail: ${detail ?? ''}`);
}
const wait = () => new Promise((resolve) => setTimeout(resolve, 20));

const { documentRoot, globalThisRef } = installDom();
globalThisRef.location = new URL('http://localhost/keywords?page=2&per_page=5&sort=name&order=asc');
globalThisRef.URL = URL;
globalThisRef.DOMParser = class { parseFromString(html) { return treeFromHtml(html); } };

// Each batch the server would render: 5 rows for pages 3 and 4, 3 for page 5.
// `nextBatch` lets a test answer with an empty batch instead.
const served = [];
let nextBatch = null;
function batchHtml(count, start) {
    let rows = '';
    for (let i = 0; i < count; i += 1) rows += `<tr><td>r${start + i}</td></tr>`;
    return `<div data-unified-table='{"mode":"server"}'><table id="keywordsTable"><tbody>${rows}</tbody></table></div>`;
}
globalThisRef.fetch = (url) => {
    served.push(String(url));
    const page = Number(new URL(String(url)).searchParams.get('page'));
    const override = nextBatch;
    nextBatch = null;
    const body = override ?? (page === 3 ? batchHtml(5, 11) : page === 4 ? batchHtml(5, 16) : batchHtml(3, 21));
    return Promise.resolve({ ok: true, status: 200, text: async () => body });
};

// The server's markup: nav links with hooks, numbered items marked as
// numbered, and the hidden prototypes the engine clones.
function navItem(hook, href, text) {
    return `<li class="unified-pagination-item"><a class="unified-pagination-link" data-ut-nav="${hook}" href="${href}">${text}</a></li>`;
}
function numberedItem(n, href) {
    return `<li data-ut-numbered class="unified-pagination-item"><a class="unified-pagination-link" href="${href}">${n}</a></li>`;
}
const pagerList = [
    navItem('first', '/keywords?page=1', 'First'),
    navItem('previous', '/keywords?page=1', 'Prev'),
    numberedItem(1, '/keywords?page=1'),
    `<li data-ut-numbered class="unified-pagination-item active"><span class="unified-pagination-link active" aria-current="page">2</span></li>`,
    numberedItem(3, '/keywords?page=3'),
    numberedItem(4, '/keywords?page=4'),
    navItem('next', '/keywords?page=3', 'Next'),
    navItem('last', '/keywords?page=5', 'Last'),
].join('');
const prototypes = `<ul hidden data-ut-protos>
  <li class="unified-pagination-item" data-ut-proto="page"><a class="unified-pagination-link" href="#" data-ut-page-link></a></li>
  <li class="unified-pagination-item active" data-ut-proto="current"><span class="unified-pagination-link active" aria-current="page" data-ut-page-link></span></li>
  <li class="unified-pagination-item disabled" data-ut-proto="gap"><span class="unified-pagination-ellipsis">...</span></li>
</ul>`;

documentRoot.appendChild(treeFromHtml(`
<div id="mainContent">
  <div data-unified-table='{"mode":"server","pageParam":"page"}'>
    <div class="ut-toolbar">
      <div class="ut-info">Showing <span data-ut-first>6</span>&ndash;<span data-ut-last>10</span> of <span data-ut-total>23</span></div>
    </div>
    <table id="keywordsTable"><tbody id="rowsBody">
      ${batchHtml(5, 6).replace(/^.*<tbody>|<\/tbody>.*$/g, '')}
    </tbody></table>
  </div>
  <div class="pagination-container" id="pagination">
    <div class="unified-pagination-container" data-endpoint="keywords_list"
         data-current-page="2" data-total-pages="5" data-page-size="5">
      <div class="unified-pagination-info"><span class="pagination-text">Page <span data-ut-page>2</span> of <span data-ut-pages>5</span></span></div>
      <div class="unified-pagination-controls">
        <div class="ut-load-more" data-ut-load-more data-table-id="keywordsTable"
             data-page="2" data-total-pages="5" data-total="23" data-shown="5"
             data-page-size="5" data-first-row="6">
          <button type="button" class="btn">Show More</button>
          <span class="ut-load-more-info">Showing <span data-ut-first>6</span>&ndash;<span data-ut-last>10</span> of <span data-ut-total>23</span></span>
          <span class="ut-load-more-error" data-ut-load-error hidden>retry</span>
        </div>
        <nav><ul class="unified-pagination-list" data-ut-pager-list>${pagerList}</ul>${prototypes}</nav>
        <div class="unified-pagination-jump">
          <input class="unified-pagination-jump-input" data-ut-jump value="2" data-total-pages="5" max="5">
        </div>
      </div>
    </div>
  </div>
</div>`));

await import('../../static/js/modules/ui/unified-table.js');

const block = documentRoot.querySelector('[data-ut-load-more]');
const pager = documentRoot.querySelector('.unified-pagination-container');
const wrapperInfo = documentRoot.querySelector('.ut-info');
const text = (selector, scope = documentRoot) => (scope.querySelector(selector) || {}).textContent;
const numberLinks = () => Array.from(pager.querySelectorAll('.unified-pagination-list .unified-pagination-link'))
    .filter((node) => /^\d+$/.test(node.textContent.trim()) && node.tagName === 'A')
    .map((node) => node.textContent.trim());
const activeNumber = () => (pager.querySelector('.unified-pagination-link.active') || {}).textContent;
const linkFor = (label) => Array.from(pager.querySelectorAll('.unified-pagination-link'))
    .find((node) => node.textContent.trim() === label);

// Batch 1: Show More on page 2. The batch is the next aligned block (page 3 at
// size 5); the pager stays on page 2 and only the revealed rows grow.
block.querySelector('button').dispatch('click');
await wait();
check('the batch asks for page 3 of the same view (sort and page size ride along)',
      served.length === 1 && served[0].includes('page=3') && served[0].includes('per_page=5')
      && served[0].includes('sort=name') && served[0].includes('order=asc'), served[0]);
check('Show More keeps the pager on page 2 and counts 10 rows shown', block.getAttribute('data-page') === '2'
      && block.getAttribute('data-shown') === '10', `${block.getAttribute('data-page')}/${block.getAttribute('data-shown')}`);
check('the range reads 6-15 in the pager and in the toolbar',
      text('[data-ut-last]', pager) === '15' && text('[data-ut-last]', wrapperInfo) === '15'
      && text('[data-ut-first]', pager) === '6', text('[data-ut-last]', pager));
check('the pager still says page 2 of 5', text('[data-ut-page]', pager) === '2' && text('[data-ut-pages]', pager) === '5');
check('the numbered window is unchanged (page 2 active; links 1,3,4 and Last 5)',
      activeNumber() === '2' && numberLinks().join(',') === '1,3,4,5', `${activeNumber()} / ${numberLinks()}`);
check('Prev points at page 1, Next at page 3, Last at page 5',
      linkFor('Prev').getAttribute('href').includes('page=1')
      && linkFor('Next').getAttribute('href').includes('page=3')
      && linkFor('Last').getAttribute('href').includes('page=5'));
check('every page link keeps the sort (no lost view state)',
      linkFor('3').getAttribute('href').includes('sort=name') && linkFor('3').getAttribute('href').includes('per_page=5'),
      linkFor('3').getAttribute('href'));
check('the jump input shows page 2', pager.querySelector('.unified-pagination-jump-input').value === '2');
check('the pager container says page 2', pager.getAttribute('data-current-page') === '2');
check('the control is still offered (more pages)', block.hidden === false);

// Batch 2: an empty batch must not move the page, and must add nothing.
nextBatch = batchHtml(0, 0);
block.querySelector('button').dispatch('click');
await wait();
check('an empty batch leaves the pager on page 2', block.getAttribute('data-page') === '2',
      block.getAttribute('data-page'));
check('an empty batch leaves the shown count alone', block.getAttribute('data-shown') === '10', block.getAttribute('data-shown'));
check('an empty batch hides the control (nothing more to load)', block.hidden === true);
check('the pager still says page 2 after an empty batch', pager.getAttribute('data-current-page') === '2',
      pager.getAttribute('data-current-page'));

// The control was hidden by the empty batch; re-offer it to continue the walk
// from the same window (page 2, 10 rows shown).
block.hidden = false;

block.querySelector('button').dispatch('click');
await wait();
check('Show More reveals a third batch: 15 shown, pager still on page 2', block.getAttribute('data-page') === '2'
      && block.getAttribute('data-shown') === '15', `${block.getAttribute('data-page')}/${block.getAttribute('data-shown')}`);
check('the numbered window does not move with Show More (page 2 active)',
      activeNumber() === '2' && numberLinks().join(',') === '1,3,4,5', `${activeNumber()} / ${numberLinks()}`);

// Last batch: the window closes at row 23 of 23 and the control hides itself.
block.querySelector('button').dispatch('click');
await wait();
check('the last batch brings 18 shown of 23 (window 6-23)', block.getAttribute('data-shown') === '18'
      && block.getAttribute('data-page') === '2', block.getAttribute('data-shown'));
check('the range closes at 23', text('[data-ut-last]', pager) === '23' && text('[data-ut-last]', wrapperInfo) === '23');
check('the control hides itself at the end', block.hidden === true);
check('the page list does not duplicate a number', new Set(numberLinks()).size === numberLinks().length,
      numberLinks().join(','));

// ------------------------------------------------------------------
// Dataset changes and large sets. The walk above left the view on page 5 of
// 5 with 18 rows; these checks set up a visible page 2 and move the dataset.
// ------------------------------------------------------------------
const rowCount = () => documentRoot.querySelector('#keywordsTable tbody').querySelectorAll('tr').length;
const UT = globalThisRef.UnifiedTable;
const stateOnlyBatch = `<div data-unified-table='{\"mode\":\"server\"}'><table id=\"keywordsTable\"><tbody><tr data-ut-state=\"empty\"><td>No keywords</td></tr></tbody></table></div>`;

block.setAttribute('data-page', '2');
block.setAttribute('data-shown', '5');
block.setAttribute('data-total', '23');
block.setAttribute('data-total-pages', '5');
block.hidden = false;
pager.setAttribute('data-current-page', '2');
const before = rowCount();

nextBatch = stateOnlyBatch;
block.querySelector('button').dispatch('click');
await wait();
check('an empty-state batch adds no rows (no placeholder reaches the list)',
      rowCount() === before && !documentRoot.querySelector('#keywordsTable tbody [data-ut-state]'),
      `${before} -> ${rowCount()}`);
check('an empty-state batch does not move the page or the shown count',
      block.getAttribute('data-page') === '2' && block.getAttribute('data-shown') === '5',
      `${block.getAttribute('data-page')}/${block.getAttribute('data-shown')}`);

check('the engine exposes syncLoadMore for the dataset change',
      UT && typeof UT.syncLoadMore === 'function', UT && Object.keys(UT));

// The dataset shrinks to 2 pages (10 records) while the view is on page 4.
block.setAttribute('data-page', '4');
UT.syncLoadMore('keywordsTable', 5, 10, 5);
check('a shrunken dataset clamps the page to the last page (2 of 2)',
      block.getAttribute('data-page') === '2' && pager.getAttribute('data-current-page') === '2'
      && text('[data-ut-page]', pager) === '2' && text('[data-ut-pages]', pager) === '2',
      `${block.getAttribute('data-page')} / ${text('[data-ut-page]', pager)}`);
check('the page-number controls follow the clamped page',
      activeNumber() === '2' && numberLinks().join(',') === '1',
      `${activeNumber()} / ${numberLinks()}`);
check('Next and Last are disabled once clamped to the last page',
      linkFor('Next').classList.contains('disabled') && linkFor('Last').classList.contains('disabled'));
check('the control hides itself at the clamped last page', block.hidden === true);

// The filter now matches nothing: one page, page 1, reads 0-0 of 0.
UT.syncLoadMore('keywordsTable', 0, 0, 5);
check('a filter with no matches clamps to page 1 of 1',
      block.getAttribute('data-page') === '1' && pager.getAttribute('data-current-page') === '1'
      && block.getAttribute('data-total-pages') === '1', block.getAttribute('data-page'));
check('an empty result reads 0-0 of 0 in the toolbar',
      text('[data-ut-first]', wrapperInfo) === '0' && text('[data-ut-last]', wrapperInfo) === '0'
      && text('[data-ut-total]', wrapperInfo) === '0');

// A very large set keeps the numbered window bounded (no node per page).
block.setAttribute('data-page', '2');
UT.syncLoadMore('keywordsTable', 5, 100000, 5);
const numbered = pager.querySelectorAll('[data-ut-numbered]').length;
check('a 20,000-page set draws a bounded numbered window (no node per page)',
      numbered <= 7 && numberLinks().includes('20000') && activeNumber() === '2',
      `${numbered} nodes: ${numberLinks()}`);
check('the range and total stay correct for a large set',
      text('[data-ut-total]', pager) === '100000' && text('[data-ut-last]', pager) === '10',
      `${text('[data-ut-total]', pager)} / ${text('[data-ut-last]', pager)}`);

const failed = checks.filter(([, ok]) => !ok).length;
console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
