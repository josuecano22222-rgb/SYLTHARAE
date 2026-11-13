// Runs the real Detection page module (static/js/pages/detection-page.js)
// against the vm DOM stub and a fake server. Checks: every number is the
// server's; "never analysed" and an unavailable version are shown as such;
// the per-detector action queues exactly {scope: stale, detectors: [d]} with
// the CSRF header and follows the job to its end, then reloads; the form
// refuses bad ids and an unconfirmed "all" without calling the server;
// server errors are verbatim; run rows are text (hostile error), NULL is
// "none", files link to /file/<path_id>; paging follows has_more.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom, counters } from './_vm_dom.mjs';

const HOSTILE = '<img src=x onerror="window.__pwned=1">';
const LABELS = JSON.parse(fs.readFileSync('templates/Signals/detection.html', 'utf8')
  .match(/id="detection-page-labels">\{\{ (\{[\s\S]*?\}) \|tojson \}\}/)[1]
  .replace(/_\((['"])((?:\\.|(?!\1).)*)\1\)/g, (_, q, s) => JSON.stringify(s.replace(/\\'/g, "'")))
  .replace(/,\s*\}$/, '}'));

const STATUS = {
  success: true, analysable_contents: 1200, stale_any_detector: 1200, exact: true,
  snapshot: '1:1:', measured_at: '2030-01-01T00:00:00+00:00',
  detectors: [
    { detector: 'temporal', current_version: 'temporal-1.2', current_version_available: true,
      at_current_version: { complete: 900, truncated: 3, no_text: 7, failed: 2 },
      at_older_versions: { complete: 40, truncated: 0, no_text: 0, failed: 1 },
      never_analysed: 248, stale: 291 },
    { detector: 'places', current_version: null, current_version_available: false,
      at_current_version: { complete: 0, truncated: 0, no_text: 0, failed: 0 },
      at_older_versions: { complete: 0, truncated: 0, no_text: 0, failed: 0 },
      never_analysed: 1200, stale: 1200 },
  ],
};

function run1(i, over = {}) {
  return { hash_id: 100 + i, detector: 'temporal', detector_ver: 'temporal-1.2', status: 'complete',
           anchor_date: null, chars_total: 5000, chars_scanned: 5000, signal_count: 4,
           trigger: 'ingestion', job_id: null, error: null, ran_at: '2030-01-01T00:00:00+00:00',
           path_id: 50 + i, file_name: `f${i}.txt`, path_count: 1, is_current_version: null, ...over };
}

async function start(routes) {
  const { document } = makeDom();
  document.getElementById('detection-page-data').textContent = JSON.stringify(
    { detectors: ['temporal', 'places'], statuses: ['complete', 'truncated', 'no_text', 'failed'],
      page_size: 2, max_hash_ids: 10000 });
  document.getElementById('detection-page-labels').textContent = JSON.stringify(LABELS);
  document.getElementById('detectionError').classList.add('d-none');
  document.getElementById('redetectScope').value = 'stale';
  const calls = [];
  const fetchImpl = (url, init = {}) => {
    calls.push({ method: init.method || 'GET', url, body: init.body ? JSON.parse(init.body) : null,
                 csrf: init.headers && init.headers['X-CSRFToken'] });
    const hit = routes.find(([m, re]) => m === (init.method || 'GET') && re.test(url));
    const [status, body] = hit ? hit[2](url, init) : [404, { success: false, error: { message: `no route ${url}` } }];
    return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
  };
  const ctx = { document, fetch: fetchImpl, console, JSON, Promise, Object, String, Number, Math, Set,
                Error, URL, URLSearchParams, setTimeout, Date, getCSRFTokenAsync: async () => 'tok',
                Node: function Node() {} };
  Object.defineProperty(ctx.Node, Symbol.hasInstance, { value: (v) => v && typeof v === 'object' && 'nodeType' in v });
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/js/pages/detection-page.js', 'utf8').replace(/^import .*$/m, ''), ctx);
  await tick();
  const ids = new Proxy({}, { get: (_, id) => document.getElementById(String(id)) });
  return { byId: ids, calls };
}

