// Runs the real Reports page module (static/js/pages/reports-page.js)
// against a minimal DOM and a fake server, and checks what it renders:
// server/user text is inserted as text (never markup), NULL is shown as the
// "none" label, a truncated dataset says so, a failed run shows the server's
// error verbatim, and a viewer's Run button is disabled with the reason.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom, counters, esc } from './_vm_dom.mjs';

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const LABELS = JSON.parse(fs.readFileSync('templates/Reports/reports.html', 'utf8')
  .match(/id="reports-page-labels">\{\{ (\{[\s\S]*?\}) \|tojson \}\}/)[1]
  .replace(/_\((['"])((?:\\.|(?!\1).)*)\1\)/g, (_, q, s) => JSON.stringify(s.replace(/\\'/g, "'")))
  .replace(/,\s*\}$/, '}'));

async function run({ data, routes, search = '?run=5' }) {
  const { document, byId } = makeDom();
  document.getElementById('reports-page-data').textContent = JSON.stringify(data);
  document.getElementById('reports-page-labels').textContent = JSON.stringify(LABELS);
  document.getElementById('reportsError').classList.add('d-none');
  const calls = [];
  const fetchImpl = (url, init) => {
    calls.push([init && init.method, url]);
    const hit = routes.find(([re]) => re.test(url));
    const body = hit ? hit[1](url, init) : { success: false, error: { message: 'no route' } };
    return Promise.resolve({ ok: body.success !== false, status: body.success === false ? 400 : 200,
                             json: () => Promise.resolve(body) });
  };
  const href = `http://x/reports${search}`;
  const ctx = {
    document, fetch: fetchImpl, console, JSON, Promise, Object, String, Number, Math, Set, Error,
    URL, URLSearchParams, setTimeout,
    window: { location: { href, search }, history: { replaceState() {} } },
    Node: function Node() {},
    getCSRFTokenAsync: async () => 'tok',
  };
  // `child instanceof Node` in el(): make stub nodes count as Nodes.
  ctx.Node = function Node() {};
  Object.defineProperty(ctx.Node, Symbol.hasInstance, { value: (v) => v && typeof v === 'object' && 'nodeType' in v });
  vm.createContext(ctx);
  const src = fs.readFileSync('static/js/pages/reports-page.js', 'utf8').replace(/^import .*$/m, '');
  vm.runInContext(src, ctx);
  await new Promise((r) => setTimeout(r, 50));
  return { byId, calls };
}

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}

const DEFINITION = {
  report_id: 'search_results', version: 1, key: 'search_results@1', title: 'Search Results Report',
  description: 'desc', unit: 'path', help: { title: 't', summary: 's' }, can_run: true,
  parameters: [{ name: 'criteria', type: 'criteria', label: 'Search criteria', required: true, default: null,
                 choices: [], minimum: null, maximum: null, max_length: null }],
  datasets: [{ key: 'search_results.matches@1', semantics: 'capped', row_limit: 2, columns: [] }],
};
const COLUMNS = [{ name: 'path_id', type: 'integer', nullable: false, label: 'File ID' },
                 { name: 'file_name', type: 'text', nullable: false, label: 'File name' },
                 { name: 'note', type: 'text', nullable: true, label: 'Note' }];
const RUN = {
  id: 5, report_key: 'search_results@1', title: 'Search Results Report', status: 'completed',
  requester_username: HOSTILE, parameters: { criteria: { text: HOSTILE } }, snapshot: '100:100:',
  snapshot_at: '2026-09-28T10:00:00+00:00', saved_search_id: null, criteria_fingerprint: 'a'.repeat(64),
  datasets: [{ dataset_key: 'search_results.matches@1', row_count: 2, truncated: true, semantics: 'capped',
               query_fingerprint: 'b'.repeat(64) }],
};
const FILE = { id: 9, run_id: 5, format: 'csv', dataset_key: 'search_results.matches@1',
  renderer_version: 'report-csv/1', filename: 'report_search_results_v1_run5_search_results.csv',
  media_type: 'text/csv', byte_size: 120, sha256: 'c'.repeat(64), manifest_sha256: 'd'.repeat(64),
  creator_username: HOSTILE, creator_role: 'analyst', job_id: 'J2', created_at: '2026-09-28T10:01:00+00:00',
  language: 'en' };
