/**
 * Upload queue helpers — pure functions, no DOM.
 *
 * The ingestion page keeps a list of what the operator selected and has to
 * answer four questions about it continuously: what is in it (size, kind),
 * what is a duplicate *selection*, how it will be transferred (one request or
 * many chunks), and whether the run can start. Those answers are pure
 * functions of the list and the engine's declared limits, so they live here
 * and are unit-tested under node (tests/unit/test_upload_queue_helpers.py)
 * instead of being entangled with rendering.
 */

import { displayNameFor, fileOf, relativePathFor } from './path-rules.js';

export const KIB = 1024;
export const MIB = 1024 * 1024;
export const GIB = 1024 * 1024 * 1024;

/** Human-readable size, one decimal above 1 KB. */
export function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < KIB) return `${value} B`;
  if (value < MIB) return `${(value / KIB).toFixed(value < 10 * KIB ? 1 : 0)} KB`;
  if (value < GIB) return `${(value / MIB).toFixed(1)} MB`;
  return `${(value / GIB).toFixed(2)} GB`;
}

const ICONS = {
  doc: 'bi-file-earmark-text', sheet: 'bi-file-earmark-spreadsheet', slide: 'bi-file-earmark-slides',
  pdf: 'bi-file-earmark-pdf', image: 'bi-file-earmark-image', archive: 'bi-file-earmark-zip',
  audio: 'bi-file-earmark-music', video: 'bi-file-earmark-play', mail: 'bi-envelope-paper',
  code: 'bi-file-earmark-code', book: 'bi-book', data: 'bi-filetype-json',
  text: 'bi-file-earmark-text', other: 'bi-file-earmark',
};

const EXT_KINDS = {
  pdf: 'pdf',
  doc: 'doc', docx: 'doc', odt: 'doc', rtf: 'doc', pages: 'doc',
  xls: 'sheet', xlsx: 'sheet', ods: 'sheet', csv: 'sheet', tsv: 'sheet',
  ppt: 'slide', pptx: 'slide', odp: 'slide',
  png: 'image', jpg: 'image', jpeg: 'image', gif: 'image', bmp: 'image', tif: 'image',
  tiff: 'image', webp: 'image', svg: 'image', heic: 'image',
  zip: 'archive', rar: 'archive', '7z': 'archive', tar: 'archive', gz: 'archive',
  bz2: 'archive', xz: 'archive', zst: 'archive', iso: 'archive', cab: 'archive',
  mp3: 'audio', wav: 'audio', flac: 'audio', m4a: 'audio', ogg: 'audio', aac: 'audio',
  mp4: 'video', mov: 'video', avi: 'video', mkv: 'video', webm: 'video',
  eml: 'mail', msg: 'mail', mbox: 'mail', pst: 'mail', ost: 'mail',
  txt: 'text', log: 'text', md: 'text', ini: 'text', cfg: 'text',
  epub: 'book', mobi: 'book', azw3: 'book', djvu: 'book',
  json: 'data', xml: 'data', yaml: 'data', yml: 'data', html: 'data', htm: 'data',
  js: 'code', ts: 'code', py: 'code', java: 'code', cs: 'code', cpp: 'code', c: 'code',
  sh: 'code', ps1: 'code', sql: 'code', css: 'code',
};

/** Extension of a name (lower-case, without the dot; '' when there is none). */
export function extensionOf(name) {
  const base = String(name || '').split(/[\\/]/).pop() || '';
  const dot = base.lastIndexOf('.');
  if (dot <= 0 || dot === base.length - 1) return '';
  return base.slice(dot + 1).toLowerCase();
}

/** Bootstrap icon class for a file name — the queue shows what a file *is*. */
export function iconForName(name) {
  return ICONS[EXT_KINDS[extensionOf(name)] || 'other'];
}

/** Short kind label ("pdf", "archive", …) used next to the icon. */
export function kindOf(name) {
  const ext = extensionOf(name);
  return EXT_KINDS[ext] || (ext ? 'other' : 'other');
}

/**
 * Identity of a *selection* (not of the content): the same file chosen twice
 * in one dialog, or a file added again by a second drag, must not queue twice.
 * Content identity is the engine's job — this only stops accidental repeats.
 */
export function selectionKey(item) {
  const file = fileOf(item);
  return [
    file.name,
    file.size,
    file.lastModified || 0,
    relativePathFor(item),
  ].join('::');
}

/** Display name for a selected entry: its path inside a folder selection, or
 *  its bare name. The rule itself lives in path-rules.js. */
export const displayName = displayNameFor;

/**
 * Merge newly chosen files into the queue.
 * Returns the new list plus how many were added and which were skipped as
 * already-present selections, so the page can say so instead of silently
 * dropping them.
 */
export function mergeSelection(existing, incoming) {
  const seen = new Set(existing.map((entry) => entry.key));
  const entries = existing.slice();
  let added = 0;
  const skipped = [];
  for (const item of incoming) {
    const file = fileOf(item);
    if (!file) continue;
    const key = selectionKey(item);
    if (seen.has(key)) {
      skipped.push(displayName(item) || file.name);
      continue;
    }
    seen.add(key);
    entries.push({
      key,
      file,
      name: displayName(item),
      size: file.size || 0,
      kind: kindOf(file.name),
      status: 'ready',
      progress: 0,
      message: '',
      stagedPath: null,
      storedName: null,
    });
    added += 1;
  }
  return { entries, added, skipped };
}

