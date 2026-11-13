/**
 * Live processing-progress tracker.
 *
 * WHY THIS EXISTS
 * ---------------
 * The progress bar used to sit at 0% for an entire run and then jump to 100%.
 * The backend now reports a dynamically growing workload (top-level files plus
 * every archive member, email attachment, embedded Office/OpenDocument object
 * and recursive descendant discovered while processing), but the frontend had
 * two problems of its own:
 *
 *   1. The page that used to ship a `#processingProgressContainer` block
 *      (`templates/file/upload.html`, removed in the ingestion-interface
 *      consolidation) styled the bar but never polled `/upload/active-tasks`,
 *      so the bar was dead markup and never appeared at all. That page is gone;
 *      the dashboard is the surviving consumer of this tracker.
 *   2. The dashboard's copy rebuilt the whole list with `innerHTML` on every
 *      poll. That destroys the DOM node carrying `transition: width .3s`, so
 *      even correct percentages rendered as a jump instead of a movement.
 *
 * This module is the single renderer for those pages. It polls, keeps one DOM
 * node per task (so CSS transitions animate), and surfaces the nested/terminal
 * breakdown rather than a bare percentage.
 *
 * Scope note: the tasks it shows come from Api/task_manager.py (the import
 * flows create them). New ingestions do not appear here - they are Jobs, shown
 * in the Jobs Center (/operations/jobs) and created by the ingestion page
 * (/operations/input).
 */

const DEFAULT_INTERVAL_MS = 1500;
const ACTIVE_TASKS_URL = '/upload/active-tasks';

