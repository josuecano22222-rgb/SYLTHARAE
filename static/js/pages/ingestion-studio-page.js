/**
 * Ingestion page controller (/operations/input).
 *
 * This is the single ingestion surface: the page that used to live here and
 * the CLI-styled /upload page are one interface now, and this module drives
 * it. The server owns every rule (size ceilings, allowed roots, hashing,
 * duplicate identity, taxonomy); the page owns the operator's experience of
 * them — what is selected, what is staged, what the engine says it will do,
 * and what is happening right now.
 *
 * Nothing here guesses a limit or offers a control the backend would refuse:
 * capabilities come from GET /api/input/options-info, the queue's state
 * machine is modules/upload/upload-queue.js (unit-tested), large files go
 * through modules/upload/large-file-upload.js, and the job is created with the
 * payload POST /api/input/jobs validates.
 */

import {
  buildJobPayload,
  extensionOf,
  formatBytes,
  iconForName,
  mergeSelection,
  needsChunkedUpload,
  planChunks,
  planStaging,
  preflightTiles,
  queueTotals,
  readiness,
  sampledFiles,
} from '../modules/upload/upload-queue.js';
import { stageLargeFile } from '../modules/upload/large-file-upload.js';
import {
  examplePathFor,
  folderStructureAvailable,
  isInside,
  joinRelative,
  shortenPath,
} from '../modules/upload/path-rules.js';

const pageData = readPageData();

const state = {
  mode: 'upload',
  entries: [],
  source: '',
  side: '',
  serverPath: '',
  recursive: true,
  workers: 0,
  checkpoint: 'auto',
  hashNoticeShown: false,
  flatFolderNoticeShown: false,
  monitoring: true,
  limits: { direct: 0, chunked: 0, chunkSize: 8 * 1024 * 1024 },
  roots: [],
  platform: 'posix',
  serverPathsAvailable: false,
  busy: false,
  abort: null,
};

/** Source/side pick lists — must live above init(); accessing them from
 *  renderAll during a top-of-file init() hit the const TDZ and surfaced as
 *  "engine capabilities Cannot access 'taxonomy' before initialization". */
const taxonomy = { sources: [], sides: [] };

let telemetryTimer = null;

const el = (id) => document.getElementById(id);

// The studio's root node for this visit. Async loaders capture it before they
// await, so they can tell whether the page that started them is still mounted.
function studioAnchor() {
  return el('ingestAction') || document.getElementById('ingestionPageData');
}

// False once soft navigation has replaced the studio: a late response must not
// render into the page that took its place.
function studioStillMounted(anchor) {
  return Boolean(anchor) && studioAnchor() === anchor;
}

function initializeIngestionStudio() {
  const anchor = el('ingestAction') || document.getElementById('ingestionPageData');
  if (!anchor) return;
  // Guard per DOM, not per module copy: the self-run, the soft-nav default export
  // and cache-busted copies can all run against the same document. The anchor is
  // recreated on every visit, so a fresh visit still binds and boots.
  if (anchor.dataset.ingestBooted === '1') return;
  anchor.dataset.ingestBooted = '1';
  bindModes();
  bindPicker();
  bindServerPath();
  bindTaxonomy();
  bindOptions();
  bindRail();
  bindAction();
  bindKeys();
  loadCapabilities();
  loadTaxonomy();
  refreshTelemetry();
  if (!telemetryTimer) {
    telemetryTimer = window.setInterval(refreshTelemetry, 5000);
  }
  renderAll();
}

function stopIngestionStudio() {
  if (telemetryTimer) {
    window.clearInterval(telemetryTimer);
    telemetryTimer = null;
  }
}

document.addEventListener('syltharae:soft-nav', (event) => {
  const path = (event.detail && event.detail.path) || window.location.pathname;
  if (path !== '/operations/input') stopIngestionStudio();
});

export default function init() {
  if (document.readyState === 'loading') {
    return new Promise((resolve) => {
      document.addEventListener('DOMContentLoaded', () => {
        initializeIngestionStudio();
        resolve();
      }, { once: true });
    });
  }
  initializeIngestionStudio();
  return Promise.resolve();
}

/* -------------------------------------------------------------- plumbing -- */

function readPageData() {
  const node = document.getElementById('ingestionPageData');
  if (!node) return {};
  try {
    const data = JSON.parse(node.textContent) || {};
    // Strings this module emits itself are translated through the app's i18n
    // runtime (window.t, loaded by base.html) with the English source as the
    // fallback; the island can override single keys if a page needs it.
    window.ingestionStrings = data.strings || {};
    return data;
  } catch (error) {
    console.error('Ingestion page data is not valid JSON', error);
    return {};
  }
}

function csrfHeaders() {
  const token = document.querySelector('meta[name="csrf-token"]');
  return { 'X-CSRFToken': token ? token.content : (pageData.csrfToken || '') };
}

async function api(url, options = {}) {
  const response = await fetch(url, options);
  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    // A body is not guaranteed (204/empty error page); the status still tells
    // the operator what happened.
    if (!response.ok) body = {};
  }
  if (!response.ok || body.success === false) {
    const detail = (body.error && body.error.message) || (body.error && body.error.code);
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return body;
}

/** Render a string through the app's i18n runtime (it interpolates {name}). */
function msg(template, values = {}) {
  const overrides = window.ingestionStrings || {};
  if (overrides[template]) return fill(overrides[template], values);
  if (typeof window.t === 'function') return window.t(template, values);
  return fill(template, values);
}

function fill(template, values) {
  let text = String(template);
  Object.entries(values || {}).forEach(([key, value]) => {
    text = text.split(`{${key}}`).join(String(value));
  });
  return text;
}

