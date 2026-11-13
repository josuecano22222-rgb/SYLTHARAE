/**
 * Jobs list: live table, cancel/pause/resume, event stream.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
// Server-rendered values from #jobs-page-data.
var JOBS_PAGE_DATA = (function () {
  var el = document.getElementById('jobs-page-data');
  if (!el) return {};
  try { return JSON.parse(el.textContent || '{}'); }
  catch (_error) { return {}; }
})();

(function () {
  // The static sidebar re-runs this page's classic script on every
  // in-application navigation. A previous run's polling timer and event
  // stream must die before this one binds to the fresh DOM, or they keep
  // firing against elements that no longer exist.
  if (typeof window.__jobsPageTeardown === 'function') {
    try { window.__jobsPageTeardown(); } catch (e) { /* already dead */ }
    window.__jobsPageTeardown = null;
  }

  if (!document.getElementById('jobsBody') || !document.getElementById('btnRefresh')) return;

  const fmtDur = (s) => {
    if (s == null) return '—';
    const m = Math.floor(s / 60), sec = Math.round(s % 60);
    return m ? `${m}m ${sec}s` : `${sec}s`;
  };
  // The status words and their colours come from the shared vocabulary
  // (core/frontend/status_vocabulary.py, injected as JSON by base.html):
  // the job pages used to disagree with each other about a status such as
  // COMPLETED_WITH_WARNINGS.
  const statusBadge = (s) => (window.InforaxisStatus ? window.InforaxisStatus.badge(s) : '<span class="badge bg-secondary">' + String(s == null ? '' : s) + '</span>');

  async function loadJobs() {
    // A tick can land after the reader navigated away (the swap keeps this
    // window alive): the page's controls are gone - say nothing, do nothing.
    const fType = document.getElementById('fType');
    const fStatus = document.getElementById('fStatus');
    const fUser = document.getElementById('fUser');
    const body = document.getElementById('jobsBody');
    if (!fType || !fStatus || !fUser || !body) return;
    const p = new URLSearchParams();
    const t = fType.value;
    const st = fStatus.value;
    const u = fUser.value.trim();
    if (t) p.set('type', t === 'import' ? '' : t);
    if (st) p.set('status', st);
    if (u) p.set('user', u);
    p.set('limit', '100');
    const r = await fetch('/api/jobs?' + p.toString());
    const d = await r.json();
    if (!d.success || !(d.jobs || []).length) {
      body.innerHTML = '<tr><td colspan="11" class="text-center text-muted py-4">' + JOBS_PAGE_DATA.noJobsYetStartAnIngestion + '</td></tr>';
      return;
    }
    const esc = (s) => String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    const issueBadge = (j) => {
      const ec = Number(j.errors_count) || 0;
      const wc = Number(j.warnings_count) || 0;
      if (!ec && !wc) return '<span class="text-muted">—</span>';
      const parts = [];
      if (ec) parts.push(`<span class="badge bg-danger-subtle text-danger">${ec} err</span>`);
      if (wc) parts.push(`<span class="badge bg-warning-subtle text-warning">${wc} warn</span>`);
      return parts.join(' ');
    };
    const classifySource = (j) => {
      if (j.classification_source) return j.classification_source;
      if (j.job_type === 'ingestion' || j.job_type === 'batch_import') return '—';
      return j.source || '—';
    };
    body.innerHTML = d.jobs.map(j => {
      const start = j.started_at ? new Date(j.started_at).toLocaleTimeString() : '—';
      let dur = '—';
      if (j.started_at) {
        const end = j.completed_at ? new Date(j.completed_at) : new Date();
        dur = fmtDur((end - new Date(j.started_at)) / 1000);
      }
      const active = ['QUEUED','RUNNING','PAUSED','CANCELLING'].includes(j.status);
      const actions = [
        `<a href="/operations/jobs/${j.job_id}" class="btn btn-sm btn-outline-primary">View</a>`
      ];
      if (active && !['PAUSED'].includes(j.status))
        actions.push(`<button class="btn btn-sm btn-outline-danger" data-on-click="cancelJob('${j.job_id}')">Cancel</button>`);
      if (j.status === 'PAUSED')
        actions.push(`<button class="btn btn-sm btn-outline-success" data-on-click="resumeJob('${j.job_id}')">Resume</button>`);
      if (['FAILED','CANCELLED','COMPLETED_WITH_WARNINGS'].includes(j.status))
        actions.push(`<button class="btn btn-sm btn-outline-secondary" data-on-click="retryJob('${j.job_id}')">Retry</button>`);
      return `<tr>
        <td><a href="/operations/jobs/${j.job_id}"><code>${j.job_id}</code></a></td>
        <td>${(j.job_type||'').replace(/_/g,' ')}</td>
        <td class="small text-break">${esc(classifySource(j))}</td>
        <td class="small text-break">${esc(j.classification_side || '—')}</td>
        <td>${statusBadge(j.status)}</td>
        <td>
          <div class="progress" style="height:8px;">
            <!-- Width transitions so a poll shows movement, not a snap. -->
            <div class="progress-bar${active ? ' progress-bar-striped progress-bar-animated' : ''}"
                 role="progressbar" aria-valuemin="0" aria-valuemax="100"
                 aria-valuenow="${j.progress||0}"
                 style="width:${j.progress||0}%; transition: width .3s ease;"></div>
          </div>
          <small class="text-muted">
            ${j.progress||0}%
            ${(j.statistics && j.statistics.files_discovered != null)
              ? ` · ${(j.statistics.files_processed ?? 0)}/${j.statistics.files_discovered}`
              : ''}
            ${(j.statistics && j.statistics.files_nested)
              ? ` · <span title="Objects found inside archives, attachments and embedded documents">+${j.statistics.files_nested} nested</span>`
              : ''}
          </small>
        </td>
        <td class="small">${issueBadge(j)}</td>
        <td>${start}</td><td>${dur}</td><td>${j.created_by||'—'}</td>
        <td class="d-flex gap-1 flex-wrap">${actions.join('')}</td>
      </tr>`;
    }).join('');
  }

  // The page owns the operation; the component owns the question. The action
  // id is the one the Action Registry knows ('jobs.cancel'), so the dialog,
  // the inspector and the catalog are talking about the same thing.
  window.cancelJob = async function (id) {
    const confirmed = await ConfirmDialog.request({
      action: 'jobs.cancel',
      scope: id,
      confirmLabel: JOBS_PAGE_DATA.cancelJob,
      cancelLabel: JOBS_PAGE_DATA.keepRunning,
      dangerous: true,
    });
    if (!confirmed) return;
    try {
      const response = await fetch(`/api/jobs/${id}/cancel`, {method:'POST',
        headers: {'X-CSRFToken': JOBS_PAGE_DATA.csrfToken}});
      if (!response.ok) throw new Error(String(response.status));
      Toast.success(JOBS_PAGE_DATA.jobCancelled, {detail: id});
    } catch (error) {
      Toast.error(JOBS_PAGE_DATA.theJobCouldNotBeCancelled, {detail: id});
    }
    loadJobs();
  };
  window.resumeJob = async function (id) {
    await fetch(`/api/jobs/${id}/resume`, {method:'POST',
      headers: {'X-CSRFToken': JOBS_PAGE_DATA.csrfToken}});
    loadJobs();
  };
  window.retryJob = async function (id) {
    await fetch(`/api/jobs/${id}/retry`, {method:'POST',
      headers: {'X-CSRFToken': JOBS_PAGE_DATA.csrfToken}});
    loadJobs();
  };

  const btnRefresh = document.getElementById('btnRefresh');
  btnRefresh.addEventListener('click', loadJobs);
  ['fType','fStatus'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.addEventListener('change', loadJobs);
  });

  // Live updates: SSE when available (single-process), polling fallback.
  let pollTimer = setInterval(function () {
    // Soft navigation keeps the document; stop once this page is swapped out.
    if (!btnRefresh.isConnected) {
      if (typeof window.__jobsPageTeardown === 'function') window.__jobsPageTeardown();
      window.__jobsPageTeardown = null;
      return;
    }
    loadJobs();
  }, 2000);
  let stream = null;
  if (window.EventSource) {
    try {
      stream = new EventSource('/api/jobs/stream');
      const refresh = () => { loadJobs(); };
      ['JOB_CREATED','JOB_STARTED','PROGRESS','JOB_COMPLETED','JOB_CANCELLED','JOB_PAUSED','FAILED','CANCELLING'].forEach(ev =>
        stream.addEventListener(ev, refresh));
      const live = document.getElementById('liveIndicator');
      if (live) live.textContent = JOBS_PAGE_DATA.liveUpdatesEventStreamConnected;
    } catch (e) { stream = null; /* keep polling */ }
  }
  window.__jobsPageTeardown = function () {
    clearInterval(pollTimer);
    if (stream) stream.close();
  };
  loadJobs();
})();
