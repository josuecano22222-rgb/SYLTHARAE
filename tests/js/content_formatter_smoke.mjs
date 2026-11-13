// Client-side smoke test for the type-aware content formatters.
// Runs anywhere node is available:  node tests/js/content_formatter_smoke.mjs
// Exit code 0 = all structural checks passed (tabs, deck nav, markdown,
// log gutter, page dividers, headings, sanitized links).
// Minimal DOM stub for escapeHtml (the only DOM dependency at call time)
globalThis.document = {
    createElement: () => {
        const el = { textContent: '' };
        Object.defineProperty(el, 'innerHTML', {
            get() { return String(el.textContent).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
        });
        return el;
    }
};
globalThis.window = globalThis;

// Resolve the module next to this file so the harness runs from any checkout.
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const _formatterPath = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../../static/js/modules/content-formatter.js');
const { formatContentByType } = await import(_formatterPath);

const checks = [];
const check = (name, cond) => { checks.push([name, !!cond]); };

// --- Excel: 2 sheets, blank row + empty leading cell (exact positions) ---
const xlsx = `Sheet: Budget\nRows: 5\nColumns: 3\nRegion\tQ1\tQ2\nEMEA\t1200\t1350\n\n\tCORNERSTONE\t9\nAPAC\t800\t940\n\nSheet: Notes\nRows: 2\nColumns: 2\nAuthor\tRemark\nops\tReviewed ZEPHYR figures`;
let out = formatContentByType(xlsx, 'xlsx');
check('excel: tab bar with 2 tabs', (out.match(/class="sheet-tab[ "]/g) || []).length === 2);
check('excel: anchored sections', (out.match(/sheet-section-anchored/g) || []).length === 2);
check('excel: section titles', out.includes('sheet-section-title') && out.includes('Budget') && out.includes('Notes'));
check('excel: nav onclick wired', out.includes('contentViewerNav.showSheet(this)'));

// Address grid: column letters header (A, B, C for 3 columns)
check('excel: column letters A,B,C', out.includes('>A</th>') && out.includes('>B</th>') && out.includes('>C</th>'));
// Row gutter: rows 1..5 rendered (blank row 3 preserved)
check('excel: row numbers 1-5 gutter', (out.match(/class="sheet-row-num">\d+</g) || []).length >= 5);
// Blank row kept: the row AFTER the blank must carry number 4, not 3
check('excel: blank row keeps position (row 4 after blank row 3)',
      out.includes('data-cell-addr="A4"') && out.includes('data-cell-addr="B4"'));
// Empty leading cell preserved: CORNERSTONE sits in column B of row 4
check('excel: empty leading cell keeps column (CORNERSTONE at B4)',
      /data-cell-addr="B4" title="B4">CORNERSTONE</.test(out));
