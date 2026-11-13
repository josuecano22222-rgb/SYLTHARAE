/**
 * Notifications page — single workspace:
 * type tab → filters → read-status → list.
 */

const notificationsPage = {
    sources: {},
    sides: {},
    t: {},
    currentTab: 'all',
    currentStatus: 'all',
    filters: {
        source: '',
        side: '',
        search: '',
        sort_by: 'created_at',
        sort_order: 'desc',
        page: 1,
    },
    searchTimeout: null,
    currentNotificationId: null,
};

let initialized = false;

function t(key, fallback) {
    return notificationsPage.t[key] || window.appTranslations?.[fallback] || fallback;
}

function initializeNotificationsPage() {
    if (initialized) return;
    initialized = true;

    const pageDataEl = document.getElementById('notifications-page-data');
    if (pageDataEl) {
        try {
            const data = JSON.parse(pageDataEl.textContent);
            notificationsPage.sources = data.sources || {};
            notificationsPage.sides = data.sides || {};
            notificationsPage.t = data.translations || {};
        } catch (e) {
            console.error('Error parsing notifications page data:', e);
        }
    }

    notificationsPage.loadList();
    notificationsPage.updateNotificationBadge();
}

export default function init() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeNotificationsPage);
    } else {
        initializeNotificationsPage();
    }
}

/* ── Window handlers (declarative-events) ─────────────────────────── */

window.switchTab = function (tab) {
    notificationsPage.switchTab(tab);
};

window.switchStatus = function (status) {
    notificationsPage.switchStatus(status);
};

window.setFilter = function (filterType, value) {
    notificationsPage.setFilter(filterType, value);
};

window.handleSearch = function (event) {
    notificationsPage.handleSearch(event);
};

window.refreshAll = function () {
    notificationsPage.loadList();
};

window.setSort = function (sortValue) {
    notificationsPage.setSort(sortValue);
};

window.markAsRead = function (notificationId, reload = true) {
    notificationsPage.markAsRead(notificationId, reload);
};

window.markAllAsRead = function () {
    notificationsPage.markAllAsRead();
};

window.dismissNotification = function (notificationId) {
    notificationsPage.dismissNotification(notificationId);
};

window.openMessageDetail = function (notificationId) {
    notificationsPage.openMessageDetail(notificationId);
};

window.closeMessageDetail = function (event) {
    notificationsPage.closeMessageDetail(event);
};

window.markAsReadFromDetail = function () {
    notificationsPage.markAsReadFromDetail();
};

window.dismissNotificationFromDetail = function () {
    notificationsPage.dismissNotificationFromDetail();
};

window.scanForNotifications = async function () {
    const scanBtn = document.getElementById('scanBtn');
    const scanBtnText = document.getElementById('scanBtnText');
    const scanSpinner = document.getElementById('scanSpinner');
    if (!scanBtn || scanBtn.disabled) return;

    scanBtn.disabled = true;
    scanBtn.classList.add('disabled');
    scanSpinner?.classList.remove('d-none');
    if (scanBtnText) scanBtnText.textContent = t('scanning', 'Scanning…');

    try {
        const response = await fetch('/api/notifications/scan', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || '',
            },
        });
        const data = await response.json();
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        if (!data.success) throw new Error(data.error || data.message || t('scanFailed', 'Scan failed'));

        const parts = [];
        if (data.duplicates_found > 0) {
            parts.push(`${data.duplicates_found} ${t('duplicatesFound', 'duplicate file(s)')}`);
        }
        if (data.future_dates_found > 0) {
            parts.push(`${data.future_dates_found} ${t('futureFound', 'future date(s)')}`);
        }
        const created = data.notifications_created?.length > 0
            ? `${data.notifications_created.length} ${t('created', 'notification(s) created')}`
            : t('noNew', 'No new notifications created');
        const message = parts.length
            ? `${t('scanDone', 'Scan completed')}: ${parts.join(', ')}. ${created}.`
            : `${t('scanDone', 'Scan completed')}. ${created}.`;

        if (window.showMessage) window.showMessage(message, 'success');
        else alert(message);

        setTimeout(() => {
            if (data.duplicates_found > 0) notificationsPage.switchTab('duplicates');
            else if (data.future_dates_found > 0) notificationsPage.switchTab('future');
            else notificationsPage.loadList();
            notificationsPage.updateNotificationBadge();
        }, 800);
    } catch (error) {
        console.error('Error scanning for notifications:', error);
        const errorMsg = `${t('scanFailed', 'Scan failed')}: ${error.message}`;
        if (window.showMessage) window.showMessage(errorMsg, 'error');
        else alert(errorMsg);
    } finally {
        scanBtn.disabled = false;
        scanBtn.classList.remove('disabled');
        scanSpinner?.classList.add('d-none');
        if (scanBtnText) scanBtnText.textContent = t('scan', 'Scan');
    }
};

