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
            }
        })
        .catch(error => console.error('Error fetching metrics:', error));
    
    // Update detailed lists
    updateThreads();
    updateProcesses();
    updateAsyncTasks();
    updatePools();
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
                if (!data.processes || data.processes.length === 0) {
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
                if (!data.pools || data.pools.length === 0) {
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