/** Escape untrusted strings before they reach innerHTML. */
export function escapeHtml(value) {
    if (value === null || value === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(value);
    return div.innerHTML;
}

/** Clamp a percentage into 0..100 and normalise junk to 0. */
function clampPercent(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 0;
    return Math.min(100, Math.max(0, n));
}

/**
 * Build the human-readable detail line for one task.
 *
 * Nested work is called out explicitly: when a container is opened mid-run the
 * denominator grows, and a user watching "37 / 140" needs to know why the total
 * moved rather than assuming the bar went backwards.
 */
function describeTask(task) {
    const detail = task.detail || {};
    const parts = [];

    const done = Number(task.current) || 0;
    const total = Number(task.total) || 0;
    parts.push(`${done} / ${total} files`);

    const nested = Number(task.nested) || 0;
    if (nested > 0) {
        const initial = Number(task.initial_total) || (total - nested);
        parts.push(`${nested} nested from ${initial} top-level`);
    }

    const running = Number(detail.in_progress) || 0;
    const queued = Number(detail.pending) || 0;
    if (running > 0) parts.push(`${running} running`);
    if (queued > 0) parts.push(`${queued} queued`);

    const failed = Number(detail.failed) || 0;
    const skipped = Number(detail.skipped) || 0;
    const unsupported = Number(detail.unsupported) || 0;
    const retryable = Number(detail.retryable) || 0;
    if (failed > 0) parts.push(`${failed} failed`);
    if (retryable > 0) parts.push(`${retryable} retryable`);
    if (skipped > 0) parts.push(`${skipped} skipped`);
    if (unsupported > 0) parts.push(`${unsupported} unsupported`);

    return parts.join(' · ');
}

const TASK_TEMPLATE = `
    <div class="processing-task-item" data-role="item">
        <div class="task-header">
            <div class="task-info">
                <i class="bi" data-role="icon"></i>
                <span class="task-label" data-role="label"></span>
            </div>
            <span class="task-percent" data-role="percent"></span>
        </div>
        <div class="task-message" data-role="message"></div>
        <div class="progress-bar-wrapper" style="margin-top: 0.5rem;">
            <div class="progress-bar">
                <div class="progress-bar-fill" data-role="fill" style="width: 0%;"></div>
                <div class="progress-bar-text" data-role="counters"></div>
            </div>
        </div>
        <div class="task-message" data-role="detail" style="margin-top:0.35rem;"></div>
    </div>
`;

/**
 * Poll `/upload/active-tasks` and render live progress.
 *
 * Usage:
 *   const tracker = new ProcessingProgressTracker();
 *   tracker.start();      // begins polling
 *   tracker.stop();       // clears the interval
 */
export class ProcessingProgressTracker {
    constructor(options = {}) {
        this.containerId = options.containerId || 'processingProgressContainer';
        this.listId = options.listId || 'activeTasksList';
        this.intervalMs = options.intervalMs || DEFAULT_INTERVAL_MS;
        this.onUpdate = options.onUpdate || null;
        this._timer = null;
        this._nodes = new Map();   // task_id -> element
        this._inflight = false;
        this._lastError = null;
    }

    start() {
        if (this._timer) return;
        this.refresh();
        this._timer = setInterval(() => this.refresh(), this.intervalMs);
    }

    stop() {
        if (this._timer) {
            clearInterval(this._timer);
            this._timer = null;
        }
    }

    get isRunning() {
        return this._timer !== null;
    }

    async refresh() {
        // Never stack requests: a slow response must not cause a burst of
        // overlapping polls that render out of order.
        if (this._inflight) return;
        this._inflight = true;
        try {
            const response = await fetch(ACTIVE_TASKS_URL, {
                headers: { 'Accept': 'application/json' },
                cache: 'no-store',
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const data = await response.json();
            const tasks = (data && data.success && Array.isArray(data.tasks)) ? data.tasks : [];
            this._render(tasks);
            this._lastError = null;
            if (typeof this.onUpdate === 'function') this.onUpdate(tasks);
        } catch (error) {
            // A transient poll failure must not blank a bar that was showing
            // real progress; keep the last render and retry on the next tick.
            this._lastError = error;
            console.debug('Progress poll failed (will retry):', error);
        } finally {
            this._inflight = false;
        }
    }

    _render(tasks) {
        const container = document.getElementById(this.containerId);
        const list = document.getElementById(this.listId);
        if (!container || !list) return;

        if (!tasks.length) {
            container.style.display = 'none';
            this._nodes.forEach((node) => node.remove());
            this._nodes.clear();
            return;
        }
        container.style.display = 'block';

        const seen = new Set();
        tasks.forEach((task) => {
            const id = String(task.task_id || task.job_id || Math.random());
            seen.add(id);

            let node = this._nodes.get(id);
            if (!node) {
                const holder = document.createElement('div');
                holder.innerHTML = TASK_TEMPLATE.trim();
                node = holder.firstElementChild;
                node.dataset.taskId = id;
                list.appendChild(node);
                this._nodes.set(id, node);
            }
            this._paint(node, task);
        });

        // Drop nodes for tasks that are no longer active.
        [...this._nodes.keys()].forEach((id) => {
            if (!seen.has(id)) {
                const node = this._nodes.get(id);
                if (node) node.remove();
                this._nodes.delete(id);
            }
        });
    }

    _paint(node, task) {
        const status = String(task.status || 'pending').toLowerCase();
        const running = status === 'running';
        const percent = clampPercent(
            task.progress_percent !== undefined && task.progress_percent !== null
                ? task.progress_percent
                : (task.detail && task.detail.percent)
        );

        node.classList.toggle('running', running);
        node.classList.toggle('pending', !running);

        const icon = node.querySelector('[data-role="icon"]');
        if (icon) {
            icon.className = `bi ${running ? 'bi-arrow-repeat' : 'bi-hourglass-split'}`;
        }

        const label = node.querySelector('[data-role="label"]');
        if (label) label.textContent = task.label || 'Processing…';

        const percentEl = node.querySelector('[data-role="percent"]');
        if (percentEl) percentEl.textContent = `${Math.round(percent)}%`;

        const message = node.querySelector('[data-role="message"]');
        if (message) message.textContent = task.message || '';

        // Only `style.width` changes, so the CSS transition animates the
        // movement instead of the bar snapping on every poll.
        const fill = node.querySelector('[data-role="fill"]');
        if (fill) fill.style.width = `${percent}%`;

        const counters = node.querySelector('[data-role="counters"]');
        if (counters) counters.textContent = describeTask(task);

        const detailEl = node.querySelector('[data-role="detail"]');
        if (detailEl) {
            const bits = [];
            if (task.detail && task.detail.containers_opened) {
                bits.push(`${task.detail.containers_opened} container(s) expanded`);
            }
            if (task.total_is_dynamic) bits.push('total grows as containers open');
            detailEl.textContent = bits.join(' · ');
        }
    }
}

/**
 * Convenience wrapper matching the historical dashboard globals, so existing
 * `onclick`/`DOMContentLoaded` wiring keeps working.
 */
let _sharedTracker = null;

export function startProcessingProgressPolling(options = {}) {
    if (_sharedTracker) _sharedTracker.stop();
    _sharedTracker = new ProcessingProgressTracker(options);
    _sharedTracker.start();
    // Exposed so page code can force an immediate refresh (e.g. right after a
    // job is submitted) instead of waiting out the poll interval.
    if (typeof window !== 'undefined') {
        window.processingProgressTracker = _sharedTracker;
    }
    return _sharedTracker;
}

export function stopProcessingProgressPolling() {
    if (_sharedTracker) {
        _sharedTracker.stop();
        _sharedTracker = null;
    }
    if (typeof window !== 'undefined') {
        window.processingProgressTracker = null;
    }
}

export function getProcessingProgressTracker() {
    return _sharedTracker;
}

export default ProcessingProgressTracker;
