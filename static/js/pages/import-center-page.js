/**
 * Import center: backup validation, restore and bulk-import staging.
 *
 * Soft-nav re-boots this classic script whenever /operations/import is swapped
 * in, so selects and buttons bind to the live DOM.
 */
(function () {
  'use strict';

  function readPageData() {
    var el = document.getElementById('import-center-page-data');
    if (!el) return {};
    try {
      return JSON.parse(el.textContent || '{}');
    } catch (_error) {
      return {};
    }
  }

  function bootImportCenter() {
    if (!document.getElementById('diValidate') || !document.getElementById('biSource')) return;

    var main = document.getElementById('mainContent');
    if (main) main.dataset.importCenterWired = '1';

    var data = readPageData();
    var csrfHeader = { 'X-CSRFToken': data.csrfToken || '' };
    var jsonHeaders = { 'Content-Type': 'application/json', ...csrfHeader };
    var pendingImport = null;

    function esc(v) {
      var d = document.createElement('div');
      d.textContent = String(v);
      return d.innerHTML;
    }

    function fill(id, rows) {
      var el = document.getElementById(id);
      if (!el) return;
      el.innerHTML =
        '<option value="">' + esc(data.select || '-- select --') + '</option>' +
        (rows || []).map(function (r) {
          return '<option value="' + esc(r.name) + '">' + esc(r.name) + '</option>';
        }).join('');
    }

    async function loadSelects() {
      try {
        var pair = await Promise.all([
          fetch('/api/input/sources').then(function (r) { return r.json(); }),
          fetch('/api/input/sides').then(function (r) { return r.json(); })
        ]);
        fill('biSource', pair[0].sources || []);
        fill('biSide', pair[1].sides || []);
      } catch (_error) { /* non-critical */ }
    }

    function showPreview(preview, pending) {
      var box = document.getElementById('previewBox');
      if (!box) return;
      pendingImport = pending;
      box.classList.remove('text-muted');
      box.innerHTML = Object.entries(preview || {}).map(function (entry) {
        var k = entry[0];
        var v = entry[1];
        if (v === null || v === undefined || typeof v === 'object') return '';
        return '<div><strong>' + esc(String(k).replace(/_/g, ' ')) + ':</strong> ' + esc(v) + '</div>';
      }).join('') +
        '<div class="text-muted mt-2">' + esc(data.reviewCarefullyImportsRunAsJobs || '') + '</div>';
      var confirmRow = document.getElementById('confirmRow');
      if (confirmRow) confirmRow.classList.remove('d-none');
    }

    function showMsg(text, ok) {
      var host = document.getElementById('importMsg');
      if (!host) return;
      var box = document.createElement('div');
      box.className = 'alert ' + (ok ? 'alert-success' : 'alert-danger') + ' py-2';
      box.textContent = text;
      host.replaceChildren(box);
    }

    async function postJson(url, body) {
      var r = await fetch(url, { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) });
      return r.json();
    }

    function v(id) {
      var el = document.getElementById(id);
      return el ? String(el.value || '').trim() : '';
    }

    function pathList() {
      var el = document.getElementById('biPaths');
      if (!el) return [];
      return String(el.value || '').split(String.fromCharCode(10)).map(function (s) {
        return s.trim();
      }).filter(Boolean);
    }

    function goJobs(jobId) {
      var url = '/operations/jobs/' + jobId;
      if (window.swapNavigate) window.swapNavigate(url);
      else window.location.href = url;
    }

    async function startJob(body) {
      var d = await postJson('/api/import/jobs', body);
      if (d.success && d.job) goJobs(d.job.job_id);
      else showMsg((d.error && d.error.message) || data.importJobCouldNotBeCreated || 'Import job could not be created.', false);
    }

    // Avoid double-binding the same live DOM nodes.
    if (document.getElementById('diValidate').dataset.importBound === '1') {
      loadSelects();
      return;
    }
    document.getElementById('diValidate').dataset.importBound = '1';

    loadSelects();

    document.getElementById('diValidate').addEventListener('click', async function () {
      var d = await postJson('/api/import/validate', { type: 'domain_import', data_file: v('diFile') || null });
      if (d.success) showPreview(Object.assign({}, d.preview || {}, { note: 'validated' }), null);
      else showMsg(d.error ? d.error.message : (data.validationFailed || 'Validation failed'), false);
    });

    document.getElementById('diPreview').addEventListener('click', async function () {
      var d = await postJson('/api/import/preview', { type: 'domain_import', data_file: v('diFile') || null });
      if (d.success) {
        showPreview(
          d.stats || (d.job && d.job.statistics) || { status: d.job && d.job.status, note: 'dry run job completed' },
          { type: 'domain_import', data_file: v('diFile') || null }
        );
      } else {
        showMsg(d.error ? d.error.message : (data.previewFailed || 'Preview failed'), false);
      }
    });

    document.getElementById('diStart').addEventListener('click', function () {
      startJob({ type: 'domain_import', data_file: v('diFile') || null });
    });

    document.getElementById('buValidate').addEventListener('click', async function () {
      var input = document.getElementById('buFile');
      var f = input && input.files && input.files[0];
      if (!f) { showMsg(data.chooseABackupFileFirst || 'Choose a backup file first.', false); return; }
      var fd = new FormData();
      fd.append('file', f);
      var r = await fetch('/api/import/jobs', { method: 'POST', headers: csrfHeader, body: fd });
      var d = await r.json();
      if (d.success) {
        showMsg(data.backupStagedStartingValidationJob || 'Backup staged.', true);
        goJobs(d.job.job_id);
      } else {
        showMsg(d.error ? d.error.message : (data.stagingFailed || 'Staging failed'), false);
      }
    });

    document.getElementById('buStart').addEventListener('click', async function () {
      var input = document.getElementById('buFile');
      var f = input && input.files && input.files[0];
      if (!f) { showMsg(data.chooseABackupFileFirst || 'Choose a backup file first.', false); return; }
      var fd = new FormData();
      fd.append('file', f);
      var r = await fetch('/api/import/jobs?type=backup_import', { method: 'POST', headers: csrfHeader, body: fd });
      var d = await r.json();
      if (d.success) goJobs(d.job.job_id);
      else showMsg(d.error ? d.error.message : (data.restoreJobFailedToStart || 'Restore job failed to start'), false);
    });

    document.getElementById('biValidate').addEventListener('click', async function () {
      var list = pathList();
      var d = await postJson('/api/import/validate', { type: 'batch_import', file_paths: list });
      if (d.success) {
        showPreview(d.preview, {
          type: 'batch_import',
          file_paths: list,
          source: v('biSource'),
          side: v('biSide')
        });
      } else {
        showMsg(d.error ? d.error.message : 'Validation failed', false);
      }
    });

    document.getElementById('biStart').addEventListener('click', function () {
      if (!v('biSource') || !v('biSide')) {
        showMsg(data.selectASourceAndASide || 'Select a source and a side.', false);
        return;
      }
      startJob({
        type: 'batch_import',
        file_paths: pathList(),
        source: v('biSource'),
        side: v('biSide')
      });
    });

    document.getElementById('btnConfirmStart').addEventListener('click', function () {
      if (!pendingImport) {
        showMsg(data.nothingToStartRunAPreview || 'Nothing to start - run a preview first.', false);
        return;
      }
      startJob(pendingImport);
    });

    document.getElementById('btnClearPreview').addEventListener('click', function () {
      var box = document.getElementById('previewBox');
      if (box) box.textContent = data.cleared || 'Cleared.';
      var confirmRow = document.getElementById('confirmRow');
      if (confirmRow) confirmRow.classList.add('d-none');
      pendingImport = null;
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootImportCenter);
  } else {
    bootImportCenter();
  }
  document.addEventListener('syltharae:soft-nav', function () {
    if (window.location.pathname.indexOf('/operations/import') === 0) bootImportCenter();
  });
})();