/** Totals for the queue summary and the run facts. */
export function queueTotals(entries) {
  const total = entries.reduce((sum, entry) => sum + (entry.size || 0), 0);
  const largest = entries.reduce((max, entry) => Math.max(max, entry.size || 0), 0);
  const staged = entries.filter((entry) => entry.status === 'staged').length;
  const failed = entries.filter((entry) => entry.status === 'failed').length;
  return {
    count: entries.length,
    bytes: total,
    largest,
    staged,
    failed,
    estimatedChunks: entries.reduce(
      (sum, entry) => sum + Math.max(planChunks(entry.size || 0, CHUNK_PLAN_SIZE), 1),
      0,
    ),
  };
}

//: Only used to *estimate* the transfer plan shown to the operator; the real
//: chunk size is whatever the server reports from /api/input/options-info.
export const CHUNK_PLAN_SIZE = 8 * MIB;

/** Number of chunks a file of `size` bytes will be sent in. */
export function planChunks(size, chunkSize = CHUNK_PLAN_SIZE) {
  const chunk = chunkSize > 0 ? chunkSize : CHUNK_PLAN_SIZE;
  return Math.max(Math.ceil((Number(size) || 0) / chunk), 1);
}

/** Does this file need the chunked path, given the direct-upload ceiling? */
export function needsChunkedUpload(size, directLimitBytes) {
  const limit = Number(directLimitBytes) || 0;
  if (limit <= 0) return true;
  return (Number(size) || 0) > limit;
}

/**
 * Decide how the operator's choices read on the rail and the launch dock.
 *
 * Returns the per-step state (done/current/pending), whether the run can
 * start, and — when it cannot — the single most useful blocking reason. One
 * reason at a time on purpose: a list of five problems in a dock nobody reads
 * is how interfaces become ambiguous.
 */
export function readiness(state) {
  const hasInput = state.mode === 'server'
    ? Boolean(String(state.serverPath || '').trim())
    : (state.entries || []).length > 0;
  const hasSource = Boolean(state.source);
  const hasSide = Boolean(state.side);
  const uploadAvailable = state.uploadAvailable !== false;

  const steps = {
    source: hasInput ? 'done' : 'current',
    classify: hasSource && hasSide ? 'done' : (hasInput ? 'current' : 'pending'),
    processing: 'done',
    launch: hasInput && hasSource && hasSide ? 'current' : 'pending',
  };

  const blocking = [];
  if (state.mode === 'upload' && !uploadAvailable) {
    blocking.push({ code: 'upload-off', text: 'File upload is switched off in Settings — use a server path.' });
  }
  if (!hasInput) {
    blocking.push({
      code: 'no-input',
      text: state.mode === 'server'
        ? 'Enter a server path (step 1).'
        : 'Add files or a folder (step 1).',
    });
  }
  if (!hasSource) blocking.push({ code: 'no-source', text: 'Choose a source (step 2).' });
  if (!hasSide) blocking.push({ code: 'no-side', text: 'Choose a side (step 2).' });

  return {
    steps,
    ready: blocking.length === 0,
    blocking,
    reason: blocking.length ? blocking[0].text : '',
  };
}

/** Shape the job payload the API expects, from the page state. */
export function buildJobPayload(state, { dryRun }) {
  const processing = {
    max_workers: Number(state.workers) || 0,
    checkpoint: state.checkpoint || 'auto',
    enable_monitoring: state.monitoring !== false,
  };
  const payload = {
    source: state.source || '',
    side: state.side || '',
    recursive: state.recursive !== false,
    dry_run: Boolean(dryRun),
    processing,
  };
  if (state.mode === 'server') {
    payload.path = String(state.serverPath || '').trim();
  } else {
    payload.file_paths = (state.entries || [])
      .filter((entry) => entry.stagedPath)
      .map((entry) => entry.stagedPath);
  }
  return payload;
}

/** Tiles for the preflight panel, from the dry-run preview the API returns. */
export function preflightTiles(preview) {
  const p = preview || {};
  return [
    { label: 'discovered', value: p.files_discovered ?? 0 },
    { label: 'to read', value: p.files_eligible ?? 0 },
    { label: 'not readable', value: p.files_unsupported ?? 0, warn: (p.files_unsupported || 0) > 0 },
    { label: 'volume', value: formatBytes(p.estimated_bytes || 0), warn: false },
  ];
}

/** First eligible files a dry run reported — shown so "eligible" is concrete. */
export function sampledFiles(preview, limit = 5) {
  const samples = (preview && preview.sample_files) || [];
  return samples.slice(0, limit).map((entry) => entry.path || entry.name || '');
}

/**
 * Split the queue into staging requests: one request per batch of small files
 * (bounded by the per-request ceiling the server enforces) plus one transfer
 * per large file.
 */
export function planStaging(entries, directLimitBytes, maxFilesPerRequest = 40) {
  const pending = entries.filter((entry) => !entry.stagedPath && entry.status !== 'failed');
  const direct = [];
  const chunked = [];
  let batch = [];
  let batchBytes = 0;

  for (const entry of pending) {
    const size = entry.size || 0;
    if (needsChunkedUpload(size, directLimitBytes)) {
      chunked.push(entry);
      continue;
    }
    if (batch.length >= maxFilesPerRequest || (batchBytes + size) > directLimitBytes) {
      if (batch.length) direct.push(batch);
      batch = [];
      batchBytes = 0;
    }
    batch.push(entry);
    batchBytes += size;
  }
  if (batch.length) direct.push(batch);
  return { batches: direct, chunked };
}
