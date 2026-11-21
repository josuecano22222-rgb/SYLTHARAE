// The jobs list must not call a failed request an empty list.
//
// AUDIT-UI-01: `loadJobs()` reported `!d.success || !d.jobs.length` as the empty
// state - "No jobs yet. Start an ingestion or import." - so a 500, a 401 and a
// dead network all told the operator their jobs were gone. This runs the real
// page script against the vm DOM with a controllable fetch and checks the three
// answers are distinguishable: empty, failed, and unauthorised.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom } from './_vm_dom.mjs';

const SOURCE = fs.readFileSync('static/js/pages/jobs-page.js', 'utf8');
const DATA = {
  noJobsYetStartAnIngestion: 'No jobs yet. Start an ingestion or import.',
  jobsCouldNotBeLoaded: 'The job list could not be loaded.',
  retrying: 'Retrying…',
  cancelJob: 'Cancel job',
  keepRunning: 'Keep running',
  jobCancelled: 'Job cancelled.',
  theJobCouldNotBeCancelled: 'The job could not be cancelled.',
  liveUpdatesEventStreamConnected: 'Live updates: event stream connected',
  csrfToken: 'token',
};

let failures = 0;
let checks = 0;
function check(what, condition, detail = '') {
  checks += 1;
  if (!condition) { failures += 1; console.log(`FAIL ${what} ${detail}`); }
  else console.log(`ok   ${what}`);
}

// The stub DOM does not store innerHTML (it only counts the writes), so the
// body's setter is replaced here to keep what the page wrote.
function watchInnerHtml(element) {
  let written = '';
  Object.defineProperty(element, 'innerHTML', {
    get() { return written; },
    set(value) { written = String(value); },
    configurable: true,
  });
  return () => written;
}

async function render(response) {
  const { document, byId } = makeDom();
  document.getElementById('jobs-page-data').textContent = JSON.stringify(DATA);
  document.getElementById('btnRefresh');   // the guard needs both to exist
  const readBody = watchInnerHtml(document.getElementById('jobsBody'));
  const context = vm.createContext({
    document, window: {}, console, fetch: async () => response,
    setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1,
    clearTimeout: () => {}, Math, JSON, Number, String, Object, Array, Promise,
    Date, URLSearchParams,
  });
  vm.runInContext(SOURCE, context);
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  return readBody();
}

const jsonOk = (body, status = 200) => ({
  ok: status >= 200 && status < 300, status,
  json: async () => body,
});

// 1. Success + no rows: the empty state.
const empty = await render(jsonOk({ success: true, jobs: [] }));
check('an empty list says there are no jobs', empty.includes(DATA.noJobsYetStartAnIngestion), empty);
check('an empty list does not claim a failure', !empty.includes(DATA.jobsCouldNotBeLoaded));

// 2a. A server error with no sentence: the generic failure text.
const failed = await render(jsonOk({ success: false }, 500));
check('a 500 shows the failure text', failed.includes(DATA.jobsCouldNotBeLoaded), failed);
// 2b. A server error that explains itself: its sentence wins over the generic one.
const explained = await render(jsonOk({ success: false, error: { code: 'internal_error', message: 'The database is unavailable.' } }, 500));
check('a 500 with a message shows that message', explained.includes('The database is unavailable.'), explained);
check('a 500 with a message still counts as a failure', !explained.includes(DATA.noJobsYetStartAnIngestion), explained);
check('a 500 does not claim the list is empty', !failed.includes(DATA.noJobsYetStartAnIngestion), failed);
check('a 500 shows the retry hint', failed.includes(DATA.retrying), failed);
check('the failure row is marked as an error', failed.includes('text-danger'), failed);

// 3. The server explains itself: its sentence is shown, escaped.
const hostile = await render(jsonOk({ success: false, error: { code: 'x', message: '<b>boom</b>' } }, 503));
check('the server sentence is shown', hostile.includes('boom'), hostile);
check('the server sentence is not markup', !hostile.includes('<b>boom</b>'), hostile);

// 4. A non-JSON body (an HTML error page, a proxy) must not throw.
const html = await render({ ok: false, status: 502, json: async () => { throw new SyntaxError('not json'); } });
check('a non-JSON failure still renders the failure row', html.includes(DATA.jobsCouldNotBeLoaded), html);

// 5. The network is down.
const down = await render({ ok: false, status: 0, json: async () => { throw new TypeError('offline'); } });
check('a dead network renders the failure row', down.includes(DATA.jobsCouldNotBeLoaded), down);
check('a dead network does not claim the list is empty', !down.includes(DATA.noJobsYetStartAnIngestion), down);

console.log(`\njobs_page_states_smoke: ${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
