/**
 * Concurrency Dashboard Page JavaScript
 * Extracted from concurrency/dashboard.html
 */
import { escapeHtml } from '../modules/core/utils.js';


let refreshTimer = null;

function dashboardPresent() {
    return !!document.getElementById('thread-metrics');
}

// Initialize page
document.addEventListener('DOMContentLoaded', function() {
    if (!dashboardPresent()) return;
    updateMetrics();
    if (refreshTimer) clearInterval(refreshTimer);
    // Soft navigation keeps this document alive, so the poll ends itself once
    // the dashboard is no longer on screen.
    refreshTimer = setInterval(function () {
        if (!dashboardPresent()) {
            clearInterval(refreshTimer);
            refreshTimer = null;
            return;
        }
        updateMetrics();
    }, 2000);
});

function updateMetrics() {
    fetch('/concurrency/api/metrics')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                updateManagerCard('thread', data.metrics.thread_manager);
                updateManagerCard('process', data.metrics.process_manager);
                updateManagerCard('async', data.metrics.async_manager);
                updateManagerCard('pool', data.metrics.pool_manager);
                updateMode(data);
            }
        })
        .catch(error => console.error('Error fetching metrics:', error));
    
    // Update detailed lists
    updateThreads();
    updateProcesses();
    updateAsyncTasks();
    updatePools();
}

function updateMode(data) {
    const banner = document.getElementById('concurrency-mode');
    if (!banner) return;
    if (!data.degraded) {
        banner.classList.add('d-none');
        banner.textContent = '';
        return;
    }
    const reason = (data.metrics.process_manager && data.metrics.process_manager.reason)
        || (data.metrics.pool_manager && data.metrics.pool_manager.reason)
        || 'Running without the process and pool managers.';
    const capacity = data.metrics.worker_capacity || {};
    const recommended = capacity.recommended_workers;
    banner.classList.remove('d-none');
    banner.textContent = recommended
        ? `${reason} This host recommends ${recommended} worker(s).`
        : reason;
}

function updateManagerCard(type, metrics) {
    const cardId = type + '-metrics';
    const card = document.getElementById(cardId);
    
    if (!card) {
        console.warn(`Card element not found: ${cardId}`);
        return;
    }
    
    if (metrics.status === 'error') {
        card.innerHTML = `<p class="text-danger">Error: ${escapeHtml(metrics.error || 'Unknown error')}</p>`;
        return;
    }

    if (metrics.status === 'unavailable') {
        // The host cannot start a multiprocessing manager. The server sends a
        // fixed sentence (never an exception text) and the card says what it
        // means instead of pretending the manager is idle.
        card.innerHTML = '<p><strong>Status:</strong> <span class="badge bg-secondary">unavailable</span></p>'
            + `<p class="text-muted mb-0">${escapeHtml(metrics.reason || 'Not available on this host.')}</p>`;
        return;
    }

    const statusClass = metrics.status === 'active' ? 'bg-success' : 'bg-secondary';
    let html = `<p><strong>Status:</strong> <span class="badge ${statusClass}">${metrics.status || 'unknown'}</span></p>`;
    
    if (type === 'thread') {
        html += `<p><strong>Active:</strong> ${metrics.active_threads || 0}</p>`;
        html += `<p><strong>Total:</strong> ${metrics.total_threads || 0}</p>`;
        html += `<p><strong>Completed:</strong> ${metrics.completed || 0}</p>`;
        html += `<p><strong>Errors:</strong> ${metrics.errors || 0}</p>`;
        if (metrics.paused) html += `<p><strong>Paused:</strong> ${metrics.paused}</p>`;
    } else if (type === 'process') {
        html += `<p><strong>Active:</strong> ${metrics.active_processes || 0}</p>`;
        html += `<p><strong>Total:</strong> ${metrics.total_processes || 0}</p>`;
        html += `<p><strong>Completed:</strong> ${metrics.completed || 0}</p>`;
        html += `<p><strong>Errors:</strong> ${metrics.errors || 0}</p>`;
        if (metrics.total_memory_mb) html += `<p><strong>Memory:</strong> ${metrics.total_memory_mb.toFixed(2)} MB</p>`;
        if (metrics.avg_cpu_percent) html += `<p><strong>Avg CPU:</strong> ${metrics.avg_cpu_percent.toFixed(1)}%</p>`;
    } else if (type === 'async') {
        html += `<p><strong>Active:</strong> ${metrics.active_tasks || 0}</p>`;
        html += `<p><strong>Total:</strong> ${metrics.total_tasks || 0}</p>`;
        html += `<p><strong>Completed:</strong> ${metrics.completed || 0}</p>`;
        html += `<p><strong>Errors:</strong> ${metrics.errors || 0}</p>`;
        if (metrics.paused) html += `<p><strong>Paused:</strong> ${metrics.paused}</p>`;
        if (metrics.total_runtime) html += `<p><strong>Total Runtime:</strong> ${metrics.total_runtime.toFixed(2)}s</p>`;
    } else if (type === 'pool') {
        html += `<p><strong>Total Pools:</strong> ${metrics.total_pools || 0}</p>`;
        html += `<p><strong>Total Workers:</strong> ${metrics.total_workers || 0}</p>`;
        html += `<p><strong>Completed:</strong> ${metrics.total_completed || 0}</p>`;
        html += `<p><strong>Failed:</strong> ${metrics.total_failed || 0}</p>`;
        html += `<p><strong>Pending:</strong> ${metrics.total_pending || 0}</p>`;
        if (metrics.total_memory_mb) html += `<p><strong>Memory:</strong> ${metrics.total_memory_mb.toFixed(2)} MB</p>`;
    }
    
    card.innerHTML = html;
}

