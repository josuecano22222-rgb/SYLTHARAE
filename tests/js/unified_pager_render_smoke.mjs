/**
 * Client pager smoke: the shared renderer draws ONE Load More control when a
 * table is bound, and the same markup the server renders.
 *
 *   node tests/js/unified_pager_render_smoke.mjs
 */
import { installDom, treeFromHtml } from './_dom_stub.mjs';

const checks = [];
function check(name, ok, detail) {
    checks.push([name, ok === true]);
    console.log(`${ok === true ? 'ok  ' : 'FAIL'} ${name}`);
    if (ok !== true) console.log(`     detail: ${detail ?? ''}`);
}

const { documentRoot, globalThisRef } = installDom();
globalThisRef.location = new URL('http://localhost/keywords?page=2');
globalThisRef.URL = URL;
globalThisRef.DOMParser = class { parseFromString(html) { return treeFromHtml(html); } };

const { renderUnifiedPagination, loadMoreBlockHtml } = await import(
    '../../static/js/modules/rendering/unified-pagination.js');

// The pure builder: the block's attributes are the engine's contract.
const block = loadMoreBlockHtml({ tableId: 'keywordsTable', page: 2, totalPages: 4, total: 18, shown: 10 });
check('the builder emits one Load More block', (block.match(/data-ut-load-more/g) || []).length === 1);
check('the block names its table', block.includes('data-table-id="keywordsTable"'));
check('the block carries the page state',
      block.includes('data-page="2"') && block.includes('data-total-pages="4"')
      && block.includes('data-total="18"') && block.includes('data-shown="10"'));
check('the range and total are present for the engine',
      block.includes('data-ut-first>1<') && block.includes('data-ut-last>10<') && block.includes('data-ut-total>18<'));
check('the builder escapes a hostile table id',
      !loadMoreBlockHtml({ tableId: '"><img src=x>', page: 1, totalPages: 2, total: 1, shown: 1 }).includes('<img'));

// The renderer, with a container that captures what it would write.
function captureContainer(id) {
    const el = documentRoot.querySelector(`[id="${id}"]`) || (() => {
        const made = treeFromHtml(`<div id="${id}"></div>`).children[0];
        documentRoot.appendChild(made);
        return made;
    })();
    let written = '';
    Object.defineProperty(el, 'innerHTML', {
        configurable: true,
        get() { return written; },
        set(value) { written = String(value); },
    });
    el.insertAdjacentHTML = (_where, value) => { written += String(value); };
    return { el, output: () => written };
}

const bound = captureContainer('paginationContainer');
renderUnifiedPagination({
    currentPage: 2, totalPages: 4, containerId: 'paginationContainer',
    tableId: 'keywordsTable', totalCount: 40, rowsShown: 10, pageSize: 10,
    onPageChange: () => {}, urlParams: {}, showInfo: true, showJump: false,
    baseUrl: '/keywords',
});
const out = bound.output();
check('a bound pager renders exactly one Load More', (out.match(/data-ut-load-more/g) || []).length === 1);
check('a bound pager keeps its numbered pages', out.includes('unified-pagination-link') && out.includes('aria-label="Next Page"'));
check('a bound pager renders no second Show More button', !out.includes('unified-pagination-show-more-btn'));

const unbound = captureContainer('paginationContainerPlain');
renderUnifiedPagination({
    currentPage: 2, totalPages: 4, containerId: 'paginationContainerPlain',
    onPageChange: () => {}, urlParams: {}, showInfo: true, showJump: false, baseUrl: '/x',
});
check('an unbound pager renders no Load More', !unbound.output().includes('data-ut-load-more'));

const last = captureContainer('paginationContainerLast');
renderUnifiedPagination({
    currentPage: 4, totalPages: 4, containerId: 'paginationContainerLast',
    tableId: 'keywordsTable', totalCount: 18, rowsShown: 18,
    onPageChange: () => {}, urlParams: {}, showInfo: true, showJump: false, baseUrl: '/x',
});
check('the last page renders no Load More', !last.output().includes('data-ut-load-more'));

const failed = checks.filter(([, ok]) => !ok).length;
console.log(`\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
