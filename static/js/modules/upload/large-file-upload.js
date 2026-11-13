/**
 * Large-file staging: stream a file to the server in chunks.
 *
 * Why this exists: a single request has a size ceiling
 * (OPERATIONS_MAX_UPLOAD_MB), so a multi-gigabyte file could not be handed to
 * the engine at all. The server accepts chunks for such files under
 * /api/input/uploads/chunked, assembles them in order, checks the declared
 * size and SHA-256, and hands back a staged path that feeds exactly the same
 * ingestion job as a small file. This module is the client half.
 *
 * Design choices worth stating:
 *  - chunks go up one at a time. Chunks are few and large; parallelising them
 *    only makes the failure cases harder to reason about and the progress bar
 *    jump around, and on a local install the disk is the bottleneck anyway.
 *  - each chunk is retried a few times with backoff (a dropped connection
 *    should not restart a 10 GB transfer), and the whole transfer is
 *    cancellable through an AbortSignal.
 *  - every chunk carries its own SHA-256, so corruption in transit is caught
 *    at the chunk that suffered it, not at the end. Browsers only expose
 *    SubtleCrypto in a secure context: an installation reached over plain HTTP
 *    from another machine on the LAN does not have it, and there the hashes are
 *    simply omitted — the server still verifies the assembled file's size and
 *    reports the SHA-256 of what it stored, so the transfer is checked, just at
 *    the end instead of per chunk. (`crypto.subtle` cannot be polyfilled
 *    offline, and refusing to upload at all would be the worse trade.)
 *  - the caller is told real progress in bytes, which is what the queue row
 *    renders.
 */

const DEFAULT_RETRIES = 3;
const DEFAULT_BACKOFF_MS = 600;

/** Is SubtleCrypto available? (https:// or localhost — secure contexts only.) */
export function canHash() {
  // Strictly a boolean: a caller that tests `=== false` must be able to trust
  // this. (`crypto.subtle` is simply absent outside a secure context, which is
  // exactly the plain-HTTP-over-the-LAN case, so the earlier short-circuit
  // returned `undefined` and the page's warning about it never fired.)
  return typeof crypto !== 'undefined'
    && Boolean(crypto.subtle)
    && typeof crypto.subtle.digest === 'function';
}

async function sha256Hex(buffer) {
  if (!canHash()) return null;
  const digest = await crypto.subtle.digest('SHA-256', buffer);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    if (signal) {
      signal.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      }, { once: true });
    }
  });
}

/**
 * Stage one file through the chunked endpoints.
 *
 * @param {File} file
 * @param {object} options
 *   endpoint    base URL, e.g. '/api/input/uploads/chunked'
 *   headers     CSRF and other headers
 *   onProgress  ({ sentBytes, totalBytes, chunkIndex, totalChunks }) => void
 *   signal      AbortSignal (optional)
 *   retries     per-chunk retry count (optional)
 * @returns {Promise<{stagedPath: string, bytes: number, sha256: string, name: string}>}
 */
export async function stageLargeFile(file, options = {}) {
  const {
    endpoint = '/api/input/uploads/chunked',
    headers = {},
    onProgress = () => {},
    signal,
    retries = DEFAULT_RETRIES,
    // The path inside a folder selection when there is one; the server reduces
    // it to a name this host can write (Windows reserved names, drive
    // prefixes and over-long components are all handled there).
    filename = file.webkitRelativePath || file.relativePath || file.name,
  } = options;

  const start = await postJSON(`${endpoint}/start`, {
    filename,
    size: file.size,
  }, headers, signal);

  const uploadId = start.upload_id;
  const chunkSize = start.chunk_size;
  const totalChunks = start.total_chunks;
  let sentBytes = 0;

  try {
    for (let index = 0; index < totalChunks; index += 1) {
      const from = index * chunkSize;
      const blob = file.slice(from, Math.min(from + chunkSize, file.size));
      let attempt = 0;
      // eslint-disable-next-line no-constant-condition
      while (true) {
        try {
          await postChunk(`${endpoint}/${uploadId}/chunk/${index}`, blob, headers, signal);
          break;
        } catch (error) {
          if (error.name === 'AbortError' || attempt >= retries) throw error;
          attempt += 1;
          await sleep(DEFAULT_BACKOFF_MS * attempt, signal);
        }
      }
      sentBytes += blob.size;
      onProgress({ sentBytes, totalBytes: file.size, chunkIndex: index + 1, totalChunks });
    }

    const done = await postJSON(`${endpoint}/${uploadId}/complete`, {}, headers, signal);
    return {
      stagedPath: done.staged_path,
      bytes: done.bytes,
      // The hash the server computed over what it stored: on a host without
      // SubtleCrypto this is the only hash in the exchange, and it is the one
      // that matters (it is taken from the file that will be ingested).
      sha256: done.sha256,
      name: done.name || file.name,
      clientHashed: canHash(),
    };
  } catch (error) {
    // Leave nothing half-staged on the server when the transfer is abandoned.
    try {
      await fetch(`${endpoint}/${uploadId}`, { method: 'DELETE', headers });
    } catch (_) { /* best effort */ }
    throw error;
  }
}

async function postJSON(url, body, headers, signal) {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...headers },
    body: JSON.stringify(body),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    throw new Error((data.error && data.error.message) || `Request failed (${response.status})`);
  }
  return data;
}

async function postChunk(url, blob, headers, signal, digest) {
  const buffer = await blob.arrayBuffer();
  const hash = digest === undefined ? await sha256Hex(buffer) : digest;
  const form = new FormData();
  form.append('chunk', new Blob([buffer]), 'chunk');
  if (hash) form.append('sha256', hash);
  const response = await fetch(url, { method: 'POST', headers, body: form, signal });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.success === false) {
    throw new Error((data.error && data.error.message) || `Chunk rejected (${response.status})`);
  }
  return data;
}

export default stageLargeFile;