function esc(value) {
  return String(value == null ? '' : value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

/* ----------------------------------------------------------- capabilities -- */

async function loadCapabilities() {
  const box = el('capabilities');
  if (!box) return;
  const anchor = studioAnchor();
  try {
    const info = await api(pageData.optionsInfoUrl || '/api/input/options-info');
    if (!studioStillMounted(anchor)) return;
    state.limits = {
      direct: (info.max_direct_upload_mb || 0) * 1024 * 1024,
      chunked: (info.max_chunked_upload_mb || 0) * 1024 * 1024,
      chunkSize: (info.chunk_size_mb || 8) * 1024 * 1024,
    };
    state.roots = info.ingestion_roots || [];
    state.platform = info.platform === 'nt' ? 'nt' : 'posix';
    state.serverPathsAvailable = Boolean(info.server_path_import_available);

    const chips = [
      capChip('ok', 'bi-fingerprint', msg('duplicate detection'), info.deduplication),
      capChip('ok', 'bi-shield-check', msg('archive safety'), info.archive_safety),
      capChip('ok', 'bi-hash', msg('hashing'), info.hashing),
      capChip('', 'bi-cloud-arrow-up', msg('direct upload'),
        info.max_direct_upload_mb ? msg('up to {v}', { v: `${info.max_direct_upload_mb} MB` }) : ''),
      capChip('', 'bi-layers', msg('large files'),
        info.max_chunked_upload_mb
          ? msg('{n} MB chunks, up to {v}', {
            n: info.chunk_size_mb, v: formatBytes(state.limits.chunked),
          })
          : ''),
      state.serverPathsAvailable
        ? capChip('', 'bi-hdd-network', msg('server paths'), msg('{n} configured root(s)', { n: state.roots.length }))
        : capChip('off', 'bi-hdd-network', msg('server paths'), msg('disabled — no roots configured')),
    ];
    box.innerHTML = chips.join('');
    // The field shows an example in the shape this host accepts (drive-letter
    // on Windows, leading slash elsewhere) instead of a POSIX example on a
    // Windows install, where it would be wrong for every path.
    const field = el('serverPath');
    if (field) field.placeholder = examplePathFor(state.roots[0], state.platform);
    renderRoots();
    if (!state.serverPathsAvailable && state.mode === 'server') setMode('upload', { silent: true });
    renderAll();
  } catch (error) {
    box.innerHTML = capChip('warn', 'bi-exclamation-triangle', msg('engine capabilities'), error.message);
  }
}

function capChip(kind, icon, label, value) {
  const cls = kind ? ` is-${kind}` : '';
  return `<span class="ing-cap${cls}"><i class="bi ${icon}"></i>
    <span class="ing-cap-label">${esc(label)}</span>
    ${value ? `<span class="ing-cap-value">${esc(value)}</span>` : ''}</span>`;
}

/* ------------------------------------------------------------------ mode -- */

function bindModes() {
  el('modeUpload').addEventListener('click', () => setMode('upload'));
  el('modeServer').addEventListener('click', () => setMode('server'));
}

function setMode(mode, { silent = false } = {}) {
  if (mode === 'server' && !state.serverPathsAvailable) {
    if (!silent) {
      pushMessage(msg('Server-path input is disabled: no INGESTION_ROOTS are configured. Set them in Settings (or the INGESTION_ROOTS environment variable) to enable it.'), 'warn');
    }
    return;
  }
  state.mode = mode;
  [['modeUpload', 'upload'], ['modeServer', 'server']].forEach(([id, value]) => {
    const button = el(id);
    button.classList.toggle('is-active', mode === value);
    button.setAttribute('aria-selected', String(mode === value));
  });
  el('paneUpload').classList.toggle('d-none', mode !== 'upload');
  el('paneServer').classList.toggle('d-none', mode !== 'server');
  el('serverModeNote').textContent = state.serverPathsAvailable
    ? msg('{n} configured root(s)', { n: state.roots.length })
    : msg('disabled — no roots configured');
  renderAll();
}

/* ------------------------------------------------------------------ files -- */

function bindPicker() {
  const drop = el('dropZone');
  const fileInput = el('fileInput');
  const folderInput = el('folderInput');

  el('pickFilesBtn').addEventListener('click', (event) => {
    event.stopPropagation();
    fileInput.click();
  });
  // A browser without webkitdirectory silently turns "Choose folder" into a
  // plain file picker. Say so on the button rather than pretending the folder
  // was read; on Windows (Edge/Chrome) this never fires.
  if (!('webkitdirectory' in folderInput)) {
    const folderButton = el('pickFolderBtn');
    folderButton.disabled = true;
    folderButton.title = msg('This browser cannot send a whole folder — drag it here instead.');
  }
  el('pickFolderBtn').addEventListener('click', (event) => {
    event.stopPropagation();
    folderInput.click();
  });
  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      fileInput.click();
    }
  });

  fileInput.addEventListener('change', () => {
    addFiles(Array.from(fileInput.files || []));
    fileInput.value = '';
  });
  folderInput.addEventListener('change', () => {
    // webkitdirectory: every file carries its path within the chosen folder,
    // '/'-separated on all platforms, so the tree is reproduced server-side
    // (Windows included). If a browser hands over bare names anyway, the
    // operator is told the folders will not be reproduced - once, not per file.
    const files = Array.from(folderInput.files || []);
    if (!folderStructureAvailable(files) && !state.flatFolderNoticeShown) {
      state.flatFolderNoticeShown = true;
      pushMessage(msg('This browser did not report the folder structure, so these files are placed without their folders.'), 'warn');
    }
    addFiles(files);
    folderInput.value = '';
  });

  ['dragenter', 'dragover'].forEach((type) => {
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.add('is-over');
    });
  });
  ['dragleave', 'dragend'].forEach((type) => {
    drop.addEventListener(type, () => drop.classList.remove('is-over'));
  });
  drop.addEventListener('drop', async (event) => {
    event.preventDefault();
    drop.classList.remove('is-over');
    const transfer = event.dataTransfer;
    if (!transfer) return;

    // Dropping a *folder* (the usual way on Windows: drag it from Explorer)
    // is not a file list - the browser gives an entry tree instead. Reading it
    // keeps the folder's structure, exactly like "Choose folder" does.
    const items = Array.from(transfer.items || []);
    const entries = items
      .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
      .filter(Boolean);
    if (entries.length) {
      const collected = await collectEntries(entries);
      if (collected.length) {
        if (!folderStructureAvailable(collected) && !state.flatFolderNoticeShown) {
          state.flatFolderNoticeShown = true;
          pushMessage(msg('This browser did not report the folder structure, so these files are placed without their folders.'), 'warn');
        }
        addFiles(collected);
        return;
      }
    }

    const dropped = Array.from(transfer.files || []);
    if (dropped.length) {
      addFiles(dropped);
      return;
    }
    if (items.length) {
      pushMessage(msg('This browser cannot read a dropped folder. Use “Choose folder” — it works on Windows, macOS and Linux.'), 'warn');
    }
  });

  el('clearQueueBtn').addEventListener('click', () => {
    state.entries = [];
    renderAll();
  });
}

