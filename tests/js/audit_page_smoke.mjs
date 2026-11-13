// Runs the real Audit Log page module (static/js/pages/audit-page.js) against
// the vm DOM stub and a fake server, and checks what it renders: server text
// (user names, resources, detail JSON) is inserted as text, never markup;
// NULL is "none"; the list is the server's page in the server's order;
// Older/Newer follow the server's keyset cursor; "more entries" comes from
// has_more; filters are sent to the server, not applied in the browser; the
// server's error is shown verbatim.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom, counters } from './_vm_dom.mjs';

const HOSTILE = '<img src=x onerror="window.__pwned=1">';

const LABELS = JSON.parse(fs.readFileSync('templates/auth/audit.html', 'utf8')
  .match(/id="audit-page-labels">\{\{ (\{[\s\S]*?\}) \|tojson \}\}/)[1]
  .replace(/_\((['"])((?:\\.|(?!\1).)*)\1\)/g, (_, q, s) => JSON.stringify(s.replace(/\\'/g, "'")))
  .replace(/,\s*\}$/, '}'));

function entry(id, over = {}) {
  return { id, user_id: 7, username: 'alice', action: 'DATA_EXPORTED',
           resource: `export:report_artifact:report_run:${id}`, detail: { row_count: id },
           ip_address: '10.0.0.1', created_at: '2030-01-01T00:00:00+00:00', ...over };
}

async function run(routes, opts = {}) {
  const { document, byId } = makeDom();
  document.getElementById('audit-page-data').textContent = JSON.stringify({ page_size: 2, max_page_size: 200 });
  document.getElementById('audit-page-labels').textContent = JSON.stringify(LABELS);
  if (opts.noErrorBox) {
    // The error box can be absent on some renders; a real missing element is null.
    const lookup = document.getElementById.bind(document);
    document.getElementById = (id) => (id === 'auditError' ? null : lookup(id));
  } else {
    document.getElementById('auditError').classList.add('d-none');
  }
  document.getElementById('auditDetail').classList.add('d-none');
  const calls = [];
  const fetchImpl = (url, init) => {
    calls.push(url);
    const hit = routes.find(([re]) => re.test(url));
    const [status, body] = hit ? hit[1](url, init) : [404, { success: false, error: { message: 'no route' } }];
    return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) });
  };
  const ctx = { document, fetch: fetchImpl, console: opts.console || console, JSON, Promise, Object, String, Number, Math, Set,
                Error, URL, URLSearchParams, setTimeout, Date, Node: function Node() {} };
  Object.defineProperty(ctx.Node, Symbol.hasInstance, { value: (v) => v && typeof v === 'object' && 'nodeType' in v });
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync('static/js/pages/audit-page.js', 'utf8'), ctx);
  await new Promise((r) => setTimeout(r, 30));
  return { byId, calls };
}

const tick = () => new Promise((r) => setTimeout(r, 30));
const click = (node) => node.listeners.click.forEach((f) => f({ preventDefault() {} }));

let failures = 0;
function check(name, cond, detail) {
  if (!cond) { failures += 1; console.log('FAIL', name, detail === undefined ? '' : detail); } else console.log('ok', name);
}

// ---- 1: first page, hostile text, NULLs, has_more, paging by cursor
{
  const pages = {
    '': { items: [entry(9, { username: HOSTILE, resource: HOSTILE, detail: { note: HOSTILE } }),
                  entry(8, { username: null, user_id: null, ip_address: null, resource: null })],
          has_more: true, next_before_id: 8, total: null },
    '8': { items: [entry(3)], has_more: false, next_before_id: null, total: null },
  };
  const { byId, calls } = await run([
    [/^\/api\/audit\/actions/, () => [200, { success: true, items: ['DATA_EXPORTED', HOSTILE], capped: false, cap: 500 }]],
    [/^\/api\/audit\/9$/, () => [200, { success: true, entry: entry(9, { username: HOSTILE, detail: { note: HOSTILE } }) }]],
    [/^\/api\/audit\?/, (url) => [200, { success: true, ...pages[new URL(url, 'http://x').searchParams.get('before_id') || ''] }]],
  ]);
  const rows = byId.auditRows.children;
  check('renders the server page in its order', rows.length === 2
        && rows[0].children[5].children[0].attrs['data-entry'] === '9'
        && rows[1].children[5].children[0].attrs['data-entry'] === '8');
  const html = byId.auditRows.innerHTML + byId.auditAction.innerHTML;
  check('hostile user name, resource and action are text', !html.includes('<img') && html.includes('&lt;img'));
  check('NULL user is the "no user" label', rows[1].children[1].textContent === LABELS.no_user);
  check('NULL resource and IP are "none"', rows[1].children[3].textContent === LABELS.none
        && rows[1].children[4].textContent === LABELS.none);
  check('page size and no cursor on the first request',
        calls.some((u) => /^\/api\/audit\?limit=2$/.test(u)), calls);
  check('more entries stated from has_more; total not counted',
        byId.auditNote.textContent.includes(LABELS.more) && byId.auditNote.textContent.includes(LABELS.not_counted));
  check('Newer disabled on the first page, Older enabled', byId.auditNewer.disabled === true && byId.auditOlder.disabled === false);
  click(byId.auditOlder); await tick();
  check('Older sends the server cursor', calls.at(-1).includes('before_id=8'), calls.at(-1));
  check('last page says these are the oldest', byId.auditNote.textContent.includes(LABELS.no_more)
        && byId.auditOlder.disabled === true && byId.auditNewer.disabled === false);
  click(byId.auditNewer); await tick();
  check('Newer returns to the first page', !calls.at(-1).includes('before_id') && byId.auditRows.children.length === 2);
  click(byId.auditRows.children[0].children[5].children[0]); await tick();
  check('detail shown, JSON as text', !byId.auditDetail.classList.contains('d-none')
        && JSON.parse(byId.auditDetailJson.textContent).note === HOSTILE
        && !byId.auditDetailFields.innerHTML.includes('<img'));
  check('no innerHTML assignment', counters.innerHtmlWrites === 0, counters.innerHtmlWrites);
}