const ARTIFACTS = { success: true, items: [FILE],
  formats: [{ format: 'csv', renderer_version: 'report-csv/1', single_dataset: true },
            { format: 'json', renderer_version: 'report-json/1', single_dataset: false }],
  unavailable: [{ format: 'pdf', reason: `step 18 ${HOSTILE}` }] };
const baseRoutes = (run) => [
  [/\/api\/reports\/definitions$/, () => ({ success: true, items: [DEFINITION] })],
  [/\/api\/search\/saved$/, () => ({ success: true, searches: [{ id: 3, name: HOSTILE }] })],
  [/\/api\/reports\/runs\?/, () => ({ success: true, items: [run], total: 1 })],
  [/\/api\/reports\/runs\/5\/datasets\//, () => ({
    success: true, dataset_key: 'search_results.matches@1', semantics: 'capped', row_limit: 2,
    row_count: 2, truncated: true, columns: COLUMNS,
    rows: [{ path_id: 1, file_name: HOSTILE, note: null }, { path_id: 2, file_name: 'b', note: '' }] })],
  [/\/api\/reports\/runs\/5\/artifacts$/, (url, init) => (init && init.method === 'POST'
    ? { success: true, existing: true, artifact: FILE, job: null } : ARTIFACTS)],
  [/\/api\/reports\/artifacts\/9\/verify$/, () => ({ success: true, ok: false,
    checks: { content_sha256: true, manifest_sha256: false } })],
  [/\/api\/reports\/runs\/5$/, () => ({ success: true, run })],
];

// 1. A completed, truncated run with hostile text everywhere.
{
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes: baseRoutes(RUN) });
  const html = ['runRows', 'runProvenance', 'runDataRows', 'reportParameters', 'runDetailTitle']
    .map((id) => byId[id].innerHTML).join('\n');
  check('hostile text is rendered', html.includes('&lt;img'), html.slice(0, 300));
  check('hostile text never becomes markup', !html.includes('<img'), html);
  check('no innerHTML assignment', counters.innerHtmlWrites === 0, counters.innerHtmlWrites);
  const cells = byId.runDataRows.children[0].children;
  check('NULL shown as the none label', cells[2].textContent === LABELS.none, cells[2].textContent);
  check('empty string is not shown as none', byId.runDataRows.children[1].children[2].textContent === '');
  check('truncation stated', byId.runDatasetNote.textContent.includes(LABELS.truncated_capped.split(':')[0]),
        byId.runDatasetNote.textContent);
  check('rows column shows truncation mark', byId.runRows.children[0].children[3].textContent === '2+');
  check('detail panel shown', !byId.runDetail.classList.contains('d-none'));
}

// 2. A failed run: the server's error verbatim.
{
  const failed = { ...RUN, status: 'failed', error: `boom ${HOSTILE}`, datasets: [], snapshot: null };
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes: baseRoutes(failed) });
  check('failure text verbatim', byId.runOutcome.textContent === LABELS.failed.replace('%(error)s', `boom ${HOSTILE}`),
        byId.runOutcome.textContent);
  check('failure not markup', !byId.runOutcome.innerHTML.includes('<img'));
  check('no rows for a failed run', byId.runDataRows.textContent === LABELS.no_rows, byId.runDataRows.textContent);
}

