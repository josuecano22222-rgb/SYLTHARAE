/**
 * Operations widget: active-job summary, refreshed every 3 seconds.
 *
 * Moved out of an inline <script> so the Content-Security-Policy can forbid
 * inline script (RES-CSP-01). Behaviour is unchanged.
 */
(function () {
  'use strict';

  var refreshTimer = null;

  function readTranslations() {
    var el = document.getElementById('operations-widget-data');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (_error) {
      return {};
    }
  }

  function badge(s) {
    return window.InforaxisStatus
      ? window.InforaxisStatus.badge(s)
      : '<span class="badge bg-secondary">' + String(s == null ? '' : s) + '</span>';
  }

  async function refreshOps() {
    if (!document.getElementById('opsRows')) return;
    var data = readTranslations();
    var T_ACTIVE = data.active || 'active';
    var T_NO_OPS = data.noOperationsYetStartAnIngestion || 'No operations yet - start an ingestion.';
    try {
      var sum = await fetch('/api/jobs/summary').then(function (r) { return r.json(); });
      var opsActive = document.getElementById('opsActive');
      if (sum.success && opsActive) {
        opsActive.textContent =
          sum.active + ' ' + T_ACTIVE + ' · ' +
          Object.entries(sum.counts || {}).map(function (entry) {
            return entry[0].replace(/_/g, ' ') + ': ' + entry[1];
          }).join(' · ');
      }
      var jobs = await fetch('/api/jobs?limit=5').then(function (r) { return r.json(); });
      var rows = (jobs.jobs || []).slice(0, 5);
      var opsRows = document.getElementById('opsRows');
      if (!opsRows) return;
      opsRows.innerHTML = rows.length ? rows.map(function (j) {
        return '' +
          '<tr>' +
            '<td><a href="/operations/jobs/' + j.job_id + '"><code>' + j.job_id + '</code></a></td>' +
            '<td>' + (j.job_type || '').replace(/_/g, ' ') + '</td>' +
            '<td>' + badge(j.status) + '</td>' +
            '<td style="min-width:90px;">' +
              '<div class="progress" style="height:6px;">' +
                '<div class="progress-bar" role="progressbar"' +
                     ' aria-valuemin="0" aria-valuemax="100" aria-valuenow="' + (j.progress || 0) + '"' +
                     ' style="width:' + (j.progress || 0) + '%; transition: width .3s ease;"></div>' +
              '</div>' +
            '</td>' +
            '<td class="text-muted">' +
              (j.progress || 0) + '%' +
              ((j.statistics && j.statistics.files_discovered != null)
                ? ' (' + (j.statistics.files_processed ?? 0) + '/' + j.statistics.files_discovered + ')'
                : '') +
            '</td>' +
          '</tr>';
      }).join('')
        : '<tr><td class="text-muted">' + T_NO_OPS + '</td></tr>';
    } catch (_error) { /* widget is non-critical */ }
  }

  function bootOperationsWidget() {
    if (!document.getElementById('opsWidget')) return;
    refreshOps();
    if (refreshTimer) clearInterval(refreshTimer);
    refreshTimer = setInterval(refreshOps, 3000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootOperationsWidget);
  } else {
    bootOperationsWidget();
  }
  document.addEventListener('syltharae:soft-nav', bootOperationsWidget);
})();