/**
 * Walk dropped entries (files and directories) and return `{file, path}`
 * items, so a dropped folder keeps its structure. `readEntries` hands back
 * batches and must be called until it returns nothing.
 */
async function collectEntries(entries) {
  const collected = [];

  const readAll = (reader) => new Promise((resolve) => {
    const batch = [];
    const next = () => reader.readEntries((chunk) => {
      if (!chunk.length) {
        resolve(batch);
        return;
      }
      batch.push(...chunk);
      next();
    }, () => resolve(batch));
    next();
  });

  const walk = async (entry, prefix) => {
    if (!entry) return;
    if (entry.isFile) {
      const file = await new Promise((resolve) => entry.file(resolve, () => resolve(null)));
      if (file) collected.push({ file, path: joinRelative(prefix, file.name) });
      return;
    }
    if (entry.isDirectory) {
      const children = await readAll(entry.createReader());
      for (const child of children) {
        await walk(child, joinRelative(prefix, entry.name));
      }
    }
  };

  for (const entry of entries) await walk(entry, '');
  return collected;
}

function addFiles(files) {
  if (!files.length) return;
  const { entries, added, skipped } = mergeSelection(state.entries, files);
  state.entries = entries;
  renderAll();
  if (skipped.length) {
    pushMessage(msg('{n} already in the list — not added twice.', { n: skipped.length }), 'warn');
  }
  if (added) {
    const totals = queueTotals(state.entries);
    pushMessage(msg('{n} file(s) queued — {v} in total.', {
      n: added, v: formatBytes(totals.bytes),
    }), 'ok');
  }
}

function renderQueue() {
  const list = el('fileList');
  const block = el('queueBlock');
  const totals = queueTotals(state.entries);
  block.classList.toggle('d-none', state.entries.length === 0);

  el('queueSummary').textContent = state.entries.length
    ? msg('{n} file(s) · {v}', { n: totals.count, v: formatBytes(totals.bytes) })
      + (totals.staged ? msg(' · {n} staged', { n: totals.staged }) : '')
    : '';
  el('clearQueueBtn').disabled = !state.entries.length;

  list.innerHTML = state.entries.map((entry, index) => {
    const stateClass = entry.status === 'staged' ? 'is-done'
      : entry.status === 'failed' ? 'is-failed'
        : entry.status === 'staging' ? 'is-busy' : 'is-ready';
    const stateText = entry.status === 'staged' ? msg('staged')
      : entry.status === 'failed' ? (entry.message || msg('failed'))
        : entry.status === 'staging' ? `${entry.progress || 0}%`
          : msg('ready');
    const chunked = needsChunkedUpload(entry.size, state.limits.direct);
    const renamed = entry.storedName && entry.storedName !== entry.name;
    return `
      <li class="ing-row" data-index="${index}">
        <span class="ing-row-icon"><i class="bi ${iconForName(entry.name)}"></i></span>
        <span class="ing-row-main">
          <span class="ing-row-name" title="${esc(entry.name)}">${esc(shortenPath(entry.name, 72))}</span>
          <span class="ing-row-meta">${esc(extensionOf(entry.name).toUpperCase() || msg('file'))} · ${formatBytes(entry.size)}${
            chunked ? ` · <span class="ing-warn-text">${msg('chunked')}</span>` : ''
          }${renamed ? ` · <span class="ing-warn-text">${msg('stored as {name}', { name: esc(shortenPath(entry.storedName, 40)) })}</span>` : ''}</span>
        </span>
        <span class="ing-row-state ${stateClass}">${esc(stateText)}</span>
        <button type="button" class="ing-row-remove" data-remove="${index}"
                aria-label="${msg('Remove {name}', { name: entry.name })}">
          <i class="bi bi-x-lg"></i>
        </button>
        <span class="ing-row-bar"><span style="width:${entry.status === 'staged' ? 100 : (entry.progress || 0)}%"></span></span>
      </li>`;
  }).join('');

  list.querySelectorAll('[data-remove]').forEach((button) => {
    button.addEventListener('click', () => {
      state.entries.splice(Number(button.dataset.remove), 1);
      renderAll();
    });
  });
}

