/**
 * Message Router
 * Ensures ALL messages in the system go through the notification system
 * and are translatable. This is the single source of truth for all user-facing messages.
 */

(function() {
    'use strict';
    
    class MessageRouter {
        constructor() {
            this.messageSystem = null;
            this.translator = null;
            this.init();
        }
        
        init() {
            // Wait for message system to be available
            this.initializeMessageSystem();
            
            // If not available, try again after a short delay
            if (!this.messageSystem) {
                setTimeout(() => {
                    this.initializeMessageSystem();
                }, 100);
            }
            
            // Also try after DOM is ready if still not available
            if (!this.messageSystem && document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', () => {
                    this.initializeMessageSystem();
                });
            }
            
            // Get translator
            if (window.TranslationHelper) {
                this.translator = window.TranslationHelper;
            }
            
            // Override console methods to also show notifications for errors/warnings
            this.interceptConsoleMessages();
        }
        
        /**
         * Initialize message system - handle both instance and class
         */
        initializeMessageSystem() {
            if (window.MessageSystem) {
                // Check if it's an instance (has show method) or a class
                if (typeof window.MessageSystem.show === 'function') {
                    // It's an instance
                    this.messageSystem = window.MessageSystem;
                } else if (typeof window.MessageSystem === 'function') {
                    // It's a class, create an instance
                    try {
                        this.messageSystem = new window.MessageSystem();
                        // Update window.MessageSystem to be the instance
                        window.MessageSystem = this.messageSystem;
                    } catch (e) {
                        console.warn('Failed to create MessageSystem instance:', e);
                    }
                }
            }
        }
        
        /**
         * Intercept console.error and console.warn to show notifications
         */
        interceptConsoleMessages() {
            const originalError = console.error;
            const originalWarn = console.warn;
            
            // Only intercept if explicitly enabled (to avoid spam)
            if (window.ENABLE_CONSOLE_NOTIFICATIONS) {
                console.error = (...args) => {
                    originalError.apply(console, args);
                    // Show notification for user-facing errors
                    const message = args.join(' ');
                    if (message && !message.includes('Error loading') && !message.includes('Failed to')) {
                        // Only show if it looks like a user-facing error
                        if (this.messageSystem && message.length < 200) {
                            this.messageSystem.error(message);
                        }
                    }
                };
                
                console.warn = (...args) => {
                    originalWarn.apply(console, args);
                    // Show notification for important warnings
                    const message = args.join(' ');
                    if (message && message.length < 200) {
                        if (this.messageSystem) {
                            this.messageSystem.warning(message);
                        }
                    }
                };
            }
        }
        
        /**
         * Show a confirmation dialog
         * @param {string} message - The message to display
         * @param {object} options - Options for the confirmation
         * @returns {Promise<boolean>} - Promise that resolves to true if confirmed, false otherwise
         */
        confirm(message, options = {}) {
            return new Promise((resolve) => {
                const {
                    title = null,
                    confirmLabel = 'Yes',
                    cancelLabel = 'No',
                    type = 'warning',
                    persistent = true
                } = options;
                
                // Ensure messageSystem is initialized
                if (!this.messageSystem) {
                    this.initializeMessageSystem();
                }
                
                if (!this.messageSystem || typeof this.messageSystem.show !== 'function') {
                    // Fallback to original native confirm (avoid recursion)
                    const originalConfirm = window.__originalConfirm || window.confirm;
                    resolve(originalConfirm(message));
                    return;
                }
                
                // Translate message and labels
                const translatedMessage = this.translate(message);
                const translatedTitle = title ? this.translate(title) : (this.translate('Confirmation') || 'Confirmation');
                const translatedConfirm = this.translate(confirmLabel);
                const translatedCancel = this.translate(cancelLabel);
                
                // Show confirmation notification
                const messageId = this.messageSystem.show(translatedMessage, type, {
                    title: translatedTitle,
                    persistent: persistent,
                    action: {
                        label: translatedConfirm,
                        callback: () => {
                            resolve(true);
                        }
                    },
                    onClose: () => {
                        resolve(false);
                    }
                });
                
                // Add cancel button (as a second action)
                // Note: We'll need to enhance the message system to support multiple actions
                // For now, clicking outside or close button will resolve to false
            });
        }
        
        /**
         * Show a success message with optional confirmation
         */
        success(message, options = {}) {
            if (!this.messageSystem) {
                if (window.alert) {
                    window.alert(message);
                }
                return;
            }
            
            const translatedMessage = this.translate(message);
            this.messageSystem.success(translatedMessage, options);
        }
        
        /**
         * Show an error message
         */
        error(message, options = {}) {
            if (!this.messageSystem) {
                if (window.alert) {
                    window.alert(message);
                }
                return;
            }
            
            const translatedMessage = this.translate(message);
            this.messageSystem.error(translatedMessage, options);
        }
        
        /**
         * Show a warning message
         */
        warning(message, options = {}) {
            if (!this.messageSystem) {
                if (window.alert) {
                    window.alert(message);
                }
                return;
            }
            
            const translatedMessage = this.translate(message);
            this.messageSystem.warning(translatedMessage, options);
        }
        
        /**
         * Show an info message
         */
        info(message, options = {}) {
            if (!this.messageSystem) {
                if (window.alert) {
                    window.alert(message);
                }
                return;
            }
            
            const translatedMessage = this.translate(message);
            this.messageSystem.info(translatedMessage, options);
        }
        
        /**
         * Translate a message
         */
        translate(message) {
            if (this.translator) {
                return this.translator.translate(message);
            }
            if (window.t) {
                return window.t(message);
            }
            if (window.translate) {
                return window.translate(message);
            }
            return message;
        }
        
        /**
         * Show a confirmation before performing an action
         * @param {string} message - Confirmation message
         * @param {function} onConfirm - Callback if confirmed
         * @param {object} options - Additional options
         */
        confirmAction(message, onConfirm, options = {}) {
            this.confirm(message, options).then((confirmed) => {
                if (confirmed && onConfirm) {
                    onConfirm();
                }
            });
        }
    }
    
    // Initialize message router instance
    const messageRouter = new MessageRouter();
    
    // Export globally as the single source for all messages
    // Keep the INSTANCE on window.MessageRouter (not the class)
    if (typeof window !== 'undefined') {
        window.MessageRouter = messageRouter; // Instance, not class
        window.MessageRouterClass = MessageRouter; // Class for ES module exports
        window.showConfirm = (msg, opts) => messageRouter.confirm(msg, opts);
        window.confirmAction = (msg, callback, opts) => messageRouter.confirmAction(msg, callback, opts);
    }
    
    // Override global message functions to use router
    if (window.showSuccess) {
        const originalSuccess = window.showSuccess;
        window.showSuccess = (msg, opts) => {
            messageRouter.success(msg, opts);
            return originalSuccess ? originalSuccess(msg, opts) : null;
        };
    }
    
    if (window.showError) {
        const originalError = window.showError;
        window.showError = (msg, opts) => {
            messageRouter.error(msg, opts);
            return originalError ? originalError(msg, opts) : null;
        };
    }
    
    if (window.showWarning) {
        const originalWarning = window.showWarning;
        window.showWarning = (msg, opts) => {
            messageRouter.warning(msg, opts);
            return originalWarning ? originalWarning(msg, opts) : null;
        };
    }
    
    if (window.showInfo) {
        const originalInfo = window.showInfo;
        window.showInfo = (msg, opts) => {
            messageRouter.info(msg, opts);
            return originalInfo ? originalInfo(msg, opts) : null;
        };
    }
    
    // Export for CommonJS
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = MessageRouter; // Export class for CommonJS
    }
})();

// Export for ES modules - get the class from window since it's in IIFE
const MessageRouter = typeof window !== 'undefined' ? (window.MessageRouterClass || window.MessageRouter) : class {};
export { MessageRouter };
export default MessageRouter;