/* ── Core behaviour ───────────────────────────────────────────────── */

notificationsPage.switchTab = function (tab) {
    this.currentTab = tab;
    this.filters.page = 1;

    document.querySelectorAll('.notif-type-btn').forEach((btn) => {
        const active = btn.dataset.tab === tab;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    this.loadList();
};

notificationsPage.switchStatus = function (status) {
    this.currentStatus = status;
    this.filters.page = 1;

    document.querySelectorAll('.notif-status-btn').forEach((btn) => {
        const active = btn.dataset.status === status;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    this.loadList();
};

notificationsPage.setFilter = function (filterType, value) {
    this.filters[filterType] = value;
    this.filters.page = 1;
    this.loadList();
};

notificationsPage.handleSearch = function (event) {
    const apply = () => {
        this.filters.search = event.target.value.trim();
        this.filters.page = 1;
        this.loadList();
    };
    if (event.key === 'Enter' || event.keyCode === 13) {
        clearTimeout(this.searchTimeout);
        apply();
        return;
    }
    clearTimeout(this.searchTimeout);
    this.searchTimeout = setTimeout(apply, 400);
};

notificationsPage.setSort = function (sortValue) {
    let sortBy;
    let sortOrder;
    if (sortValue.startsWith('created_at_')) {
        sortBy = 'created_at';
        sortOrder = sortValue.replace('created_at_', '');
    } else if (sortValue.startsWith('priority_')) {
        sortBy = 'priority';
        sortOrder = sortValue.replace('priority_', '');
    } else if (sortValue.startsWith('title_')) {
        sortBy = 'title';
        sortOrder = sortValue.replace('title_', '');
    } else {
        const parts = sortValue.split('_');
        sortBy = parts[0];
        sortOrder = parts[parts.length - 1];
    }
    this.filters.sort_by = sortBy;
    this.filters.sort_order = sortOrder;
    this.filters.page = 1;
    this.loadList();
};

notificationsPage.loadList = async function () {
    const list = document.getElementById('messagesList');
    if (!list) return;

    const loadingText = t('loading', 'Loading…');
    list.innerHTML = `<div class="loading-spinner"><i class="bi bi-arrow-repeat" aria-hidden="true"></i><p>${this.escapeHtml(loadingText)}</p></div>`;

    try {
        const params = new URLSearchParams({
            page: String(this.filters.page),
            per_page: '100',
            search: this.filters.search || '',
            read_status: this.currentStatus,
            sort_by: this.filters.sort_by || 'created_at',
            sort_order: this.filters.sort_order || 'desc',
        });

        if (this.currentTab === 'duplicates') {
            params.set('type', 'similar_files');
        } else if (this.currentTab === 'future') {
            params.set('type', 'future_date,future_event');
        }
        if (this.filters.source) params.set('source_id', this.filters.source);
        if (this.filters.side) params.set('side_id', this.filters.side);

        const response = await fetch(`/api/notifications/paginated?${params}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        if (!data.success) {
            const errorMsg = data.error || t('errorLoading', 'Error loading notifications');
            list.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i><h3>Error</h3><p>${this.escapeHtml(errorMsg)}</p></div>`;
            return;
        }

        const summary = data.summary || { total: 0, unread: 0, read: 0 };
        this.updateOverview(summary);
        this.renderNotifications(data.notifications || []);

        const sortSelect = document.getElementById('notifSort');
        if (sortSelect) {
            sortSelect.value = `${this.filters.sort_by}_${this.filters.sort_order}`;
        }
    } catch (error) {
        console.error('Error loading notifications:', error);
        list.innerHTML = `<div class="empty-state"><i class="bi bi-exclamation-triangle" aria-hidden="true"></i><h3>Error</h3><p>${this.escapeHtml(t('errorLoading', 'Error loading notifications'))}</p></div>`;
    }
};

notificationsPage.updateOverview = function (summary) {
    const total = Number(summary.total) || 0;
    const unread = Number(summary.unread) || 0;
    const read = Number(summary.read) || 0;

    const setText = (id, value) => {
        const el = document.getElementById(id);
        if (el) el.textContent = String(value);
    };

    setText('statTotal', total);
    setText('statUnread', unread);
    setText('statRead', read);
    setText('countAll', total);
    setText('countUnread', unread);
    setText('countRead', read);
};

notificationsPage.renderNotifications = function (notifications) {
    const list = document.getElementById('messagesList');
    if (!list) return;

    if (!notifications.length) {
        list.innerHTML = `
            <div class="empty-state">
                <i class="bi bi-bell-slash" aria-hidden="true"></i>
                <h3>${this.escapeHtml(t('noneTitle', 'No notifications'))}</h3>
                <p>${this.escapeHtml(t('noneBody', 'Nothing matches the current type, status, and filters.'))}</p>
            </div>`;
        return;
    }

    const markReadTitle = t('markRead', 'Mark as Read');
    const dismissTitle = t('dismiss', 'Dismiss');

    list.innerHTML = notifications.map((n) => {
        const timeAgo = this.formatTime(n.created_at);
        const typeIcon = this.getTypeIcon(n.type);
        const typeLabel = String(n.type || '').replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
        const priorityLabel = String(n.priority || '').charAt(0).toUpperCase() + String(n.priority || '').slice(1);
        const preview = (n.message || '').length > 160
            ? `${n.message.substring(0, 160)}…`
            : (n.message || '');
        const unreadClass = n.read ? '' : 'unread';
        const fileName = n.file_name
            ? (n.file_name.length > 36 ? `${n.file_name.substring(0, 36)}…` : n.file_name)
            : '';

        return `
            <article class="message-card ${unreadClass}" data-on-click="openMessageDetail(${n.id})"
                     data-id="${n.id}" data-type="${this.escapeHtml(n.type || '')}">
                <div class="message-header">
                    <div class="message-avatar priority-${this.escapeHtml(n.priority || 'low')}">${typeIcon}</div>
                    <div class="message-info">
                        <div class="message-title-row">
                            <h3 class="message-title">${this.escapeHtml(n.title || '')}</h3>
                            <time class="message-time">${timeAgo}</time>
                        </div>
                        <div class="message-badges">
                            <span class="type-badge">${this.escapeHtml(typeLabel)}</span>
                            <span class="priority-badge priority-${this.escapeHtml(n.priority || 'low')}">${this.escapeHtml(priorityLabel)}</span>
                        </div>
                    </div>
                </div>
                <div class="message-content">${this.escapeHtml(preview)}</div>
                <div class="message-footer">
                    <div class="message-meta">
                        ${fileName ? `<span><i class="bi bi-file-earmark" aria-hidden="true"></i> ${this.escapeHtml(fileName)}</span>` : ''}
                        ${n.event_date ? `<span><i class="bi bi-calendar" aria-hidden="true"></i> ${new Date(n.event_date).toLocaleDateString()}</span>` : ''}
                    </div>
                    <div class="message-actions">
                        <button type="button" class="message-action-btn"
                                data-on-click="event.stopPropagation(); markAsRead(${n.id})"
                                title="${this.escapeHtml(markReadTitle)}" aria-label="${this.escapeHtml(markReadTitle)}">
                            <i class="bi bi-check" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="message-action-btn danger"
                                data-on-click="event.stopPropagation(); dismissNotification(${n.id})"
                                title="${this.escapeHtml(dismissTitle)}" aria-label="${this.escapeHtml(dismissTitle)}">
                            <i class="bi bi-x" aria-hidden="true"></i>
                        </button>
                    </div>
                </div>
            </article>`;
    }).join('');
};

notificationsPage.getTypeIcon = function (type) {
    const icons = {
        similar_files: '<i class="bi bi-files" aria-hidden="true"></i>',
        future_date: '<i class="bi bi-calendar-event" aria-hidden="true"></i>',
        future_event: '<i class="bi bi-calendar2-week" aria-hidden="true"></i>',
        processing_complete: '<i class="bi bi-check-circle-fill" aria-hidden="true"></i>',
        batch_complete: '<i class="bi bi-box-seam" aria-hidden="true"></i>',
        error: '<i class="bi bi-x-octagon-fill" aria-hidden="true"></i>',
        warning: '<i class="bi bi-exclamation-triangle-fill" aria-hidden="true"></i>',
        info: '<i class="bi bi-info-circle-fill" aria-hidden="true"></i>',
        scenario_outcome: '<i class="bi bi-diagram-3-fill" aria-hidden="true"></i>',
        scenario_status: '<i class="bi bi-slash-circle" aria-hidden="true"></i>',
    };
    return icons[type] || '<i class="bi bi-bell-fill" aria-hidden="true"></i>';
};

notificationsPage.formatTime = function (dateString) {
    const date = new Date(dateString);
    const now = new Date();
    const diff = now - date;
    const minutes = Math.floor(diff / 60000);
    const hours = Math.floor(diff / 3600000);
    const days = Math.floor(diff / 86400000);

    if (minutes < 1) return window.appTranslations?.['Just now'] || 'Just now';
    if (minutes < 60) return `${minutes} ${window.appTranslations?.['m ago'] || 'm ago'}`;
    if (hours < 24) return `${hours} ${window.appTranslations?.['h ago'] || 'h ago'}`;
    if (days < 7) return `${days} ${window.appTranslations?.['d ago'] || 'd ago'}`;
    return date.toLocaleDateString();
};

notificationsPage.escapeHtml = function (text) {
    const div = document.createElement('div');
    div.textContent = text == null ? '' : String(text);
    return div.innerHTML;
};

notificationsPage.markAsRead = async function (notificationId) {
    try {
        const response = await fetch(`/api/notifications/${notificationId}/read`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
            },
        });
        const data = await response.json();
        if (data.success) {
            this.loadList();
            this.updateNotificationBadge();
        }
    } catch (error) {
        console.error('Error marking as read:', error);
    }
};

notificationsPage.markAllAsRead = async function () {
    const confirmMsg = t('markAllConfirm', 'Mark all visible notifications as read?');
    if (!confirm(confirmMsg)) return;

    try {
        const cards = document.querySelectorAll('.message-card[data-id]');
        await Promise.all(Array.from(cards).map((card) =>
            fetch(`/api/notifications/${card.dataset.id}/read`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
                },
            })));
        this.loadList();
        this.updateNotificationBadge();
    } catch (error) {
        console.error('Error marking all as read:', error);
    }
};