/* ------------------------------------------------------------ server path -- */

function bindServerPath() {
  el('serverPath').addEventListener('input', (event) => {
    state.serverPath = event.target.value;
    renderAll();
  });
  el('recursive').addEventListener('change', (event) => {
    state.recursive = event.target.checked;
  });
  el('checkPathBtn').addEventListener('click', () => {
    const path = el('serverPath').value.trim();
    state.serverPath = path;
    if (!path) {
      pushMessage(msg('Enter a path first.'), 'error');
      return;
    }
    if (!state.serverPathsAvailable) {
      pushMessage(msg('Server-path input is disabled: no INGESTION_ROOTS are configured. Set them in Settings (or the INGESTION_ROOTS environment variable) to enable it.'), 'error');
      return;
    }
    const root = state.roots.find((candidate) => isInside(path, candidate, state.platform));
    if (!root) {
      pushMessage(msg('That path is outside every configured root ({roots}). Ingestion is fail-closed by design.', {
        roots: state.roots.join(', '),
      }), 'error');
      return;
    }
    pushMessage(msg('Inside “{root}”. The dry run confirms what it contains — nothing has been read yet.', {
      root,
    }), 'ok');
  });
}

function renderRoots() {
  const box = el('rootsBox');
  el('modeServer').classList.toggle('is-off', !state.serverPathsAvailable);
  if (!state.serverPathsAvailable) {
    box.innerHTML = `<span class="ing-note-inline"><i class="bi bi-exclamation-triangle"></i>
      ${msg('No INGESTION_ROOTS configured — server paths are disabled.')}</span>`;
    return;
  }
  box.innerHTML = `<span class="ing-note-inline"><i class="bi bi-hdd-network"></i>
      ${msg('Allowed roots — click to use one:')}</span>`
    + state.roots.map((root) =>
      `<button type="button" class="ing-root-chip" data-root="${esc(root)}">${esc(root)}</button>`).join('');
  box.querySelectorAll('[data-root]').forEach((chipButton) => {
    chipButton.addEventListener('click', () => {
      el('serverPath').value = chipButton.dataset.root;
      state.serverPath = chipButton.dataset.root;
      renderAll();
    });
  });
}

/* -------------------------------------------------------------- taxonomy -- */

function bindTaxonomy() {
  el('sourceFilter').addEventListener('input', () => renderPickList('sourceList', taxonomy.sources, 'sources'));
  el('sideFilter').addEventListener('input', () => renderPickList('sideList', taxonomy.sides, 'sides'));
  document.querySelectorAll('[data-open-form]').forEach((button) => {
    button.addEventListener('click', () => {
      el(button.dataset.openForm).classList.remove('d-none');
      const first = el(button.dataset.openForm).querySelector('input');
      if (first) first.focus();
    });
  });
  document.querySelectorAll('[data-close-form]').forEach((button) => {
    button.addEventListener('click', () => el(button.dataset.closeForm).classList.add('d-none'));
  });
  el('btnCreateSource').addEventListener('click', createSource);
  el('btnCreateSide').addEventListener('click', createSide);
}

async function loadTaxonomy() {
  const anchor = studioAnchor();
  try {
    const [sources, sides] = await Promise.all([
      api(pageData.sourcesUrl || '/api/input/sources'),
      api(pageData.sidesUrl || '/api/input/sides'),
    ]);
    if (!studioStillMounted(anchor)) return;
    taxonomy.sources = sources.sources || [];
    taxonomy.sides = sides.sides || [];
  } catch (error) {
    if (!studioStillMounted(anchor)) return;
    pushMessage(msg('Sources and sides could not be loaded: {message}', { message: error.message }), 'error');
  }
  if (!studioStillMounted(anchor)) return;
  renderPickers();
}

function renderPickers() {
  renderPickList('sourceList', taxonomy.sources, 'sources');
  renderPickList('sideList', taxonomy.sides, 'sides');
  [['sourceDisplay', state.source], ['sideDisplay', state.side]].forEach(([id, value]) => {
    const node = el(id);
    if (!node) return;
    node.textContent = value || msg('Not selected');
    node.dataset.empty = String(!value);
  });
}

function renderPickList(listId, rows, key) {
  const list = el(listId);
  const filterInput = el(key === 'sources' ? 'sourceFilter' : 'sideFilter');
  // Either element can be missing when the page has been replaced.
  if (!list || !filterInput) return;
  const filter = filterInput.value.trim().toLowerCase();
  const selected = key === 'sources' ? state.source : state.side;
  const visible = rows.filter((row) => !filter || String(row.name || '').toLowerCase().includes(filter));
  if (!visible.length) {
    list.innerHTML = `<li class="ing-pick-empty">${rows.length
      ? msg('Nothing matches that filter.')
      : msg('None exist yet — create one with “New”.')}</li>`;
    return;
  }
  list.innerHTML = visible.map((row) => {
    const isSelected = String(row.name) === String(selected);
    const importance = (row.importance == null) ? '' : Number(row.importance).toFixed(1);
    return `
      <li class="ing-pick${isSelected ? ' is-selected' : ''}" role="option"
          aria-selected="${isSelected}" data-name="${esc(row.name)}" tabindex="0">
        <span>${esc(row.name)}</span><small>${importance}</small>
      </li>`;
  }).join('');
  list.querySelectorAll('[data-name]').forEach((item) => {
    const pick = () => {
      if (key === 'sources') state.source = item.dataset.name;
      else state.side = item.dataset.name;
      renderAll();
    };
    item.addEventListener('click', pick);
    item.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        pick();
      }
    });
  });
}

