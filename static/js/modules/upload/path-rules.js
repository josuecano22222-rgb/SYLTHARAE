/**
 * Path rules for the ingestion page — pure functions, no DOM.
 *
 * The page has to answer path questions for three different inputs (a typed
 * server path, a folder selection, a single file) on three different host
 * families (Windows, macOS, Linux). Windows is the primary production
 * platform: there, paths are drive-qualified or UNC, `\` and `/` are both
 * separators, comparison is case-insensitive, and a "kept inside the roots"
 * check that ignores any of that will tell the operator the wrong thing.
 *
 * Everything here is a *hint for the interface*: the authoritative containment
 * decision is made server-side by core.path_safety.validate_ingestion_path,
 * which resolves the real filesystem (symlinks included). These functions only
 * decide what the page says before the request is made, so they must be
 * exactly as strict, never more permissive — a wrong "looks fine" costs a round
 * trip, a wrong "outside the roots" hides a working feature.
 *
 * Kept separate from the page module so it is unit-testable under node.
 */

/**
 * A queued item is either a File (picker, plain drag) or `{file, path}` — a
 * file walked out of a *dropped folder*, where the path inside that folder has
 * to be carried explicitly because the browser hands over an entry tree, not a
 * File with a webkitRelativePath. Both shapes are understood here so no other
 * module has to know about the difference.
 */
export function fileOf(item) {
  return item && item.file ? item.file : item;
}

/** The path inside a folder selection ('' for a file picked on its own). */
export function relativePathFor(item) {
  const file = fileOf(item);
  if (item && !item.file && typeof item.webkitRelativePath === 'string') {
    return item.webkitRelativePath || (item.relativePath || '');
  }
  if (item && typeof item.path === 'string' && item.path) return item.path;
  return (file && (file.webkitRelativePath || file.relativePath)) || '';
}

/**
 * Did the browser tell us where these files sit inside the folder that was
 * chosen? Every browser that implements `webkitdirectory` (Edge and Chrome on
 * Windows, Firefox, Safari) fills `webkitRelativePath` for such a selection; a
 * browser that does not sends the files flat. Saying so is the difference
 * between an operator knowing the tree will be reproduced and quietly getting
 * their folders dropped.
 */
export function folderStructureAvailable(items) {
  const list = Array.from(items || []);
  if (!list.length) return true;
  return list.some((item) => Boolean(relativePathFor(item)));
}

/** What to show (and send) for a selected file: its path within the folder
 *  selection, or its bare name when it was selected on its own. */
export function displayNameFor(item) {
  const file = fileOf(item);
  return relativePathFor(item) || (file && file.name) || '';
}

/**
 * Collapse `.` and `..` segments the way the filesystem will, textually.
 *
 * Not a substitute for server-side resolution (symlinks, junctions), just
 * enough to stop a naive prefix test from accepting `root/../elsewhere`.
 *
 * Separators are normalised only where the host treats them as separators:
 * on Windows `\` and `/` are interchangeable, on POSIX `\` is an ordinary
 * character in a file name — folding it there would make `/data\evil` look
 * like `/data/evil` and report a path inside a root it is not inside. A hint
 * may be less permissive than the server, never more.
 */
export function collapseDotSegments(path, platform = 'posix') {
  const text = platform === 'nt'
    ? String(path || '').replace(/[\\/]+/g, '/')
    : String(path || '').replace(/\/+/g, '/');
  const absolute = text.startsWith('/');
  const out = [];
  for (const segment of text.split('/')) {
    if (!segment || segment === '.') continue;
    if (segment === '..') {
      if (out.length && out[out.length - 1] !== '..') out.pop();
      else if (!absolute) out.push('..');
      continue;
    }
    out.push(segment);
  }
  return (absolute ? '/' : '') + out.join('/');
}

/** Comparison form: separators normalised, trailing slash dropped, and on
 *  Windows the case folded (Windows paths are case-insensitive there and not
 *  elsewhere — treating POSIX as case-insensitive would accept
 *  /data/Inbox as being under /data/inbox). */
export function normaliseForCompare(path, platform = 'posix') {
  let text = collapseDotSegments(path, platform);
  if (text.length > 1 && text.endsWith('/')) text = text.slice(0, -1);
  return platform === 'nt' ? text.toLowerCase() : text;
}

/** Is `path` at or below `root`? (`platform` is 'nt' for Windows hosts.) */
export function isInside(path, root, platform = 'posix') {
  const target = normaliseForCompare(path, platform);
  let boundary = normaliseForCompare(root, platform);
  if (!target || !boundary) return false;
  if (boundary.length > 1 && boundary.endsWith('/')) boundary = boundary.slice(0, -1);
  return target === boundary || target.startsWith(`${boundary}/`);
}

/** Does this look like a Windows path (drive-qualified or UNC)? */
export function isWindowsPath(path) {
  const text = String(path || '').trim();
  return /^[A-Za-z]:[\\/]/.test(text) || /^[\\/]{2}[^\\/]/.test(text);
}

/** An example path for the server-path field, built from a configured root.
 *  Shows the operator the shape that will be accepted on this host. */
export function examplePathFor(root, platform = 'posix') {
  const base = String(root || '').replace(/[\\/]+$/, '');
  if (!base) return platform === 'nt' ? 'C:\\data\\inbox' : '/data/inbox';
  const separator = platform === 'nt' || isWindowsPath(base) ? '\\' : '/';
  return `${base}${separator}subfolder`;
}

/** Where a dropped/selected file belongs, given the folder it came from. */
export function joinRelative(prefix, name) {
  const left = String(prefix || '').replace(/[\\/]+$/, '');
  const right = String(name || '').replace(/^[\\/]+/, '');
  if (!left) return right;
  if (!right) return left;
  return `${left}/${right}`;
}

/** Shorten a long path for display, keeping the file name readable. */
export function shortenPath(path, max = 64) {
  const text = String(path || '');
  if (text.length <= max) return text;
  const separator = text.includes('\\') && !text.includes('/') ? '\\' : '/';
  const parts = text.split(/[\\/]/);
  const name = parts[parts.length - 1];
  if (name.length >= max - 4) return `…${separator}${name.slice(-(max - 3))}`;
  return `${parts[0]}${separator}…${separator}${name}`;
}