function updateThreads() {
    fetch('/concurrency/api/threads')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const list = document.getElementById('threads-list');
                if (!list) return;
                if (!data.threads || data.threads.length === 0) {
                    list.innerHTML = '<p class="text-muted">No threads</p>';
                } else {
                    list.innerHTML = '<table class="table table-sm"><thead><tr><th>ID</th><th>Name</th><th>State</th><th>Priority</th></tr></thead><tbody>' +
                        data.threads.map(t => `<tr><td>${t.id || 'N/A'}</td><td>${escapeHtml(t.name || 'Unknown')}</td><td><span class="badge bg-info">${escapeHtml(t.state || 'unknown')}</span></td><td>${t.priority || 'N/A'}</td></tr>`).join('') +
                        '</tbody></table>';
                }
            }
        })
        .catch(error => console.error('Error fetching threads:', error));
}

function updateProcesses() {
    fetch('/concurrency/api/processes')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const list = document.getElementById('processes-list');
                if (!list) return;
                if (data.available === false) {
                    list.innerHTML = `<p class="text-muted">${escapeHtml(data.reason || 'Not available on this host.')}</p>`;
                } else if (!data.processes || data.processes.length === 0) {
                    list.innerHTML = '<p class="text-muted">No processes</p>';
                } else {
                    list.innerHTML = '<table class="table table-sm"><thead><tr><th>ID</th><th>Name</th><th>State</th><th>PID</th><th>Priority</th></tr></thead><tbody>' +
                        data.processes.map(p => `<tr><td>${p.id || 'N/A'}</td><td>${escapeHtml(p.name || 'Unknown')}</td><td><span class="badge bg-info">${escapeHtml(p.state || 'unknown')}</span></td><td>${p.pid || 'N/A'}</td><td>${p.priority || 'N/A'}</td></tr>`).join('') +
                        '</tbody></table>';
                }
            }
        })
        .catch(error => console.error('Error fetching processes:', error));
}

function updateAsyncTasks() {
    fetch('/concurrency/api/async-tasks')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const list = document.getElementById('async-tasks-list');
                if (!list) return;
                if (!data.tasks || data.tasks.length === 0) {
                    list.innerHTML = '<p class="text-muted">No async tasks</p>';
                } else {
                    list.innerHTML = '<table class="table table-sm"><thead><tr><th>ID</th><th>Name</th><th>State</th><th>Priority</th></tr></thead><tbody>' +
                        data.tasks.map(t => `<tr><td>${t.id || 'N/A'}</td><td>${escapeHtml(t.name || 'Unknown')}</td><td><span class="badge bg-info">${escapeHtml(t.state || 'unknown')}</span></td><td>${t.priority || 'N/A'}</td></tr>`).join('') +
                        '</tbody></table>';
                }
            }
        })
        .catch(error => console.error('Error fetching async tasks:', error));
}

function updatePools() {
    fetch('/concurrency/api/pools')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                const list = document.getElementById('pools-list');
                if (!list) return;
                if (data.available === false) {
                    list.innerHTML = `<p class="text-muted">${escapeHtml(data.reason || 'Not available on this host.')}</p>`;
                } else if (!data.pools || data.pools.length === 0) {
                    list.innerHTML = '<p class="text-muted">No pools</p>';
                } else {
                    list.innerHTML = '<table class="table table-sm"><thead><tr><th>ID</th><th>Name</th><th>State</th><th>Workers</th><th>Active Workers</th><th>Priority</th></tr></thead><tbody>' +
                        data.pools.map(p => `<tr><td>${p.id || 'N/A'}</td><td>${escapeHtml(p.name || 'Unknown')}</td><td><span class="badge bg-info">${escapeHtml(p.state || 'unknown')}</span></td><td>${p.worker_count || 0}</td><td>${p.active_workers || 0}</td><td>${p.priority || 'N/A'}</td></tr>`).join('') +
                        '</tbody></table>';
                }
            }
        })
        .catch(error => console.error('Error fetching pools:', error));
}

// Utility: Escape HTML