async function createSource() {
  const sourceForm = el('sourceForm');
  const invalidSourceField = Array.from(sourceForm.querySelectorAll('input[required]'))
    .find((field) => !field.checkValidity());
  if (invalidSourceField) {
    invalidSourceField.reportValidity();
    return;
  }
  const body = {
    name: el('nsName').value.trim(),
    job: el('nsJob').value.trim(),
    country: el('nsCountry').value.trim(),
    city: el('nsCity').value.trim() || null,
    description: el('nsDesc').value.trim() || null,
    importance: parseFloat(el('nsImportance').value || '0.5'),
  };
  el('btnCreateSource').disabled = true;
  try {
    await api(pageData.sourcesUrl || '/api/input/sources', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify(body),
    });
    state.source = body.name;
    el('sourceForm').classList.add('d-none');
    ['nsName', 'nsJob', 'nsCountry', 'nsCity', 'nsDesc'].forEach((id) => { el(id).value = ''; });
    el('nsImportance').value = '0.5';
    await loadTaxonomy();
    pushMessage(msg('Source “{name}” created and selected.', { name: body.name }), 'ok');
  } catch (error) {
    pushMessage(msg('Source could not be created: {message}', { message: error.message }), 'error');
  } finally {
    el('btnCreateSource').disabled = false;
    renderAll();
  }
}

async function createSide() {
  const nameField = el('nsdName');
  if (!nameField.checkValidity()) {
    nameField.reportValidity();
    return;
  }
  const body = {
    name: el('nsdName').value.trim(),
    importance: parseFloat(el('nsdImportance').value || '0.5'),
  };
  el('btnCreateSide').disabled = true;
  try {
    await api(pageData.sidesUrl || '/api/input/sides', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify(body),
    });
    state.side = body.name;
    el('sideForm').classList.add('d-none');
    el('nsdName').value = '';
    el('nsdImportance').value = '0.5';
    await loadTaxonomy();
    pushMessage(msg('Side “{name}” created and selected.', { name: body.name }), 'ok');
  } catch (error) {
    pushMessage(msg('Side could not be created: {message}', { message: error.message }), 'error');
  } finally {
    el('btnCreateSide').disabled = false;
    renderAll();
  }
}

/* --------------------------------------------------------------- options -- */

function bindOptions() {
  const workers = el('optWorkers');
  const syncWorkers = () => {
    state.workers = Number(workers.value) || 0;
    renderRunFacts();
    renderRail();
  };
  workers.addEventListener('input', syncWorkers);
  document.querySelectorAll('[data-step-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = el(button.dataset.stepTarget);
      const min = Number(input.min === '' ? 0 : input.min);
      const max = Number(input.max === '' ? 64 : input.max);
      const next = Math.min(Math.max((Number(input.value) || 0) + Number(button.dataset.delta), min), max);
      input.value = next;
      syncWorkers();
    });
  });

  const checkpointHelp = {
    auto: msg('Resume continues an interrupted run; finished files are not processed twice.'),
    fresh: msg('Start fresh ignores an earlier checkpoint and walks everything again — deduplication still prevents double storage.'),
    off: msg('Off writes no checkpoint: a crash means the next run starts from the beginning.'),
  };
  el('optCheckpoint').querySelectorAll('button').forEach((button) => {
    button.addEventListener('click', () => {
      el('optCheckpoint').querySelectorAll('button').forEach((other) => {
        other.classList.toggle('is-active', other === button);
        other.setAttribute('aria-checked', String(other === button));
      });
      state.checkpoint = button.dataset.value;
      el('checkpointHelp').textContent = checkpointHelp[state.checkpoint] || '';
      renderRunFacts();
      renderRail();
    });
  });

  el('optMonitoring').addEventListener('change', (event) => {
    state.monitoring = event.target.checked;
    renderRunFacts();
    renderRail();
  });
}

/* ------------------------------------------------------------- readiness -- */

function bindRail() {
  document.querySelectorAll('[data-goto]').forEach((button) => {
    button.addEventListener('click', () => {
      const target = el(button.dataset.goto);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        target.classList.add('is-flash');
        window.setTimeout(() => target.classList.remove('is-flash'), 1200);
      }
    });
  });
}

function renderRail() {
  const status = readiness(state);
  const labels = {
    source: state.mode === 'server'
      ? (state.serverPath || msg('Choose a server path'))
      : (state.entries.length
        ? msg('{n} file(s) selected', { n: state.entries.length })
        : msg('Choose what to ingest')),
    classify: state.source && state.side
      ? `${state.source} · ${state.side}`
      : msg('Source and side are mandatory'),
    processing: describeProcessing(),
    launch: status.ready ? msg('Ready to run') : msg('Preflight, then start'),
  };
  const targets = {
    source: 'railSourceState',
    classify: 'railClassifyState',
    processing: 'railProcessingState',
    launch: 'railLaunchState',
  };
  Object.entries(status.steps).forEach(([step, stepState]) => {
    const node = document.querySelector(`.ing-step[data-step="${step}"]`);
    if (node) {
      node.classList.toggle('is-current', stepState === 'current');
      node.classList.toggle('is-done', stepState === 'done');
    }
    const target = el(targets[step]);
    if (target) target.textContent = labels[step];
  });
}