// 3. A viewer: Run disabled, reason shown.
{
  const viewerDef = { ...DEFINITION, can_run: false };
  const routes = baseRoutes(RUN).map(([re, f]) => (String(re).includes('definitions')
    ? [re, () => ({ success: true, items: [viewerDef] })] : [re, f]));
  const { byId } = await run({ data: { can_run: false, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes, search: '' });
  check('run disabled for viewers', byId.reportRun && byId.reportRun.disabled === true);
  check('read-only reason shown', byId.reportsNotice.textContent === LABELS.read_only, byId.reportsNotice.textContent);
}

// 4. Submitting a saved search sends its id, not criteria.
{
  const posted = [];
  const routes = [[/\/api\/reports\/runs$/, (url, init) => { posted.push(JSON.parse(init.body));
    return { success: true, run: RUN, job: { job_id: 'J', status: 'COMPLETED' } }; }], ...baseRoutes(RUN)];
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes, search: '' });
  byId.param_criteria_saved_select.value = '3';
  byId.param_criteria_saved.checked = true;
  for (const f of byId.reportForm.listeners.submit || []) f({ preventDefault() {} });
  await new Promise((r) => setTimeout(r, 30));
  check('one run posted', posted.length === 1, posted);
  check('saved search id sent', posted[0] && posted[0].saved_search_id === 3 && !('criteria' in posted[0].parameters),
        JSON.stringify(posted[0]));
  check('report identity sent', posted[0] && posted[0].report_id === 'search_results' && posted[0].version === 1);
}

