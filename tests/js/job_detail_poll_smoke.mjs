// Runs the real job detail page script (static/js/pages/job-detail-page.js)
// against the vm DOM stub with a controllable fetch and interval.
//
// The page polls every 2 s. A slow response must not let the next tick start
// another job request on top of it: the job GET is held open here while several
// ticks fire, and only one may be in flight. Once it resolves, polling resumes.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom } from './_vm_dom.mjs';

const source = fs.readFileSync(process.env.PAGE_JS || 'static/js/pages/job-detail-page.js', 'utf8');
const { document, byId } = makeDom();
document.getElementById('job-detail-page-data').textContent =
  JSON.stringify({ jobId: 'J1', csrfToken: 'token', notFound: 'not found' });
byId['jBar'] = byId['jBar'] || document.getElementById('jBar');
byId['jBar'].style = {};

const calls = [];
const pending = [];
let intervalFn = null;

function fetchFn(url) {
  calls.push(url);
  if (url === '/api/jobs/J1') {
    return new Promise((resolve) => pending.push(() => resolve({
      json: async () => ({ success: true, job: { job_id: 'J1', status: 'RUNNING', progress: 10,
        statistics: {}, errors: [], warnings: [] }, stats: {} }),
    })));
  }
  const body = url.endsWith('/errors') ? { errors: [], warnings: [] } : { events: [] };
  return Promise.resolve({ json: async () => body });
}

const context = vm.createContext({
  document,
  window: {},
  fetch: fetchFn,
  setInterval(fn) { intervalFn = fn; return 1; },
  alert() {},
  console,
  Math, JSON, Number, String, Object, Array, Promise,
});
vm.runInContext(source, context);
const jobCalls = () => calls.filter((u) => u === '/api/jobs/J1').length;
const flush = () => new Promise((r) => setTimeout(r, 0));

async function main() {
  await flush();
  if (jobCalls() !== 1) throw new Error(`expected 1 job request on load, got ${jobCalls()}`);
  if (typeof intervalFn !== 'function') throw new Error('page did not start a poll interval');

  // Five ticks while the first request is still pending: none may start a new one.
  for (let i = 0; i < 5; i += 1) intervalFn();
  await flush();
  if (jobCalls() !== 1) throw new Error(`overlapping poll: ${jobCalls()} job requests while one is pending`);

  // Release the pending response; the next tick must be allowed through again.
  pending.shift()();
  for (let i = 0; i < 20 && jobCalls() < 1; i += 1) await flush();
  await flush(); await flush();
  intervalFn();
  await flush();
  if (jobCalls() !== 2) throw new Error(`polling did not resume after the response: ${jobCalls()} job requests`);
  console.log('job_detail_poll_smoke: ok');
}

main().catch((err) => { console.error(err.message); process.exit(1); });