function describeProcessing() {
  const parts = [state.workers
    ? msg('{n} worker(s)', { n: state.workers })
    : msg('engine default workers')];
  parts.push({ auto: msg('resume'), fresh: msg('fresh start'), off: msg('no checkpoints') }[state.checkpoint]);
  if (state.monitoring) parts.push(msg('monitored'));
  return parts.join(' · ');
}

function renderDock() {
  const status = readiness(state);
  const totals = queueTotals(state.entries);
  const dot = el('dockDot');
  const headline = el('dockHeadline');
  const detail = el('dockDetail');

  dot.className = 'ing-dock-dot';
  if (state.busy) {
    dot.classList.add('is-busy');
    headline.textContent = msg('Working…');
    detail.textContent = msg('Staging files — keep this page open.');
  } else if (status.ready) {
    dot.classList.add('is-ready');
    headline.textContent = state.mode === 'server'
      ? msg('Ready: {path}', { path: state.serverPath })
      : msg('Ready: {n} file(s) · {v}', { n: totals.count, v: formatBytes(totals.bytes) });
    detail.textContent = `${state.source} → ${state.side}`;
  } else {
    dot.classList.add('is-blocked');
    headline.textContent = msg('Not ready yet');
    detail.textContent = status.reason;
  }

  const startButton = el('btnStart');
  const label = startButton.querySelector('.ing-primary-label');
  const progress = el('startProgress');
  const stopping = state.busy && Boolean(state.abort);
  startButton.disabled = state.busy ? !stopping : !status.ready;
  startButton.classList.toggle('is-stop', stopping);
  label.innerHTML = stopping
    ? `<i class="bi bi-stop-fill"></i> ${msg('Stop staging')}`
    : `<i class="bi bi-play-fill"></i> ${msg('Start Analysis')}`;
  label.classList.toggle('d-none', state.busy && !stopping);
  progress.classList.toggle('d-none', !state.busy || stopping);
  progress.textContent = state.busy ? msg('staging…') : '';
  el('btnDryRun').disabled = state.busy || !status.ready;

  const inputDone = state.mode === 'server' ? Boolean(state.serverPath) : totals.count > 0;
  el('launchNotes').innerHTML = `<ul class="ing-checklist">
    <li class="${inputDone ? 'is-done' : 'is-blocked'}">
      <i class="bi ${inputDone ? 'bi-check-circle-fill' : 'bi-circle'}"></i>
      ${state.mode === 'server' ? msg('Server path set') : msg('Files selected')}
    </li>
    <li class="${state.source ? 'is-done' : 'is-blocked'}">
      <i class="bi ${state.source ? 'bi-check-circle-fill' : 'bi-circle'}"></i> ${msg('Source chosen')}
    </li>
    <li class="${state.side ? 'is-done' : 'is-blocked'}">
      <i class="bi ${state.side ? 'bi-check-circle-fill' : 'bi-circle'}"></i> ${msg('Side chosen')}
    </li>
    <li class="${totals.failed ? 'is-blocked' : 'is-done'}">
      <i class="bi ${totals.failed ? 'bi-exclamation-circle-fill' : 'bi-check-circle-fill'}"></i>
      ${totals.failed
        ? msg('{n} file(s) failed to stage', { n: totals.failed })
        : msg('Nothing in the queue has failed')}
    </li>
  </ul>`;
}

function renderRunFacts() {
  const totals = queueTotals(state.entries);
  const chunked = state.entries.filter((entry) => needsChunkedUpload(entry.size, state.limits.direct));
  const facts = [
    [msg('Mode'), state.mode === 'server' ? msg('server path') : msg('this computer')],
    [msg('Files'), String(totals.count)],
    [msg('Volume'), formatBytes(totals.bytes)],
    [msg('Largest'), formatBytes(totals.largest)],
    [msg('Workers'), state.workers ? String(state.workers) : msg('default')],
    [msg('Checkpoint'), state.checkpoint],
  ];
  if (state.mode === 'upload') {
    const requests = planStaging(state.entries, state.limits.direct).batches.length + chunked.length;
    facts.push([msg('Transfers'), String(requests)]);
  }
  if (chunked.length) {
    facts.push([msg('Chunks'), String(chunked.reduce(
      (sum, entry) => sum + planChunks(entry.size, state.limits.chunkSize), 0,
    ))]);
  }
  el('runFacts').innerHTML = facts
    .map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join('');
}

/* ------------------------------------------------------------------ run --- */

function bindAction() {
  el('btnStart').addEventListener('click', () => {
    if (state.busy && state.abort) {
      state.abort.abort();
      return;
    }
    run(false);
  });
  el('btnDryRun').addEventListener('click', () => run(true));
  el('closePreflight').addEventListener('click', () => el('preflight').classList.add('d-none'));
}

function bindKeys() {
  document.addEventListener('keydown', (event) => {
    const meta = event.ctrlKey || event.metaKey;
    if (!meta) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      if (!state.busy) run(false);
    } else if (event.key === 'd' || event.key === 'D') {
      event.preventDefault();
      if (!state.busy) run(true);
    }
  });
}

