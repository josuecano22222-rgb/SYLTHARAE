/**
 * Alert Replacement Helper
 * Provides a drop-in replacement for alert(), confirm(), and prompt() that uses the notification system
 * This ensures all alerts are translated and use the modern notification UI
 */

(function() {
    'use strict';
    
    // Store original functions
    const originalAlert = window.alert;
    const originalConfirm = window.confirm;
    const originalPrompt = window.prompt;
    
    /**
     * Replace alert() with notification system
     */
    window.alert = function(message) {
        if (window.MessageSystem && typeof window.MessageSystem.show === 'function') {
            window.MessageSystem.show(message || '', 'info', {
                title: window.TranslationHelper ? window.TranslationHelper.translate('Information') : 'Information',
                persistent: true,
                action: {
                    label: window.TranslationHelper ? window.TranslationHelper.translate('OK') : 'OK',
                    callback: function() {}
                }
            });
        } else {
            // Fallback to original alert if message system not available
            return originalAlert(message);
        }
    };
    
    /**
     * Replace confirm() with notification system
     * Returns a Promise that resolves to true/false
     */
    window.confirm = function(message) {
        // Use MessageRouter instance if available and has confirm method (better confirmation UI)
        if (window.MessageRouter && typeof window.MessageRouter.confirm === 'function') {
            return window.MessageRouter.confirm(message, {
                type: 'warning',
                persistent: true
            });
        }
        
        // Fallback to MessageSystem
        return new Promise(function(resolve) {
            if (window.MessageSystem && typeof window.MessageSystem.show === 'function') {
                window.MessageSystem.show(message || '', 'warning', {
                    title: window.TranslationHelper ? window.TranslationHelper.translate('Confirmation') : 'Confirmation',
                    persistent: true,
                    action: {
                        label: window.TranslationHelper ? window.TranslationHelper.translate('Yes') : 'Yes',
                        callback: function() {
                            resolve(true);
                        }
                    },
                    onClose: function() {
                        resolve(false);
                    }
                });
            } else {
                // Fallback to original confirm if message system not available
                resolve(originalConfirm(message));
            }
        });
    };
    
    /**
     * Helper to use confirm() in async/await context
     */
    window.confirmAsync = async function(message) {
        return await window.confirm(message);
    };
    
    /**
     * Helper to use prompt() in async/await context
     */
    window.promptAsync = async function(message, defaultValue) {
        return await window.prompt(message, defaultValue);
    };
    
    /**
     * Replace prompt() with a custom modal
     * Returns a Promise that resolves to the input value or null if cancelled
     */
    window.prompt = function(message, defaultValue) {
        return new Promise(function(resolve) {
            // Create modal HTML if it doesn't exist
            let promptModal = document.getElementById('customPromptModal');
            if (!promptModal) {
                promptModal = document.createElement('div');
                promptModal.id = 'customPromptModal';
                promptModal.className = 'modal fade';
                promptModal.setAttribute('tabindex', '-1');
                promptModal.setAttribute('aria-labelledby', 'customPromptModalLabel');
                promptModal.setAttribute('aria-hidden', 'true');
                promptModal.innerHTML = `
                    <div class="modal-dialog">
                        <div class="modal-content">
                            <div class="modal-header">
                                <h5 class="modal-title" id="customPromptModalLabel">
                                    ${window.TranslationHelper ? window.TranslationHelper.translate('Input') : 'Input'}
                                </h5>
                                <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                            </div>
                            <div class="modal-body">
                                <p id="customPromptMessage"></p>
                                <input type="text" class="form-control" id="customPromptInput" autocomplete="off">
                            </div>
                            <div class="modal-footer">
                                <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">
                                    ${window.TranslationHelper ? window.TranslationHelper.translate('Cancel') : 'Cancel'}
                                </button>
                                <button type="button" class="btn btn-primary" id="customPromptOk">
                                    ${window.TranslationHelper ? window.TranslationHelper.translate('OK') : 'OK'}
                                </button>
                            </div>
                        </div>
                    </div>
                `;
                document.body.appendChild(promptModal);
            }
            
            // Set message and default value
            const messageEl = document.getElementById('customPromptMessage');
            const inputEl = document.getElementById('customPromptInput');
            
            if (messageEl) {
                messageEl.textContent = message || '';
            }
            if (inputEl) {
                inputEl.value = defaultValue || '';
            }
            
            // Handle Bootstrap modal
            if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const modal = new bootstrap.Modal(promptModal);
                
                // Set up event handlers
                const okButton = document.getElementById('customPromptOk');
                const cancelHandler = function() {
                    resolve(null);
                    promptModal.removeEventListener('hidden.bs.modal', cancelHandler);
                };
                
                const okHandler = function() {
                    const value = inputEl ? inputEl.value : null;
                    resolve(value);
                    modal.hide();
                    promptModal.removeEventListener('hidden.bs.modal', cancelHandler);
                };
                
                // Remove any existing listeners
                const newOkButton = okButton.cloneNode(true);
                okButton.parentNode.replaceChild(newOkButton, okButton);
                
                // Add new listeners
                newOkButton.addEventListener('click', okHandler);
                promptModal.addEventListener('hidden.bs.modal', cancelHandler);
                
                // Handle Enter key in input
                if (inputEl) {
                    const enterHandler = function(e) {
                        if (e.key === 'Enter') {
                            e.preventDefault();
                            newOkButton.click();
                        }
                    };
                    inputEl.addEventListener('keypress', enterHandler);
                    
                    // Clean up enter handler when modal is hidden
                    promptModal.addEventListener('hidden.bs.modal', function() {
                        inputEl.removeEventListener('keypress', enterHandler);
                    }, { once: true });
                }
                
                // Show modal and focus input
                modal.show();
                
                // Focus input after modal is shown
                promptModal.addEventListener('shown.bs.modal', function() {
                    if (inputEl) {
                        inputEl.focus();
                        inputEl.select();
                    }
                }, { once: true });
                
            } else {
                // Fallback to original prompt if Bootstrap not available
                console.warn('Bootstrap not available for prompt replacement, using original');
                resolve(originalPrompt(message, defaultValue));
            }
        });
    };
    
    // Export for CommonJS
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            originalAlert: originalAlert,
            originalConfirm: originalConfirm,
            originalPrompt: originalPrompt
        };
    }
    
    // Expose on window for ES module exports
    if (typeof window !== 'undefined') {
        window.__originalAlert = originalAlert;
        window.__originalConfirm = originalConfirm;
        window.__originalPrompt = originalPrompt;
    }
})();

// Export for ES modules - get from window since they're in IIFE
const originalAlert = typeof window !== 'undefined' ? window.__originalAlert : function() {};
const originalConfirm = typeof window !== 'undefined' ? window.__originalConfirm : function() {};
const originalPrompt = typeof window !== 'undefined' ? window.__originalPrompt : function() {};
export { originalAlert, originalConfirm, originalPrompt };

