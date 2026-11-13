/**
 * Global Modal Fix
 * Ensures proper modal behavior and body overflow restoration
 */

/**
 * Check if any modal is currently open
 */
export function isAnyModalOpen() {
    const modalSelectors = [
        '.modal.active',
        '.archives-modal.active',
        '.modal-overlay.active',
        '.message-detail-modal.active',
        '.preview-modal.active',
        '#fileModal.active'
    ];
    
    return modalSelectors.some(selector => {
        const modal = document.querySelector(selector);
        return modal && modal.classList.contains('active');
    });
}

/**
 * Restore body overflow if no modals are open
 */
export function restoreBodyOverflow() {
    if (!isAnyModalOpen()) {
        document.body.style.overflow = '';
    }
}

/**
 * Setup global modal close handlers
 */
export function setupGlobalModalHandlers() {
    // Listen for modal close events
    document.addEventListener('click', function(e) {
        // Close modal when clicking outside content (only on overlay itself, not children)
        const modals = document.querySelectorAll('.modal.active, .archives-modal.active, .modal-overlay.active, .message-detail-modal.active, .preview-modal.active');
        modals.forEach(modal => {
            // Only close if clicking directly on the modal overlay, not on any child elements
            if (e.target === modal && !modal.querySelector('.modal-content, .archives-modal-content, .modal-container, .message-detail-content, .preview-modal-content').contains(e.target)) {
                // Clicked on overlay background, close modal
                const closeBtn = modal.querySelector('.modal-close-btn, .add-item-modal-close, .archives-close-btn, [data-dismiss="modal"]');
                if (closeBtn) {
                    closeBtn.click();
                } else {
                    // Try to find close function
                    const modalId = modal.id;
                    if (modalId && window.closeAddItemModal) {
                        window.closeAddItemModal(modalId);
                    } else if (window.closeFileModal) {
                        window.closeFileModal();
                    }
                }
                restoreBodyOverflow();
            }
        });
    });
    
    // Restore body overflow when modals are closed
    const observer = new MutationObserver(function(mutations) {
        mutations.forEach(function(mutation) {
            if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
                restoreBodyOverflow();
            }
        });
    });
    
    // Observe all modal elements
    const modalElements = document.querySelectorAll('.modal, .archives-modal, .modal-overlay, .message-detail-modal, .preview-modal, #fileModal');
    modalElements.forEach(modal => {
        observer.observe(modal, {
            attributes: true,
            attributeFilter: ['class']
        });
    });
    
    // Also restore on page unload
    window.addEventListener('beforeunload', function() {
        document.body.style.overflow = '';
    });
}

// Initialize on DOM ready
if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setupGlobalModalHandlers);
    } else {
        setupGlobalModalHandlers();
    }
}