async function run(dryRun) {
  if (state.busy) return;
  const status = readiness(state);
  if (!status.ready) {
    pushMessage(status.reason, 'warn');
    return;
  }

  const willStage = state.mode === 'upload' && hasUnstaged(state.entries);
  state.busy = true;
  state.abort = willStage ? new AbortController() : null;
  renderDock();
  try {
    if (state.mode === 'upload') await stageQueue();
    const payload = buildJobPayload(state, { dryRun });
    if (!dryRun && (!payload.file_paths || !payload.file_paths.length) && !payload.path) {
      throw new Error(msg('Nothing was staged — add files again.'));
    }
    const result = await api(pageData.jobsUrl || '/api/input/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...csrfHeaders() },
      body: JSON.stringify(payload),
    });
    if (dryRun) {
      renderPreflight(result.preview || {});
      pushMessage(msg('Preflight finished. Nothing was read into the system and nothing was written.'), 'ok');
    } else if (result.job && result.job.job_id) {
      pushMessage(msg('Job {id} created — opening it…', { id: result.job.job_id }), 'ok');
      window.location = `${pageData.jobsPageUrl || '/operations/jobs/'}${encodeURIComponent(result.job.job_id)}`;
    } else {
      throw new Error(msg('The engine accepted the request but returned no job id.'));
    }
  } catch (error) {
    const aborted = error && (error.name === 'AbortError' || /abort/i.test(error.message || ''));
    if (aborted) {
      pushMessage(msg('Staging stopped. Files already staged stay staged and can be reused.'), 'warn', { panel: true });
    } else {
      pushMessage(error.message, 'error', { panel: true });
    }
  } finally {
    state.busy = false;
    state.abort = null;
    renderAll();
  }
}

function hasUnstaged(entries) {
  return entries.some((entry) => !entry.stagedPath && entry.status !== 'failed');
}

async function stageQueue() {
  if (!hasUnstaged(state.entries)) return;

  const { batches, chunked } = planStaging(state.entries, state.limits.direct);

  for (const batch of batches) {
    batch.forEach((entry) => {
      entry.status = 'staging';
      entry.progress = 5;
    });
    renderQueue();
    const form = new FormData();
    batch.forEach((entry) => form.append('files', entry.file, entry.file.name));
    // The server stores files under the browser's relative path, so a folder
    // upload keeps its structure (and two same-named files coexist).
    form.append('relative_paths', JSON.stringify(batch.map((entry) => entry.name)));
    try {
      const staged = await api(pageData.uploadsUrl || '/api/input/uploads', {
        method: 'POST',
        headers: csrfHeaders(),
        body: form,
        signal: state.abort ? state.abort.signal : undefined,
      });
      const rows = staged.staged || [];
      const failures = new Map((staged.failed || []).map((item) => [item.name, item.message]));
      batch.forEach((entry, index) => {
        const row = rows[index];
        const rejection = failures.get(entry.name);
        if (row && row.path) {
          entry.status = 'staged';
          entry.progress = 100;
          entry.stagedPath = row.path;
          // The server may have had to adjust a name (reserved device name,
          // trailing dot, illegal character): show what was stored.
          entry.storedName = row.name && row.name !== entry.name ? row.name : null;
        } else {
          entry.status = 'failed';
          entry.message = rejection || msg('not staged');
        }
      });
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      batch.forEach((entry) => {
        entry.status = 'failed';
        entry.message = error.message;
      });
      renderQueue();
      throw new Error(msg('Staging failed: {message}', { message: error.message }));
    }
    renderQueue();
    const rejected = batch.filter((entry) => entry.status === 'failed');
    if (rejected.length && rejected.length < batch.length) {
      pushMessage(msg('{n} file(s) could not be staged — see the list.', { n: rejected.length }), 'warn');
    }
  }

  for (const entry of chunked) {
    entry.status = 'staging';
    entry.progress = 0;
    renderQueue();
    try {
      const result = await stageLargeFile(entry.file, {
        endpoint: pageData.chunkedUrl || '/api/input/uploads/chunked',
        // A large file selected inside a folder keeps its place in the tree;
        // the server sanitises it for the host filesystem.
        filename: entry.name,
        headers: csrfHeaders(),
        signal: state.abort ? state.abort.signal : undefined,
        onProgress: ({ sentBytes, totalBytes }) => {
          entry.progress = Math.round((sentBytes / Math.max(totalBytes, 1)) * 100);
          renderQueue();
        },
      });
      entry.status = 'staged';
      entry.progress = 100;
      entry.stagedPath = result.stagedPath;
      entry.storedName = result.name && result.name !== entry.name ? result.name : null;
      if (result.clientHashed === false && !state.hashNoticeShown) {
        // Not a failure: the server still verifies size and reports the hash of
        // what it stored. Said once, with the way to get per-chunk hashes back.
        state.hashNoticeShown = true;
        pushMessage(msg('This browser context has no hashing API (serve the app over https, or open it on localhost): chunks are verified by the server at the end instead.'), 'warn');
      }
    } catch (error) {
      if (error.name === 'AbortError') throw error;
      entry.status = 'failed';
      entry.message = error.message;
      renderQueue();
      throw new Error(msg('{name} could not be staged: {message}', {
        name: entry.name, message: error.message,
      }));
    }
    renderQueue();
  }
}

/* ------------------------------------------------------------ preflight -- */

function renderPreflight(preview) {
  const box = el('preflight');
  box.classList.remove('d-none', 'is-error');
  el('preflightTiles').innerHTML = preflightTiles(preview).map((tile) => `
    <div class="ing-tile${tile.warn ? ' is-warn' : ''}">
      <div class="ing-tile-value">${esc(String(tile.value))}</div>
      <div class="ing-tile-label">${esc(tile.label)}</div>
    </div>`).join('');

  const notes = [];
  const samples = sampledFiles(preview, 8);
  if (samples.length) {
    notes.push(`<div class="ing-note"><strong>${msg('sampled from the walk')}</strong>
      <ul>${samples.map((name) => `<li class="ing-mono">${esc(name)}</li>`).join('')}</ul></div>`);
  }
  if (preview.ingestion_roots_configured === false) {
    notes.push(`<div class="ing-note is-warn"><i class="bi bi-exclamation-triangle"></i>
      ${msg('No ingestion roots are configured — server paths stay disabled until they are.')}</div>`);
  }
  el('preflightNotes').innerHTML = notes.join('');
}