const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const click = (n) => n.listeners.click.forEach((f) => f({ preventDefault() {} }));
const submit = (n) => n.listeners.submit.forEach((f) => f({ preventDefault() {} }));
const posts = (calls) => calls.filter((c) => c.method === 'POST');

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}

const baseRoutes = (runsPages, extra = []) => [
  ...extra,
  ['GET', /^\/api\/signals\/detection\/status/, () => [200, STATUS]],
  ['GET', /^\/api\/signals\/detection\/runs/, (url) => {
    const offset = Number(new URL(url, 'http://x').searchParams.get('offset'));
    return [200, { success: true, ...runsPages[offset] }];
  }],
];

// ---- 1: coverage, runs, paging
{
  const pages = {
    0: { items: [run1(1, { status: 'failed', error: HOSTILE, signal_count: 0, chars_total: null, chars_scanned: null }),
                 run1(2, { path_count: 3, job_id: 'j-9' })], has_more: true, offset: 0, limit: 2 },
    2: { items: [run1(3, { path_id: null, file_name: null, path_count: 0 })], has_more: false, offset: 2, limit: 2 },
  };
  const { byId, calls } = await start(baseRoutes(pages));
  const rows = byId.coverageRows.children;
  const t = rows[0].children.map((c) => c.textContent);
  check('coverage row shows the server numbers', t[1] === 'temporal-1.2' && t[2] === (900).toLocaleString()
        && t[5] === '2' && t[6] === (41).toLocaleString() && t[7] === (248).toLocaleString() && t[8] === (291).toLocaleString(), t);
  check('unavailable version is shown as unavailable', rows[1].children[1].textContent === LABELS.version_unavailable);
  check('stale action only where stale > 0', rows[0].children[9].children[0].tagName === 'BUTTON'
        && rows[1].children[9].children[0].tagName === 'BUTTON');
  check('coverage note from server counts', byId.coverageNote.textContent.includes((1200).toLocaleString()));
  const r = byId.runRows.children;
  check('failed run error is text', !byId.runRows.innerHTML.includes('<img') && r[0].children[10].textContent === HOSTILE);
  check('NULL characters shown as none', r[0].children[7].textContent === LABELS.none);
  check('file links to /file/<path_id>', r[0].children[1].children[0].children[0].attrs.href === '/file/51');
  check('other files counted', r[1].children[1].textContent.includes('2'));
  check('job link', r[1].children[9].children[0].attrs.href === '/operations/jobs/j-9');
  check('more runs stated; Newer off', byId.runNote.textContent.includes(LABELS.runs_more) && byId.runNewer.disabled === true
        && byId.runOlder.disabled === false);
  click(byId.runOlder); await tick();
  check('Older asks for offset 2', calls.at(-1).url.includes('offset=2'));
  check('no file shown as such', byId.runRows.children[0].children[1].textContent === LABELS.no_file);
  check('end of runs stated', byId.runNote.textContent.includes(LABELS.runs_end) && byId.runOlder.disabled === true);
  byId.runDetector.value = 'temporal'; byId.runStatus.value = 'failed'; byId.runVersion.value = 'older';
  byId.runHash.value = ' 77 ';
  submit(byId.runFilters); await tick();
  const q = new URL(calls.at(-1).url, 'http://x').searchParams;
  check('run filters sent to the server', q.get('detector') === 'temporal' && q.get('status') === 'failed'
        && q.get('version') === 'older' && q.get('hash_id') === '77' && q.get('offset') === '0', calls.at(-1).url);
  check('no innerHTML assignment', counters.innerHtmlWrites === 0, counters.innerHtmlWrites);
}