// ---- 2: filters go to the server; clicking a user filters by it; errors verbatim
{
  let fail = false;
  const { byId, calls } = await run([
    [/^\/api\/audit\/actions/, () => [200, { success: true, items: ['report.run'], capped: true, cap: 1 }]],
    [/^\/api\/audit\?/, () => (fail
      ? [503, { success: false, error: { code: 'QUERY_TIMEOUT', message: 'the audit query took longer than 5 s' } }]
      : [200, { success: true, items: [entry(4, { username: 'bob' })], has_more: false, next_before_id: null, total: null }])],
  ]);
  check('capped action list is stated', byId.auditNote.textContent !== undefined
        && calls.some((u) => u.startsWith('/api/audit/actions')));
  byId.auditAction.value = 'report.run';
  byId.auditResource.value = ' report_run: ';
  byId.auditForm.listeners.submit.forEach((f) => f({ preventDefault() {} })); await tick();
  const q = new URL(calls.at(-1), 'http://x').searchParams;
  check('filters sent to the server (trimmed)', q.get('action') === 'report.run' && q.get('resource') === 'report_run:'
        && !q.has('before_id'), calls.at(-1));
  click(byId.auditRows.children[0].children[1].children[0]); await tick();
  check('clicking a user filters by that user', new URL(calls.at(-1), 'http://x').searchParams.get('username') === 'bob');
  fail = true;
  click(byId.auditReset); await tick();
  check('reset clears the filters', !new URL(calls.at(-1), 'http://x').searchParams.has('action'));
  check('server error shown verbatim with its code', !byId.auditError.classList.contains('d-none')
        && byId.auditError.textContent === 'the audit query took longer than 5 s (QUERY_TIMEOUT)', byId.auditError.textContent);
}

// ---- 3: an empty log and an empty filtered result read differently
{
  const { byId } = await run([
    [/^\/api\/audit\/actions/, () => [200, { success: true, items: [], capped: false, cap: 500 }]],
    [/^\/api\/audit\?/, () => [200, { success: true, items: [], has_more: false, next_before_id: null, total: null }]],
  ]);
  check('empty log says the log is empty', byId.auditRows.textContent === LABELS.empty_log);
  byId.auditUsername.value = 'nobody';
  byId.auditForm.listeners.submit.forEach((f) => f({ preventDefault() {} })); await tick();
  check('empty filtered result says nothing matches', byId.auditRows.textContent === LABELS.empty);
}

// ---- 4: a failed request with no error box on the page must not throw
{
  const logged = [];
  const fakeConsole = { log() {}, warn() {}, error: (...args) => logged.push(args), info() {}, debug() {} };
  const rejections = [];
  const onRejection = (reason) => rejections.push(String((reason && reason.message) || reason));
  process.on('unhandledRejection', onRejection);
  await run([
    [/^\/api\/audit\/actions/, () => [200, { success: true, items: [], capped: false, cap: 500 }]],
    [/^\/api\/audit\?/, () => [503, { success: false, error: { code: 'QUERY_TIMEOUT', message: 'query timed out' } }]],
  ], { noErrorBox: true, console: fakeConsole });
  await tick(); await tick();
  process.off('unhandledRejection', onRejection);
  check('no uncaught TypeError when the error box is absent', rejections.length === 0, rejections.join(' | '));
  check('the server error is still logged when there is no box', logged.some((a) => String(a[0]).includes('Audit page error')), JSON.stringify(logged.map((a) => String(a[0]))));
}

if (failures) { console.log(`${failures} failure(s)`); process.exit(1); }
