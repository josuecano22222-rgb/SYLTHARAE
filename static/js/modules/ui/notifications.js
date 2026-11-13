/**
 * Global Notification System
 * Provides toast notifications throughout the application
 */

class NotificationSystem {
    constructor() {
        this.container = null;
        this.notifications = [];
        this.maxNotifications = 5;
        this.defaultDuration = 5000;
        this.init();
    }

    init() {
        // Create notification container if it doesn't exist
        if (!document.getElementById('notification-container')) {
            this.container = document.createElement('div');
            this.container.id = 'notification-container';
            this.container.className = 'notification-container';
            document.body.appendChild(this.container);
        } else {
            this.container = document.getElementById('notification-container');
        }

        // Listen for system setting changes
        document.addEventListener('systemSettingChanged', (e) => {
            if (e.detail.key === 'notifications_enabled') {
                // Notifications can be globally disabled
                if (!e.detail.value) {
                    this.clearAll();
                }
            }
        });
    }

    /**
     * Show a notification
     * @param {string} message - The notification message
     * @param {string} type - Type: 'success', 'error', 'warning', 'info'
     * @param {object} options - Additional options
     */
    show(message, type = 'info', options = {}) {
        // Check if notifications are enabled
        if (window.systemSettings && !window.systemSettings.shouldShowNotifications()) {
            return;
        }

        const {
            title = null,
            duration = this.defaultDuration,
            persistent = false,
            action = null,
            onClose = null
        } = options;

        // One notification stack for the whole app. Every page's showSuccess and
        // notification call lands in the top-end stack (core/toast.js), not in a
        // second container at the bottom of the page.
        if (window.Toast) {
            const tone = ['success', 'error', 'warning', 'info'].includes(type) ? type : 'info';
            return window.Toast.show({
                tone: tone,
                title: title || undefined,
                message: message,
                persistent: !!persistent,
                timeout: persistent ? undefined : duration,
                actionLabel: action && action.label ? action.label : undefined,
                onAction: action && typeof action.callback === 'function' ? action.callback : undefined,
                dismissible: true
            });
        }

        // Remove oldest notification if at max
        if (this.notifications.length >= this.maxNotifications) {
            this.remove(this.notifications[0].id);
        }

        const notification = {
            id: `notification-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
            message,
            type,
            title,
            duration,
            persistent,
            action,
            onClose,
            element: null
        };

        this.notifications.push(notification);
        this.render(notification);

        // Auto-remove if not persistent
        if (!persistent && duration > 0) {
            notification.timeout = setTimeout(() => {
                this.remove(notification.id);
            }, duration);
        }

        return notification.id;
    }

    render(notification) {
        const element = document.createElement('div');
        element.className = `notification ${notification.type}`;
        element.id = notification.id;
        element.setAttribute('role', 'alert');
        element.setAttribute('aria-live', notification.type === 'error' ? 'assertive' : 'polite');

        // Icon
        const iconMap = {
            success: 'bi-check-circle-fill',
            error: 'bi-x-circle-fill',
            warning: 'bi-exclamation-triangle-fill',
            info: 'bi-info-circle-fill'
        };

        const icon = document.createElement('i');
        icon.className = `bi ${iconMap[notification.type] || iconMap.info} notification-icon`;
        element.appendChild(icon);

        // Content
        const content = document.createElement('div');
        content.className = 'notification-content';

        if (notification.title) {
            const title = document.createElement('div');
            title.className = 'notification-title';
            title.textContent = notification.title;
            content.appendChild(title);
        }

        const message = document.createElement('p');
        message.className = 'notification-message';
        message.textContent = notification.message;
        content.appendChild(message);

        element.appendChild(content);

        // Action button (if provided)
        if (notification.action) {
            const actionBtn = document.createElement('button');
            actionBtn.className = 'btn btn-sm btn-outline-primary ms-2';
            actionBtn.textContent = notification.action.label || 'Action';
            actionBtn.onclick = () => {
                if (notification.action.callback) {
                    notification.action.callback();
                }
                this.remove(notification.id);
            };
            content.appendChild(actionBtn);
        }

        // Close button
        const closeBtn = document.createElement('button');
        closeBtn.className = 'notification-close';
        closeBtn.setAttribute('aria-label', 'Close notification');
        closeBtn.innerHTML = '<i class="bi bi-x"></i>';
        closeBtn.onclick = () => this.remove(notification.id);
        element.appendChild(closeBtn);

        // Progress bar for auto-dismiss
        if (!notification.persistent && notification.duration > 0) {
            const progressBar = document.createElement('div');
            progressBar.className = 'notification-progress-bar';
            progressBar.style.animationDuration = `${notification.duration}ms`;
            element.appendChild(progressBar);
            element.classList.add('progress');
        }

        this.container.appendChild(element);
        notification.element = element;

        // Trigger animation
        requestAnimationFrame(() => {
            element.style.animation = 'slideInRight 0.3s ease-out';
        });
    }

    remove(id) {
        const index = this.notifications.findIndex(n => n.id === id);
        if (index === -1) return;

        const notification = this.notifications[index];

        // Clear timeout if exists
        if (notification.timeout) {
            clearTimeout(notification.timeout);
        }

        // Trigger onClose callback
        if (notification.onClose) {
            notification.onClose();
        }

        // Animate out
        if (notification.element) {
            notification.element.classList.add('slide-out');
            setTimeout(() => {
                if (notification.element && notification.element.parentNode) {
                    notification.element.parentNode.removeChild(notification.element);
                }
            }, 300);
        }

        // Remove from array
        this.notifications.splice(index, 1);
    }

    clearAll() {
        this.notifications.forEach(notification => {
            this.remove(notification.id);
        });
    }

    // Convenience methods
    success(message, options = {}) {
        return this.show(message, 'success', options);
    }

    error(message, options = {}) {
        return this.show(message, 'error', options);
    }

    warning(message, options = {}) {
        return this.show(message, 'warning', options);
    }

    info(message, options = {}) {
        return this.show(message, 'info', options);
    }
}

// Initialize notification system
let notificationSystem;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        notificationSystem = new NotificationSystem();
    });
} else {
    notificationSystem = new NotificationSystem();
}

// Export for use in other scripts
export default notificationSystem;

// Also expose on window for backward compatibility
window.notifications = notificationSystem;

// Convenience functions for backward compatibility
window.showSuccess = (message) => notificationSystem.success(message);
window.showError = (message) => notificationSystem.error(message);
window.showWarning = (message) => notificationSystem.warning(message);
window.showInfo = (message) => notificationSystem.info(message);