// ---- 2: per-detector action queues stale for that detector and follows the job
{
  let polls = 0;
  const { byId, calls } = await start(baseRoutes({ 0: { items: [], has_more: false } }, [
    ['POST', /^\/api\/signals\/redetect$/, () => [202, { success: true, job: { job_id: 'job-1', status: 'QUEUED' } }]],
    ['GET', /^\/api\/jobs\/job-1$/, () => {
      polls += 1;
      return [200, { success: true, job: { job_id: 'job-1', status: 'COMPLETED_WITH_WARNINGS',
        result_summary: { processed: 291, signals: 812, failed: 3 } } }];
    }],
  ]));
  const statusCallsBefore = calls.filter((c) => c.url.startsWith('/api/signals/detection/status')).length;
  click(byId.coverageRows.children[0].children[9].children[0]); await tick(60);
  const [post] = posts(calls);
  check('queues exactly stale for that detector', JSON.stringify(post.body) === JSON.stringify({ scope: 'stale', detectors: ['temporal'] }), post.body);
  check('write carries the CSRF token', post.csrf === 'tok');
  check('job followed to its end', polls === 1 && byId.redetectJob.textContent.includes('291')
        && byId.redetectJob.textContent.includes('812') && byId.redetectJob.textContent.includes(LABELS.job_done_warnings));
  check('coverage reloaded after the job', calls.filter((c) => c.url.startsWith('/api/signals/detection/status')).length === statusCallsBefore + 1);
}

// ---- 3: the form: bad ids and unconfirmed "all" never reach the server; errors verbatim
{
  let fail = false;
  const { byId, calls } = await start(baseRoutes({ 0: { items: [], has_more: false } }, [
    ['POST', /^\/api\/signals\/redetect$/, () => (fail
      ? [400, { success: false, error: { code: 'VALIDATION_FAILED', message: 'at most 10000 hash_ids per request' } }]
      : [202, { success: true, job: { job_id: 'job-2', status: 'QUEUED' } }])],
    ['GET', /^\/api\/jobs\/job-2$/, () => [200, { success: true, job: { job_id: 'job-2', status: 'COMPLETED', result_summary: {} } }]],
  ]));
  byId.redetectScope.value = 'hash_ids';
  byId.redetectScope.listeners.change.forEach((f) => f({}));
  check('ids box shown for listed ids', !byId.redetectIdsBox.classList.contains('d-none'));
  byId.redetectIds.value = '12, x7';
  submit(byId.redetectForm); await tick();
  check('invalid id refused locally, named', posts(calls).length === 0 && byId.detectionError.textContent.includes('x7'));
  byId.redetectIds.value = '12 13,\n14';
  byId.redetect_places.checked = false;
  submit(byId.redetectForm); await tick(60);
  check('listed ids sent as numbers with the chosen detectors',
        JSON.stringify(posts(calls)[0].body) === JSON.stringify({ scope: 'hash_ids', detectors: ['temporal'], hash_ids: [12, 13, 14] }), posts(calls)[0].body);
  check('a job without a summary states no counts', byId.redetectJob.textContent.startsWith('Job job-2: COMPLETED.')
        && !/\b0\b/.test(byId.redetectJob.textContent), byId.redetectJob.textContent);
  byId.redetect_temporal.checked = false;
  submit(byId.redetectForm); await tick();
  check('no detector chosen is refused locally', posts(calls).length === 1 && byId.detectionError.textContent === LABELS.choose_detector);
  byId.redetect_temporal.checked = true;
  byId.redetectScope.value = 'all';
  submit(byId.redetectForm); await tick();
  check('unconfirmed "all" refused locally', posts(calls).length === 1 && byId.detectionError.textContent === LABELS.confirm_all);
  byId.redetectAllConfirm.checked = true;
  fail = true;
  submit(byId.redetectForm); await tick();
  check('server refusal shown verbatim with its code', posts(calls).length === 2
        && byId.detectionError.textContent === 'at most 10000 hash_ids per request (VALIDATION_FAILED)', byId.detectionError.textContent);
}

// ---- 4: empty history reads differently from an empty filtered result
{
  const { byId } = await start(baseRoutes({ 0: { items: [], has_more: false } }));
  check('no analysis yet', byId.runRows.textContent === LABELS.runs_empty_all);
  byId.runStatus.value = 'failed';
  submit(byId.runFilters); await tick();
  check('empty filtered result', byId.runRows.textContent === LABELS.runs_empty);
}

if (failures) { console.log(`${failures} failure(s)`); process.exit(1); }
