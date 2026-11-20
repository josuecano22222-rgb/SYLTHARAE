// Cancelling a job from its detail page must ask first (AUDIT-UI-02).
//
// The list page already asked through ConfirmDialog; the detail page posted
// immediately, so a mis-click killed an ingestion that could be hours into a
// folder. This runs the real page script and drives `act('cancel')` with the
// dialog answering "no" and then "yes", checking the request is only made in
// the second case.
import fs from 'node:fs';
import vm from 'node:vm';
import { makeDom } from './_vm_dom.mjs';

const SOURCE = fs.readFileSync('static/js/pages/job-detail-page.js', 'utf8');
const DATA = {
  notFound: 'NOT FOUND', jobId: 'J1', csrfToken: 'token',
  cancelJob: 'Cancel job', keepRunning: 'Keep running',
  jobCancelled: 'Job cancelled.', actionFailed: 'The action could not be completed.',
};

let failures = 0;
let checks = 0;
function check(what, condition, detail = '') {
  checks += 1;
  if (!condition) { failures += 1; console.log(`FAIL ${what} ${detail}`); }
  else console.log(`ok   ${what}`);
}

async function run({ answer }) {
  const { document, byId } = makeDom();
  document.getElementById('job-detail-page-data').textContent = JSON.stringify(DATA);
  document.getElementById('jStatus');
  const posts = [];
  const asked = [];
  const toasts = [];

  const fetchFn = async (url, options = {}) => {
    if (options.method === 'POST') {
      posts.push(url);
      return { ok: true, status: 200, json: async () => ({ success: true }) };
    }
    return { ok: true, status: 200, json: async () => ({
      success: true, job: { job_id: 'J1', status: 'RUNNING', statistics: {}, errors: [], warnings: [] },
      stats: {},
    }) };
  };

  const context = vm.createContext({
    document,
    window: {
      ConfirmDialog: { request: async (request) => { asked.push(request); return answer; } },
      Toast: { success: (m, o) => toasts.push([m, o]), error: (m) => toasts.push([m, 'error']) },
    },
    fetch: fetchFn,
    setInterval: () => 1,
    clearInterval: () => {},
    alert: (m) => toasts.push([m, 'alert']),
    console, Math, JSON, Number, String, Object, Array, Promise, Date,
  });
  vm.runInContext(SOURCE, context);
  await new Promise((resolve) => setImmediate(resolve));
  return { context, posts, asked, toasts, byId };
}

// 1. The dialog says no: nothing is posted.
const refused = await run({ answer: false });
await refused.context.window.act('cancel');
check('cancelling asks first', refused.asked.length === 1, JSON.stringify(refused.asked));
check('the question names the action', refused.asked[0] && refused.asked[0].action === 'jobs.cancel',
      JSON.stringify(refused.asked[0]));
check('the question is marked dangerous', refused.asked[0] && refused.asked[0].dangerous === true);
check('the question carries the job id', refused.asked[0] && refused.asked[0].scope === 'J1');
check('refusing the question posts nothing', refused.posts.length === 0, JSON.stringify(refused.posts));

// 2. The dialog says yes: exactly one cancel request.
const accepted = await run({ answer: true });
await accepted.context.window.act('cancel');
check('confirming posts the cancel once', accepted.posts.length === 1 &&
      accepted.posts[0] === '/api/jobs/J1/cancel', JSON.stringify(accepted.posts));
check('confirming reports the cancellation', accepted.toasts.some((t) => t[0] === DATA.jobCancelled),
      JSON.stringify(accepted.toasts));

// 3. Pause does not need a question, and still posts.
const paused = await run({ answer: false });
await paused.context.window.act('pause');
check('pausing posts without a dialog', paused.asked.length === 0 && paused.posts.length === 1,
      JSON.stringify({ asked: paused.asked.length, posts: paused.posts }));

// 4. Without the dialog runtime the page must still work (no crash, still asks
//    nothing) - the guard is what keeps an older page bundle alive.
const { document } = makeDom();
document.getElementById('job-detail-page-data').textContent = JSON.stringify(DATA);
document.getElementById('jStatus');
const postsNoDialog = [];
const contextNoDialog = vm.createContext({
  document, window: {}, console, Math, JSON, Number, String, Object, Array, Promise, Date,
  fetch: async (url, options = {}) => {
    if (options.method === 'POST') postsNoDialog.push(url);
    return { ok: true, status: 200, json: async () => ({ success: true, job: { job_id: 'J1', status: 'RUNNING', statistics: {}, errors: [], warnings: [] } }) };
  },
  setInterval: () => 1, clearInterval: () => {}, alert: () => {},
});
vm.runInContext(SOURCE, contextNoDialog);
await new Promise((resolve) => setImmediate(resolve));
await contextNoDialog.window.act('cancel');
check('without the dialog the action still works', postsNoDialog.length === 1, JSON.stringify(postsNoDialog));

console.log(`\njob_detail_cancel_smoke: ${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