// 5. Files: formats from the server, CSV needs a dataset, links, verification.
{
  const posted = [];
  const routes = baseRoutes(RUN).map(([re, f]) => (String(re).includes('artifacts$')
    ? [re, (url, init) => { if (init && init.method === 'POST') posted.push(JSON.parse(init.body)); return f(url, init); }]
    : [re, f]));
  const { byId, calls } = await run({ data: { can_run: true, is_admin: false, languages: ['en', 'ar'],
                                              list_limit: 50, row_page: 100,
                                              max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                                      routes });
  check('files section shown', !byId.runFiles.classList.contains('d-none'));
  check('formats from the server', byId.fileFormat.children.map((o) => o.attrs.value).join() === 'csv,json',
        byId.fileFormat.children.map((o) => o.attrs.value));
  check('csv offers the run datasets', !byId.fileDataset.disabled
        && byId.fileDataset.children.map((o) => o.attrs.value).join() === 'search_results.matches@1');
  const row = byId.fileRows.children[0];
  const links = row.children[7].children;
  check('download link', links[0].attrs.href === '/api/reports/artifacts/9/download'
        && links[0].attrs.download === FILE.filename, links[0].attrs);
  check('manifest link', links[1].attrs.href === '/api/reports/artifacts/9/manifest');
  check('sha shown', row.children[5].textContent === FILE.sha256);
  check('language column shows the file language', row.children[2].textContent === FILE.language,
        row.children[2].textContent);
  check('language picker offers the shipped languages',
        byId.fileLanguage.children.map((o) => o.attrs.value).join() === 'en,ar',
        byId.fileLanguage.children.map((o) => o.attrs.value));
  check('creator is text', !row.innerHTML.includes('<img') && row.textContent.includes(HOSTILE));
  check('unavailable format stated as text', byId.fileUnavailable.textContent.includes('PDF')
        && !byId.fileUnavailable.innerHTML.includes('<img'), byId.fileUnavailable.textContent);
  for (const f of byId.fileCreate.listeners.click || []) f();
  await new Promise((r) => setTimeout(r, 30));
  check('create posts format, dataset and language', posted.length === 1
        && posted[0].format === 'csv' && posted[0].dataset_key === 'search_results.matches@1'
        && posted[0].language === 'en', posted);
  check('existing file reported', byId.fileJob.textContent === LABELS.file_existing, byId.fileJob.textContent);
  byId.fileFormat.value = 'json';
  for (const f of byId.fileFormat.listeners.change || []) f();
  check('json covers all datasets', byId.fileDataset.disabled && byId.fileDataset.value === '');
  for (const f of byId.fileCreate.listeners.click || []) f();
  await new Promise((r) => setTimeout(r, 30));
  check('format choice kept after the list reloads', byId.fileFormat.value === 'json', byId.fileFormat.value);
  check('json posts no dataset', posted.length === 2 && posted[1].format === 'json' && !('dataset_key' in posted[1]),
        posted[1]);
  for (const f of links[2].listeners.click || []) f();
  await new Promise((r) => setTimeout(r, 30));
  check('failed verification named', byId.fileVerify.textContent
        === LABELS.verified_bad.replace('%(checks)s', 'manifest_sha256'), byId.fileVerify.textContent);
  check('file list read from the server', calls.some(([m, u]) => m === 'GET' && u === '/api/reports/runs/5/artifacts'));
}

// 6. Analyses (step 16): the server-rendered voices are shown verbatim, as
// text, in voice order; not-measurable is stated; the definition lists its
// analyses and enum choices show their translated labels.
{
  const KDEF = { ...DEFINITION, report_id: 'term_keyness', key: 'term_keyness@1',
    analyses: [{ key: 'term_keyness@1', title: `Distinctive ${HOSTILE}`, kind: 'keyness' }],
    parameters: [...DEFINITION.parameters, { name: 'direction', type: 'enum', label: 'Direction',
      required: false, default: 'over', choices: ['over', 'under'],
      choice_labels: ['More often', 'Less often'], minimum: null, maximum: null, max_length: null }] };
  const voices = ['measure', 'finding', 'confidence', 'consequence', 'caveat'];
  const KRUN = { ...RUN, report_key: 'term_keyness@1', analyses: [
    { analysis_key: 'term_keyness@1', title: 'Distinctive terms', state: 'measured', reason: null,
      template_set: 'keyness', template_version: 1,
      text: voices.map((v) => ({ voice: v, text: `${v} says ${HOSTILE}` })) },
    { analysis_key: 'other@1', title: 'Other', state: 'not_measurable', reason: 'no_target',
      template_set: 'keyness', template_version: 1,
      text: voices.map((v) => ({ voice: v, text: v })) }] };
  const routes = baseRoutes(KRUN).map(([re, f]) => (String(re).includes('definitions')
    ? [re, () => ({ success: true, items: [KDEF] })] : [re, f]));
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 }, routes });
  check('analyses section shown', !byId.runAnalyses.classList.contains('d-none'));
  const first = byId.runAnalysisList.children[0];
  const dl = first.children[2];
  const terms = dl.children.filter((_, i) => i % 2 === 0).map((n) => n.textContent);
  check('five voices in order with their labels', terms.join('|') === voices.map((v) => LABELS[`voice_${v}`]).join('|'), terms);
  check('voice text verbatim and not markup', dl.children[3].textContent === `finding says ${HOSTILE}`
        && !byId.runAnalysisList.innerHTML.includes('<img'), dl.children[3].textContent);
  check('state and templates stated', first.children[0].textContent.includes(LABELS.analysis_measured)
        && first.children[1].textContent.includes('keyness@1'), first.children[1].textContent);
  check('not measurable stated', byId.runAnalysisList.children[1].children[0].textContent
        .includes(LABELS.analysis_not_measurable));
  check('definition lists its analyses as text', byId.reportAbout.textContent.includes('term_keyness@1')
        && !byId.reportAbout.innerHTML.includes('<img'));
  const direction = byId.param_direction;
  check('enum choices show translated labels', direction.children.map((o) => o.textContent).join('|')
        === `${LABELS.none}|More often|Less often` && direction.value === 'over',
        direction.children.map((o) => o.textContent));
}

// 7. A run without analyses keeps the section hidden.
{
  const { byId } = await run({ data: { can_run: true, is_admin: false, list_limit: 50, row_page: 100,
                                       max_row_page: 500, job_poll_ms: 1, job_poll_limit: 3 },
                               routes: baseRoutes(RUN) });
  check('no analyses: section hidden', byId.runAnalyses.classList.contains('d-none'));
}

process.exit(failures ? 1 : 0);