function renderPreflightError(message) {
  const box = el('preflight');
  box.classList.remove('d-none');
  box.classList.add('is-error');
  el('preflightTiles').innerHTML = '';
  el('preflightNotes').innerHTML = `<div class="ing-note is-error">
    <i class="bi bi-x-octagon"></i> ${esc(message)}</div>`;
}

/* ------------------------------------------------------------ telemetry -- */

async function refreshTelemetry() {
  // Soft nav leaves this module's interval alive. Bail (and stop the timer)
  // once the ingestion studio is no longer in the document.
  if (!document.getElementById('ingestionStudio') || !el('liveSummary')) {
    if (telemetryTimer) {
      window.clearInterval(telemetryTimer);
      telemetryTimer = null;
    }
    return;
  }
  try {
    const summary = await api(pageData.jobsSummaryUrl || '/api/jobs/summary');
    const counts = summary.counts || {};
    const liveSummary = el('liveSummary');
    if (!liveSummary) return;
    liveSummary.textContent = Object.keys(counts).length
      ? Object.entries(counts)
        .map(([key, value]) => `${key.replace(/_/g, ' ')}: ${value}`).join(' · ')
      : msg('no jobs yet');
  } catch (error) {
    const liveSummary = el('liveSummary');
    if (liveSummary) liveSummary.textContent = msg('unavailable');
  }
  try {
    const list = await api(`${pageData.jobsListUrl || '/api/jobs'}?limit=6`);
    const jobs = list.jobs || [];
    const active = jobs.filter((job) => ACTIVE_STATUSES.includes(job.status));
    const finished = jobs.filter((job) => !ACTIVE_STATUSES.includes(job.status));
    const liveJobs = el('liveJobs');
    const recentJobs = el('recentJobs');
    const liveEmpty = el('liveEmpty');
    if (liveJobs) renderJobs(liveJobs, active);
    if (recentJobs) renderJobs(recentJobs, finished);
    if (liveEmpty) liveEmpty.classList.toggle('d-none', active.length > 0);
  } catch (error) {
    /* the panel keeps its last good content; the summary line reports state */
  }
}

const ACTIVE_STATUSES = ['RUNNING', 'QUEUED', 'PAUSED', 'CANCELLING'];

function renderJobs(target, jobs) {
  const reportBase = pageData.batchReportUrl || '/operations/input/reports/';
  target.innerHTML = jobs.map((job) => {
    const status = String(job.status || '');
    const finished = !ACTIVE_STATUSES.includes(status);
    const isIngest = String(job.job_type || '') === 'ingestion';
    const lang = (document.documentElement.lang || 'en').split('-')[0];
    const report = (finished && isIngest)
      ? `<a class="ing-link-btn" href="${reportBase}${encodeURIComponent(job.job_id)}?lang=${encodeURIComponent(lang)}"
            target="_blank" rel="noopener" title="${msg('Open batch report')}">${msg('Batch report')}</a>`
      : '';
    const src = job.classification_source || '—';
    const side = job.classification_side || '—';
    const errN = Number(job.errors_count) || 0;
    const warnN = Number(job.warnings_count) || 0;
    const issues = (errN || warnN)
      ? `<small class="text-muted">${errN ? `${errN} err` : ''}${errN && warnN ? ' · ' : ''}${warnN ? `${warnN} warn` : ''}</small>`
      : '';
    return `
      <li class="ing-live-item">
        <div class="ing-live-row">
          <a href="${pageData.jobsPageUrl || '/operations/jobs/'}${encodeURIComponent(job.job_id)}"
             title="${msg('Open job')}"><code>${esc(job.job_id)}</code></a>
          <span class="ing-badge ${statusClass(status)}">${esc(status.replace(/_/g, ' '))}</span>
        </div>
        <div class="ing-live-row">
          <small class="text-break">${esc(src)} → ${esc(side)}</small>
        </div>
        <div class="ing-bar"><span style="width:${Number(job.progress) || 0}%"></span></div>
        <div class="ing-live-row">
          <small>${esc(String(job.job_type || '').replace(/_/g, ' '))}</small>
          <small>${Number(job.progress) || 0}%</small>
          ${issues}
        </div>
        ${report}
      </li>`;
  }).join('');
}

function statusClass(status) {
  if (status === 'RUNNING') return 'is-running';
  if (status === 'COMPLETED') return 'is-ok';
  if (status === 'FAILED') return 'is-failed';
  if (['QUEUED', 'PAUSED', 'CANCELLING', 'COMPLETED_WITH_WARNINGS'].includes(status)) return 'is-warn';
  return '';
}

/* -------------------------------------------------------------- messages -- */

function pushMessage(text, kind = 'ok', { panel = false } = {}) {
  if (panel) renderPreflightError(text);
  const icons = {
    ok: 'bi-check-circle',
    warn: 'bi-exclamation-triangle',
    error: 'bi-x-octagon',
  };
  el('inputMsg').innerHTML = `<div class="ing-alert is-${kind}">
    <i class="bi ${icons[kind] || icons.ok}"></i><span>${esc(text)}</span></div>`;
}

function renderAll() {
  renderQueue();
  renderPickers();
  renderRail();
  renderDock();
  renderRunFacts();
}

// Full page loads run the module via <script type="module">; soft nav also
// calls the default export. Guard inside initializeIngestionStudio keeps
// listeners from stacking when both fire on the same instance.
init();