notificationsPage.dismissNotification = async function (notificationId) {
    const confirmMsg = t('dismissConfirm', 'Dismiss this notification?');
    if (!confirm(confirmMsg)) return;

    try {
        const response = await fetch(`/api/notifications/${notificationId}/dismiss`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '',
            },
        });
        const data = await response.json();
        if (data.success) {
            this.loadList();
            this.updateNotificationBadge();
        }
    } catch (error) {
        console.error('Error dismissing notification:', error);
    }
};

notificationsPage.updateNotificationBadge = async function () {
    try {
        const response = await fetch('/api/notifications/stats');
        const data = await response.json();
        if (!data.success || !data.stats) return;
        const badge = document.getElementById('notificationsBadge');
        if (!badge) return;
        const unreadCount = data.stats.unread || 0;
        if (unreadCount > 0) {
            badge.textContent = unreadCount > 99 ? '99+' : unreadCount;
            badge.style.display = 'block';
        } else {
            badge.style.display = 'none';
        }
    } catch (error) {
        console.error('Error updating notification badge:', error);
    }
};

notificationsPage.openMessageDetail = function (notificationId) {
    this.currentNotificationId = notificationId;
    const modal = document.getElementById('messageDetailModal');
    const detailBody = document.getElementById('detailBody');
    const detailTitle = document.getElementById('detailTitle');

    fetch(`/api/notifications/${notificationId}`)
        .then((res) => res.json())
        .then((data) => {
            if (!data.success || !data.notification) return;
            const n = data.notification;
            const typeValue = String(n.type || '').replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase());
            const priorityValue = String(n.priority || '').charAt(0).toUpperCase() + String(n.priority || '').slice(1);
            const createdDate = new Date(n.created_at).toLocaleString();
            const eventDate = n.event_date ? new Date(n.event_date).toLocaleDateString() : null;
            const sourceName = n.metadata?.source_id
                ? (this.sources[n.metadata.source_id] || `Source ${n.metadata.source_id}`)
                : null;
            const sideName = n.metadata?.side_id
                ? (this.sides[n.metadata.side_id] || `Side ${n.metadata.side_id}`)
                : null;

            if (detailTitle) detailTitle.textContent = n.title || '';

            const L = (k, f) => window.appTranslations?.[k] || f;
            let html = `
                <div class="message-detail-section">
                    <h4>${L('Message', 'Message')}</h4>
                    <p>${this.escapeHtml(n.message || '')}</p>
                </div>
                <div class="message-detail-info-grid">
                    <div class="message-detail-info-item">
                        <strong>${L('Type', 'Type')}</strong>
                        <span>${this.escapeHtml(typeValue)}</span>
                    </div>
                    <div class="message-detail-info-item">
                        <strong>${L('Priority', 'Priority')}</strong>
                        <span class="priority-badge priority-${this.escapeHtml(n.priority || 'low')}">${this.escapeHtml(priorityValue)}</span>
                    </div>
                    <div class="message-detail-info-item">
                        <strong>${L('Created', 'Created')}</strong>
                        <span>${createdDate}</span>
                    </div>`;

            if (eventDate) {
                html += `
                    <div class="message-detail-info-item">
                        <strong>${L('Event Date', 'Event Date')}</strong>
                        <span>${eventDate}</span>
                    </div>`;
            }
            if (n.file_name) {
                html += `
                    <div class="message-detail-info-item">
                        <strong>${L('File', 'File')}</strong>
                        <span>${this.escapeHtml(n.file_name)}</span>
                    </div>`;
            }
            if (sourceName) {
                html += `
                    <div class="message-detail-info-item">
                        <strong>${L('Source', 'Source')}</strong>
                        <span>${this.escapeHtml(sourceName)}</span>
                    </div>`;
            }
            if (sideName) {
                html += `
                    <div class="message-detail-info-item">
                        <strong>${L('Side', 'Side')}</strong>
                        <span>${this.escapeHtml(sideName)}</span>
                    </div>`;
            }
            if (n.file_path) {
                html += `
                    <div class="message-detail-info-item">
                        <strong>${L('File Path', 'File Path')}</strong>
                        <span class="text-break">${this.escapeHtml(n.file_path)}</span>
                    </div>`;
            }
            html += '</div>';

            if (n.metadata && Object.keys(n.metadata).length) {
                const skip = new Set(['source_id', 'side_id']);
                const extras = Object.entries(n.metadata).filter(([k]) => !skip.has(k));
                if (extras.length) {
                    html += `<div class="message-detail-section mt-3"><h4>${L('Additional Information', 'Additional Information')}</h4>`;
                    extras.forEach(([key, value]) => {
                        html += `<div class="message-detail-info-item mb-2"><strong>${this.escapeHtml(key)}</strong><span>${this.escapeHtml(typeof value === 'object' ? JSON.stringify(value) : value)}</span></div>`;
                    });
                    html += '</div>';
                }
            }

            if (detailBody) detailBody.innerHTML = html;

            const markBtn = document.getElementById('detailMarkReadBtn');
            const markText = document.getElementById('markReadText');
            if (markBtn && markText) {
                if (n.read) {
                    markBtn.disabled = true;
                    markText.textContent = L('Already Read', 'Already Read');
                } else {
                    markBtn.disabled = false;
                    markText.textContent = L('Mark as Read', 'Mark as Read');
                }
            }
            modal?.classList.add('active');
        })
        .catch((error) => console.error('Error loading notification detail:', error));
};

notificationsPage.closeMessageDetail = function (event) {
    if (event && event.target !== event.currentTarget && !event.target.closest('.message-detail-close')) {
        return;
    }
    document.getElementById('messageDetailModal')?.classList.remove('active');
    this.currentNotificationId = null;
};

notificationsPage.markAsReadFromDetail = async function () {
    if (!this.currentNotificationId) return;
    await this.markAsRead(this.currentNotificationId);
    this.closeMessageDetail();
};

notificationsPage.dismissNotificationFromDetail = async function () {
    if (!this.currentNotificationId) return;
    await this.dismissNotification(this.currentNotificationId);
    this.closeMessageDetail();
};
