/**
 * Message Formatter
 * Links messages to notifications with better formatting
 * Allows adding, updating, and deleting message templates
 * Notification only - not storage, formatted notification only
 * 
 * Usage Examples:
 * 
 * // Show a formatted notification
 * MessageFormatter.showDeleteSuccess('Source');
 * MessageFormatter.showAddError('Side');
 * 
 * // Add a custom template
 * MessageFormatter.addTemplate('custom', 'success', 'Custom operation completed: {item}');
 * MessageFormatter.showNotification('custom', 'success', { item: 'My Item' });
 * 
 * // Update an existing template
 * MessageFormatter.updateTemplate('delete', 'success', '{item} has been permanently removed');
 * 
 * // Delete a custom template
 * MessageFormatter.deleteTemplate('custom', 'success');
 * 
 * // Get all templates
 * const templates = MessageFormatter.getAllTemplates();
 * 
 * // Reset to defaults
 * MessageFormatter.resetTemplates();
 */

(function() {
    'use strict';
    
    class MessageFormatter {
        constructor() {
            // Default message templates
            this.templates = {
                // Add operations
                add: {
                    success: '{item} added successfully',
                    error: 'Error adding {item}',
                    processing: 'Adding {item}...'
                },
                // Update operations
                update: {
                    success: '{item} updated successfully',
                    error: 'Error updating {item}',
                    processing: 'Updating {item}...'
                },
                // Delete operations
                delete: {
                    success: '{item} deleted successfully',
                    error: 'Error deleting {item}',
                    processing: 'Deleting {item}...',
                    confirm: 'Are you sure you want to delete {item}? This action cannot be undone.'
                },
                // Create operations
                create: {
                    success: '{item} created successfully',
                    error: 'Error creating {item}',
                    processing: 'Creating {item}...'
                },
                // Save operations
                save: {
                    success: 'Changes saved successfully',
                    error: 'Failed to save changes',
                    processing: 'Saving...'
                },
                // Remove operations
                remove: {
                    success: '{item} removed successfully',
                    error: 'Error removing {item}',
                    processing: 'Removing {item}...'
                },
                // Generic operations
                generic: {
                    success: 'Operation completed successfully',
                    error: 'Operation failed',
                    processing: 'Processing...',
                    info: 'Information',
                    warning: 'Warning'
                }
            };
            
            // Notification formatters
            this.formatters = {
                success: this.formatSuccess.bind(this),
                error: this.formatError.bind(this),
                warning: this.formatWarning.bind(this),
                info: this.formatInfo.bind(this),
                processing: this.formatProcessing.bind(this)
            };

            // Open "processing" toasts; the next outcome toast closes them.
            this.pendingProcessing = [];
            
            this.init();
        }
        
        init() {
            // Load custom templates from localStorage if available
            this.loadCustomTemplates();
        }
        
        /**
         * Load custom templates from localStorage
         */
        loadCustomTemplates() {
            try {
                const stored = localStorage.getItem('messageFormatterTemplates');
                if (stored) {
                    const customTemplates = JSON.parse(stored);
                    // Merge with defaults (custom overrides defaults)
                    this.templates = { ...this.templates, ...customTemplates };
                }
            } catch (e) {
                console.warn('Could not load custom message templates:', e);
            }
        }
        
        /**
         * Save custom templates to localStorage
         */
        saveCustomTemplates() {
            try {
                // Only save custom templates (not defaults)
                const customTemplates = {};
                for (const [key, value] of Object.entries(this.templates)) {
                    // Check if it's a custom template (not in original defaults)
                    if (this.isCustomTemplate(key)) {
                        customTemplates[key] = value;
                    }
                }
                localStorage.setItem('messageFormatterTemplates', JSON.stringify(customTemplates));
            } catch (e) {
                console.warn('Could not save custom message templates:', e);
            }
        }
        
        /**
         * Check if a template is custom (not in original defaults)
         */
        isCustomTemplate(key) {
            const defaultKeys = ['add', 'update', 'delete', 'create', 'save', 'remove', 'generic'];
            return !defaultKeys.includes(key);
        }
        
        /**
         * Format a message using a template
         * @param {string} operation - Operation type (add, update, delete, etc.)
         * @param {string} status - Status (success, error, processing)
         * @param {object} params - Parameters to replace in template (e.g., {item: 'Source'})
         * @returns {string} Formatted message
         */
        format(operation, status, params = {}) {
            const template = this.getTemplate(operation, status);
            if (!template) {
                return this.formatGeneric(status, params);
            }
            
            // Replace placeholders
            let message = template;
            for (const [key, value] of Object.entries(params)) {
                message = message.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
            }
            
            return message;
        }
        
        /**
         * Get a template for an operation and status
         */
        getTemplate(operation, status) {
            if (this.templates[operation] && this.templates[operation][status]) {
                return this.templates[operation][status];
            }
            return null;
        }
        
        /**
         * Format a generic message
         */
        formatGeneric(status, params = {}) {
            const template = this.templates.generic[status] || this.templates.generic.info;
            let message = template;
            for (const [key, value] of Object.entries(params)) {
                message = message.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
            }
            return message;
        }
        
        /**
         * Format success notification
         */
        formatSuccess(message, options = {}) {
            return {
                message: message,
                type: 'success',
                title: options.title || 'Success',
                icon: 'bi-check-circle-fill',
                duration: options.duration || 4000,
                ...options
            };
        }
        
        /**
         * Format error notification
         */
        formatError(message, options = {}) {
            return {
                message: message,
                type: 'error',
                title: options.title || 'Error',
                icon: 'bi-x-circle-fill',
                duration: options.duration || 6000,
                ...options
            };
        }
        
        /**
         * Format warning notification
         */
        formatWarning(message, options = {}) {
            return {
                message: message,
                type: 'warning',
                title: options.title || 'Warning',
                icon: 'bi-exclamation-triangle-fill',
                duration: options.duration || 5000,
                ...options
            };
        }
        
        /**
         * Format info notification
         */
        formatInfo(message, options = {}) {
            return {
                message: message,
                type: 'info',
                title: options.title || 'Information',
                icon: 'bi-info-circle-fill',
                duration: options.duration || 4000,
                ...options
            };
        }
        
        /**
         * Format processing notification
         */
        formatProcessing(message, options = {}) {
            return {
                message: message,
                type: 'info',
                title: options.title || 'Processing',
                icon: 'bi-hourglass-split',
                duration: 0, // Persistent until manually closed
                persistent: true,
                ...options
            };
        }
        
        /**
         * Show a formatted notification
         * @param {string} operation - Operation type
         * @param {string} status - Status (success, error, processing)
         * @param {object} params - Parameters for template
         * @param {object} options - Additional options
         */
        showNotification(operation, status, params = {}, options = {}) {
            // Format the message; options.detail carries the server's reason
            let message = this.format(operation, status, params);
            if (options.detail) {
                message += ': ' + options.detail;
            }
            
            // Format the notification
            const formatter = this.formatters[status] || this.formatters.info;
            const notification = formatter(message, options);
            
            // Show using available notification system
            this.displayNotification(notification);
            
            return notification;
        }
        
        /**
         * Display notification using available system
         */
        displayNotification(notification) {
            if (!notification.persistent) {
                this.settleProcessing();
            }

            // Try NotificationSystem first
            if (window.notifications && window.notifications.show) {
                const handle = window.notifications.show(notification.message, notification.type, {
                    title: notification.title,
                    duration: notification.duration,
                    persistent: notification.persistent
                });
                if (notification.persistent && handle) {
                    this.pendingProcessing.push(handle);
                }
                return;
            }
            
            // Try MessageSystem
            if (window.MessageSystem && window.MessageSystem.show) {
                window.MessageSystem.show(notification.message, notification.type, {
                    title: notification.title,
                    duration: notification.duration
                });
                return;
            }
            
            // Try showToast (from templates)
            if (window.showToast) {
                window.showToast(notification.message, notification.type, notification.duration);
                return;
            }
            
            // Fallback to alert
            console.warn('No notification system available, using alert');
            alert(`${notification.title}: ${notification.message}`);
        }

        /**
         * Close every open processing notification: its operation has an outcome now.
         */
        settleProcessing() {
            const pending = this.pendingProcessing.splice(0);
            pending.forEach((handle) => {
                if (handle instanceof Element) {
                    const toast = window.bootstrap && window.bootstrap.Toast
                        ? window.bootstrap.Toast.getInstance(handle) : null;
                    if (toast) {
                        toast.hide();
                    } else {
                        handle.remove();
                    }
                } else if (window.notifications && window.notifications.remove) {
                    window.notifications.remove(handle);
                }
            });
        }
        
        /**
         * Add a new message template
         * @param {string} operation - Operation type
         * @param {string} status - Status (success, error, processing, etc.)
         * @param {string} template - Template string with {placeholders}
         */
        addTemplate(operation, status, template) {
            if (!this.templates[operation]) {
                this.templates[operation] = {};
            }
            this.templates[operation][status] = template;
            this.saveCustomTemplates();
            return true;
        }
        
        /**
         * Update an existing message template
         * @param {string} operation - Operation type
         * @param {string} status - Status
         * @param {string} template - New template string
         */
        updateTemplate(operation, status, template) {
            if (!this.templates[operation] || !this.templates[operation][status]) {
                throw new Error(`Template not found: ${operation}.${status}`);
            }
            this.templates[operation][status] = template;
            this.saveCustomTemplates();
            return true;
        }
        
        /**
         * Delete a message template
         * @param {string} operation - Operation type
         * @param {string} status - Status (optional, if not provided, deletes entire operation)
         */
        deleteTemplate(operation, status = null) {
            if (status === null) {
                // Delete entire operation
                if (this.isCustomTemplate(operation)) {
                    delete this.templates[operation];
                    this.saveCustomTemplates();
                    return true;
                } else {
                    throw new Error(`Cannot delete default template: ${operation}`);
                }
            } else {
                // Delete specific status template
                if (this.isCustomTemplate(operation) || 
                    (this.templates[operation] && this.templates[operation][status] && 
                     !this.isDefaultTemplate(operation, status))) {
                    if (this.templates[operation]) {
                        delete this.templates[operation][status];
                        // Clean up empty operation objects
                        if (Object.keys(this.templates[operation]).length === 0) {
                            delete this.templates[operation];
                        }
                        this.saveCustomTemplates();
                        return true;
                    }
                } else {
                    throw new Error(`Cannot delete default template: ${operation}.${status}`);
                }
            }
            return false;
        }
        
        /**
         * Check if a template is a default template
         */
        isDefaultTemplate(operation, status) {
            const defaultTemplates = {
                add: ['success', 'error', 'processing'],
                update: ['success', 'error', 'processing'],
                delete: ['success', 'error', 'processing', 'confirm'],
                create: ['success', 'error', 'processing'],
                save: ['success', 'error', 'processing'],
                remove: ['success', 'error', 'processing'],
                generic: ['success', 'error', 'processing', 'info', 'warning']
            };
            return defaultTemplates[operation] && defaultTemplates[operation].includes(status);
        }
        
        /**
         * Get all templates
         */
        getAllTemplates() {
            return JSON.parse(JSON.stringify(this.templates));
        }
        
        /**
         * Get templates for a specific operation
         */
        getTemplates(operation) {
            return this.templates[operation] ? JSON.parse(JSON.stringify(this.templates[operation])) : null;
        }
        
        /**
         * Reset templates to defaults
         */
        resetTemplates() {
            // Reload defaults
            this.templates = {
                add: {
                    success: '{item} added successfully',
                    error: 'Error adding {item}',
                    processing: 'Adding {item}...'
                },
                update: {
                    success: '{item} updated successfully',
                    error: 'Error updating {item}',
                    processing: 'Updating {item}...'
                },
                delete: {
                    success: '{item} deleted successfully',
                    error: 'Error deleting {item}',
                    processing: 'Deleting {item}...',
                    confirm: 'Are you sure you want to delete {item}? This action cannot be undone.'
                },
                create: {
                    success: '{item} created successfully',
                    error: 'Error creating {item}',
                    processing: 'Creating {item}...'
                },
                save: {
                    success: 'Changes saved successfully',
                    error: 'Failed to save changes',
                    processing: 'Saving...'
                },
                remove: {
                    success: '{item} removed successfully',
                    error: 'Error removing {item}',
                    processing: 'Removing {item}...'
                },
                generic: {
                    success: 'Operation completed successfully',
                    error: 'Operation failed',
                    processing: 'Processing...',
                    info: 'Information',
                    warning: 'Warning'
                }
            };
            
            // Clear custom templates from localStorage
            try {
                localStorage.removeItem('messageFormatterTemplates');
            } catch (e) {
                console.warn('Could not clear custom templates:', e);
            }
        }
        
        /**
         * Convenience methods for common operations
         */
        showAddSuccess(itemName = 'Item', options = {}) {
            return this.showNotification('add', 'success', { item: itemName }, options);
        }
        
        showAddError(itemName = 'Item', options = {}) {
            return this.showNotification('add', 'error', { item: itemName }, options);
        }
        
        showUpdateSuccess(itemName = 'Item', options = {}) {
            return this.showNotification('update', 'success', { item: itemName }, options);
        }
        
        showUpdateError(itemName = 'Item', options = {}) {
            return this.showNotification('update', 'error', { item: itemName }, options);
        }
        
        showDeleteSuccess(itemName = 'Item', options = {}) {
            return this.showNotification('delete', 'success', { item: itemName }, options);
        }
        
        showDeleteError(itemName = 'Item', options = {}) {
            return this.showNotification('delete', 'error', { item: itemName }, options);
        }
        
        showCreateSuccess(itemName = 'Item', options = {}) {
            return this.showNotification('create', 'success', { item: itemName }, options);
        }
        
        showCreateError(itemName = 'Item', options = {}) {
            return this.showNotification('create', 'error', { item: itemName }, options);
        }
        
        showSaveSuccess(options = {}) {
            return this.showNotification('save', 'success', {}, options);
        }
        
        showSaveError(options = {}) {
            return this.showNotification('save', 'error', {}, options);
        }
    }
    
    // Initialize message formatter
    let messageFormatter;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            messageFormatter = new MessageFormatter();
            window.MessageFormatter = messageFormatter;
        });
    } else {
        messageFormatter = new MessageFormatter();
        window.MessageFormatter = messageFormatter;
    }
    
    // Export for CommonJS
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = MessageFormatter;
    }
    
    // Expose on window for ES module exports. MSGFMT-01: only assign when no
    // instance exists yet — this module can be evaluated more than once
    // (universal-initializer re-import), and overwriting the *instance* with
    // the raw class removed the instance methods (showNotification etc.) that
    // page scripts call directly, breaking e.g. source deletion feedback.
    if (typeof window !== 'undefined' && !window.MessageFormatter) {
        window.MessageFormatter = MessageFormatter;
    }
})();

// Export for ES modules - get from window since it's in IIFE
const MessageFormatter = typeof window !== 'undefined' ? window.MessageFormatter : class {};
export { MessageFormatter };
export default MessageFormatter;

