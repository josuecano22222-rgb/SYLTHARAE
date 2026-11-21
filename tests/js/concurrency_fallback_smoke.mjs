// The concurrency dashboard must say what is true when a manager is missing.
//
// AUDIT-ROUND-3: `/concurrency/api/metrics` used to answer with an "error" stub
// for a manager whose multiprocessing start had failed, and the page rendered
// the exception text. Now the server answers with a fixed sentence and a
// `mode`, and this harness runs the real page module against that answer:
//
//   * an unavailable manager is labelled `unavailable` and explains itself;
//   * the page-level banner appears only when the server says it is degraded,
//     and it carries the measured worker recommendation;
//   * the process and pool lists say why they are empty instead of "No
//     processes";
//   * whatever the server sends goes through the escaper, never into markup.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom, esc } from './_vm_dom.mjs';

let source = fs.readFileSync('static/js/pages/concurrency-dashboard-page.js', 'utf8');
// The page imports the escaper; the vm has no module loader, so the import is
// dropped and an escaping spy is injected in its place. Using a spy is the
// point: it records that the server's sentence went *through* the escaper.
source = source.replace(/^import .*$/m, '');

let failures = 0;
let checks = 0;
function check(what, condition, detail = '') {
  checks += 1;
  if (!condition) { failures += 1; console.log(`FAIL ${what} ${detail}`); }
  else console.log(`ok   ${what}`);
}

const REASON = 'The multiprocessing manager could not be started on this host, '
  + 'so process and worker-pool figures are not available.';

const FULL = {
  success: true, mode: 'full', degraded: false, unavailable: [],
  metrics: {
    thread_manager: { status: 'active', active_threads: 2, total_threads: 5, completed: 3, errors: 0 },
    process_manager: { status: 'inactive', active_processes: 0, total_processes: 0 },
    async_manager: { status: 'active', active_tasks: 0, total_tasks: 1 },
    pool_manager: { status: 'inactive', total_pools: 0, total_workers: 0 },
  },
};

const DEGRADED = {
  success: true, mode: 'threads-only', degraded: true,
  unavailable: ['pool_manager', 'process_manager'],
  metrics: {
    thread_manager: { status: 'active', active_threads: 2, total_threads: 5, completed: 3, errors: 0 },
    process_manager: {
      status: 'unavailable', mode: 'threads-only',
      reason_code: 'multiprocessing_unavailable', reason: REASON,
    },
    async_manager: { status: 'active', active_tasks: 0, total_tasks: 1 },
    pool_manager: {
      status: 'unavailable', mode: 'threads-only',
      reason_code: 'multiprocessing_unavailable', reason: REASON,
    },
    worker_capacity: { recommended_workers: 2, cpu_count: 2 },
  },
};

function watch(element) {
  let written = '';
  Object.defineProperty(element, 'innerHTML', {
    get() { return written; },
    set(value) { written = String(value); },
    configurable: true,
  });
  return () => written;
}

async function render(routes) {
  const { document } = makeDom();
  const escaped = [];
  const escapeHtml = (text) => { escaped.push(text); return esc(text); };

  const fetch = async (url) => {
    const body = routes[url] ?? { success: false, error: { message: 'no route' } };
    return { ok: true, status: 200, json: async () => body };
  };

  const listeners = {};
  document.addEventListener = (type, fn) => { (listeners[type] ||= []).push(fn); };

  // The banner is written with textContent (a text node in the stub), the cards
  // with innerHTML, so both are read back the way the page writes them.
  const bannerElement = document.getElementById('concurrency-mode');
  const processCard = watch(document.getElementById('process-metrics'));
  const threadCard = watch(document.getElementById('thread-metrics'));
  const processList = watch(document.getElementById('processes-list'));
  const poolList = watch(document.getElementById('pools-list'));

  const context = vm.createContext({
    document, window: {}, console, fetch, escapeHtml,
    setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1,
    clearTimeout: () => {}, Math, JSON, Number, String, Object, Array, Promise,
    Date, Error, URLSearchParams,
  });
  vm.runInContext(source, context);

  for (const fn of listeners.DOMContentLoaded || []) fn();
  for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setImmediate(resolve));

  return { banner: bannerElement.textContent, processCard: processCard(), threadCard: threadCard(),
           processList: processList(), poolList: poolList(), escaped };
}

// 1. A healthy host: no banner, ordinary cards.
{
  const page = await render({
    '/concurrency/api/metrics': FULL,
    '/concurrency/api/threads': { success: true, threads: [] },
    '/concurrency/api/processes': { success: true, processes: [] },
    '/concurrency/api/async-tasks': { success: true, tasks: [] },
    '/concurrency/api/pools': { success: true, pools: [] },
  });
  check('a healthy host shows no degradation banner', page.banner === '', page.banner);
  check('a healthy host still renders the thread card',
        page.threadCard.includes('active'), page.threadCard);
  check('process list falls back to its own empty state',
        page.processList.includes('No processes'), page.processList);
}

// 2. Degraded host: banner, unavailable cards, honest lists.
{
  const page = await render({
    '/concurrency/api/metrics': DEGRADED,
    '/concurrency/api/threads': { success: true, threads: [] },
    '/concurrency/api/processes': {
      success: true, available: false, reason: REASON, processes: [], count: 0,
    },
    '/concurrency/api/async-tasks': { success: true, tasks: [] },
    '/concurrency/api/pools': {
      success: true, available: false, reason: REASON, pools: [], count: 0,
    },
  });
  check('the banner names the mode and the measurement',
        page.banner.includes('worker-pool figures are not available')
        && page.banner.includes('2 worker'), page.banner);
  check('the unavailable card says so', page.processCard.includes('unavailable'), page.processCard);
  check('the unavailable card explains itself', page.processCard.includes('could not be started'),
        page.processCard);
  check('the thread card is still live', page.threadCard.includes('active'), page.threadCard);
  check('the process list explains the empty list',
        page.processList.includes('could not be started') && !page.processList.includes('No processes'),
        page.processList);
  check('the pool list explains the empty list',
        page.poolList.includes('could not be started') && !page.poolList.includes('No pools'),
        page.poolList);
  check('the server sentence went through the escaper',
        page.escaped.some((value) => String(value).includes('could not be started')),
        JSON.stringify(page.escaped));
}

// 3. A hostile sentence is escaped, not executed.
{
  const hostile = '<img src=x onerror="window.__pwned=1">';
  const page = await render({
    '/concurrency/api/metrics': {
      success: true, mode: 'threads-only', degraded: true, unavailable: ['process_manager'],
      metrics: {
        thread_manager: { status: 'active' },
        process_manager: { status: 'unavailable', reason: hostile },
        async_manager: { status: 'active' },
        pool_manager: { status: 'unavailable', reason: hostile },
      },
    },
    '/concurrency/api/threads': { success: true, threads: [] },
    '/concurrency/api/processes': { success: true, available: false, reason: hostile, processes: [] },
    '/concurrency/api/async-tasks': { success: true, tasks: [] },
    '/concurrency/api/pools': { success: true, available: false, reason: hostile, pools: [] },
  });
  check('the hostile sentence is not markup',
        !page.processCard.includes('<img') && !page.processList.includes('<img'),
        page.processCard);
  check('the hostile sentence is still readable (escaped)',
        page.processCard.includes('&lt;img'), page.processCard);
}

console.log(`\nconcurrency_fallback_smoke: ${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
