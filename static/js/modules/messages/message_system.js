/**
 * Enhanced Message System
 * Provides modern, user-friendly error messages, processing messages, and notifications
 * for all interface tasks across the application
 * Integrated with translation system for multilingual support
 */

(function() {
    'use strict';
    
    class MessageSystem {
        constructor() {
            this.container = null;
            this.messages = [];
            this.maxMessages = 5;
            this.defaultDuration = 5000;
            this.processingOverlay = null;
            this.translator = null;
            this.init();
        }
        
        init() {
            // Create message container
            // The cards live in the shared top-end notification stack when the
            // page has one, so they never sit in a second corner of their own.
            if (!document.getElementById('message-container')) {
                this.container = document.createElement('div');
                this.container.id = 'message-container';
                this.container.className = 'message-container';
                const stack = document.querySelector('[data-toast-region]');
                (stack || document.body).appendChild(this.container);
            } else {
                this.container = document.getElementById('message-container');
            }
            
            // Create processing overlay
            this.createProcessingOverlay();
            
            // Initialize translator (will be available after translations.js loads)
            this.initTranslator();
        }
        
        initTranslator() {
            // Wait for translation helper to be available
            if (window.TranslationHelper) {
                this.translator = window.TranslationHelper;
            } else {
                // Try again after a short delay
                setTimeout(() => {
                    if (window.TranslationHelper) {
                        this.translator = window.TranslationHelper;
                    }
                }, 100);
            }
        }
        
        /**
         * Translate a message using the translation helper
         * @param {string} message - Message to translate
         * @returns {string} Translated message
         */
        translateMessage(message) {
            if (!message || typeof message !== 'string') {
                return message || '';
            }
            
            // If translator is available, use it
            if (this.translator) {
                return this.translator.translate(message);
            }
            
            // Fallback: try window.t or window.translate
            if (window.t) {
                return window.t(message);
            }
            if (window.translate) {
                return window.translate(message);
            }
            
            // If no translator available, return message as-is
            return message;
        }
        
        createProcessingOverlay() {
            if (!document.getElementById('processing-overlay')) {
                this.processingOverlay = document.createElement('div');
                this.processingOverlay.id = 'processing-overlay';
                this.processingOverlay.className = 'processing-overlay';
                this.processingOverlay.innerHTML = `
                    <div class="processing-content">
                        <div class="processing-spinner"></div>
                        <div class="processing-text" id="processing-text">Processing...</div>
                        <div class="processing-progress" id="processing-progress"></div>
                    </div>
                `;
                document.body.appendChild(this.processingOverlay);
            } else {
                this.processingOverlay = document.getElementById('processing-overlay');
            }
        }
        
        /**
         * Show a message
         * @param {string} message - The message text (will be translated automatically)
         * @param {string} type - Type: 'success', 'error', 'warning', 'info', 'processing'
         * @param {object} options - Additional options
         * @param {boolean} options.skipTranslation - Skip automatic translation (default: false)
         */
        show(message, type = 'info', options = {}) {
            const {
                title = null,
                duration = this.defaultDuration,
                persistent = false,
                action = null,
                onClose = null,
                details = null,
                code = null,
                skipTranslation = false
            } = options;
            
            // Translate message and title unless skipTranslation is true
            const translatedMessage = skipTranslation ? message : this.translateMessage(message);
            const translatedTitle = title && !skipTranslation ? this.translateMessage(title) : title;
            
            // Translate action label if present
            let translatedAction = action;
            if (action && action.label && !skipTranslation) {
                translatedAction = {
                    ...action,
                    label: this.translateMessage(action.label)
                };
            }
            
            // Remove oldest message if at max
            if (this.messages.length >= this.maxMessages) {
                this.remove(this.messages[0].id);
            }
            
            const messageObj = {
                id: `msg-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
                message: translatedMessage,
                type,
                title: translatedTitle,
                duration,
                persistent,
                action: translatedAction,
                onClose,
                details,
                code,
                element: null,
                timestamp: new Date()
            };
            
            this.messages.push(messageObj);
            this.render(messageObj);
            
            // Auto-remove if not persistent
            if (!persistent && duration > 0 && type !== 'processing') {
                messageObj.timeout = setTimeout(() => {
                    this.remove(messageObj.id);
                }, duration);
            }
            
            return messageObj.id;
        }
        
        render(messageObj) {
            const element = document.createElement('div');
            element.className = `message message-${messageObj.type}`;
            element.id = messageObj.id;
            element.setAttribute('role', 'alert');
            element.setAttribute('aria-live', messageObj.type === 'error' ? 'assertive' : 'polite');
            
            // Icon mapping
            const iconMap = {
                success: 'bi-check-circle-fill',
                error: 'bi-x-circle-fill',
                warning: 'bi-exclamation-triangle-fill',
                info: 'bi-info-circle-fill',
                processing: 'bi-hourglass-split'
            };
            
            // Color mapping
            const colorMap = {
                success: '#10b981',
                error: '#ef4444',
                warning: '#f59e0b',
                info: '#3b82f6',
                processing: '#6366f1'
            };
            
            // Icon
            const icon = document.createElement('i');
            icon.className = `bi ${iconMap[messageObj.type] || iconMap.info} message-icon`;
            icon.style.color = colorMap[messageObj.type] || colorMap.info;
            element.appendChild(icon);
            
            // Content
            const content = document.createElement('div');
            content.className = 'message-content';
            
            // Title
            if (messageObj.title) {
                const title = document.createElement('div');
                title.className = 'message-title';
                title.textContent = messageObj.title;
                content.appendChild(title);
            }
            
            // Message
            const messageEl = document.createElement('div');
            messageEl.className = 'message-text';
            messageEl.textContent = messageObj.message;
            content.appendChild(messageEl);
            
            // Details (for errors)
            if (messageObj.details && messageObj.type === 'error') {
                const detailsEl = document.createElement('div');
                detailsEl.className = 'message-details';
                const showDetailsText = this.translateMessage('Show Details') || 'Show Details';
                detailsEl.innerHTML = `
                    <button class="message-details-toggle" data-on-click="this.nextElementSibling.classList.toggle('expanded')">
                        <i class="bi bi-chevron-down"></i> ${this.escapeHtml(showDetailsText)}
                    </button>
                    <div class="message-details-content">
                        <pre>${this.escapeHtml(messageObj.details)}</pre>
                    </div>
                `;
                content.appendChild(detailsEl);
            }
            
            // Error code
            if (messageObj.code) {
                const codeEl = document.createElement('div');
                codeEl.className = 'message-code';
                const errorCodeText = this.translateMessage('Error Code') || 'Error Code';
                codeEl.textContent = `${errorCodeText}: ${messageObj.code}`;
                content.appendChild(codeEl);
            }
            
            element.appendChild(content);
            
            // Action buttons (support multiple actions)
            if (messageObj.action) {
                const actionsContainer = document.createElement('div');
                actionsContainer.className = 'message-actions';
                
                // Primary action
                const actionBtn = document.createElement('button');
                actionBtn.className = 'btn btn-sm message-action message-action-primary';
                const actionLabel = messageObj.action.label || this.translateMessage('Action') || 'Action';
                actionBtn.textContent = actionLabel;
                actionBtn.onclick = () => {
                    if (messageObj.action.callback) {
                        messageObj.action.callback();
                    }
                    this.remove(messageObj.id);
                };
                actionsContainer.appendChild(actionBtn);
                
                // Secondary action (cancel) if this is a confirmation
                if (messageObj.type === 'warning' && messageObj.action.callback) {
                    const cancelBtn = document.createElement('button');
                    cancelBtn.className = 'btn btn-sm btn-outline-secondary message-action message-action-cancel';
                    const cancelLabel = this.translateMessage('Cancel') || 'Cancel';
                    cancelBtn.textContent = cancelLabel;
                    cancelBtn.onclick = () => {
                        if (messageObj.onClose) {
                            messageObj.onClose();
                        }
                        this.remove(messageObj.id);
                    };
                    actionsContainer.appendChild(cancelBtn);
                }
                
                content.appendChild(actionsContainer);
            }
            
            // Close button
            const closeBtn = document.createElement('button');
            closeBtn.className = 'message-close';
            const closeLabel = this.translateMessage('Close') || 'Close';
            closeBtn.setAttribute('aria-label', closeLabel);
            closeBtn.innerHTML = '<i class="bi bi-x"></i>';
            closeBtn.onclick = () => this.remove(messageObj.id);
            element.appendChild(closeBtn);
            
            // Progress bar for auto-dismiss
            if (!messageObj.persistent && messageObj.duration > 0 && messageObj.type !== 'processing') {
                const progressBar = document.createElement('div');
                progressBar.className = 'message-progress';
                progressBar.style.animationDuration = `${messageObj.duration}ms`;
                element.appendChild(progressBar);
            }
            
            this.container.appendChild(element);
            messageObj.element = element;
            
            // Trigger animation
            requestAnimationFrame(() => {
                element.classList.add('message-visible');
            });
        }
        
        remove(id) {
            const index = this.messages.findIndex(m => m.id === id);
            if (index === -1) return;
            
            const message = this.messages[index];
            
            // Clear timeout
            if (message.timeout) {
                clearTimeout(message.timeout);
            }
            
            // Trigger onClose callback
            if (message.onClose) {
                message.onClose();
            }
            
            // Animate out
            if (message.element) {
                message.element.classList.add('message-hiding');
                setTimeout(() => {
                    if (message.element && message.element.parentNode) {
                        message.element.parentNode.removeChild(message.element);
                    }
                }, 300);
            }
            
            // Remove from array
            this.messages.splice(index, 1);
        }
        
        clearAll() {
            this.messages.forEach(msg => {
                this.remove(msg.id);
            });
        }
        
        // Processing overlay methods
        showProcessing(message = 'Processing...', progress = null) {
            if (this.processingOverlay) {
                const textEl = document.getElementById('processing-text');
                const progressEl = document.getElementById('processing-progress');
                
                // Translate processing message
                const translatedMessage = this.translateMessage(message) || message;
                if (textEl) textEl.textContent = translatedMessage;
                if (progressEl && progress !== null) {
                    progressEl.style.display = 'block';
                    progressEl.style.width = `${progress}%`;
                } else if (progressEl) {
                    progressEl.style.display = 'none';
                }
                
                this.processingOverlay.classList.add('visible');
            }
        }
        
        hideProcessing() {
            if (this.processingOverlay) {
                this.processingOverlay.classList.remove('visible');
            }
        }
        
        updateProcessing(message, progress = null) {
            if (this.processingOverlay && this.processingOverlay.classList.contains('visible')) {
                const textEl = document.getElementById('processing-text');
                const progressEl = document.getElementById('processing-progress');
                
                // Translate processing message
                const translatedMessage = this.translateMessage(message) || message;
                if (textEl) textEl.textContent = translatedMessage;
                if (progressEl && progress !== null) {
                    progressEl.style.width = `${progress}%`;
                }
            }
        }
        
        // Convenience methods
        success(message, options = {}) {
            return this.show(message, 'success', options);
        }
        
        error(message, options = {}) {
            return this.show(message, 'error', { duration: 8000, ...options });
        }
        
        warning(message, options = {}) {
            return this.show(message, 'warning', { duration: 6000, ...options });
        }
        
        info(message, options = {}) {
            return this.show(message, 'info', options);
        }
        
        processing(message, options = {}) {
            return this.show(message, 'processing', { persistent: true, ...options });
        }
        
        // Error handling helpers
        handleApiError(error, context = {}) {
            let message = this.translateMessage('An error occurred') || 'An error occurred';
            let details = null;
            let code = null;
            
            if (error.response) {
                // HTTP error response
                const status = error.response.status;
                const data = error.response.data || {};
                
                code = status;
                
                switch (status) {
                    case 400:
                        message = data.error || this.translateMessage('Invalid request. Please check your input.') || 'Invalid request. Please check your input.';
                        break;
                    case 401:
                        message = this.translateMessage('Authentication required. Please log in.') || 'Authentication required. Please log in.';
                        break;
                    case 403:
                        message = this.translateMessage('You do not have permission to perform this action.') || 'You do not have permission to perform this action.';
                        break;
                    case 404:
                        message = data.error || this.translateMessage('The requested resource was not found.') || 'The requested resource was not found.';
                        break;
                    case 429:
                        message = this.translateMessage('Too many requests. Please wait a moment and try again.') || 'Too many requests. Please wait a moment and try again.';
                        break;
                    case 500:
                        message = this.translateMessage('Server error. Please try again later.') || 'Server error. Please try again later.';
                        details = data.error || error.message;
                        break;
                    case 503:
                        message = this.translateMessage('Service temporarily unavailable. Please try again later.') || 'Service temporarily unavailable. Please try again later.';
                        break;
                    default:
                        const errorMsg = data.error || error.message || this.translateMessage('Unknown error') || 'Unknown error';
                        message = this.translateMessage('Error {status}: {message}', { status, message: errorMsg }) || `Error ${status}: ${errorMsg}`;
                }
                
                details = data.details || data.message || error.message;
            } else if (error.request) {
                // Request made but no response
                message = this.translateMessage('Network error. Please check your connection.') || 'Network error. Please check your connection.';
                details = this.translateMessage('The server did not respond. This may be due to network issues or the server being unavailable.') || 'The server did not respond. This may be due to network issues or the server being unavailable.';
            } else {
                // Error in request setup
                message = error.message || this.translateMessage('An unexpected error occurred') || 'An unexpected error occurred';
                details = error.stack;
            }
            
            // Add context information
            if (context.operation) {
                const operationText = this.translateMessage(context.operation) || context.operation;
                message = `${operationText}: ${message}`;
            }
            
            return this.error(message, {
                title: this.translateMessage('Error') || 'Error',
                details: details,
                code: code,
                action: context.retry ? {
                    label: this.translateMessage('Retry') || 'Retry',
                    callback: context.retry
                } : null
            });
        }
        
        // Success helpers
        showSuccess(operation, details = null) {
            const operationText = this.translateMessage(operation) || operation;
            const successMessage = this.translateMessage('{operation} completed successfully', { operation: operationText }) || `${operationText} completed successfully`;
            return this.success(successMessage, {
                title: this.translateMessage('Success') || 'Success',
                details: details
            });
        }
        
        // Processing helpers
        showProcessingWithProgress(operation, progressCallback = null) {
            const operationText = this.translateMessage(operation) || operation;
            const messageId = this.processing(operationText, {
                title: this.translateMessage('Processing') || 'Processing'
            });
            
            if (progressCallback) {
                // Set up progress updates
                const interval = setInterval(() => {
                    const progress = progressCallback();
                    if (progress !== null) {
                        this.updateProcessing(operationText, progress);
                        if (progress >= 100) {
                            clearInterval(interval);
                        }
                    }
                }, 100);
            }
            
            return messageId;
        }
        
        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }
    }
    
    // Initialize message system
    let messageSystem;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            messageSystem = new MessageSystem();
            window.MessageSystem = messageSystem;
            window.showMessage = (msg, type, opts) => messageSystem.show(msg, type, opts);
            window.showError = (msg, opts) => messageSystem.error(msg, opts);
            window.showSuccess = (msg, opts) => messageSystem.success(msg, opts);
            window.showWarning = (msg, opts) => messageSystem.warning(msg, opts);
            window.showInfo = (msg, opts) => messageSystem.info(msg, opts);
            window.showProcessing = (msg, progress) => messageSystem.showProcessing(msg, progress);
            window.hideProcessing = () => messageSystem.hideProcessing();
            window.handleApiError = (err, ctx) => messageSystem.handleApiError(err, ctx);
        });
    } else {
        messageSystem = new MessageSystem();
        window.MessageSystem = messageSystem;
        window.showMessage = (msg, type, opts) => messageSystem.show(msg, type, opts);
        window.showError = (msg, opts) => messageSystem.error(msg, opts);
        window.showSuccess = (msg, opts) => messageSystem.success(msg, opts);
        window.showWarning = (msg, opts) => messageSystem.warning(msg, opts);
        window.showInfo = (msg, opts) => messageSystem.info(msg, opts);
        window.showProcessing = (msg, progress) => messageSystem.showProcessing(msg, progress);
        window.hideProcessing = () => messageSystem.hideProcessing();
        window.handleApiError = (err, ctx) => messageSystem.handleApiError(err, ctx);
    }
    
    // Export for CommonJS
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = MessageSystem;
    }
    
    // Expose on window for ES module exports
    // Use a different name for the class to avoid overwriting the instance
    if (typeof window !== 'undefined') {
        window.MessageSystemClass = MessageSystem;
        // Only set window.MessageSystem to class if instance wasn't already set
        if (!window.MessageSystem || typeof window.MessageSystem.show !== 'function') {
            window.MessageSystem = MessageSystem;
        }
    }
})();

// Export for ES modules
// The IIFE above sets window.MessageSystem, so we need to export a getter
// that will access it when the module is imported
export function getMessageSystem() {
    if (typeof window !== 'undefined' && window.MessageSystem) {
        return window.MessageSystem;
    }
    throw new Error('MessageSystem not initialized yet');
}

// For default export, we'll export the class from window
// This is a workaround for IIFE modules
const MessageSystem = (typeof window !== 'undefined' && window.MessageSystem) 
    ? window.MessageSystem 
    : class MessageSystemPlaceholder {
        constructor() {
            throw new Error('MessageSystem not initialized. Import after DOM is ready.');
        }
    };

export { MessageSystem };
export default MessageSystem;

