/**
 * Interface Messages Helper
 * Provides helper functions for common interface operations (Add, Update, Save, Delete, etc.)
 * All messages are automatically translated and use the notification system
 */

(function() {
    'use strict';
    
    class InterfaceMessages {
        constructor() {
            this.messageRouter = null;
            this.init();
        }
        
        init() {
            // Wait for MessageRouter to be available
            if (window.MessageRouter) {
                this.messageRouter = window.MessageRouter;
            } else {
                setTimeout(() => {
                    if (window.MessageRouter) {
                        this.messageRouter = window.MessageRouter;
                    }
                }, 100);
            }
        }
        
        /**
         * Translate a message
         */
        translate(key) {
            if (this.messageRouter) {
                return this.messageRouter.translate(key);
            }
            if (window.t) {
                return window.t(key);
            }
            return key;
        }
        
        /**
         * Show success message for Add operation
         */
        addSuccess(itemName = 'Item') {
            const message = this.translate('Item added successfully').replace('Item', itemName);
            if (this.messageRouter) {
                this.messageRouter.success(message);
            } else if (window.showSuccess) {
                window.showSuccess(message);
            }
        }
        
        /**
         * Show success message for Update operation
         */
        updateSuccess(itemName = 'Item') {
            const message = this.translate('Item updated successfully').replace('Item', itemName);
            if (this.messageRouter) {
                this.messageRouter.success(message);
            } else if (window.showSuccess) {
                window.showSuccess(message);
            }
        }
        
        /**
         * Show success message for Save operation
         */
        saveSuccess() {
            const message = this.translate('Changes saved successfully');
            if (this.messageRouter) {
                this.messageRouter.success(message);
            } else if (window.showSuccess) {
                window.showSuccess(message);
            }
        }
        
        /**
         * Show success message for Delete operation
         */
        deleteSuccess(itemName = 'Item') {
            const message = this.translate('Item deleted successfully').replace('Item', itemName);
            if (this.messageRouter) {
                this.messageRouter.success(message);
            } else if (window.showSuccess) {
                window.showSuccess(message);
            }
        }
        
        /**
         * Show success message for Create operation
         */
        createSuccess(itemName = 'Item') {
            const message = this.translate('Item created successfully').replace('Item', itemName);
            if (this.messageRouter) {
                this.messageRouter.success(message);
            } else if (window.showSuccess) {
                window.showSuccess(message);
            }
        }
        
        /**
         * Show success message for Remove operation
         */
        removeSuccess(itemName = 'Item') {
            const message = this.translate('Item removed successfully').replace('Item', itemName);
            if (this.messageRouter) {
                this.messageRouter.success(message);
            } else if (window.showSuccess) {
                window.showSuccess(message);
            }
        }
        
        /**
         * Show error message for Add operation
         */
        addError(itemName = 'Item') {
            const message = this.translate('Error adding item');
            if (this.messageRouter) {
                this.messageRouter.error(message);
            } else if (window.showError) {
                window.showError(message);
            }
        }
        
        /**
         * Show error message for Update operation
         */
        updateError(itemName = 'Item') {
            const message = this.translate('Error updating item');
            if (this.messageRouter) {
                this.messageRouter.error(message);
            } else if (window.showError) {
                window.showError(message);
            }
        }
        
        /**
         * Show error message for Save operation
         */
        saveError() {
            const message = this.translate('Failed to save changes');
            if (this.messageRouter) {
                this.messageRouter.error(message);
            } else if (window.showError) {
                window.showError(message);
            }
        }
        
        /**
         * Show error message for Delete operation
         */
        deleteError(itemName = 'Item') {
            const message = this.translate('Error deleting item');
            if (this.messageRouter) {
                this.messageRouter.error(message);
            } else if (window.showError) {
                window.showError(message);
            }
        }
        
        /**
         * Show error message for Create operation
         */
        createError(itemName = 'Item') {
            const message = this.translate('Error creating item');
            if (this.messageRouter) {
                this.messageRouter.error(message);
            } else if (window.showError) {
                window.showError(message);
            }
        }
        
        /**
         * Show error message for Remove operation
         */
        removeError(itemName = 'Item') {
            const message = this.translate('Error removing item');
            if (this.messageRouter) {
                this.messageRouter.error(message);
            } else if (window.showError) {
                window.showError(message);
            }
        }
        
        /**
         * Show processing message for Add operation
         */
        adding() {
            const message = this.translate('Adding...');
            if (window.showProcessing) {
                window.showProcessing(message);
            }
        }
        
        /**
         * Show processing message for Update operation
         */
        updating() {
            const message = this.translate('Updating...');
            if (window.showProcessing) {
                window.showProcessing(message);
            }
        }
        
        /**
         * Show processing message for Save operation
         */
        saving() {
            const message = this.translate('Saving...');
            if (window.showProcessing) {
                window.showProcessing(message);
            }
        }
        
        /**
         * Show processing message for Delete operation
         */
        deleting() {
            const message = this.translate('Deleting...');
            if (window.showProcessing) {
                window.showProcessing(message);
            }
        }
        
        /**
         * Show processing message for Create operation
         */
        creating() {
            const message = this.translate('Creating...');
            if (window.showProcessing) {
                window.showProcessing(message);
            }
        }
        
        /**
         * Show processing message for Remove operation
         */
        removing() {
            const message = this.translate('Removing...');
            if (window.showProcessing) {
                window.showProcessing(message);
            }
        }
        
        /**
         * Show validation error
         */
        validationError(message = null) {
            const errorMsg = message || this.translate('Please fill in all required fields');
            if (this.messageRouter) {
                this.messageRouter.warning(errorMsg);
            } else if (window.showWarning) {
                window.showWarning(errorMsg);
            }
        }
        
        /**
         * Show "no items selected" warning
         */
        noItemsSelected() {
            const message = this.translate('No items selected');
            if (this.messageRouter) {
                this.messageRouter.warning(message);
            } else if (window.showWarning) {
                window.showWarning(message);
            }
        }
        
        /**
         * Show "no changes to save" info
         */
        noChangesToSave() {
            const message = this.translate('No changes to save');
            if (this.messageRouter) {
                this.messageRouter.info(message);
            } else if (window.showInfo) {
                window.showInfo(message);
            }
        }
        
        /**
         * Confirm delete action
         */
        confirmDelete(itemName = 'this item', callback) {
            const message = this.translate('Are you sure you want to delete {item}? This action cannot be undone.').replace('{item}', itemName);
            if (window.showConfirm) {
                window.showConfirm(message, {
                    title: this.translate('Delete Confirmation'),
                    confirmLabel: this.translate('Delete'),
                    cancelLabel: this.translate('Cancel'),
                    type: 'warning'
                }).then((confirmed) => {
                    if (confirmed && callback) {
                        callback();
                    }
                });
            } else if (window.confirm) {
                if (window.confirm(message)) {
                    if (callback) callback();
                }
            }
        }
        
        /**
         * Confirm save action (if there are unsaved changes)
         */
        confirmSave(callback) {
            const message = this.translate('You have unsaved changes. Do you want to save them?');
            if (window.showConfirm) {
                window.showConfirm(message, {
                    title: this.translate('Save Confirmation'),
                    confirmLabel: this.translate('Save'),
                    cancelLabel: this.translate('Cancel'),
                    type: 'warning'
                }).then((confirmed) => {
                    if (confirmed && callback) {
                        callback();
                    }
                });
            } else if (window.confirm) {
                if (window.confirm(message)) {
                    if (callback) callback();
                }
            }
        }
        
        /**
         * Handle API response for Add operation
         */
        handleAddResponse(response, itemName = 'Item') {
            if (response && response.success !== false) {
                this.addSuccess(itemName);
                return true;
            } else {
                this.addError(itemName);
                return false;
            }
        }
        
        /**
         * Handle API response for Update operation
         */
        handleUpdateResponse(response, itemName = 'Item') {
            if (response && response.success !== false) {
                this.updateSuccess(itemName);
                return true;
            } else {
                this.updateError(itemName);
                return false;
            }
        }
        
        /**
         * Handle API response for Delete operation
         */
        handleDeleteResponse(response, itemName = 'Item') {
            if (response && response.success !== false) {
                this.deleteSuccess(itemName);
                return true;
            } else {
                this.deleteError(itemName);
                return false;
            }
        }
        
        /**
         * Handle API response for Save operation
         */
        handleSaveResponse(response) {
            if (response && response.success !== false) {
                this.saveSuccess();
                return true;
            } else {
                this.saveError();
                return false;
            }
        }
    }
    
    // Initialize interface messages helper
    const interfaceMessages = new InterfaceMessages();
    
    // Export globally
    window.InterfaceMessages = interfaceMessages;
    
    // Convenience functions
    window.showAddSuccess = (itemName) => interfaceMessages.addSuccess(itemName);
    window.showUpdateSuccess = (itemName) => interfaceMessages.updateSuccess(itemName);
    window.showSaveSuccess = () => interfaceMessages.saveSuccess();
    window.showDeleteSuccess = (itemName) => interfaceMessages.deleteSuccess(itemName);
    window.showCreateSuccess = (itemName) => interfaceMessages.createSuccess(itemName);
    window.showRemoveSuccess = (itemName) => interfaceMessages.removeSuccess(itemName);
    
    window.showAddError = (itemName) => interfaceMessages.addError(itemName);
    window.showUpdateError = (itemName) => interfaceMessages.updateError(itemName);
    window.showSaveError = () => interfaceMessages.saveError();
    window.showDeleteError = (itemName) => interfaceMessages.deleteError(itemName);
    window.showCreateError = (itemName) => interfaceMessages.createError(itemName);
    window.showRemoveError = (itemName) => interfaceMessages.removeError(itemName);
    
    window.showValidationError = (message) => interfaceMessages.validationError(message);
    window.showNoItemsSelected = () => interfaceMessages.noItemsSelected();
    window.showNoChangesToSave = () => interfaceMessages.noChangesToSave();
    
    window.confirmDelete = (itemName, callback) => interfaceMessages.confirmDelete(itemName, callback);
    window.confirmSave = (callback) => interfaceMessages.confirmSave(callback);
    
    // Export for modules
    // Export for CommonJS
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = InterfaceMessages;
    }
    
    // Expose on window for ES module exports
    if (typeof window !== 'undefined') {
        window.InterfaceMessages = InterfaceMessages;
    }
})();

// Export for ES modules - get from window since it's in IIFE
const InterfaceMessages = typeof window !== 'undefined' ? window.InterfaceMessages : class {};
export { InterfaceMessages };
export default InterfaceMessages;