// Every data cell carries its address
check('excel: cells carry addr + tooltip', (out.match(/data-cell-addr="/g) || []).length >= 12);

// --- Excel: single bare table (CSV path) with blank row ---
const csv = `region\tproduct\tunits\nEMEA\tWidget\t12\n\nAPAC\tGadget\t7`;
out = formatContentByType(csv, 'csv');
check('csv: single grid, no tab bar', !out.includes('sheet-tab') && out.includes('EMEA') && out.includes('Widget'));
check('csv: column letters', out.includes('>A</th>') && out.includes('>B</th>'));
check('csv: blank row keeps position (APAC at row 4)', /data-cell-addr="A4" title="A4">APAC</.test(out));

// --- Email: one sender, multiple recipients ---
const eml = `Message #1\nFrom: Control <control@example.org>\nTo: Alice <alice@example.org>, Bob <bob@example.org>\nCC: Dave <dave@example.org>, Erin <erin@example.org>\nSubject: Quarterly digest\nDate: Mon, 13 Apr 2026\n--- Message Content ---\nThe digest is attached.`;
out = formatContentByType(eml, 'eml');
check('email: sender badge', out.includes('recipient-from') && out.includes('control@example.org'));
check('email: recipient badges rendered (4 recipients + 1 sender)', (out.match(/class="recipient-badge/g) || []).length === 5);
check('email: each recipient its own badge',
      out.includes('alice@example.org') && out.includes('bob@example.org')
      && out.includes('dave@example.org') && out.includes('erin@example.org'));
check('email: subject + body kept', out.includes('Quarterly digest') && out.includes('The digest is attached.'));

// --- PowerPoint: 3 slides, one table ---
const pptx = `Total Slides: 3\n\nSlide 1\n\nQuarterly Overview\n\nRevenue grew strongly\n\nSlide 2\n\nRegional Split\n\nTable 1\nRegion\tShare\nEMEA\t60%\n\nSlide 3\n\nOutlook\n\nZEPHYR expansion planned`;
out = formatContentByType(pptx, 'pptx');
check('ppt: slide nav bar', out.includes('slide-nav') && out.includes('data-slide-counter'));
check('ppt: counter 1 / 3', out.includes('1 / 3'));
check('ppt: 3 chips', (out.match(/class="slide-chip[ "]/g) || []).length === 3);
check('ppt: chip titles', out.includes('title="Quarterly Overview"'));
check('ppt: 3 slide blocks with chips', (out.match(/slide-number-chip/g) || []).length === 3);
check('ppt: table rendered in slide', out.includes('EMEA') && out.includes('60%'));
// ... and as a real table, not as tab-separated slide text (the same defect
// the Word path had: the "Table N" marker was eaten as explanatory text).
check('ppt: table markup inside the slide',
    /class="formatted-table-container"[\s\S]{0,200}<td>Region<\/td><td>Share<\/td>/.test(out)
    && !out.includes('Table 1'));
check('ppt: first slide active', out.includes('formatted-slide slide-active'));
check('ppt: prev/next wired', out.includes('goToSlide(this, -1)') && out.includes('goToSlide(this, 1)'));
check('ppt: default aria-labels (en)', out.includes('aria-label="Slides"')
    && out.includes('aria-label="Previous slide"') && out.includes('aria-label="Next slide"'));
check('excel: default sheet tablist aria-label', formatContentByType(xlsx, 'xlsx').includes('aria-label="Sheets"'));

// --- PDF: 2 pages ---
const pdf = `Page 1 | Method: text | Length: 20 chars\nfirst page content here\n\nPage 2 | Method: text | Length: 20 chars\nsecond page content there`;
out = formatContentByType(pdf, 'pdf');
check('pdf: 2 page dividers', (out.match(/page-number-chip/g) || []).length === 2);
check('pdf: chips numbered 1,2', out.includes('>1</span>') && out.includes('>2</span>'));
check('pdf: page content', out.includes('first page content') && out.includes('second page content'));

// --- Markdown ---
const md = `# Project ZEPHYR\n\nA **demo** README with \`inline code\`.\n\n## Features\n\n- Fast ingestion\n- Structured display\n\n\`\`\`python\nprint('hello')\n\`\`\`\n\n| Col A | Col B |\n|-------|-------|\n| 1     | 2     |\n\n> A wise quote\n\n[Docs](https://example.com) and [XSS](javascript:alert(1))`;
out = formatContentByType(md, 'md');
check('md: h1', out.includes('md-h1') && out.includes('Project ZEPHYR'));
check('md: h2', out.includes('md-h2'));
check('md: strong', out.includes('<strong>demo</strong>'));
check('md: inline code', out.includes('md-code'));
check('md: list', out.includes('md-list') && out.includes('<li>Fast ingestion</li>'));
check('md: code block', out.includes('md-code-block') && out.includes('print(&#x27;hello&#x27;)') || out.includes("print('hello')"));
check('md: table', out.includes('md-table') && out.includes('<th>Col A</th>'));
check('md: blockquote', out.includes('md-blockquote') && out.includes('A wise quote'));
check('md: safe link', out.includes('href="https://example.com"'));
check('md: javascript: link sanitized', !out.includes('javascript:alert'));
check('md: raw html escaped', !/<script/i.test(out));

// --- Log ---
const log = `2026-04-01T10:00:00Z INFO startup complete\n2026-04-01T10:00:05Z WARN disk usage at 81%`;
out = formatContentByType(log, 'log');
check('log: line gutter', (out.match(/log-line-num/g) || []).length === 2);
check('log: numbered 1,2', out.includes('>1</span>') && out.includes('>2</span>'));
check('log: content kept', out.includes('startup complete'));

// --- Word ---
const docx = `[Style: Heading 1] Annual Report\nThe year in review was stable.\n[Style: Heading 2] Details\nTable 1\nMetric\tValue\nUptime\t99.9%`;
out = formatContentByType(docx, 'docx');
check('word: h1 styled', out.includes('formatted-h1') && out.includes('Annual Report'));
check('word: h2 styled', out.includes('formatted-h2'));
check('word: table rendered', out.includes('Uptime') && out.includes('99.9%'));
// The checks above pass even when the table is flattened to a paragraph,
// so they never guarded the reported defect. These do: the stored rows must
// come back out as real table markup with one <td> per cell.
check('word: table markup emitted', out.includes('formatted-content-table')
    && (out.match(/<table/g) || []).length === 1);
check('word: every cell is its own cell', (out.match(/<td[^>]*>/g) || []).length === 4
    && /<td>Metric<\/td><td>Value<\/td>/.test(out));
check('word: rows stay rows', (out.match(/<tr[^>]*>/g) || []).length === 2);
check('word: marker line is not displayed', !out.includes('Table 1'));
// --- Word: one-column table + empty row + caption on the marker --------
// The extractor writes an all-empty row with an explicit tab so it cannot be
// mistaken for the blank line that separates document elements.
const docx2 = `Table 2 | Caption: Status legend\nApproved\n\t\nPending\n\nEnd of report.`;
out = formatContentByType(docx2, 'docx');
check('word: single-column table kept as table', (out.match(/<table/g) || []).length === 1);
check('word: empty row kept in position (Approved / empty / Pending)',
    (out.match(/<tr[^>]*>/g) || []).length === 3
    && /<td>Approved<\/td><\/tr><tr><td><\/td><\/tr><tr><td>Pending<\/td>/.test(out));
check('word: marker with caption is not displayed as prose',
    !out.includes('Table 2') && !out.includes('Caption:'));
check('word: paragraph after table is a paragraph',
    out.includes('End of report.') && !out.includes('Pending<\/p>'));
// --- Word: each "Table N" marker opens a new table ---------------------
const docx3 = `Table 3\nName\tRole\nAda\tAnalyst\n\nTable 4\nName\tRole\nGrace\tAdmiral`;
out = formatContentByType(docx3, 'docx');
check('word: two tables from two blocks', (out.match(/<table/g) || []).length === 2
    && out.includes('Ada') && out.includes('Grace'));
// --- Word: order is document order (heading then paragraph then table) --
const docx4 = `[Style: Heading 1] Title\nBody text follows.\n\nTable 5\nA\tB`;
out = formatContentByType(docx4, 'docx');
check('word: document order preserved',
    out.indexOf('Title') < out.indexOf('Body text follows.')
    && out.indexOf('Body text follows.') < out.indexOf('<table'));

// --- nav global ---
check('nav global exposed', typeof globalThis.contentViewerNav === 'object'
    && typeof globalThis.contentViewerNav.showSheet === 'function'
    && typeof globalThis.contentViewerNav.goToSlide === 'function');

// --- accessibility-label i18n injection ---
const { setContentFormatterTranslations } = await import(_formatterPath);
setContentFormatterTranslations({
    slidesNavLabel: 'שקופיות',
    previousSlideLabel: 'שקופית קודמת',
    nextSlideLabel: 'שקופית הבאה',
    sheetsNavLabel: 'גליונות'
});
out = formatContentByType(pptx, 'pptx');
check('i18n: translated slide nav aria-labels', out.includes('aria-label="שקופיות"')
    && out.includes('aria-label="שקופית קודמת"') && out.includes('aria-label="שקופית הבאה"'));
check('i18n: translated sheet tablist aria-label', formatContentByType(xlsx, 'xlsx').includes('aria-label="גליונות"'));
setContentFormatterTranslations(null);  // must not throw nor reset
check('i18n: null injection is a safe no-op', formatContentByType(pptx, 'pptx').includes('aria-label="שקופיות"'));

let failed = 0;
for (const [name, ok] of checks) {
    if (!ok) { failed++; console.log('FAIL:', name); }
}
console.log(`\\n${checks.length - failed}/${checks.length} checks passed`);
process.exit(failed ? 1 : 0);
